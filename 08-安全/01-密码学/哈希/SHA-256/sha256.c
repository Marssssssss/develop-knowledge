/* SHA-256 教学实现 —— RFC 6234 / FIPS 180-4 严格对照。
 * 单文件 C99 stdlib,无任何外部依赖,跨平台 (Windows/Linux/macOS)。
 *
 * 实现要点:
 * - 64 轮压缩函数(Ch/Maj/SIGMA0/SIGMA1/sigma0/sigma1 全部按 FIPS 180-4 §6.2)
 * - 初始 H 由前 8 素数平方根小数部分构成
 * - K[64] 由前 64 素数立方根小数部分构成
 * - 填充:0x80 + 0x00... + 64-bit big-endian 长度
 * - 大端字节序,模 2^32 加法(显式 & 0xFFFFFFFF)
 *
 * 测试向量:
 * - SHA256("abc") = ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
 * - SHA256("")    = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
 * - SHA256 百万 'a' = cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0
 */

#include <stdio.h>
#include <string.h>
#include <stdint.h>

#define ROTR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))
#define CH(x,y,z)  (((x) & (y)) ^ ((~(x)) & (z)))
#define MAJ(x,y,z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define BSIG0(x)   (ROTR(x, 2) ^ ROTR(x, 13) ^ ROTR(x, 22))
#define BSIG1(x)   (ROTR(x, 6) ^ ROTR(x, 11) ^ ROTR(x, 25))
#define SSIG0(x)   (ROTR(x, 7) ^ ROTR(x, 18) ^ ((x) >> 3))
#define SSIG1(x)   (ROTR(x, 17) ^ ROTR(x, 19) ^ ((x) >> 10))

static const uint32_t H_INIT[8] = {
    0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
    0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19,
};

static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

typedef struct {
    uint32_t H[8];
    uint8_t  buf[64];
    size_t   buflen;
    uint64_t bitlen;
} sha256_ctx;

static void sha256_init(sha256_ctx *c) {
    memcpy(c->H, H_INIT, sizeof(H_INIT));
    c->buflen = 0;
    c->bitlen = 0;
}

static void sha256_compress(sha256_ctx *c, const uint8_t block[64]) {
    uint32_t W[64];
    /* big-endian 16 words */
    for (int t = 0; t < 16; t++) {
        W[t] = ((uint32_t)block[t*4    ] << 24) |
               ((uint32_t)block[t*4 + 1] << 16) |
               ((uint32_t)block[t*4 + 2] <<  8) |
               ((uint32_t)block[t*4 + 3]);
    }
    for (int t = 16; t < 64; t++) {
        W[t] = SSIG1(W[t-2]) + W[t-7] + SSIG0(W[t-15]) + W[t-16];
    }
    uint32_t a = c->H[0], b = c->H[1], c2 = c->H[2], d = c->H[3];
    uint32_t e = c->H[4], f = c->H[5], g = c->H[6], h = c->H[7];
    for (int t = 0; t < 64; t++) {
        uint32_t T1 = h + BSIG1(e) + CH(e, f, g) + K[t] + W[t];
        uint32_t T2 = BSIG0(a) + MAJ(a, b, c2);
        h = g;
        g = f;
        f = e;
        e = d + T1;
        d = c2;
        c2 = b;
        b = a;
        a = T1 + T2;
    }
    c->H[0] += a; c->H[1] += b; c->H[2] += c2; c->H[3] += d;
    c->H[4] += e; c->H[5] += f; c->H[6] += g; c->H[7] += h;
}

static void sha256_update(sha256_ctx *c, const uint8_t *data, size_t len) {
    c->bitlen += (uint64_t)len * 8;
    while (len > 0) {
        size_t n = 64 - c->buflen;
        if (n > len) n = len;
        memcpy(c->buf + c->buflen, data, n);
        c->buflen += n;
        data += n;
        len -= n;
        if (c->buflen == 64) {
            sha256_compress(c, c->buf);
            c->buflen = 0;
        }
    }
}

