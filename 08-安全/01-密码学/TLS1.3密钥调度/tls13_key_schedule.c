/* TLS 1.3 密钥调度（RFC 8446 §7.1-§7.3）的 C 实现：零依赖、只吃固定长度缓冲。
 * 编译： cc -O2 -o ks tls13_key_schedule.c && ./ks
 *
 * 自底向上：SHA-256(FIPS 180-4) -> HMAC(RFC 2104) -> HKDF(RFC 5869)
 *           -> HKDF-Expand-Label / Derive-Secret -> 五层 Secret -> traffic key/iv/nonce
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

typedef unsigned char u8;
typedef unsigned int u32;

#define HL 32 /* Hash.length(SHA-256) */

/* ------------------------------- SHA-256 ------------------------------- */
static const u32 K[64] = {
    0x428A2F98u,0x71374491u,0xB5C0FBCFu,0xE9B5DBA5u,0x3956C25Bu,0x59F111F1u,
    0x923F82A4u,0xAB1C5ED5u,0xD807AA98u,0x12835B01u,0x243185BEu,0x550C7DC3u,
    0x72BE5D74u,0x80DEB1FEu,0x9BDC06A7u,0xC19BF174u,0xE49B69C1u,0xEFBE4786u,
    0x0FC19DC6u,0x240CA1CCu,0x2DE92C6Fu,0x4A7484AAu,0x5CB0A9DCu,0x76F988DAu,
    0x983E5152u,0xA831C66Du,0xB00327C8u,0xBF597FC7u,0xC6E00BF3u,0xD5A79147u,
    0x06CA6351u,0x14292967u,0x27B70A85u,0x2E1B2138u,0x4D2C6DFCu,0x53380D13u,
    0x650A7354u,0x766A0ABBu,0x81C2C92Eu,0x92722C85u,0xA2BFE8A1u,0xA81A664Bu,
    0xC24B8B70u,0xC76C51A3u,0xD192E819u,0xD6990624u,0xF40E3585u,0x106AA070u,
    0x19A4C116u,0x1E376C08u,0x2748774Cu,0x34B0BCB5u,0x391C0CB3u,0x4ED8AA4Au,
    0x5B9CCA4Fu,0x682E6FF3u,0x748F82EEu,0x78A5636Fu,0x84C87814u,0x8CC70208u,
    0x90BEFFFAu,0xA4506CEBu,0xBEF9A3F7u,0xC67178F2u};

#define ROTR(x,n) (((x) >> (n)) | ((x) << (32-(n))))

static void sha256(const u8 *d, size_t n, u8 out[HL]) {
    u32 h[8], w[64]; unsigned long long bits; size_t total, i, off; u8 *buf;
    u32 a,b,c,dd,e,f,g,hh;
    const u32 iv[8] = {0x6A09E667u,0xBB67AE85u,0x3C6EF372u,0xA54FF53Au,
                       0x510E527Fu,0x9B05688Cu,0x1F83D9ABu,0x5BE0CD19u};
    memcpy(h, iv, sizeof(iv));
    total = ((n + 8) / 64 + 1) * 64;
    buf = (u8*)malloc(total);
    memset(buf, 0, total);
    memcpy(buf, d, n);
    buf[n] = 0x80;
    bits = (unsigned long long)n * 8;
    for (i = 0; i < 8; i++) buf[total-1-i] = (u8)(bits >> (8*i));
    for (off = 0; off < total; off += 64) {
        for (i = 0; i < 16; i++)
            w[i] = ((u32)buf[off+4*i]<<24)|((u32)buf[off+4*i+1]<<16)
                 |((u32)buf[off+4*i+2]<<8)|(u32)buf[off+4*i+3];
        for (i = 16; i < 64; i++) {
            u32 s0 = ROTR(w[i-15],7)^ROTR(w[i-15],18)^(w[i-15]>>3);
            u32 s1 = ROTR(w[i-2],17)^ROTR(w[i-2],19)^(w[i-2]>>10);
            w[i] = w[i-16]+s0+w[i-7]+s1;
        }
        a=h[0];b=h[1];c=h[2];dd=h[3];e=h[4];f=h[5];g=h[6];hh=h[7];
        for (i = 0; i < 64; i++) {
            u32 s1 = ROTR(e,6)^ROTR(e,11)^ROTR(e,25);
            u32 ch = (e&f)^((~e)&g);
            u32 t1 = hh+s1+ch+K[i]+w[i];
            u32 s0 = ROTR(a,2)^ROTR(a,13)^ROTR(a,22);
            u32 mj = (a&b)^(a&c)^(b&c);
            u32 t2 = s0+mj;
            hh=g;g=f;f=e;e=dd+t1;dd=c;c=b;b=a;a=t1+t2;
        }
        h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=dd;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=hh;
    }
    for (i = 0; i < 8; i++) {
        out[4*i]=(u8)(h[i]>>24); out[4*i+1]=(u8)(h[i]>>16);
        out[4*i+2]=(u8)(h[i]>>8); out[4*i+3]=(u8)h[i];
    }
    free(buf);
}

