/*
 * ech_hpke_ctx_impl.h —— HPKE 的 AEAD、密钥计划与加密上下文（RFC 9180 §5.1/§5.2/§6）
 *
 * 密钥计划（§5.1，base mode）：
 *   psk_id_hash = LabeledExtract("", "psk_id_hash", "")     ← psk_id 为空串
 *   info_hash   = LabeledExtract("", "info_hash", info)
 *   ksc         = mode | psk_id_hash | info_hash            ← mode_base = 0x00
 *   secret          = LabeledExtract(shared_secret, "secret", "")
 *   key             = LabeledExpand(secret, "key", ksc, Nk)
 *   base_nonce      = LabeledExpand(secret, "base_nonce", ksc, Nn)
 *   exporter_secret = LabeledExpand(secret, "exp", ksc, Nh)
 *
 * 注意 **secret 的盐是 shared_secret**，而 psk_id_hash / info_hash 的盐是空串 ——
 * 三个 LabeledExtract 的盐各不相同，这是 §5.1 里最容易抄漏的一处。
 *
 * 加密上下文（§6）：nonce = base_nonce XOR I2OSP(序号, Nn)，序号每 Seal/Open 一次自增。
 *
 * 另外附带 TLS 1.3 的 HKDF-Expand-Label（RFC 8446 §7.1）：
 * 它和 HPKE 的 LabeledExpand 长得很像但**编码完全不同**（HkdfLabel 结构 + "tls13 " 前缀，
 * 且没有 "HPKE-v1"/suite_id）。ECH 的 accept_confirmation 用的是前者，不能混用。
 */
#ifndef ECH_HPKE_CTX_IMPL_H
#define ECH_HPKE_CTX_IMPL_H

#include "ech_hpke_kem_impl.h"

/* ---------------------------------------------------------------- AEAD */

static int ech_aead_seal(const uint8_t key[ECH_NK], const uint8_t nonce[ECH_NN],
                         const uint8_t *aad, size_t aad_len, const uint8_t *pt,
                         size_t pt_len, uint8_t *out);
static int ech_aead_open(const uint8_t key[ECH_NK], const uint8_t nonce[ECH_NN],
                         const uint8_t *aad, size_t aad_len, const uint8_t *sealed,
                         size_t sealed_len, uint8_t *out);

/* 返回 密文||tag 的长度，失败返回负数 */
static int ech_aead_seal(const uint8_t key[ECH_NK], const uint8_t nonce[ECH_NN],
                         const uint8_t *aad, size_t aad_len, const uint8_t *pt,
                         size_t pt_len, uint8_t *out)
{
    EVP_CIPHER_CTX *c = EVP_CIPHER_CTX_new();
    int n = 0, total = 0, rc = -1;
    if (c == NULL) {
        return -1;
    }
    if (EVP_EncryptInit_ex(c, EVP_chacha20_poly1305(), NULL, NULL, NULL) != 1) {
        goto done;
    }
    if (EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_AEAD_SET_IVLEN, ECH_NN, NULL) != 1) {
        goto done;
    }
    if (EVP_EncryptInit_ex(c, NULL, NULL, key, nonce) != 1) {
        goto done;
    }
    if (aad_len > 0 && EVP_EncryptUpdate(c, NULL, &n, aad, (int)aad_len) != 1) {
        goto done;
    }
    if (pt_len > 0 && EVP_EncryptUpdate(c, out, &n, pt, (int)pt_len) != 1) {
        goto done;
    }
    total = n;
    if (EVP_EncryptFinal_ex(c, out + total, &n) != 1) {
        goto done;
    }
    total += n;
    if (EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_AEAD_GET_TAG, ECH_TAG_LEN, out + total) != 1) {
        goto done;
    }
    rc = total + ECH_TAG_LEN;
done:
    EVP_CIPHER_CTX_free(c);
    return rc;
}

/* sealed_len 含 16 字节 tag；认证失败返回 -1（ECH 里这就是「ClientHelloOuter 被改过」） */
static int ech_aead_open(const uint8_t key[ECH_NK], const uint8_t nonce[ECH_NN],
                         const uint8_t *aad, size_t aad_len, const uint8_t *sealed,
                         size_t sealed_len, uint8_t *out)
{
    EVP_CIPHER_CTX *c = EVP_CIPHER_CTX_new();
    int n = 0, total = 0, rc = -1;
    size_t ct_len;
    if (c == NULL || sealed_len < ECH_TAG_LEN) {
        EVP_CIPHER_CTX_free(c);
        return -1;
    }
    ct_len = sealed_len - ECH_TAG_LEN;
    if (EVP_DecryptInit_ex(c, EVP_chacha20_poly1305(), NULL, NULL, NULL) != 1) {
        goto done;
    }
    if (EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_AEAD_SET_IVLEN, ECH_NN, NULL) != 1) {
        goto done;
    }
    if (EVP_DecryptInit_ex(c, NULL, NULL, key, nonce) != 1) {
        goto done;
    }
    if (aad_len > 0 && EVP_DecryptUpdate(c, NULL, &n, aad, (int)aad_len) != 1) {
        goto done;
    }
    if (ct_len > 0 && EVP_DecryptUpdate(c, out, &n, sealed, (int)ct_len) != 1) {
        goto done;
    }
    total = n;
    if (EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_AEAD_SET_TAG, ECH_TAG_LEN,
                            (void *)(sealed + ct_len)) != 1) {
        goto done;
    }
    if (EVP_DecryptFinal_ex(c, out + total, &n) != 1) {
        goto done;                       /* tag 不匹配 */
    }
    rc = total + n;
