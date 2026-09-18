#ifndef WG_CRYPTO_IMPL_H
#define WG_CRYPTO_IMPL_H
/* WireGuard 用到的密码学原语：BLAKE2s（含 keyed 模式）+ HMAC-BLAKE2s + HKDF(BLAKE2s)。
 *
 * 为什么自己写 BLAKE2s：OpenSSL 只暴露**未键控**的 BLAKE2s 摘要，而 WireGuard 的
 * MAC1/MAC2 依赖 keyed-BLAKE2s（32 字节密钥 / 16 字节输出），且它的 HKDF 是
 * 「HMAC-BLAKE2s（块长 64）上的 HKDF」，OpenSSL 也没有直接对应的封装。
 *
 * 用「实现头」而不是第二个 .c：本文件被 wg_handshake.c 文本级 #include，处于同一
 * 翻译单元，static 符号可见性与直接写在一起完全一致，构建命令也只需编译 .c。
 */

#include <openssl/evp.h>
#include <openssl/rand.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

#define PUB_LEN   32
#define SYM_LEN   32
#define TAG_LEN   16
#define TS_LEN    12
#define HASH_LEN  32
#define COOKIE_LEN 16
#define MACS_LEN  (2 * COOKIE_LEN)

#define LEN_INITIATION (4 + 4 + PUB_LEN + (PUB_LEN + TAG_LEN) + (TS_LEN + TAG_LEN) + MACS_LEN)
#define LEN_RESPONSE   (4 + 4 + 4 + PUB_LEN + TAG_LEN + MACS_LEN)
#define LEN_DATA_HDR   (4 + 4 + 8)

static const char HANDSHAKE_NAME[] = "Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s";
static const char IDENTIFIER_NAME[] = "WireGuard v1 zx2c4 Jason@zx2c4.com";
/* 注意用显式字符列表：写成 = "mac1----" 会引入结尾 '\0'，数组长度变 9 */
static const uint8_t MAC1_LABEL[8] =
    {'m', 'a', 'c', '1', '-', '-', '-', '-'};
static const uint8_t COOKIE_LABEL[8] =
    {'c', 'o', 'o', 'k', 'i', 'e', '-', '-'};
/* kdf/hmac 的输入在本 demo 中不超过 64 字节，超出直接报错而不是静默截断 */
#define HMAC_MAX_IN 256



static void check(const char *label, int cond, long got, long want) {
    printf("%-42s %s (got=%ld want=%ld)\n", label, cond ? "ok" : "FAIL", got, want);
    if (!cond) failures++;
}

/* ============================================================
 * 1. BLAKE2s（RFC 7693）—— 支持 keyed 模式与可变输出长度
 * ============================================================ */

static const uint32_t B2S_IV[8] = {
    0x6A09E667u, 0xBB67AE85u, 0x3C6EF372u, 0xA54FF53Au,
    0x510E527Fu, 0x9B05688Cu, 0x1F83D9ABu, 0x5BE0CD19u
};
static const uint8_t B2S_SIGMA[10][16] = {
    {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15},
    {14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3},
    {11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4},
    {7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8},
    {9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13},
    {2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9},
    {12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11},
    {13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10},
    {6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5},
    {10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0}
};

#define ROTR32(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void b2s_compress(uint32_t h[8], const uint8_t block[64], uint64_t counter,
                         int is_last, int outlen) {
    uint32_t m[16], v[16];
    int i, r;
    for (i = 0; i < 16; i++)
        m[i] = (uint32_t)block[i * 4] | ((uint32_t)block[i * 4 + 1] << 8) |
               ((uint32_t)block[i * 4 + 2] << 16) | ((uint32_t)block[i * 4 + 3] << 24);
    for (i = 0; i < 8; i++) v[i] = h[i];
    for (i = 0; i < 8; i++) v[8 + i] = B2S_IV[i];
    v[12] ^= (uint32_t)counter;
    v[13] ^= (uint32_t)(counter >> 32);
    /* BLAKE2s 把「最后一轮标志」与输出长度编码进 v[14]/v[15] */
    v[14] ^= is_last ? 0xFFFFFFFFu : 0u;
    v[15] ^= (uint32_t)outlen;

#define G(a, b, c, d, x, y)                                     \
    do {                                                        \
        v[a] = v[a] + v[b] + (x);                               \
        v[d] = ROTR32(v[d] ^ v[a], 16);                         \
        v[c] = v[c] + v[d];                                     \
        v[b] = ROTR32(v[b] ^ v[c], 12);                         \
        v[a] = v[a] + v[b] + (y);                               \
        v[d] = ROTR32(v[d] ^ v[a], 8);                          \
        v[c] = v[c] + v[d];                                     \
        v[b] = ROTR32(v[b] ^ v[c], 7);                          \
    } while (0)

    for (r = 0; r < 10; r++) {
        const uint8_t *s = B2S_SIGMA[r];
        G(0, 4, 8, 12, m[s[0]], m[s[1]]);
        G(1, 5, 9, 13, m[s[2]], m[s[3]]);
        G(2, 6, 10, 14, m[s[4]], m[s[5]]);
        G(3, 7, 11, 15, m[s[6]], m[s[7]]);
        G(0, 5, 10, 15, m[s[8]], m[s[9]]);
        G(1, 6, 11, 12, m[s[10]], m[s[11]]);
        G(2, 7, 8, 13, m[s[12]], m[s[13]]);
        G(3, 4, 9, 14, m[s[14]], m[s[15]]);
    }
#undef G
    for (i = 0; i < 8; i++) h[i] ^= v[i] ^ v[8 + i];
}

