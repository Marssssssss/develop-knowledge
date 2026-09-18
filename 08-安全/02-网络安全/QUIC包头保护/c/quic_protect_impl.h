/*
 * quic_protect_impl.h —— 头保护与 AEAD（RFC 9001 §5.3 / §5.4）
 *
 * 作为实现头被 quic_demo.c 文本级 #include（与 quic_packet_impl.h 同属一个 TU）。
 *
 * C 版用 OpenSSL，因此可以**同时演示两套头保护**：
 *   - AES 套件：mask = AES-ECB(hp_key, sample) 的前 5 字节（RFC 9001 附录 A.2 实例）
 *   - ChaCha20 套件：样本前 4 字节当块计数器（小端）、后 12 字节当 nonce，
 *     加密 5 个零字节得到 mask（附录 A.5 实例）
 * 纯 Python 标准库没有 AES，所以那边只做了 ChaCha20 路径，两侧互为补充。
 */
#ifndef QUIC_PROTECT_IMPL_H
#define QUIC_PROTECT_IMPL_H

#include <string.h>

#include <openssl/evp.h>

#include "quic_packet_impl.h"

/* ----------------------------------------------- 头保护掩码（两套套件） */

/* ChaCha20 套件：样本前 4 字节 = 块计数器（小端），后 12 字节 = nonce */
static int quic_hp_mask_chacha20(const uint8_t *hp_key, const uint8_t *sample,
                                 uint8_t mask[5])
{
    uint8_t iv[16], zeros[5], out[16];
    int len = 0, fin = 0, ok;
    EVP_CIPHER_CTX *ctx;
    memset(zeros, 0, sizeof(zeros));
    memcpy(iv, sample, 4);
    memcpy(iv + 4, sample + 4, 12);
    ctx = EVP_CIPHER_CTX_new();
    if (ctx == NULL) return -1;
    ok = EVP_EncryptInit_ex(ctx, EVP_chacha20(), NULL, hp_key, iv) == 1;
    ok = ok && EVP_EncryptUpdate(ctx, out, &len, zeros, 5) == 1;
    ok = ok && EVP_EncryptFinal_ex(ctx, out + len, &fin) == 1;
    EVP_CIPHER_CTX_free(ctx);
    if (!ok || len != 5) return -1;
    memcpy(mask, out, 5);
    return 0;
}

/* AES 套件：mask = AES-ECB(hp_key, sample) 的前 5 字节（附录 A.2 的实例） */
static int quic_hp_mask_aes(const uint8_t *hp_key16, const uint8_t *sample,
                            uint8_t mask[5])
{
    uint8_t out[32];
    int len = 0, fin = 0, ok;
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (ctx == NULL) return -1;
    ok = EVP_EncryptInit_ex(ctx, EVP_aes_128_ecb(), NULL, hp_key16, NULL) == 1;
    ok = ok && EVP_CIPHER_CTX_set_padding(ctx, 0) == 1;
    ok = ok && EVP_EncryptUpdate(ctx, out, &len, sample, QUIC_SAMPLE_LEN) == 1;
    ok = ok && EVP_EncryptFinal_ex(ctx, out + len, &fin) == 1;
    EVP_CIPHER_CTX_free(ctx);
    if (!ok || len != QUIC_SAMPLE_LEN) return -1;
    memcpy(mask, out, 5);
    return 0;
}

/*
 * 就地加上头保护：长头只掩首字节低 4 位（保住类型位与固定位），
 * 短头掩低 5 位（保住固定位与密钥相位）。
 */
static void quic_apply_header_protection(uint8_t *pkt, size_t pn_offset,
                                         unsigned pn_len, const uint8_t mask[5])
{
    unsigned i;
    if (pkt[0] & 0x80) {
        pkt[0] ^= (uint8_t)(mask[0] & 0x0f);
    } else {
        pkt[0] ^= (uint8_t)(mask[0] & 0x1f);
    }
    for (i = 0; i < pn_len; i++) {
        pkt[pn_offset + i] ^= mask[1 + i];
    }
}

/*
 * 去头保护并返回解出的包号长度；采样不足返回 0（调用方应丢弃整包）。
 * 顺序不能反：先用只依赖偏移的采样算掩码，还原首字节后才知道包号多长。
 */
