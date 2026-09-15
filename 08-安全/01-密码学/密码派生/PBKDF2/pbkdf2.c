/* PBKDF2: 基于口令的密钥派生(RFC 8018 §5.2), 向量取自 RFC 6070 — C 实现(自含 SHA-1)。
 * F(P,S,c,i): U1=PRF(P,S||INT(i)), Uj=PRF(P,U(j-1)), F=U1^...^Uc; DK=拼接截断。
 * 编译: cc pbkdf2.c -o pbkdf2 && ./pbkdf2 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ---------- SHA-1(FIPS 180-1) ---------- */
static void sha1_compress(uint32_t h[5], const uint8_t blk[64]) {
    uint32_t w[80];
    for (int i = 0; i < 16; i++)
        w[i] = ((uint32_t)blk[4 * i] << 24) | ((uint32_t)blk[4 * i + 1] << 16) |
               ((uint32_t)blk[4 * i + 2] << 8) | (uint32_t)blk[4 * i + 3];
    for (int i = 16; i < 80; i++) {
        uint32_t v = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
        w[i] = (v << 1) | (v >> 31);
    }
    uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4];
    for (int i = 0; i < 80; i++) {
        uint32_t f, k;
        if (i < 20) { f = (b & c) | (~b & d); k = 0x5A827999; }
        else if (i < 40) { f = b ^ c ^ d; k = 0x6ED9EBA1; }
        else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC; }
        else { f = b ^ c ^ d; k = 0xCA62C1D6; }
        uint32_t t = ((a << 5) | (a >> 27)) + f + e + k + w[i];
        e = d; d = c; c = (b << 30) | (b >> 2); b = a; a = t;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e;
}

static void sha1(const uint8_t *msg, size_t len, uint8_t out[20]) {
    uint32_t h[5] = {0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0};
    size_t off = 0;
    while (off + 64 <= len) {
        sha1_compress(h, msg + off);
        off += 64;
    }
    uint8_t blk[64];
    size_t rem = len - off;
    memset(blk, 0, 64);
    memcpy(blk, msg + off, rem);
    blk[rem] = 0x80;
    uint64_t bits = (uint64_t)len * 8;
    if (rem >= 56) {
        sha1_compress(h, blk);
        memset(blk, 0, 64);
    }
    for (int i = 0; i < 8; i++)
        blk[63 - i] = (uint8_t)(bits >> (8 * i));
    sha1_compress(h, blk);
    for (int i = 0; i < 5; i++) {
        out[4 * i] = (uint8_t)(h[i] >> 24);
        out[4 * i + 1] = (uint8_t)(h[i] >> 16);
        out[4 * i + 2] = (uint8_t)(h[i] >> 8);
        out[4 * i + 3] = (uint8_t)h[i];
    }
}

/* ---------- HMAC-SHA-1(RFC 2104, B=64, L=20) ---------- */
static void hmac_sha1(const uint8_t *key, size_t klen, const uint8_t *msg,
                      size_t mlen, uint8_t out[20]) {
    uint8_t k[64], inner[20], *m = malloc(64 + (mlen > 20 ? mlen : 20));
    memset(k, 0, 64);
    if (klen > 64)
        sha1(key, klen, k);
    else
        memcpy(k, key, klen);
    for (int i = 0; i < 64; i++)
        m[i] = k[i] ^ 0x36;
    memcpy(m + 64, msg, mlen);
    sha1(m, 64 + mlen, inner);
    for (int i = 0; i < 64; i++)
        m[i] = k[i] ^ 0x5c;
    memcpy(m + 64, inner, 20);
    sha1(m, 84, out);
    free(m);
}

/* ---------- PBKDF2(RFC 8018 §5.2) ---------- */
static void pbkdf2(const uint8_t *pw, size_t plen, const uint8_t *salt, size_t slen,
                   long iterations, size_t dkLen, uint8_t *dk) {
    int blocks = (int)((dkLen + 19) / 20); /* ceil(dkLen/20), SHA-1 输出 20 字节 */
    uint8_t u[20], f[20];
    for (int i = 1; i <= blocks; i++) {
        uint8_t *si = malloc(slen + 4);
        memcpy(si, salt, slen);
        si[slen] = (uint8_t)(i >> 24); /* INT(i) 大端 4 字节 */
        si[slen + 1] = (uint8_t)(i >> 16);
        si[slen + 2] = (uint8_t)(i >> 8);
        si[slen + 3] = (uint8_t)i;
        hmac_sha1(pw, plen, si, slen + 4, u);
        memcpy(f, u, 20);
        for (long j = 1; j < iterations; j++) {
            hmac_sha1(pw, plen, u, 20, u);
            for (int k = 0; k < 20; k++)
                f[k] ^= u[k];
        }
        size_t take = dkLen - (size_t)(i - 1) * 20 < 20
                          ? dkLen - (size_t)(i - 1) * 20 : 20;
        memcpy(dk + (size_t)(i - 1) * 20, f, take);
        free(si);
    }
}

