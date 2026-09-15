/* HKDF: 基于 HMAC 的 Extract-and-Expand 密钥派生(RFC 5869)。
 * 依据: https://www.rfc-editor.org/rfc/rfc5869.html
 *   §2.2 Extract: PRK = HMAC-Hash(salt, IKM), salt 缺省 = HashLen 个 0x00
 *   §2.3 Expand: T(i) = HMAC(PRK, T(i-1)|info|i), OKM = 前 L 字节
 * 自含 SHA-256(FIPS 180-4) 与 HMAC-SHA-256(RFC 2104), 无外部依赖。
 * 编译: cc hkdf.c -o hkdf && ./hkdf */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ---------- SHA-256 (FIPS 180-4) ---------- */
static const uint32_t K256[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void sha256_compress(uint32_t h[8], const uint8_t blk[64]) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++)
        w[i] = ((uint32_t)blk[4 * i] << 24) | ((uint32_t)blk[4 * i + 1] << 16) |
               ((uint32_t)blk[4 * i + 2] << 8) | (uint32_t)blk[4 * i + 3];
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t S1 = ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25);
        uint32_t ch = (e & f) ^ (~e & g);
        uint32_t t1 = hh + S1 + ch + K256[i] + w[i];
        uint32_t S0 = ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        hh = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + (S0 + maj);
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d;
    h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
}

static void sha256(const uint8_t *msg, size_t len, uint8_t out[32]) {
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                     0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    size_t off = 0;
    while (off + 64 <= len) { /* 整块直接压缩 */
        sha256_compress(h, msg + off);
        off += 64;
    }
    /* 尾块填充 */
    uint8_t blk[64];
    size_t rem = len - off;
    memset(blk, 0, 64);
    memcpy(blk, msg + off, rem);
    blk[rem] = 0x80;
    if (rem >= 56) { /* 1 字节放不下长度, 需两个尾块 */
        sha256_compress(h, blk);
        memset(blk, 0, 64);
    }
    uint64_t bits = (uint64_t)len * 8;
    for (int i = 0; i < 8; i++)
        blk[63 - i] = (uint8_t)(bits >> (8 * i));
    sha256_compress(h, blk);
    for (int i = 0; i < 8; i++) {
        out[4 * i] = (uint8_t)(h[i] >> 24);
        out[4 * i + 1] = (uint8_t)(h[i] >> 16);
        out[4 * i + 2] = (uint8_t)(h[i] >> 8);
        out[4 * i + 3] = (uint8_t)h[i];
    }
}

/* ---------- HMAC-SHA-256 (RFC 2104) ---------- */
static void hmac_sha256(const uint8_t *key, size_t klen, const uint8_t *msg,
                        size_t mlen, uint8_t out[32]) {
    uint8_t k[64], ipad[64], opad[64], inner[32], *m;
    memset(k, 0, 64);
    if (klen > 64)
        sha256(key, klen, k); /* 长密钥先哈希 */
    else
        memcpy(k, key, klen);
    for (int i = 0; i < 64; i++) {
        ipad[i] = k[i] ^ 0x36;
        opad[i] = k[i] ^ 0x5c;
    }
    m = malloc(64 + mlen);
    memcpy(m, ipad, 64);
    memcpy(m + 64, msg, mlen);
    sha256(m, 64 + mlen, inner);
    memcpy(m, opad, 64);
    memcpy(m + 64, inner, 32);
    sha256(m, 96, out);
    free(m);
}

/* ---------- HKDF (RFC 5869) ---------- */
static void hkdf_extract(const uint8_t *salt, size_t slen, const uint8_t *ikm,
                          size_t ilen, uint8_t prk[32]) {
    uint8_t zeros[32];
    if (!salt || slen == 0) {
        memset(zeros, 0, 32);
        salt = zeros;
        slen = 32;
    }
    hmac_sha256(salt, slen, ikm, ilen, prk);
}

static void hkdf_expand(const uint8_t prk[32], const uint8_t *info, size_t ilen,
                        size_t L, uint8_t *okm) {
    uint8_t t[32], buf[32 + 256 + 1];
    size_t off = 0;
    uint8_t i = 0;
    while (off < L) {
        i++;
        size_t tn = off == 0 ? 0 : 32;
        memcpy(buf, t, tn);
        memcpy(buf + tn, info, ilen);
        buf[tn + ilen] = i;
        hmac_sha256(prk, 32, buf, tn + ilen + 1, t);
        size_t take = L - off < 32 ? L - off : 32;
        memcpy(okm + off, t, take);
        off += take;
    }
}