static unsigned quic_remove_header_protection(uint8_t *pkt, size_t pkt_len,
                                              size_t pn_offset, int use_chacha,
                                              const uint8_t *hp_key)
{
    uint8_t mask[5];
    unsigned pn_len, i;
    const uint8_t *sample;
    if (!quic_sample_ok(pkt_len, pn_offset)) return 0;
    sample = pkt + pn_offset + QUIC_HP_OFFSET_FROM_PN;
    if (use_chacha) {
        if (quic_hp_mask_chacha20(hp_key, sample, mask) != 0) return 0;
    } else if (quic_hp_mask_aes(hp_key, sample, mask) != 0) {
        return 0;
    }
    if (pkt[0] & 0x80) {
        pkt[0] ^= (uint8_t)(mask[0] & 0x0f);
    } else {
        pkt[0] ^= (uint8_t)(mask[0] & 0x1f);
    }
    pn_len = quic_pn_length_from_first_byte(pkt[0]);
    if (pn_len > 4) return 0;
    for (i = 0; i < pn_len; i++) {
        pkt[pn_offset + i] ^= mask[1 + i];
    }
    return pn_len;
}

/* --------------------------------------------------------------- AEAD 封装 */

/* ChaCha20-Poly1305：输出密文 || 16 字节标签 */
static int quic_aead_seal(const uint8_t *key, const uint8_t *nonce,
                          const uint8_t *aad, size_t aad_len,
                          const uint8_t *pt, size_t pt_len,
                          uint8_t *out, size_t *out_len)
{
    int len = 0, fin = 0, ok;
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (ctx == NULL) return -1;
    ok = EVP_EncryptInit_ex(ctx, EVP_chacha20_poly1305(), NULL, NULL, NULL) == 1;
    ok = ok && EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_AEAD_SET_IVLEN, 12, NULL) == 1;
    ok = ok && EVP_EncryptInit_ex(ctx, NULL, NULL, key, nonce) == 1;
    if (aad_len > 0) {
        ok = ok && EVP_EncryptUpdate(ctx, NULL, &len, aad, (int)aad_len) == 1;
    }
    ok = ok && EVP_EncryptUpdate(ctx, out, &len, pt, (int)pt_len) == 1;
    ok = ok && EVP_EncryptFinal_ex(ctx, out + len, &fin) == 1;
    if (ok) {
        len += fin;
        ok = EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_AEAD_GET_TAG, 16, out + len) == 1;
    }
    EVP_CIPHER_CTX_free(ctx);
    if (!ok) return -1;
    *out_len = (size_t)len + 16;
    return 0;
}

/* 校验失败必须返回 -1 且不输出任何明文（C 没有异常，这一步最容易被漏掉） */
static int quic_aead_open(const uint8_t *key, const uint8_t *nonce,
                          const uint8_t *aad, size_t aad_len,
                          const uint8_t *sealed, size_t sealed_len,
                          uint8_t *out, size_t *out_len)
{
    int len = 0, fin = 0, ok;
    size_t ct_len;
    uint8_t tag[16];
    EVP_CIPHER_CTX *ctx;
    if (sealed_len < 16) return -1;
    ct_len = sealed_len - 16;
    memcpy(tag, sealed + ct_len, 16);
    ctx = EVP_CIPHER_CTX_new();
    if (ctx == NULL) return -1;
    ok = EVP_DecryptInit_ex(ctx, EVP_chacha20_poly1305(), NULL, NULL, NULL) == 1;
    ok = ok && EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_AEAD_SET_IVLEN, 12, NULL) == 1;
    ok = ok && EVP_DecryptInit_ex(ctx, NULL, NULL, key, nonce) == 1;
    if (aad_len > 0) {
        ok = ok && EVP_DecryptUpdate(ctx, NULL, &len, aad, (int)aad_len) == 1;
    }
    ok = ok && EVP_DecryptUpdate(ctx, out, &len, sealed, (int)ct_len) == 1;
    if (ok) {
        ok = EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_AEAD_SET_TAG, 16, tag) == 1;
    }
    ok = ok && EVP_DecryptFinal_ex(ctx, out + len, &fin) == 1;
    EVP_CIPHER_CTX_free(ctx);
    if (!ok) return -1;
    *out_len = (size_t)(len + fin);
    return 0;
}

#endif /* QUIC_PROTECT_IMPL_H */