static int hex2bin(const char *s, uint8_t *out) {
    size_t n = strlen(s) / 2;
    for (size_t i = 0; i < n; i++)
        sscanf(s + 2 * i, "%2hhx", out + i);
    return (int)n;
}

int main(void) {
    uint8_t dk[64], want[64];
    int fail = 0, n;
    /* demo1: c=1 / c=2 */
    pbkdf2((const uint8_t *)"password", 8, (const uint8_t *)"salt", 4, 1, 20, dk);
    n = hex2bin("0c60c80f961f0e71f3a9b524af6012062fe037a6", want);
    if (memcmp(dk, want, n)) { puts("FAIL c=1"); fail++; }
    pbkdf2((const uint8_t *)"password", 8, (const uint8_t *)"salt", 4, 2, 20, dk);
    n = hex2bin("ea6c014dc72d6f8ccd1ed92ace1d41f0d8de8957", want);
    if (memcmp(dk, want, n)) { puts("FAIL c=2"); fail++; }
    if (!fail) puts("demo1 RFC 6070 c=1 / c=2 基本向量: PASS");
    /* demo2: c=4096 */
    pbkdf2((const uint8_t *)"password", 8, (const uint8_t *)"salt", 4, 4096, 20, dk);
    n = hex2bin("4b007901b765489abead49d926f721d065a429c1", want);
    if (memcmp(dk, want, n)) { puts("FAIL c=4096"); fail++; }
    else puts("demo2 RFC 6070 c=4096 标准迭代: PASS");
    /* demo3: 长口令长盐 dkLen=25 */
    pbkdf2((const uint8_t *)"passwordPASSWORDpassword", 24,
           (const uint8_t *)"saltSALTsaltSALTsaltSALTsaltSALTsalt", 36, 4096, 25, dk);
    n = hex2bin("3d2eec4fe41c849b80c8d83662c0e44a8b291a964cf2f07038", want);
    if (memcmp(dk, want, n)) { puts("FAIL dkLen=25"); fail++; }
    else puts("demo3 RFC 6070 长口令长盐 dkLen=25: PASS");
    /* demo4: NUL 字节用例 */
    {
        uint8_t pw[9] = {'p', 'a', 's', 's', 0, 'w', 'o', 'r', 'd'};
        uint8_t st[5] = {'s', 'a', 0, 'l', 't'};
        pbkdf2(pw, 9, st, 5, 4096, 16, dk);
        n = hex2bin("56fa6aa75548099dcc37d7f03425e0c3", want);
        if (memcmp(dk, want, n)) { puts("FAIL NUL"); fail++; }
        else puts("demo4 RFC 6070 含 NUL 字节口令/盐: PASS");
    }
    /* demo5: 确定性/迭代敏感/口令雪崩/盐隔离 */
    {
        uint8_t a[20], b[20], c[20], d[20], e[20];
        pbkdf2((const uint8_t *)"pw", 2, (const uint8_t *)"salt", 4, 100, 20, a);
        pbkdf2((const uint8_t *)"pw", 2, (const uint8_t *)"salt", 4, 100, 20, b);
        pbkdf2((const uint8_t *)"pw", 2, (const uint8_t *)"salt", 4, 101, 20, c);
        pbkdf2((const uint8_t *)"px", 2, (const uint8_t *)"salt", 4, 100, 20, d);
        pbkdf2((const uint8_t *)"pw", 2, (const uint8_t *)"slat", 4, 100, 20, e);
        if (memcmp(a, b, 20) || !memcmp(a, c, 20) || !memcmp(a, d, 20) ||
            !memcmp(a, e, 20)) {
            puts("FAIL demo5");
            fail++;
        } else {
            puts("demo5 确定性/迭代敏感/口令雪崩/盐隔离: PASS");
        }
    }
    if (fail) { puts("SOME FAILED"); return 1; }
    puts("ALL PASS");
    return 0;
}
