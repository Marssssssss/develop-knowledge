// TLS 1.3 握手核心机制 (RFC 8446 §7.1 Key Schedule + §4.1 1-RTT)
//
// 本程序演示:
//   - Client/Server 各自生成临时 X25519 密钥对 (OpenSSL EVP_PKEY_X25519)
//   - ECDHE → shared secret
//   - HKDF-Extract + HKDF-Expand-Label 派生:
//       handshake_secret
//       server/client handshake traffic secret
//       server/client write_key + write_iv
//   - 用 AES-128-GCM 加密一份 "Certificate" + "Finished" 消息
//   - 客户端验证 AEAD 解密
//
// 编译:  gcc -O2 -Wall -Wextra tls13_handshake.c -lcrypto -o tls13
//
// 注意:依赖 OpenSSL 1.1.1+ 提供 X25519 (EVP_PKEY_X25519);
//      老版本用 EVP_PKEY_EC + NID_X9_62_prime256v1 (P-256) 替代。

#include <openssl/evp.h>
#include <openssl/kdf.h>
#include <openssl/hmac.h>
#include <openssl/rand.h>
#include <openssl/crypto.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <assert.h>

#define SHA256_DIGEST_LEN 32
#define SHA256_BLOCK     64

/* ============================================================
 * 1. X25519 ECDH (用 OpenSSL EVP_PKEY API, RFC 7748)
 * ============================================================ */

static int x25519_keypair(uint8_t priv[32], uint8_t pub[32]) {
    EVP_PKEY_CTX *ctx = EVP_PKEY_CTX_new_id(EVP_PKEY_X25519, NULL);
    if (!ctx) return -1;
    EVP_PKEY *pkey = NULL;
    if (EVP_PKEY_keygen_init(ctx) <= 0 ||
        EVP_PKEY_keygen(ctx, &pkey) <= 0) {
        EVP_PKEY_CTX_free(ctx);
        return -1;
    }
    size_t priv_len = 32, pub_len = 32;
    if (EVP_PKEY_get_raw_private_key(pkey, priv, &priv_len) <= 0 ||
        EVP_PKEY_get_raw_public_key(pkey, pub, &pub_len) <= 0) {
        EVP_PKEY_free(pkey); EVP_PKEY_CTX_free(ctx);
        return -1;
    }
    EVP_PKEY_free(pkey);
    EVP_PKEY_CTX_free(ctx);
    return 0;
}

static int x25519_derive(const uint8_t priv[32], const uint8_t peer_pub[32],
                         uint8_t shared[32]) {
    EVP_PKEY *priv_key = EVP_PKEY_new_raw_private_key(EVP_PKEY_X25519, NULL, priv, 32);
    EVP_PKEY *peer_key = EVP_PKEY_new_raw_public_key(EVP_PKEY_X25519, NULL, peer_pub, 32);
    if (!priv_key || !peer_key) return -1;
    EVP_PKEY_CTX *ctx = EVP_PKEY_CTX_new(priv_key, NULL);
    if (!ctx) return -1;
    int rc = -1;
    if (EVP_PKEY_derive_init(ctx) > 0 &&
        EVP_PKEY_derive_set_peer(ctx, peer_key) > 0) {
        size_t shared_len = 32;
        if (EVP_PKEY_derive(ctx, shared, &shared_len) > 0 && shared_len == 32) {
            rc = 0;
        }
    }
    EVP_PKEY_CTX_free(ctx);
    EVP_PKEY_free(priv_key);
    EVP_PKEY_free(peer_key);
    return rc;
}

/* ============================================================
 * 2. HKDF (RFC 5869) + HKDF-Expand-Label (RFC 8446 §7.1)
 * ============================================================ */

static int hkdf_extract(const uint8_t *salt, size_t salt_len,
                        const uint8_t *ikm, size_t ikm_len,
                        uint8_t prk[SHA256_DIGEST_LEN]) {
    unsigned int len = SHA256_DIGEST_LEN;
    if (!HMAC(salt, (int)salt_len, ikm, ikm_len, prk, &len)) return -1;
    return 0;
}

