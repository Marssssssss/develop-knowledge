#ifndef WG_NOISE_IMPL_H
#define WG_NOISE_IMPL_H
/* X25519 与 ChaCha20-Poly1305 走 OpenSSL；握手哈希链（ck/h/k）与 Noise 的
 * MixHash/MixKey/MixKeyAndHash 在此实现。依赖 wg_crypto_impl.h 的 BLAKE2s 与 KDF。 */
#include "wg_crypto_impl.h"

/* ============================================================
 * 2. X25519 / ChaCha20-Poly1305（OpenSSL）
 * ============================================================ */

static int x25519_keypair(uint8_t priv[PUB_LEN], uint8_t pub[PUB_LEN]) {
    EVP_PKEY_CTX *ctx = EVP_PKEY_CTX_new_id(EVP_PKEY_X25519, NULL);
    EVP_PKEY *pkey = NULL;
    size_t plen = PUB_LEN, slen = PUB_LEN;
    int ok = 0;
    if (ctx && EVP_PKEY_keygen_init(ctx) > 0 && EVP_PKEY_keygen(ctx, &pkey) > 0 &&
        EVP_PKEY_get_raw_private_key(pkey, priv, &slen) > 0 &&
        EVP_PKEY_get_raw_public_key(pkey, pub, &plen) > 0)
        ok = 1;
    EVP_PKEY_free(pkey);
    EVP_PKEY_CTX_free(ctx);
    return ok;
}

static int x25519(uint8_t shared[PUB_LEN], const uint8_t priv[PUB_LEN],
                  const uint8_t peer[PUB_LEN]) {
    EVP_PKEY *sk = EVP_PKEY_new_raw_private_key(EVP_PKEY_X25519, NULL, priv, PUB_LEN);
    EVP_PKEY *pk = EVP_PKEY_new_raw_public_key(EVP_PKEY_X25519, NULL, peer, PUB_LEN);
    EVP_PKEY_CTX *ctx = (sk && pk) ? EVP_PKEY_CTX_new(sk, NULL) : NULL;
    size_t len = PUB_LEN;
    int ok = 0;
    if (ctx && EVP_PKEY_derive_init(ctx) > 0 && EVP_PKEY_derive_set_peer(ctx, pk) > 0 &&
        EVP_PKEY_derive(ctx, shared, &len) > 0)
        ok = 1;
    EVP_PKEY_CTX_free(ctx);
    EVP_PKEY_free(sk);
    EVP_PKEY_free(pk);
    return ok;
}

/* WireGuard 固定用 12 字节全零 nonce + 96 位计数器（Noise 规定 nonce 从 0 起） */
static int aead_seal(uint8_t *ct, const uint8_t key[SYM_LEN], const uint8_t *ad,
                     size_t adlen, const uint8_t *pt, size_t ptlen) {
    static const uint8_t zero_nonce[12] = {0};
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    int len = 0, outlen = 0, ok = 0;
    if (ctx && EVP_EncryptInit_ex(ctx, EVP_chacha20_poly1305(), NULL, NULL, NULL) == 1 &&
        EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_AEAD_SET_IVLEN, 12, NULL) == 1 &&
        EVP_EncryptInit_ex(ctx, NULL, NULL, key, zero_nonce) == 1) {
        if (adlen > 0 && EVP_EncryptUpdate(ctx, NULL, &len, ad, (int)adlen) != 1)
            goto done;
        if (ptlen > 0 &&
            EVP_EncryptUpdate(ctx, ct, &len, pt, (int)ptlen) != 1)
            goto done;
        outlen = len;
        if (EVP_EncryptFinal_ex(ctx, ct + outlen, &len) != 1)
            goto done;
        outlen += len;
        if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_AEAD_GET_TAG, TAG_LEN, ct + outlen) != 1)
            goto done;
        ok = 1;
    }
done:
    EVP_CIPHER_CTX_free(ctx);
    return ok;
}

/* ============================================================
 * 3. 握手哈希链与消息
 * ============================================================ */

typedef struct {
    uint8_t ck[HASH_LEN];
    uint8_t h[HASH_LEN];
    uint8_t k[SYM_LEN];
    int have_key;
} Symmetric;

static void mix_hash(Symmetric *sy, const uint8_t *data, size_t len) {
    uint8_t buf[HASH_LEN + 512];
    if (len > 512) len = 512;
    memcpy(buf, sy->h, HASH_LEN);
    memcpy(buf + HASH_LEN, data, len);
    blake2s(sy->h, HASH_LEN, NULL, 0, buf, HASH_LEN + len);
}

static void mix_key(Symmetric *sy, const uint8_t *ikm, size_t len) {
    uint8_t newk[SYM_LEN];
    kdf(sy->ck, newk, NULL, ikm, len, sy->ck);
    memcpy(sy->k, newk, SYM_LEN);
    sy->have_key = 1;
}

static void mix_key_only_ck(Symmetric *sy, const uint8_t *ikm, size_t len) {
    uint8_t newck[HASH_LEN];
    kdf(newck, NULL, NULL, ikm, len, sy->ck);
    memcpy(sy->ck, newck, HASH_LEN);
}

static void mix_key_and_hash(Symmetric *sy, const uint8_t *psk) {
    uint8_t newck[HASH_LEN], temp_h[HASH_LEN], newk[SYM_LEN];
    kdf(newck, temp_h, newk, psk, SYM_LEN, sy->ck);
    memcpy(sy->ck, newck, HASH_LEN);
    memcpy(sy->k, newk, SYM_LEN);
    sy->have_key = 1;
    mix_hash(sy, temp_h, HASH_LEN);
}

/* 与纯 Noise 的差别：ck0 = HASH(handshake_name)、h0 = HASH(ck0 ‖ identifier) */
static void handshake_init(Symmetric *sy, const uint8_t remote_static[PUB_LEN]) {
    blake2s(sy->ck, HASH_LEN, NULL, 0, (const uint8_t *)HANDSHAKE_NAME,
            sizeof(HANDSHAKE_NAME) - 1);
    {
        uint8_t buf[HASH_LEN + sizeof(IDENTIFIER_NAME) - 1];
        memcpy(buf, sy->ck, HASH_LEN);
        memcpy(buf + HASH_LEN, IDENTIFIER_NAME, sizeof(IDENTIFIER_NAME) - 1);
        blake2s(sy->h, HASH_LEN, NULL, 0, buf, sizeof(buf));
    }
    sy->have_key = 0;
    mix_hash(sy, remote_static, PUB_LEN);          /* IK 的 pre-message: <- s */
}

#endif /* WG_NOISE_IMPL_H */
