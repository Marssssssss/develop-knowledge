/*
 * ech_hpke_kem_impl.h —— HPKE 的 KEM 层（RFC 9180 §4.1 DHKEM(X25519, HKDF-SHA256) + §7.1.3）
 *
 * 这一层的全部内容是「HKDF 的标签化包装」+「一次 X25519 DH」：
 *
 *   LabeledExtract(salt, label, ikm) = Extract(salt, "HPKE-v1" | suite_id | label | ikm)
 *   LabeledExpand(prk, label, info, L) = Expand(prk, I2OSP(L,2) | "HPKE-v1" | suite_id | label | info, L)
 *
 * **KEM 与 HPKE 各有一条 suite_id，长度不同**（"KEM"|kem_id 是 5 字节，
 * "HPKE"|kem_id|kdf_id|aead_id 是 10 字节）。抄错或者复用其中一条，密钥就完全不对，
 * 而且不会有任何报错 —— 只能靠 RFC 9180 附录 A.2 的官方向量抓出来。
 *
 * X25519 交给 OpenSSL（EVP_PKEY_X25519）；clamping 由 OpenSSL 在私钥导入时完成，
 * 所以本文件不需要自己 clamp。
 */
#ifndef ECH_HPKE_KEM_IMPL_H
#define ECH_HPKE_KEM_IMPL_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <openssl/rand.h>
#include <openssl/sha.h>

#define ECH_NH 32                        /* SHA-256 */
#define ECH_NK 32                        /* ChaCha20-Poly1305 密钥 */
#define ECH_NN 12                        /* nonce */
#define ECH_TAG_LEN 16
#define ECH_NSECRET 32
#define ECH_KEM_ID_X25519 0x0020
#define ECH_KDF_ID_HKDF_SHA256 0x0001
#define ECH_AEAD_ID_CHACHA20POLY1305 0x0003

/* 两条 suite_id 长度不同：KEM 的 5 字节，HPKE 的 10 字节。
 * 写成 #define 而不是 static const 数组：本仓库的 c_sanity 用带 re.S 的正则扫 `^static` 行，
 * 没有 `(` 的 static 定义会跨行吞到下一个 `ident(`，制造幻影条目。 */
#define ECH_KEM_SUITE_LEN 5
#define ECH_HPKE_SUITE_LEN 10
#define ECH_KEM_SUITE_ID ((const uint8_t *)"KEM\x00\x20")
#define ECH_HPKE_SUITE_ID ((const uint8_t *)"HPKE\x00\x20\x00\x01\x00\x03")
/* X25519 的基点：u 坐标 = 9，其余 31 字节全零 */
#define ECH_X25519_BASE ((const uint8_t *)"\x09\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00" \
                                         "\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00" \
                                         "\x00\x00\x00\x00\x00\x00\x00\x00")

/* ---------------------------------------------------------------- HKDF（RFC 5869） */

static int ech_hmac256(const uint8_t *key, size_t key_len, const uint8_t *data,
                       size_t data_len, uint8_t out[ECH_NH]);
static int ech_extract(const uint8_t *salt, size_t salt_len, const uint8_t *ikm,
                       size_t ikm_len, uint8_t out[ECH_NH]);
static int ech_expand(const uint8_t *prk, const uint8_t *info, size_t info_len,
                      uint8_t *out, size_t out_len);

static int ech_hmac256(const uint8_t *key, size_t key_len, const uint8_t *data,
                       size_t data_len, uint8_t out[ECH_NH])
{
    unsigned int n = 0;
    if (HMAC(EVP_sha256(), key, (int)key_len, data, data_len, out, &n) == NULL) {
        return -1;
    }
    return n == ECH_NH ? 0 : -1;
}

/* RFC 9180 §5.1：salt 为空串时按 Hash.length 个零字节处理 */
static int ech_extract(const uint8_t *salt, size_t salt_len, const uint8_t *ikm,
                       size_t ikm_len, uint8_t out[ECH_NH])
{
    uint8_t zeros[ECH_NH];
    if (salt_len == 0) {
        memset(zeros, 0, sizeof(zeros));
        salt = zeros;
        salt_len = ECH_NH;
    }
    return ech_hmac256(salt, salt_len, ikm, ikm_len, out);
}