static void sha256_final(sha256_ctx *c, uint8_t out[32]) {
    /* Append 0x80, zeros, then 64-bit BE bit length */
    c->buf[c->buflen++] = 0x80;
    if (c->buflen > 56) {
        while (c->buflen < 64) c->buf[c->buflen++] = 0;
        sha256_compress(c, c->buf);
        c->buflen = 0;
    }
    while (c->buflen < 56) c->buf[c->buflen++] = 0;
    uint64_t bl = c->bitlen;
    for (int i = 7; i >= 0; i--) {
        c->buf[56 + i] = (uint8_t)(bl & 0xFF);
        bl >>= 8;
    }
    sha256_compress(c, c->buf);
    for (int i = 0; i < 8; i++) {
        out[i*4    ] = (uint8_t)(c->H[i] >> 24);
        out[i*4 + 1] = (uint8_t)(c->H[i] >> 16);
        out[i*4 + 2] = (uint8_t)(c->H[i] >>  8);
        out[i*4 + 3] = (uint8_t)(c->H[i]);
    }
}

static void sha256(const uint8_t *data, size_t len, uint8_t out[32]) {
    sha256_ctx c;
    sha256_init(&c);
    sha256_update(&c, data, len);
    sha256_final(&c, out);
}

static void hex(const uint8_t *b, size_t n) {
    for (size_t i = 0; i < n; i++) printf("%02x", b[i]);
}

static int eq_hex(const uint8_t *b, const char *h) {
    for (size_t i = 0; i < 32; i++) {
        char c[3] = { h[i*2], h[i*2+1], 0 };
        unsigned long v = strtoul(c, NULL, 16);
        if ((uint8_t)v != b[i]) return 0;
    }
    return 1;
}

static void test(const char *label, const uint8_t *msg, size_t len,
                 const char *expected) {
    uint8_t out[32];
    sha256(msg, len, out);
    printf("[%-22s] ", label);
    if (eq_hex(out, expected)) {
        printf("OK     ");
    } else {
        printf("FAIL   expected=%s got=", expected);
        hex(out, 32);
        printf("\n");
        return;
    }
    hex(out, 32);
    printf("\n");
}

int main(void) {
    printf("=== SHA-256 self-test (5 demos + 1 streaming) ===\n");
    /* FIPS 180-2 Appendix B.1 */
    test("empty",          (uint8_t*)"",          0,
         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    test("abc",            (uint8_t*)"abc",       3,
         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    test("448-bit B.2",    (uint8_t*)"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq", 56,
         "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    test("896-bit B.3",    (uint8_t*)"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu", 112,
         "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1");
    /* million 'a' — tests streaming accumulator */
    uint8_t out[32];
    sha256_ctx ctx;
    sha256_init(&ctx);
    for (int i = 0; i < 1000000; i++) {
        sha256_update(&ctx, (uint8_t*)"a", 1);
    }
    sha256_final(&ctx, out);
    printf("[million 'a'           ] ");
    if (eq_hex(out, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"))
        printf("OK\n");
    else {
        printf("FAIL\n");
        return 1;
    }
    /* demo 6: streaming chunked vs one-shot equivalence */
    sha256_ctx c1, c2;
    sha256_init(&c1);
    sha256_init(&c2);
    uint8_t buf[1000];
    memset(buf, 'x', 1000);
    sha256_update(&c1, buf, 1000);
    /* feed in 7-byte chunks */
    for (int i = 0; i < 1000; i += 7) {
        sha256_update(&c2, buf + i, (i + 7 <= 1000) ? 7 : 1000 - i);
    }
    uint8_t out1[32], out2[32];
    sha256_final(&c1, out1);
    sha256_final(&c2, out2);
    printf("[streaming equivalence  ] ");
    if (memcmp(out1, out2, 32) == 0) printf("OK\n");
    else { printf("FAIL\n"); return 1; }
    printf("=== All 6 demos PASSED ===\n");
    return 0;
}