/* --------------------------- HMAC / HKDF --------------------------- */
static void hmac_sha256(const u8 *key, size_t klen, const u8 *msg, size_t mlen, u8 out[HL]) {
    u8 k[64], ipad[64], opad[64], tmp[HL], *inner;
    size_t i;
    memset(k, 0, 64);
    if (klen > 64) { sha256(key, klen, tmp); memcpy(k, tmp, HL); }
    else memcpy(k, key, klen);
    for (i = 0; i < 64; i++) { ipad[i]=k[i]^0x36; opad[i]=k[i]^0x5C; }
    inner = (u8*)malloc(64 + mlen);
    memcpy(inner, ipad, 64);
    memcpy(inner+64, msg, mlen);
    sha256(inner, 64+mlen, tmp);
    free(inner);
    inner = (u8*)malloc(64 + HL);
    memcpy(inner, opad, 64);
    memcpy(inner+64, tmp, HL);
    sha256(inner, 64+HL, out);
    free(inner);
}

static void hkdf_extract(const u8 *salt, size_t slen, const u8 *ikm, size_t ilen, u8 out[HL]) {
    hmac_sha256(salt, slen, ikm, ilen, out);
}

static void hkdf_expand(const u8 prk[HL], const u8 *info, size_t ilen, u8 *out, size_t L) {
    u8 t[HL], *cat; size_t pos = 0, i; u8 ctr = 1;
    memset(t, 0, HL);
    cat = (u8*)malloc(HL + ilen + 1);
    while (pos < L) {
        memcpy(cat, t, HL);
        memcpy(cat+HL, info, ilen);
        cat[HL+ilen] = ctr++;
        hmac_sha256(prk, HL, cat, HL+ilen+1, t);
        for (i = 0; i < HL && pos < L; i++) out[pos++] = t[i];
    }
    free(cat);
}

/* ------------------- HkdfLabel / Derive-Secret ------------------- */
/* HkdfLabel = uint16 length || len(label) || "tls13 "+Label || len(ctx) || ctx */
static size_t hkdf_label(int length, const char *label, const u8 *ctx, size_t clen, u8 *out) {
    size_t n = 0, ll = strlen(label) + 6;
    out[n++] = (u8)(length >> 8); out[n++] = (u8)(length & 0xFF);
    out[n++] = (u8)ll;
    memcpy(out+n, "tls13 ", 6); n += 6;
    memcpy(out+n, label, strlen(label)); n += strlen(label);
    out[n++] = (u8)clen;
    if (clen) memcpy(out+n, ctx, clen);
    return n + clen;
}

static void expand_label(const u8 secret[HL], const char *label,
                         const u8 *ctx, size_t clen, u8 *out, int L) {
    u8 lab[320]; size_t n = hkdf_label(L, label, ctx, clen, lab);
    hkdf_expand(secret, lab, n, out, (size_t)L);
}

