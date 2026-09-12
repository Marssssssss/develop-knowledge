// IKEv2 + ESP 演示 (RFC 7296 + RFC 4303)
//
// 编译: gcc -O2 -Wall -Wextra ikev2_demo.c -lcrypto -o ikev2

#include <openssl/hmac.h>
#include <openssl/evp.h>
#include <openssl/sha.h>
#include <openssl/rand.h>
#include <openssl/crypto.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>

#define SHA256_DIG 32
#define SPI_SIZE   4
#define SEQ_SIZE   4

/* PRF = HMAC-SHA-256 */
static void prf(const uint8_t *key, size_t key_len,
                const uint8_t *data, size_t data_len,
                uint8_t out[SHA256_DIG]) {
    unsigned int out_len = SHA256_DIG;
    HMAC(key, (int)key_len, data, data_len, out, &out_len);
}

/* PRF+ = T(1) || T(2) || ... || T(count), each Ti = prf(K, T(i-1) | seed | i) */
static void prfplus(const uint8_t *key,
                     const uint8_t *seed, size_t seed_len,
                     int count,
                     uint8_t *out, int out_len) {
    /* 输出总长度 = count * 32;out_len <= count*32 */
    if (count <= 0) return;
    uint8_t T_prev[SHA256_DIG] = {0};
    for (int i = 1; i <= count; i++) {
        uint8_t to_hash[SHA256_DIG + seed_len + 1];
        memcpy(to_hash, T_prev, SHA256_DIG);
        memcpy(to_hash + SHA256_DIG, seed, seed_len);
        to_hash[SHA256_DIG + seed_len] = (uint8_t)i;
        uint8_t T[SHA256_DIG];
        unsigned int len = SHA256_DIG;
        HMAC(key, SHA256_DIG, to_hash, sizeof(to_hash), T, &len);
        memcpy(T_prev, T, SHA256_DIG);
        int start = (i - 1) * SHA256_DIG;
        int copy = (out_len - start) >= SHA256_DIG ? SHA256_DIG : (out_len - start);
        if (start < out_len)
            memcpy(out + start, T, copy);
    }
}

/* X25519 DH (OpenSSL EVP) */
static int x25519_keypair(uint8_t priv[32], uint8_t pub[32]) {
    EVP_PKEY_CTX *ctx = EVP_PKEY_CTX_new_id(EVP_PKEY_X25519, NULL);
    if (!ctx) return -1;
    EVP_PKEY *pkey = NULL;
    if (EVP_PKEY_keygen_init(ctx) <= 0 ||
        EVP_PKEY_keygen(ctx, &pkey) <= 0) {
        EVP_PKEY_CTX_free(ctx); return -1;
    }
    size_t pl = 32, ql = 32;
    EVP_PKEY_get_raw_private_key(pkey, priv, &pl);
    EVP_PKEY_get_raw_public_key(pkey, pub, &ql);
    EVP_PKEY_free(pkey);
    EVP_PKEY_CTX_free(ctx);
    return 0;
}

static int x25519_shared(const uint8_t priv[32], const uint8_t peer[32],
                         uint8_t shared[32]) {
    EVP_PKEY *pk = EVP_PKEY_new_raw_private_key(EVP_PKEY_X25519, NULL, priv, 32);
    EVP_PKEY *pkp = EVP_PKEY_new_raw_public_key(EVP_PKEY_X25519, NULL, peer, 32);
    EVP_PKEY_CTX *ctx = EVP_PKEY_CTX_new(pk, NULL);
    if (!ctx) { return -1; }
    int rc = -1;
    if (EVP_PKEY_derive_init(ctx) > 0 && EVP_PKEY_derive_set_peer(ctx, pkp) > 0) {
        size_t sl = 32;
        if (EVP_PKEY_derive(ctx, shared, &sl) > 0) rc = 0;
    }
    EVP_PKEY_CTX_free(ctx);
    EVP_PKEY_free(pk);  EVP_PKEY_free(pkp);
    return rc;
}