done:
    EVP_CIPHER_CTX_free(c);
    return rc;
}

/* ---------------------------------------------------------------- 密钥计划与上下文 */

typedef struct {
    uint8_t key[ECH_NK];
    uint8_t base_nonce[ECH_NN];
    uint8_t exporter_secret[ECH_NH];
    uint64_t seq;
} ech_context;

static int ech_key_schedule(const uint8_t shared_secret[ECH_NSECRET], const uint8_t *info,
                            size_t info_len, ech_context *ctx)
{
    uint8_t psk_id_hash[ECH_NH], info_hash[ECH_NH], secret[ECH_NH];
    uint8_t ksc[1 + 2 * ECH_NH];
    const uint8_t *sid = ECH_HPKE_SUITE_ID;
    const size_t sid_len = ECH_HPKE_SUITE_LEN;

    if (ech_labeled_extract(NULL, 0, sid, sid_len, "psk_id_hash", NULL, 0,
                            psk_id_hash) != 0) {
        return -1;
    }
    if (ech_labeled_extract(NULL, 0, sid, sid_len, "info_hash", info, info_len,
                            info_hash) != 0) {
        return -1;
    }
    ksc[0] = 0x00;                                        /* mode_base */
    memcpy(ksc + 1, psk_id_hash, ECH_NH);
    memcpy(ksc + 1 + ECH_NH, info_hash, ECH_NH);

    if (ech_labeled_extract(shared_secret, ECH_NSECRET, sid, sid_len, "secret", NULL, 0,
                            secret) != 0) {
        return -1;
    }
    if (ech_labeled_expand(secret, sid, sid_len, "key", ksc, sizeof(ksc), ctx->key,
                           ECH_NK) != 0) {
        return -1;
    }
    if (ech_labeled_expand(secret, sid, sid_len, "base_nonce", ksc, sizeof(ksc),
                           ctx->base_nonce, ECH_NN) != 0) {
        return -1;
    }
    if (ech_labeled_expand(secret, sid, sid_len, "exp", ksc, sizeof(ksc),
                           ctx->exporter_secret, ECH_NH) != 0) {
        return -1;
    }
    ctx->seq = 0;
    OPENSSL_cleanse(secret, sizeof(secret));
    return 0;
}

static void ech_ctx_nonce(const ech_context *ctx, uint8_t nonce[ECH_NN])
{
    size_t i;
    memcpy(nonce, ctx->base_nonce, ECH_NN);
    for (i = 0; i < 8; i++) {
        nonce[ECH_NN - 1 - i] ^= (uint8_t)(ctx->seq >> (8 * i));
    }
}

static int ech_ctx_seal(ech_context *ctx, const uint8_t *aad, size_t aad_len,
                        const uint8_t *pt, size_t pt_len, uint8_t *out)
{
    uint8_t nonce[ECH_NN];
    int n;
    ech_ctx_nonce(ctx, nonce);
    n = ech_aead_seal(ctx->key, nonce, aad, aad_len, pt, pt_len, out);
    if (n < 0) {
        return -1;
    }
    ctx->seq++;
    return n;
}

static int ech_ctx_open(ech_context *ctx, const uint8_t *aad, size_t aad_len,
                        const uint8_t *sealed, size_t sealed_len, uint8_t *out)
{
    uint8_t nonce[ECH_NN];
    int n;
    ech_ctx_nonce(ctx, nonce);
    n = ech_aead_open(ctx->key, nonce, aad, aad_len, sealed, sealed_len, out);
    if (n < 0) {
        return -1;
    }
    ctx->seq++;
    return n;
}

static int ech_ctx_export(const ech_context *ctx, const uint8_t *ctx_info,
                          size_t ctx_info_len, uint8_t *out, size_t out_len)
{
    return ech_labeled_expand(ctx->exporter_secret, ECH_HPKE_SUITE_ID,
                              ECH_HPKE_SUITE_LEN, "sec", ctx_info, ctx_info_len,
                              out, out_len);
}

/* ---------------------------------------------------------------- TLS 1.3 HKDF-Expand-Label */

static int ech_tls_expand_label(const uint8_t secret[ECH_NH], const char *label,
                                const uint8_t *context, size_t context_len,
                                uint8_t *out, size_t out_len)
{
    uint8_t hkdf_label[128];
    size_t label_len = strlen(label), n = 0;
    if (label_len + 6 > 255 || context_len > 255 || out_len > 0xffffu) {
        return -1;
    }
    hkdf_label[n++] = (uint8_t)(out_len >> 8);
    hkdf_label[n++] = (uint8_t)(out_len & 0xff);
    hkdf_label[n++] = (uint8_t)(label_len + 6);           /* "tls13 " 前缀计入长度 */
    memcpy(hkdf_label + n, "tls13 ", 6);
    n += 6;
    memcpy(hkdf_label + n, label, label_len);
    n += label_len;
    hkdf_label[n++] = (uint8_t)context_len;
    if (context_len > 0) {
        memcpy(hkdf_label + n, context, context_len);
        n += context_len;
    }
    return ech_expand(secret, hkdf_label, n, out, out_len);
}

#endif /* ECH_HPKE_CTX_IMPL_H */