static int hex2bin(const char *s, uint8_t *out) {
    size_t n = strlen(s) / 2;
    for (size_t i = 0; i < n; i++)
        sscanf(s + 2 * i, "%2hhx", out + i);
    return (int)n;
}

int main(void) {
    uint8_t prk[32], okm[128], want[128];
    int fail = 0, n;

    /* demo1: RFC 5869 A.1 */
    uint8_t ikm[22], salt[13], info[10];
    for (int i = 0; i < 22; i++) ikm[i] = 0x0b;
    hex2bin("000102030405060708090a0b0c", salt);
    hex2bin("f0f1f2f3f4f5f6f7f8f9", info);
    hkdf_extract(salt, 13, ikm, 22, prk);
    n = hex2bin("077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5", want);
    if (memcmp(prk, want, n)) { puts("FAIL A.1 PRK"); fail++; } else puts("demo1 A.1 PRK: PASS");
    hkdf_expand(prk, info, 10, 42, okm);
    n = hex2bin("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
                "34007208d5b887185865", want);
    if (memcmp(okm, want, n)) { puts("FAIL A.1 OKM"); fail++; } else puts("demo1 A.1 OKM: PASS");

    /* demo2: A.2 长 IKM/salt/info, L=82 */
    uint8_t ikm2[80], salt2[80], info2[80];
    for (int i = 0; i < 80; i++) {
        ikm2[i] = (uint8_t)i;
        salt2[i] = (uint8_t)(0x60 + i);
        info2[i] = (uint8_t)(0xB0 + i);
    }
    hkdf_extract(salt2, 80, ikm2, 80, prk);
    n = hex2bin("06a6b88c5853361a06104c9ceb35b45cef760014904671014a193f40c15fc244", want);
    if (memcmp(prk, want, n)) { puts("FAIL A.2 PRK"); fail++; } else puts("demo2 A.2 PRK: PASS");
    hkdf_expand(prk, info2, 80, 82, okm);
    n = hex2bin("b11e398dc80327a1c8e7f78c596a49344f012eda2d4efad8a050cc4c19afa97c"
                "59045a99cac7827271cb41c65e590e09da3275600c2f09b8367793a9aca3db71"
                "cc30c58179ec3e87c14c01d5c1f3434f1d87", want);
    if (memcmp(okm, want, n)) { puts("FAIL A.2 OKM"); fail++; } else puts("demo2 A.2 OKM (L=82): PASS");

    /* demo3: A.3 空 salt+info */
    hkdf_extract(NULL, 0, ikm, 22, prk);
    n = hex2bin("19ef24a32c717b167f33a91d6f648bdf96596776afdb6377ac434c1c293ccb04", want);
    if (memcmp(prk, want, n)) { puts("FAIL A.3 PRK"); fail++; } else puts("demo3 A.3 PRK: PASS");
    hkdf_expand(prk, NULL, 0, 42, okm);
    n = hex2bin("8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d"
                "9d201395faa4b61a96c8", want);
    if (memcmp(okm, want, n)) { puts("FAIL A.3 OKM"); fail++; } else puts("demo3 A.3 OKM: PASS");

    /* demo4: info 域分离 + IKM 雪崩 */
    uint8_t kA[32], kB[32], ikmX[22], prkX[32], kX[32], k0[32];
    memcpy(ikmX, ikm, 22);
    ikmX[3] ^= 1;
    hkdf_extract((const uint8_t *)"salt", 4, ikm, 22, prk);
    hkdf_expand(prk, (const uint8_t *)"ctx-a", 5, 32, kA);
    hkdf_expand(prk, (const uint8_t *)"ctx-b", 5, 32, kB);
    if (!memcmp(kA, kB, 32)) { puts("FAIL 域分离"); fail++; }
    hkdf_extract((const uint8_t *)"salt", 4, ikmX, 22, prkX);
    hkdf_expand(prkX, NULL, 0, 32, kX);
    hkdf_expand(prk, NULL, 0, 32, k0);
    if (!memcmp(kX, k0, 32)) { puts("FAIL 雪崩"); fail++; }
    if (!fail) puts("demo4 info 域分离 + IKM 雪崩: PASS");

    /* demo5: L=0 边界 */
    hkdf_expand(prk, NULL, 0, 0, okm);
    puts("demo5 L=0 边界: PASS");
    if (fail) { puts("SOME FAILED"); return 1; }
    puts("ALL PASS");
    return 0;
}