static int hkdf_expand_label(const uint8_t secret[32],
                              const char *label,
                              const uint8_t *context, size_t context_len,
                              size_t length,
                              uint8_t *out) {
    /* info = struct.pack(">H", len("tls13 " + label)) + "tls13 " + label
             + struct.pack(">H", context_len) + context
             + struct.pack(">H", length)
     */
    uint8_t full_label[64];
    size_t full_label_len = 0;
    memcpy(full_label, "tls13 ", 6); full_label_len = 6;
    size_t label_len = strlen(label);
    memcpy(full_label + full_label_len, label, label_len);
    full_label_len += label_len;

    uint8_t info[512];
    size_t off = 0;
    *(uint16_t *)(info + off) = htons((uint16_t)full_label_len); off += 2;
    memcpy(info + off, full_label, full_label_len); off += full_label_len;
    *(uint16_t *)(info + off) = htons((uint16_t)context_len); off += 2;
    if (context_len) memcpy(info + off, context, context_len), off += context_len;
    *(uint16_t *)(info + off) = htons((uint16_t)length); off += 2;

    /* HKDF-Expand: T(1) = HMAC(PRK, "" || info || 0x01)  (一次扩到 length 字节) */
    uint8_t T[SHA256_DIGEST_LEN];
    unsigned int T_len = SHA256_DIGEST_LEN;
    if (length > SHA256_DIGEST_LEN) return -1;
    HMAC_CTX *ctx = HMAC_CTX_new();
    HMAC_Init_ex(ctx, secret, 32, EVP_sha256(), NULL);
    HMAC_Update(ctx, info, off);
    uint8_t c = 0x01;
    HMAC_Update(ctx, &c, 1);
    HMAC_Final(ctx, T, &T_len);
    HMAC_CTX_free(ctx);

    memcpy(out, T, length);
    return 0;
}

static int derive_secret(const uint8_t secret[32],
                         const char *label,
                         const uint8_t *transcript_hash, /* 32 B SHA-256(transcript) */
                         uint8_t out[32]) {
    return hkdf_expand_label(secret, label, transcript_hash, 32, 32, out);
}

/* ============================================================
 * 3. AES-128-GCM 加解密 (用 OpenSSL EVP_aes_128_gcm)
 * ============================================================ */

static int aes_gcm_seal(const uint8_t key[16], const uint8_t iv[12],
                         const uint8_t *plaintext, size_t plaintext_len,
                         const uint8_t *aad, size_t aad_len,
                         uint8_t *ciphertext, uint8_t tag[16]) {
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return -1;
    int rc = -1;
    int outl = 0;
    if (EVP_EncryptInit_ex(ctx, EVP_aes_128_gcm(), NULL, NULL, NULL) > 0 &&
        EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, 12, NULL) > 0 &&
        EVP_EncryptInit_ex(ctx, NULL, NULL, key, iv) > 0 &&
        (aad_len == 0 || EVP_EncryptUpdate(ctx, NULL, &outl, aad, (int)aad_len) > 0) &&
        EVP_EncryptUpdate(ctx, ciphertext, &outl, plaintext, (int)plaintext_len) > 0 &&
        EVP_EncryptFinal_ex(ctx, ciphertext + outl, &outl) > 0 &&
        EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16, tag) > 0) {
        rc = 0;
    }
    EVP_CIPHER_CTX_free(ctx);
    return rc;
}