static void derive_secret(const u8 secret[HL], const char *label,
                          const u8 *ctx, size_t clen, u8 out[HL]) {
    expand_label(secret, label, ctx, clen, out, HL);
}

/* 多段握手消息拼接后的 Transcript-Hash */
static void th_of(const char **parts, int nparts, u8 out[HL]) {
    u8 *buf = NULL; size_t total = 0, off = 0; int i;
    for (i = 0; i < nparts; i++) total += strlen(parts[i]);
    buf = (u8*)malloc(total ? total : 1);
    for (i = 0; i < nparts; i++) {
        size_t l = strlen(parts[i]);
        memcpy(buf+off, parts[i], l); off += l;
    }
    sha256(buf, off, out);
    free(buf);
}

/* ------------------------- 五层 Secret 调度 ------------------------- */
typedef struct {
    u8 early[HL], binder_ext[HL], binder_res[HL], c_early[HL], e_exp[HL];
    u8 handshake[HL], c_hs[HL], s_hs[HL], master[HL];
    u8 c_ap[HL], s_ap[HL], exp_master[HL], res_master[HL];
} sched;

static void key_schedule(const u8 *psk, const u8 *dhe, sched *s) {
    static const u8 zero[HL] = {0};
    u8 derived[HL], th_ch[HL], th_hs[HL], th_ap[HL];
    const char *p1[1], *p2[2], *p3[3];
    p1[0] = "CH"; p2[0] = "CH"; p2[1] = "SH"; p3[0] = "CH"; p3[1] = "SH"; p3[2] = "SF";
    th_of(p1, 1, th_ch);
    th_of(p2, 2, th_hs);
    th_of(p3, 3, th_ap);

    if (!psk) psk = zero;
    if (!dhe) dhe = zero;
    hkdf_extract(zero, HL, psk, HL, s->early);                 /* Early Secret */
    derive_secret(s->early, "ext binder", NULL, 0, s->binder_ext);
    derive_secret(s->early, "res binder", NULL, 0, s->binder_res);
    derive_secret(s->early, "c e traffic", th_ch, HL, s->c_early);
    derive_secret(s->early, "e exp master", th_ch, HL, s->e_exp);

    derive_secret(s->early, "derived", NULL, 0, derived);
    hkdf_extract(derived, HL, dhe, HL, s->handshake);          /* Handshake Secret */
    derive_secret(s->handshake, "c hs traffic", th_hs, HL, s->c_hs);
    derive_secret(s->handshake, "s hs traffic", th_hs, HL, s->s_hs);

    derive_secret(s->handshake, "derived", NULL, 0, derived);
    hkdf_extract(derived, HL, zero, HL, s->master);            /* Master Secret */
    derive_secret(s->master, "c ap traffic", th_ap, HL, s->c_ap);
    derive_secret(s->master, "s ap traffic", th_ap, HL, s->s_ap);
    derive_secret(s->master, "exp master", th_ap, HL, s->exp_master);
    derive_secret(s->master, "res master", th_ap, HL, s->res_master);
}

/* §5.3：nonce = (左补零到 iv_length 的序列号) XOR 静态 IV */
static void aead_nonce(const u8 *iv, size_t ivlen, unsigned long long seq, u8 *out) {
    size_t i;
    for (i = 0; i < ivlen; i++) {
        int shift = (int)(8 * (ivlen - 1 - i));
        out[i] = iv[i] ^ (u8)((seq >> shift) & 0xFF);
    }
}

static void hexs(const u8 *b, size_t n, char *out) {
    static const char *HX = "0123456789abcdef"; size_t i;
    for (i = 0; i < n; i++) { out[2*i] = HX[b[i]>>4]; out[2*i+1] = HX[b[i]&15]; }
    out[2*n] = 0;
}