static int ech_expand(const uint8_t *prk, const uint8_t *info, size_t info_len,
                      uint8_t *out, size_t out_len)
{
    uint8_t t[ECH_NH];
    uint8_t buf[ECH_NH + 256 + 1];
    size_t t_len = 0, done = 0;
    uint8_t ctr = 1;
    if (info_len + ECH_NH + 1 > sizeof(buf) || out_len > 255 * ECH_NH) {
        return -1;
    }
    while (done < out_len) {
        size_t take;
        if (t_len > 0) {
            memcpy(buf, t, t_len);
        }
        if (info_len > 0) {
            memcpy(buf + t_len, info, info_len);
        }
        buf[t_len + info_len] = ctr;
        if (ech_hmac256(prk, ECH_NH, buf, t_len + info_len + 1, t) != 0) {
            return -1;
        }
        t_len = ECH_NH;
        take = (out_len - done < ECH_NH) ? out_len - done : ECH_NH;
        memcpy(out + done, t, take);
        done += take;
        ctr++;
    }
    return 0;
}

/* ---------------------------------------------------------------- 标签化 */

static int ech_labeled_extract(const uint8_t *salt, size_t salt_len,
                               const uint8_t *suite_id, size_t suite_len,
                               const char *label, const uint8_t *ikm, size_t ikm_len,
                               uint8_t out[ECH_NH]);
static int ech_labeled_expand(const uint8_t *prk, const uint8_t *suite_id,
                              size_t suite_len, const char *label, const uint8_t *info,
                              size_t info_len, uint8_t *out, size_t out_len);

static int ech_labeled_extract(const uint8_t *salt, size_t salt_len,
                               const uint8_t *suite_id, size_t suite_len,
                               const char *label, const uint8_t *ikm, size_t ikm_len,
                               uint8_t out[ECH_NH])
{
    uint8_t buf[256];
    size_t label_len = strlen(label), n = 0;
    if (7 + suite_len + label_len + ikm_len > sizeof(buf)) {
        return -1;
    }
    memcpy(buf + n, "HPKE-v1", 7);
    n += 7;
    memcpy(buf + n, suite_id, suite_len);
    n += suite_len;
    memcpy(buf + n, label, label_len);
    n += label_len;
    if (ikm_len > 0) {
        memcpy(buf + n, ikm, ikm_len);
        n += ikm_len;
    }
    return ech_extract(salt, salt_len, buf, n, out);
}

static int ech_labeled_expand(const uint8_t *prk, const uint8_t *suite_id,
                              size_t suite_len, const char *label, const uint8_t *info,
                              size_t info_len, uint8_t *out, size_t out_len)
{
    uint8_t buf[256];
    size_t label_len = strlen(label), n = 2;
    if (2 + 7 + suite_len + label_len + info_len > sizeof(buf) || out_len > 0xffffu) {
        return -1;
    }
    buf[0] = (uint8_t)(out_len >> 8);          /* L 编码成 2 字节大端放最前 */
    buf[1] = (uint8_t)(out_len & 0xff);
    memcpy(buf + n, "HPKE-v1", 7);
    n += 7;
    memcpy(buf + n, suite_id, suite_len);
    n += suite_len;
    memcpy(buf + n, label, label_len);
    n += label_len;
    if (info_len > 0) {
        memcpy(buf + n, info, info_len);
        n += info_len;
    }
    return ech_expand(prk, buf, n, out, out_len);
}

/* ---------------------------------------------------------------- X25519（RFC 7748） */