static int aes_gcm_open(const uint8_t key[16], const uint8_t iv[12],
                        const uint8_t *ciphertext, size_t ciphertext_len,
                        const uint8_t *aad, size_t aad_len,
                        const uint8_t tag[16],
                        uint8_t *plaintext) {
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return -1;
    int rc = -1;
    int outl = 0;
    if (EVP_DecryptInit_ex(ctx, EVP_aes_128_gcm(), NULL, NULL, NULL) > 0 &&
        EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, 12, NULL) > 0 &&
        EVP_DecryptInit_ex(ctx, NULL, NULL, key, iv) > 0 &&
        (aad_len == 0 || EVP_DecryptUpdate(ctx, NULL, &outl, aad, (int)aad_len) > 0) &&
        EVP_DecryptUpdate(ctx, plaintext, &outl, ciphertext, (int)ciphertext_len) > 0 &&
        EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, 16, (void *)tag) > 0 &&
        EVP_DecryptFinal_ex(ctx, plaintext + outl, &outl) > 0) {
        rc = 0;
    }
    EVP_CIPHER_CTX_free(ctx);
    return rc;
}

/* ============================================================
 * 4. 简化的 transcript 哈希 (教学,真实 ClientHello/ServerHello
 *    字节级序列化见 RFC 8446 §4.1.2)
 * ============================================================ */
static void transcript_hash(const uint8_t *a, size_t a_len,
                             const uint8_t *b, size_t b_len,
                             uint8_t out[SHA256_DIGEST_LEN]) {
    EVP_MD_CTX *mctx = EVP_MD_CTX_new();
    unsigned int len = SHA256_DIGEST_LEN;
    EVP_DigestInit_ex(mctx, EVP_sha256(), NULL);
    EVP_DigestUpdate(mctx, a, a_len);
    EVP_DigestUpdate(mctx, b, b_len);
    EVP_DigestFinal_ex(mctx, out, &len);
    EVP_MD_CTX_free(mctx);
}

/* ============================================================
 * 5. 完整演示
 * ============================================================ */