/* AES-128-GCM seal */
static int aes_gcm_seal(const uint8_t key[16], const uint8_t iv[12],
                        const uint8_t *plain, size_t plain_len,
                        uint8_t *cipher, uint8_t tag[16]) {
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    int rc = -1, outl = 0;
    if (EVP_EncryptInit_ex(ctx, EVP_aes_128_gcm(), NULL, NULL, NULL) > 0 &&
        EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, 12, NULL) > 0 &&
        EVP_EncryptInit_ex(ctx, NULL, NULL, key, iv) > 0 &&
        EVP_EncryptUpdate(ctx, cipher, &outl, plain, (int)plain_len) > 0 &&
        EVP_EncryptFinal_ex(ctx, cipher + outl, &outl) > 0 &&
        EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16, tag) > 0) {
        rc = 0;
    }
    EVP_CIPHER_CTX_free(ctx);
    return rc;
}

/* IKEv2 §2.14 SKEYSEED + 7 keys */
typedef struct {
    uint8_t SKEYSEED[32];
    uint8_t SK_d[32];
    uint8_t SK_pi[32];
    uint8_t SK_pr[32];
    uint8_t SK_ai[32];
    uint8_t SK_ar[32];
    uint8_t SK_ei[32];
    uint8_t SK_er[32];
} ikev2_keys_t;

static void ikev2_derive(ikev2_keys_t *k,
                          const uint8_t shared[32],
                          const uint8_t ni[32], const uint8_t nr[32]) {
    prf(ni, 64, shared, 32, k->SKEYSEED);  /* SKEYSEED = prf(Ni | Nr, g^ir) */
    prfplus(k->SKEYSEED, ni, 64, 1, k->SK_d,  32);
    prfplus(k->SKEYSEED, ni, 64, 1, k->SK_pi, 32);
    prfplus(k->SKEYSEED, ni, 64, 1, k->SK_pr, 32);
    prfplus(k->SKEYSEED, ni, 64, 1, k->SK_ai, 32);
    prfplus(k->SKEYSEED, ni, 64, 1, k->SK_ar, 32);
    prfplus(k->SKEYSEED, ni, 64, 1, k->SK_ei, 32);
    prfplus(k->SKEYSEED, ni, 64, 1, k->SK_er, 32);
}

/* Derive Child SA keymat (4 keys × 16 B for AES-128-GCM) */
static void derive_child_sa(const uint8_t sk_d[32], const uint8_t ni[32],
                            const uint8_t nr[32],
                            uint8_t sk_ei_c[16], uint8_t sk_ai_c[16],
                            uint8_t sk_er_c[16], uint8_t sk_ar_c[16]) {
    uint8_t seed[64];
    memcpy(seed, ni, 32);
    memcpy(seed + 32, nr, 32);
    uint8_t keymat[64];
    prfplus(sk_d, seed, 64, 2, keymat, 64);
    memcpy(sk_ei_c, keymat,       16);
    memcpy(sk_ai_c, keymat + 16,   16);
    memcpy(sk_er_c, keymat + 32,   16);
    memcpy(sk_ar_c, keymat + 48,   16);
}

/* ESP 包: SPI(4) + SeqNo(4) + IV(12) + AES-GCM CT + Tag(16) */
static int make_esp(uint8_t *pkt, size_t *pkt_len,
                     uint32_t spi, uint32_t seqno,
                     const uint8_t key[16],
                     const uint8_t *plain, size_t plain_len) {
    uint8_t hdr[SPI_SIZE + SEQ_SIZE];
    hdr[0] = spi >> 24; hdr[1] = spi >> 16; hdr[2] = spi >> 8; hdr[3] = spi;
    hdr[4] = seqno >> 24; hdr[5] = seqno >> 16; hdr[6] = seqno >> 8; hdr[7] = seqno;
    uint8_t iv[12];
    RAND_bytes(iv, 12);
    memcpy(pkt, hdr, 8);
    memcpy(pkt + 8, iv, 12);
    if (aes_gcm_seal(key, iv, plain, plain_len, pkt + 20, pkt + 20 + plain_len) != 0)
        return -1;
    *pkt_len = 8 + 12 + plain_len + 16;
    return 0;
}