static int ech_x25519(const uint8_t sk[32], const uint8_t pk[32], uint8_t out[32])
{
    EVP_PKEY *priv = NULL, *peer = NULL;
    EVP_PKEY_CTX *ctx = NULL;
    size_t out_len = 32;
    int rc = -1;
    priv = EVP_PKEY_new_raw_private_key(EVP_PKEY_X25519, NULL, sk, 32);
    peer = EVP_PKEY_new_raw_public_key(EVP_PKEY_X25519, NULL, pk, 32);
    if (priv == NULL || peer == NULL) {
        goto done;
    }
    ctx = EVP_PKEY_CTX_new(priv, NULL);
    if (ctx == NULL || EVP_PKEY_derive_init(ctx) != 1) {
        goto done;
    }
    if (EVP_PKEY_derive_set_peer(ctx, peer) != 1) {
        goto done;
    }
    if (EVP_PKEY_derive(ctx, out, &out_len) != 1 || out_len != 32) {
        goto done;
    }
    rc = 0;
done:
    EVP_PKEY_CTX_free(ctx);
    EVP_PKEY_free(priv);
    EVP_PKEY_free(peer);
    return rc;
}

/* ---------------------------------------------------------------- DHKEM（§4.1） */

static int ech_kem_extract_and_expand(const uint8_t dh[32], const uint8_t *kem_context,
                                      size_t kc_len, uint8_t out[ECH_NSECRET])
{
    uint8_t eae_prk[ECH_NH];
    if (ech_labeled_extract(NULL, 0, ECH_KEM_SUITE_ID, ECH_KEM_SUITE_LEN,
                            "eae_prk", dh, 32, eae_prk) != 0) {
        return -1;
    }
    return ech_labeled_expand(eae_prk, ECH_KEM_SUITE_ID, ECH_KEM_SUITE_LEN,
                              "shared_secret", kem_context, kc_len, out, ECH_NSECRET);
}

/* §7.1.3：X25519 不做拒绝采样，直接 LabeledExpand 出 sk */
static int ech_derive_key_pair(const uint8_t ikm[32], uint8_t sk[32], uint8_t pk[32])
{
    uint8_t dkp_prk[ECH_NH];
    if (ech_labeled_extract(NULL, 0, ECH_KEM_SUITE_ID, ECH_KEM_SUITE_LEN,
                            "dkp_prk", ikm, 32, dkp_prk) != 0) {
        return -1;
    }
    if (ech_labeled_expand(dkp_prk, ECH_KEM_SUITE_ID, ECH_KEM_SUITE_LEN, "sk",
                           NULL, 0, sk, 32) != 0) {
        return -1;
    }
    return ech_x25519(sk, ECH_X25519_BASE, pk);
}

/* ikm 非空时走 DeriveKeyPair（自检要对照向量）；否则用随机临时密钥 */
static int ech_encap(const uint8_t pk_r[32], const uint8_t *ikm, uint8_t enc[32],
                     uint8_t ss[ECH_NSECRET])
{
    uint8_t sk_e[32], dh[32], kem_ctx[64];
    int rc;
    if (ikm != NULL) {
        if (ech_derive_key_pair(ikm, sk_e, enc) != 0) {
            return -1;
        }
    } else {
        if (RAND_bytes(sk_e, (int)sizeof(sk_e)) != 1) {
            return -1;
        }
        if (ech_x25519(sk_e, ECH_X25519_BASE, enc) != 0) {
            return -1;
        }
    }
    if (ech_x25519(sk_e, pk_r, dh) != 0) {
        return -1;
    }
    memcpy(kem_ctx, enc, 32);
    memcpy(kem_ctx + 32, pk_r, 32);
    rc = ech_kem_extract_and_expand(dh, kem_ctx, sizeof(kem_ctx), ss);
    OPENSSL_cleanse(dh, sizeof(dh));
    OPENSSL_cleanse(sk_e, sizeof(sk_e));
    return rc;
}

static int ech_decap(const uint8_t enc[32], const uint8_t sk_r[32],
                     const uint8_t pk_r[32], uint8_t ss[ECH_NSECRET])
{
    uint8_t dh[32], kem_ctx[64];
    int rc;
    if (ech_x25519(sk_r, enc, dh) != 0) {
        return -1;
    }
    memcpy(kem_ctx, enc, 32);
    memcpy(kem_ctx + 32, pk_r, 32);
    rc = ech_kem_extract_and_expand(dh, kem_ctx, sizeof(kem_ctx), ss);
    OPENSSL_cleanse(dh, sizeof(dh));
    return rc;
}

#endif /* ECH_HPKE_KEM_IMPL_H */