int main(void) {
    printf("================================================================\n");
    printf(" TLS 1.3 1-RTT Handshake Demo (RFC 8446)\n");
    printf("================================================================\n");

    uint8_t c_priv[32], c_pub[32];
    uint8_t s_priv[32], s_pub[32];
    if (x25519_keypair(c_priv, c_pub) != 0 || x25519_keypair(s_priv, s_pub) != 0) {
        fprintf(stderr, "X25519 keypair failed (OpenSSL 1.1.1+ needed)\n");
        return 1;
    }

    printf("\n[Client] X25519 keypair generated\n");
    printf("  priv  (32 B) = ");
    for (int i = 0; i < 32; i += 8) printf("%02x%02x%02x%02x%02x%02x%02x%02x ",
           c_priv[i], c_priv[i+1], c_priv[i+2], c_priv[i+3],
           c_priv[i+4], c_priv[i+5], c_priv[i+6], c_priv[i+7]);
    printf("\n");
    printf("  pub   (32 B) = ");
    for (int i = 0; i < 32; i += 8) printf("%02x%02x%02x%02x%02x%02x%02x%02x ",
           c_pub[i], c_pub[i+1], c_pub[i+2], c_pub[i+3],
           c_pub[i+4], c_pub[i+5], c_pub[i+6], c_pub[i+7]);
    printf("\n");

    /* ECDHE 共享秘密 */
    uint8_t shared_c[32], shared_s[32];
    assert(x25519_derive(c_priv, s_pub, shared_c) == 0);
    assert(x25519_derive(s_priv, c_pub, shared_s) == 0);
    assert(memcmp(shared_c, shared_s, 32) == 0);
    printf("\n[ECDHE] shared secret (双方一致)\n  ");
    for (int i = 0; i < 32; i++) printf("%02x", shared_c[i]);
    printf("\n");

    /* 简化的 CH/SH 字节 (真实场景是完整 TLSPlaintext 结构) */
    const char *ch_str = "ClientHello(v3, TLS_AES_128_GCM_SHA256, X25519, key_share=...)";
    const char *sh_str = "ServerHello(TLS_AES_128_GCM_SHA256, X25519, key_share=...)";
    uint8_t ch_hash[SHA256_DIGEST_LEN], sh_hash[SHA256_DIGEST_LEN];
    transcript_hash((const uint8_t *)ch_str, strlen(ch_str),
                    (const uint8_t *)sh_str, strlen(sh_str),
                    ch_hash);   /* 这里 ch_hash 实际是 (CH||SH) 的哈希 */
    memcpy(sh_hash, ch_hash, SHA256_DIGEST_LEN); /* 简化 */

    /* HKDF-Extract (RFC 5869 §2.2) handshake_secret ← HKDF-Extract(salt=digest(""), ikm=ECDHE) */
    uint8_t zero[SHA256_DIGEST_LEN] = {0};
    uint8_t handshake_secret[32];
    hkdf_extract(zero, SHA256_DIGEST_LEN, shared_c, 32, handshake_secret);
    printf("\n[HKDF-Extract] handshake_secret = ");
    for (int i = 0; i < 32; i++) printf("%02x", handshake_secret[i]);
    printf("\n");

    /* Derive-Secret(s_handshake_secret, "s hs traffic", ClientHello..ServerHello) */
    uint8_t s_hs_secret[32], c_hs_secret[32];
    derive_secret(handshake_secret, "s hs traffic", ch_hash, 32, s_hs_secret);
    derive_secret(handshake_secret, "c hs traffic", ch_hash, 32, c_hs_secret);
    printf("[Derive-Secret] server_handshake_traffic_secret = ");
    for (int i = 0; i < 32; i++) printf("%02x", s_hs_secret[i]);
    printf("\n");

    /* write_key + write_iv */
    uint8_t server_write_key[16], server_write_iv[12];
    hkdf_expand_label(s_hs_secret, "key", NULL, 0, 16, server_write_key);
    hkdf_expand_label(s_hs_secret, "iv",  NULL, 0, 12, server_write_iv);
    printf("[AEAD] server_write_key (16 B) = ");
    for (int i = 0; i < 16; i++) printf("%02x", server_write_key[i]);
    printf("\n[AED]  server_write_iv  (12 B) = ");
    for (int i = 0; i < 12; i++) printf("%02x", server_write_iv[i]);
    printf("\n");

    /* 加密 Certificate + Finished */
    const char *cert_msg = "<fake X.509 certificate chain, ~2KB DER in real>";
    const char *fin_msg  = "<HMAC over full handshake transcript>";
    size_t cert_len = strlen(cert_msg), fin_len = strlen(fin_msg);
    uint8_t ct_cert[256], tag_cert[16], ct_fin[256], tag_fin[16];

    aes_gcm_seal(server_write_key, server_write_iv,
                 (const uint8_t *)cert_msg, cert_len,
                 ch_hash, SHA256_DIGEST_LEN,  /* AAD = transcript hash */
                 ct_cert, tag_cert);
    aes_gcm_seal(server_write_key, server_write_iv,
                 (const uint8_t *)fin_msg, fin_len,
                 ch_hash, SHA256_DIGEST_LEN,
                 ct_fin, tag_fin);

    printf("\n[Server] 加密 Certificate → ct(%zu B) + tag(16 B)\n", cert_len);
    printf("[Server] 加密 Finished → ct(%zu B) + tag(16 B)\n", fin_len);

    /* 客户端解密 */
    uint8_t pt_cert[256], pt_fin[256];
    assert(aes_gcm_open(server_write_key, server_write_iv,
                        ct_cert, cert_len, ch_hash, SHA256_DIGEST_LEN,
                        tag_cert, pt_cert) == 0);
    assert(aes_gcm_open(server_write_key, server_write_iv,
                        ct_fin, fin_len, ch_hash, SHA256_DIGEST_LEN,
                        tag_fin, pt_fin) == 0);

    pt_cert[cert_len] = '\0';
    pt_fin[fin_len] = '\0';
    printf("\n[Client] 解密 Certificate      = '%s'\n", pt_cert);
    printf("[Client] 解密 Finished        = '%s'\n", pt_fin);

    printf("\n================================================================\n");
    printf("  TLS 1.3 1-RTT 握手演示完成 ✓\n");
    printf("================================================================\n");
    return 0;
}