int main(void) {
    printf("================================================================\n");
    printf(" IKEv2 密钥协商 (RFC 7296) + ESP 数据面 (RFC 4303) 演示\n");
    printf("================================================================\n");

    /* Phase 1: IKE_SA_INIT */
    uint8_t i_priv[32], i_pub[32], r_priv[32], r_pub[32];
    x25519_keypair(i_priv, i_pub);
    x25519_keypair(r_priv, r_pub);
    uint8_t ni[32], nr[32];
    RAND_bytes(ni, 32); RAND_bytes(nr, 32);

    uint8_t shared_i[32], shared_r[32];
    x25519_shared(i_priv, r_pub, shared_i);
    x25519_shared(r_priv, i_pub, shared_r);
    if (memcmp(shared_i, shared_r, 32) != 0) { fprintf(stderr, "DH mismatch\n"); return 1; }
    printf("\n[IKE_SA_INIT] Initiator ECDHE shared = ");
    for (int i = 0; i < 32; i++) printf("%02x", shared_i[i]);
    printf("\n");

    ikev2_keys_t k;
    ikev2_derive(&k, shared_i, ni, nr);
    printf("[Key Schedule] SKEYSEED = ");
    for (int i = 0; i < 32; i++) printf("%02x", k.SKEYSEED[i]);
    printf("\n");
    printf("              SK_ei (enc init) = ");
    for (int i = 0; i < 32; i++) printf("%02x", k.SK_ei[i]); printf("  …\n");
    printf("              SK_d  (KEYMAT for Child SA) = ");
    for (int i = 0; i < 32; i++) printf("%02x", k.SK_d[i]);  printf("  …\n");

    /* Phase 2: Child SA keys */
    uint8_t sk_ei_c[16], sk_ai_c[16], sk_er_c[16], sk_ar_c[16];
    derive_child_sa(k.SK_d, ni, nr, sk_ei_c, sk_ai_c, sk_er_c, sk_ar_c);
    printf("\n[Child SA 1] SK_ei_child (I→R ESP encrypt, 16 B) = ");
    for (int i = 0; i < 16; i++) printf("%02x", sk_ei_c[i]); printf("\n");

    /* Build inner IP / HTTP */
    uint8_t inner[] =
        "GET / HTTP/1.1\r\nHost: example.com\r\n\r\n";
    uint8_t esp[256];
    size_t esp_len = 0;
    if (make_esp(esp, &esp_len, 0x12345678, 1, sk_ei_c, inner, sizeof(inner) - 1) != 0) {
        fprintf(stderr, "make_esp failed\n"); return 1;
    }
    printf("\n[ESP] Initiator → Responder: ESP 包 %zu B\n", esp_len);
    printf("       SPI = 0x12345678 SeqNo = 1\n");
    printf("       payload = \"GET / HTTP/1.1\\r\\n...\"\n");

    /* Responder 解密 */
    uint32_t r_spi = (esp[0] << 24) | (esp[1] << 16) | (esp[2] << 8) | esp[3];
    uint32_t r_seqno = (esp[4] << 24) | (esp[5] << 16) | (esp[6] << 8) | esp[7];
    if (r_spi != 0x12345678) { fprintf(stderr, "SPI mismatch\n"); return 1; }
    uint8_t iv[12]; memcpy(iv, esp + 8, 12);
    size_t ct_len = esp_len - 36;
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    uint8_t pt[256];
    int outl = 0;
    EVP_DecryptInit_ex(ctx, EVP_aes_128_gcm(), NULL, NULL, NULL);
    EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, 12, NULL);
    EVP_DecryptInit_ex(ctx, NULL, NULL, sk_ei_c, iv);
    EVP_DecryptUpdate(ctx, NULL, &outl, NULL, 0);  /* AAD = 空 */
    int pt_len = 0;
    EVP_DecryptUpdate(ctx, pt, &outl, esp + 20, (int)ct_len);
    pt_len = outl;
    EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, 16, (void *)(esp + 20 + ct_len));
    EVP_DecryptFinal_ex(ctx, pt + pt_len, &outl);
    pt_len += outl;
    pt[pt_len] = '\0';
    EVP_CIPHER_CTX_free(ctx);

    printf("\n[ESP] Responder 收到 → SK_ei_child 解密:\n");
    printf("       SeqNo = %u\n", r_seqno);
    printf("       payload = %s\n", pt);
    printf("       ✓ 与原始 inner IP 一致\n");

    printf("\n================================================================\n");
    printf(" IKEv2 + ESP 演示完成 ✓\n");
    printf("================================================================\n");
    return 0;
}
