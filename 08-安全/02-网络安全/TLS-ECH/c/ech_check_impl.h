/*
 * ech_check_impl.h —— 自检公共设施：断言计数、十六进制与协议夹具
 *
 * 计数器做成文件级 static：check() 的调用点多达九十个，逐个传指针才是抄错的主因。
 * 夹具里的大对象（ech_client_hello 约 3.4 KB）一律声明成 static —— 单个用例里同时
 * 活着五六个实例就超过 20 KB，多轮叠加会逼近 Windows 默认 1 MB 栈。
 *
 * 服务器密钥对固定取自 RFC 9180 附录 A.2 的 ikmR/skRm/pkRm：这样 ECH 端到端用例与
 * HPKE 官方向量共用同一把密钥，任何一层（suite_id 长度、"HPKE-v1" 前缀、三个
 * LabeledExtract 的盐、nonce 的 XOR 位置）错位都会同时把两组用例打红。
 */
#ifndef ECH_CHECK_IMPL_H
#define ECH_CHECK_IMPL_H

#include <stdio.h>

#include "ech_flow_impl.h"

static int g_checks;
static int g_failed;

static void check(const char *label, int ok, const char *detail)
{
    g_checks++;
    if (!ok) {
        g_failed++;
        printf("  [FAIL] %s | %s\n", label, detail != NULL ? detail : "");
    }
}

/* ---------------------------------------------------------------- 十六进制 */

static int hexval(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

/* 十六进制串 -> 字节，返回写入的字节数（遇到非十六进制字符即停） */
static size_t unhex(const char *hex, uint8_t *out, size_t cap)
{
    size_t n = 0;
    while (hex[0] != '\0' && hex[1] != '\0' && n < cap) {
        int hi = hexval(hex[0]), lo = hexval(hex[1]);
        if (hi < 0 || lo < 0) {
            break;
        }
        out[n++] = (uint8_t)((hi << 4) | lo);
        hex += 2;
    }
    return n;
}

static int eq_hex(const uint8_t *buf, size_t len, const char *hex)
{
    uint8_t tmp[128];
    size_t n = unhex(hex, tmp, sizeof(tmp));
    return n == len && memcmp(buf, tmp, n) == 0;
}

/* 失败时打印实际值：向量用例不给 detail 就只能看到一句 FAIL，无法定位 */
static void dump_hex(const char *tag, const uint8_t *buf, size_t len)
{
    static const char *digits = "0123456789abcdef";
    size_t i;
    printf("         %s=", tag);
    for (i = 0; i < len; i++) {
        printf("%c%c", digits[buf[i] >> 4], digits[buf[i] & 0x0f]);
    }
    printf("\n");
}

/* ---------------------------------------------------------------- 协议夹具 */

#define T_CONFIG_ID 0x7F
#define T_MAX_NAME 64
#define T_PUBLIC_NAME "example.com"
#define T_SNI "secret.example.org"

static void t_fill(uint8_t *buf, size_t n, uint8_t v)
{
    size_t i;
    for (i = 0; i < n; i++) {
        buf[i] = v;
    }
}

/* server_name 的 extension_data = ServerNameList<1..2^16-1> = len | name_type(0) | len | name */
static size_t t_server_name_ext(uint8_t *out, const char *name)
{
    size_t n = strlen(name);
    out[0] = (uint8_t)((n + 3) >> 8);
    out[1] = (uint8_t)((n + 3) & 0xff);
    out[2] = 0x00;                       /* name_type = host_name(0) */
    out[3] = (uint8_t)(n >> 8);
    out[4] = (uint8_t)(n & 0xff);
    memcpy(out + 5, name, n);
    return n + 5;
}

/* key_share 的占位内容：group(0x001d) + 32 字节假公钥，两端逐字节相同才可压缩 */
static void t_key_share_ext(uint8_t *out)
{
    t_fill(out, 34, 0xAB);
    out[0] = 0x00;
    out[1] = 0x1D;
}

static void t_common(ech_client_hello *ch, const uint8_t *rnd, const uint8_t *sid,
                     size_t sid_len, const char *sni, int with_ech)
{
    uint8_t sn_ext[64], ks[34];
    memset(ch, 0, sizeof(*ch));
    memcpy(ch->random, rnd, 32);
    if (sid_len > 0) {
        memcpy(ch->session_id, sid, sid_len);
    }
    ch->session_id_len = sid_len;
    ch->cipher_suites[0] = 0x1301;
    ch->cipher_suites[1] = 0x1302;
    ch->cipher_suites[2] = 0x1303;
    ch->cipher_suites_len = 3;
    ch->compression[0] = 0x00;
    ch->compression_len = 1;
    t_key_share_ext(ks);
    (void)ch_set_ext(ch, ECH_EXT_SERVER_NAME, sn_ext, t_server_name_ext(sn_ext, sni));
    (void)ch_set_ext(ch, ECH_EXT_SUPPORTED_VERSIONS, (const uint8_t *)"\x02\x03\x04", 3);
    (void)ch_set_ext(ch, ECH_EXT_KEY_SHARE, ks, 34);
    if (with_ech) {
        (void)ch_set_ext(ch, ECH_EXT_TYPE, NULL, 0);   /* inner 变体是 Empty */
    }
}

static void t_make_inner(ech_client_hello *ch, const uint8_t *rnd, const uint8_t *sid,
                         size_t sid_len)
{
    t_common(ch, rnd, sid, sid_len, T_SNI, 1);
}

static void t_make_outer_tpl(ech_client_hello *ch, const uint8_t *rnd, const uint8_t *sid,
                             size_t sid_len)
{
    t_common(ch, rnd, sid, sid_len, T_PUBLIC_NAME, 0);
}

static void t_make_config(ech_config *cfg, uint8_t config_id, const uint8_t *pk)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->config_id = config_id;
    cfg->kem_id = ECH_KEM_ID_X25519;
    cfg->kdf_id = ECH_KDF_ID_HKDF_SHA256;
    cfg->aead_id = ECH_AEAD_ID_CHACHA20POLY1305;
    memcpy(cfg->public_key, pk, 32);
    cfg->public_key_len = 32;
    cfg->maximum_name_length = T_MAX_NAME;
    memcpy(cfg->public_name, T_PUBLIC_NAME, strlen(T_PUBLIC_NAME));
    cfg->public_name_len = strlen(T_PUBLIC_NAME);
}