/* ------------------------------- 自检 ------------------------------- */
static int ok = 0;
static void chk(int cond, const char *msg) {
    if (!cond) { printf("断言失败: %s\n", msg); exit(1); }
    ok++;
}
static int eqhex(const u8 *b, size_t n, const char *want) {
    char buf[256]; hexs(b, n, buf); return strcmp(buf, want) == 0;
}

int main(void) {
    u8 h[HL], tmp[HL], key[16], iv[12], nonce[12], z[HL];
    memset(z, 0, HL);
    u8 lab[320]; size_t labn;
    sched s, s2;
    u8 dhe[HL], psk[HL], dhe2[HL];
    size_t i;
    for (i = 0; i < HL; i++) { dhe[i] = (u8)i; psk[i] = 0x11; dhe2[i] = 0x5A; }

    /* 底座向量：FIPS 180-4 / RFC 4231 */
    sha256((const u8*)"abc", 3, h);
    chk(eqhex(h, HL, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
        "SHA-256(abc)");
    memset(tmp, 0x0b, 20);
    hmac_sha256(tmp, 20, (const u8*)"Hi There", 8, h);
    chk(eqhex(h, HL, "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"),
        "HMAC RFC 4231 #1");

    /* HkdfLabel 编码 */
    labn = hkdf_label(HL, "derived", NULL, 0, lab);
    chk(labn == 2 + 1 + 13 + 1, "HkdfLabel 总长 2+1+13+1");
    chk(lab[0] == 0x00 && lab[1] == 0x20, "uint16 大端长度");
    chk(lab[2] == 13 && memcmp(lab+3, "tls13 derived", 13) == 0, "标签恒带 tls13 前缀");
    chk(lab[labn-1] == 0, "空 context 也占 1 字节长度");

    /* 五层调度 */
    key_schedule(NULL, dhe, &s);
    hkdf_extract((const u8*)"\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0",
                 HL, (const u8*)"\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0",
                 HL, tmp);
    chk(memcmp(s.early, tmp, HL) == 0, "无 PSK 时 Early Secret = HKDF-Extract(0,0)");
    chk(memcmp(s.binder_ext, s.binder_res, HL) != 0, "ext/res binder 标签分离");
    chk(memcmp(s.c_hs, s.s_hs, HL) != 0, "c/s hs traffic 分离");
    chk(memcmp(s.c_ap, s.s_ap, HL) != 0, "c/s ap traffic 分离");

    key_schedule(NULL, dhe2, &s2);
    chk(memcmp(s.early, s2.early, HL) == 0, "Early Secret 与 DHE 无关");
    chk(memcmp(s.handshake, s2.handshake, HL) != 0, "DHE 从 Handshake Secret 起注入");
    key_schedule(psk, dhe, &s2);
    chk(memcmp(s.early, s2.early, HL) != 0, "PSK 改变 Early Secret");

    /* traffic key/iv/nonce */
    expand_label(s.c_ap, "key", NULL, 0, key, 16);
    expand_label(s.c_ap, "iv", NULL, 0, iv, 12);
    aead_nonce(iv, 12, 0, nonce);
    chk(memcmp(iv, nonce, 12) == 0, "seq=0 时 nonce 等于 IV");
    aead_nonce(iv, 12, 1, nonce);
    chk(nonce[11] == (u8)(iv[11] ^ 1) && memcmp(nonce, iv, 11) == 0, "nonce = 左补零 seq XOR IV");

    /* KeyUpdate 更新链 */
    expand_label(s.c_ap, "traffic upd", NULL, 0, tmp, HL);
    chk(memcmp(tmp, s.c_ap, HL) != 0, "KeyUpdate 换新 secret");
    expand_label(tmp, "traffic upd", NULL, 0, h, HL);
    chk(memcmp(h, tmp, HL) != 0, "更新链单调推进");

    printf("TLS 1.3 密钥调度 C 自检通过：%d 项断言\n", ok);
    return 0;
}