/* inlen 允许为 0；keylen 为 0 表示未键控；keylen 合法范围在调用点保证 */
static void blake2s(uint8_t *out, size_t outlen, const uint8_t *key, size_t keylen,
                    const uint8_t *in, size_t inlen) {
    uint32_t h[8];
    uint8_t block[64], param[64];
    uint64_t counter = 0;
    size_t off = 0, i;

    memset(param, 0, sizeof(param));
    param[0] = (uint8_t)outlen;
    param[1] = (uint8_t)keylen;
    param[2] = 1;
    param[3] = 1;
    for (i = 0; i < 8; i++) h[i] = B2S_IV[i];
    b2s_compress(h, param, 0, 0, (int)outlen);

    if (keylen > 0) {                       /* 密钥先当作第一个（满）块喂入 */
        memset(block, 0, sizeof(block));
        memcpy(block, key, keylen);
        counter += 64;
        b2s_compress(h, block, counter, 0, (int)outlen);
    }
    while (inlen - off > 64) {              /* 只对「最后一个块」置标志位 */
        counter += 64;
        b2s_compress(h, in + off, counter, 0, (int)outlen);
        off += 64;
    }
    memset(block, 0, sizeof(block));
    memcpy(block, in + off, inlen - off);
    counter += inlen - off;
    b2s_compress(h, block, counter, 1, (int)outlen);
    for (i = 0; i < outlen; i++)
        out[i] = (uint8_t)(h[i / 4] >> (8 * (i % 4)));
}

/* HMAC-BLAKE2s：块长 64，等价于内核 noise.c 的 hmac()。
 * 这里用一次性接口拼出内层/外层，故对 msglen 设上限并在超出时显式报错 ——
 * 静默截断会让 MAC 校验「永远算得出来但永远不对」，是最难查的一类缺陷。 */
static void hmac_blake2s(uint8_t out[HASH_LEN], const uint8_t *key, size_t keylen,
                         const uint8_t *msg, size_t msglen) {
    uint8_t ikey[64], okey[64], inner[HASH_LEN];
    uint8_t buf[64 + HMAC_MAX_IN];
    size_t i;
    if (msglen > HMAC_MAX_IN) {
        fprintf(stderr, "hmac_blake2s: msglen %zu exceeds %d\n", msglen, HMAC_MAX_IN);
        abort();
    }
    memset(ikey, 0, sizeof(ikey));
    memset(okey, 0, sizeof(okey));
    if (keylen > 64) {
        blake2s(ikey, HASH_LEN, NULL, 0, key, keylen);
        memcpy(okey, ikey, HASH_LEN);
    } else {
        memcpy(ikey, key, keylen);
        memcpy(okey, key, keylen);
    }
    for (i = 0; i < 64; i++) {
        ikey[i] ^= 0x36;
        okey[i] ^= 0x5C;
    }
    memcpy(buf, ikey, 64);
    memcpy(buf + 64, msg, msglen);
    blake2s(inner, HASH_LEN, NULL, 0, buf, 64 + msglen);

    memcpy(buf, okey, 64);
    memcpy(buf + 64, inner, HASH_LEN);
    blake2s(out, HASH_LEN, NULL, 0, buf, 64 + HASH_LEN);
}

/* KDF = HKDF(BLAKE2s)：Extract 后按 0x01/0x02/0x03 顺序 Expand（可只取前 1~2 个）。
 * out1 允许与 ck 指向同一块内存（ck 在函数开头只被读一次，随后才被覆写）。 */
static void kdf(uint8_t *out1, uint8_t *out2, uint8_t *out3,
                const uint8_t *data, size_t datalen,
                const uint8_t ck[HASH_LEN]) {
    uint8_t secret[HASH_LEN], buf[HASH_LEN + 1];
    size_t n, i;
    hmac_blake2s(secret, ck, HASH_LEN, data, datalen);
    for (n = 1; n <= 3; n++) {
        uint8_t *dst = (n == 1) ? out1 : (n == 2) ? out2 : out3;
        if (dst == NULL) break;
        if (n == 1) {
            buf[0] = 1;
            i = 1;
        } else {
            buf[HASH_LEN] = (uint8_t)n;
            i = HASH_LEN + 1;
        }
        hmac_blake2s(dst, secret, HASH_LEN, buf, i);
    }
}

static int failures = 0;

#endif /* WG_CRYPTO_IMPL_H */