/*
 * §6.1.1 的封装流程，但明文由调用方给定 —— 用来构造恶意的 EncodedClientHelloInner
 * （例如尾部填充非零）。与 ech_client_encrypt 的区别是不做 32 字节对齐，所以服务端
 * 的「填充必须全零」检查才会真正被触发。
 */
static int t_encrypt_raw(ech_client_hello *out, const ech_client_hello *tpl,
                         const ech_config *cfg, const uint8_t *pt, size_t pt_len)
{
    static ech_client_hello zero;
    static uint8_t zerobuf[ECH_SCRATCH], extbuf[ECH_EXT_DATA], aad[2048];
    static uint8_t sealed[ECH_SCRATCH], ss[ECH_NSECRET], enc[32];
    ech_context ctx;
    size_t n = 0, ext_len = 0, filler = pt_len + ECH_TAG_LEN;
    int sealed_len, rc = -1;

    if (filler > sizeof(zerobuf)) {
        return -1;
    }
    if (ech_encap(cfg->public_key, NULL, enc, ss) != 0 ||
        ech_key_schedule(ss, NULL, 0, &ctx) != 0) {
        return -1;
    }
    memset(zerobuf, 0, filler);
    zero = *tpl;
    if (ech_outer_ext_encode(cfg->kdf_id, cfg->aead_id, cfg->config_id, enc, 32, zerobuf,
                             filler, extbuf, sizeof(extbuf), &ext_len) != 0 ||
        ch_set_ext(&zero, ECH_EXT_TYPE, extbuf, ext_len) != 0 ||
        ch_encode(&zero, aad, sizeof(aad), &n) != 0) {
        return -1;
    }
    sealed_len = ech_ctx_seal(&ctx, aad, n, pt, pt_len, sealed);
    if (sealed_len != (int)filler) {
        return -1;
    }
    *out = *tpl;
    if (ech_outer_ext_encode(cfg->kdf_id, cfg->aead_id, cfg->config_id, enc, 32, sealed,
                             (size_t)sealed_len, extbuf, sizeof(extbuf), &ext_len) != 0 ||
        ch_set_ext(out, ECH_EXT_TYPE, extbuf, ext_len) != 0) {
        goto done;
    }
    rc = 0;
done:
    OPENSSL_cleanse(ss, sizeof(ss));
    return rc;
}

#endif /* ECH_CHECK_IMPL_H */
