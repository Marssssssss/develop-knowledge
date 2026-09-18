/*
 * quic_kdf_impl.h —— QUIC 包保护的密钥派生（RFC 8446 §7.1 的 HKDF-Expand-Label）
 *
 * 作为「实现头」被 quic_demo.c 文本级 #include，因此不需要单独编译，
 * static 函数也在同一翻译单元内可见（构建命令只编译 .c）。
 *
 * KDF 用 OpenSSL 的 HMAC()：QUIC 的 Initial 密钥固定用 SHA-256，
 * 但**每条连接的密钥派生沿用 TLS 1.3 的 HKDF-Expand-Label**，
 * 而不是 QUIC 自定义的 KDF（这点常被和 WireGuard 的 HKDF 混淆）。
 */
#ifndef QUIC_KDF_IMPL_H
#define QUIC_KDF_IMPL_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <openssl/evp.h>
#include <openssl/hmac.h>

#define QUIC_HASH_LEN 32

/* RFC 9001 §5.2 的固定盐：Initial 密钥必须由它派生，否则与任何实现都对不上 */
#define QUIC_INITIAL_SALT_HEX "38762cf7f55934b34d179ae6a4c80cadccbb7f0a"

/* 十六进制字符串 -> 字节；返回 0 成功，-1 长度不符或字符非法 */
static int quic_hex2bin(const char *hex, uint8_t *out, size_t out_len)
{
    size_t i;
    if (strlen(hex) != out_len * 2) {
        return -1;
    }
    for (i = 0; i < out_len; i++) {
        unsigned hi, lo;
        char a = hex[i * 2], b = hex[i * 2 + 1];
        if (a >= '0' && a <= '9') hi = (unsigned)(a - '0');
        else if (a >= 'a' && a <= 'f') hi = (unsigned)(a - 'a' + 10);
        else if (a >= 'A' && a <= 'F') hi = (unsigned)(a - 'A' + 10);
        else return -1;
        if (b >= '0' && b <= '9') lo = (unsigned)(b - '0');
        else if (b >= 'a' && b <= 'f') lo = (unsigned)(b - 'a' + 10);
        else if (b >= 'A' && b <= 'F') lo = (unsigned)(b - 'A' + 10);
        else return -1;
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return 0;
}

static int quic_hmac256(const uint8_t *key, size_t key_len, const uint8_t *data,
                        size_t data_len, uint8_t out[QUIC_HASH_LEN])
{
    unsigned int n = 0;
    if (HMAC(EVP_sha256(), key, (int)key_len, data, data_len, out, &n) == NULL) {
        return -1;
    }
    return n == QUIC_HASH_LEN ? 0 : -1;
}

/* HKDF-Extract(salt, IKM) = HMAC-Hash(salt, IKM)；salt 为空按 RFC 5869 取全零 */
static int quic_hkdf_extract(const uint8_t *salt, size_t salt_len,
                             const uint8_t *ikm, size_t ikm_len,
                             uint8_t out[QUIC_HASH_LEN])
{
    uint8_t zeros[QUIC_HASH_LEN];
    if (salt == NULL || salt_len == 0) {
        memset(zeros, 0, sizeof(zeros));
        salt = zeros;
        salt_len = QUIC_HASH_LEN;
    }
    return quic_hmac256(salt, salt_len, ikm, ikm_len, out);
}

/* HKDF-Expand(PRK, info, L)：T(i) = HMAC(PRK, T(i-1) | info | i) */
static int quic_hkdf_expand(const uint8_t *prk, const uint8_t *info, size_t info_len,
                            uint8_t *out, size_t out_len)
{
    uint8_t t[QUIC_HASH_LEN];
    uint8_t buf[QUIC_HASH_LEN + 512 + 1];
    size_t t_len = 0, done = 0;
    uint8_t ctr = 1;

    if (out_len > 255 * QUIC_HASH_LEN || info_len + QUIC_HASH_LEN + 1 > sizeof(buf)) {
        return -1;
    }
    while (done < out_len) {
        size_t take;
        memcpy(buf, t, t_len);
        if (info_len > 0) {
            memcpy(buf + t_len, info, info_len);
        }
        buf[t_len + info_len] = ctr;
        if (quic_hmac256(prk, QUIC_HASH_LEN, buf, t_len + info_len + 1, t) != 0) {
            return -1;
        }
        t_len = QUIC_HASH_LEN;
        take = (out_len - done < QUIC_HASH_LEN) ? out_len - done : QUIC_HASH_LEN;
        memcpy(out + done, t, take);
        done += take;
        ctr++;
    }
    return 0;
}

/*
 * HKDF-Expand-Label（RFC 8446 §7.1）：
 *   HkdfLabel = uint16(length) | opaque label<7..255> | opaque context<0..255>
 * 标签**必须**带 "tls13 " 前缀；QUIC 的所有用途 context 都为空串。
 */
static int quic_expand_label(const uint8_t *secret, const char *label,
                             const uint8_t *ctx, size_t ctx_len,
                             size_t length, uint8_t *out)
{
    uint8_t info[2 + 1 + 255 + 1 + 255];
    size_t label_len = strlen(label), n = 0;

    if (label_len + 6 > 255 || ctx_len > 255) {
        return -1;
    }
    info[n++] = (uint8_t)((length >> 8) & 0xff);
    info[n++] = (uint8_t)(length & 0xff);
    info[n++] = (uint8_t)(label_len + 6);
    memcpy(info + n, "tls13 ", 6);
    n += 6;
    memcpy(info + n, label, label_len);
    n += label_len;
    info[n++] = (uint8_t)ctx_len;
    if (ctx_len > 0) {
        memcpy(info + n, ctx, ctx_len);
        n += ctx_len;
    }
    return quic_hkdf_expand(secret, info, n, out, length);
}

/* 从某个加密级别的 secret 派生 (AEAD key, IV, 头保护 key)，三个标签互相隔离 */
static int quic_packet_keys(const uint8_t *secret, size_t key_len, size_t iv_len,
                            size_t hp_len, uint8_t *key, uint8_t *iv, uint8_t *hp)
{
    if (quic_expand_label(secret, "quic key", NULL, 0, key_len, key) != 0) {
        return -1;
    }
    if (quic_expand_label(secret, "quic iv", NULL, 0, iv_len, iv) != 0) {
        return -1;
    }
    return quic_expand_label(secret, "quic hp", NULL, 0, hp_len, hp);
}

/* Initial 密钥从「客户端首个 Initial 包的 DCID」派生 */
static int quic_initial_secrets(const uint8_t *dcid, size_t dcid_len,
                                uint8_t *client, uint8_t *server)
{
    uint8_t salt[20], prk[QUIC_HASH_LEN];
    if (quic_hex2bin(QUIC_INITIAL_SALT_HEX, salt, sizeof(salt)) != 0) {
        return -1;
    }
    if (quic_hkdf_extract(salt, sizeof(salt), dcid, dcid_len, prk) != 0) {
        return -1;
    }
    if (quic_expand_label(prk, "client in", NULL, 0, QUIC_HASH_LEN, client) != 0) {
        return -1;
    }
    return quic_expand_label(prk, "server in", NULL, 0, QUIC_HASH_LEN, server);
}

/* 密钥更新只换 "quic ku" 标签 */
static int quic_next_secret(const uint8_t *secret, uint8_t out[QUIC_HASH_LEN])
{
    return quic_expand_label(secret, "quic ku", NULL, 0, QUIC_HASH_LEN, out);
}

#endif /* QUIC_KDF_IMPL_H */
