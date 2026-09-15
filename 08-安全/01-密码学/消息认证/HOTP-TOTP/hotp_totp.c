/* HOTP(RFC 4226)/TOTP(RFC 6238): 基于 HMAC 的一次性口令 — C 实现(自含 SHA-1)。
 * 依据: RFC 4226 §5.3(动态截断) + 附录 D(count 0-9 向量);
 *       RFC 6238 §4(T=floor((unix-T0)/X)) + 附录 B(SHA1 向量)。
 * 编译: cc hotp_totp.c -o hotp_totp && ./hotp_totp */
#include <stdio.h>
#include <string.h>
#include <stdint.h>

/* ---------- SHA-1(FIPS 180-1) ---------- */
static void sha1(const uint8_t *msg, size_t len, uint8_t out[20]) {
    uint32_t h[5] = {0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0};
    size_t off = 0;
    while (off + 64 <= len) {
        const uint8_t *blk = msg + off;
        uint32_t w[80];
        for (int i = 0; i < 16; i++)
            w[i] = ((uint32_t)blk[4 * i] << 24) | ((uint32_t)blk[4 * i + 1] << 16) |
                   ((uint32_t)blk[4 * i + 2] << 8) | (uint32_t)blk[4 * i + 3];
        for (int i = 16; i < 80; i++) {
            uint32_t v = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
            w[i] = (v << 1) | (v >> 31);
        }
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4];
        for (int i = 0; i < 80; i++) {
            uint32_t f, k;
            if (i < 20) {
                f = (b & c) | (~b & d);
                k = 0x5A827999;
            } else if (i < 40) {
                f = b ^ c ^ d;
                k = 0x6ED9EBA1;
            } else if (i < 60) {
                f = (b & c) | (b & d) | (c & d);
                k = 0x8F1BBCDC;
            } else {
                f = b ^ c ^ d;
                k = 0xCA62C1D6;
            }
            uint32_t t = ((a << 5) | (a >> 27)) + f + e + k + w[i];
            e = d; d = c; c = (b << 30) | (b >> 2); b = a; a = t;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e;
        off += 64;
    }
    uint8_t blk[64];
    size_t rem = len - off;
    memset(blk, 0, 64);
    memcpy(blk, msg + off, rem);
    blk[rem] = 0x80;
    uint64_t bits = (uint64_t)len * 8;
    if (rem >= 56) {
        for (int i = 0; i < 8; i++)
            blk[63 - i] = (uint8_t)(bits >> (8 * i));
        /* 先压缩第一块 */
        {
            uint32_t w[80];
            for (int i = 0; i < 16; i++)
                w[i] = ((uint32_t)blk[4 * i] << 24) | ((uint32_t)blk[4 * i + 1] << 16) |
                       ((uint32_t)blk[4 * i + 2] << 8) | (uint32_t)blk[4 * i + 3];
            for (int i = 16; i < 80; i++) {
                uint32_t v = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
                w[i] = (v << 1) | (v >> 31);
            }
            uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4];
            for (int i = 0; i < 80; i++) {
                uint32_t f, k;
                if (i < 20) { f = (b & c) | (~b & d); k = 0x5A827999; }
                else if (i < 40) { f = b ^ c ^ d; k = 0x6ED9EBA1; }
                else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC; }
                else { f = b ^ c ^ d; k = 0xCA62C1D6; }
                uint32_t t = ((a << 5) | (a >> 27)) + f + e + k + w[i];
                e = d; d = c; c = (b << 30) | (b >> 2); b = a; a = t;
            }
            h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e;
        }
        memset(blk, 0, 64);
        for (int i = 0; i < 8; i++)
            blk[63 - i] = 0;
    }
    for (int i = 0; i < 8; i++)
        blk[63 - i] = (uint8_t)(bits >> (8 * i));
    {
        uint32_t w[80];
        for (int i = 0; i < 16; i++)
            w[i] = ((uint32_t)blk[4 * i] << 24) | ((uint32_t)blk[4 * i + 1] << 16) |
                   ((uint32_t)blk[4 * i + 2] << 8) | (uint32_t)blk[4 * i + 3];
        for (int i = 16; i < 80; i++) {
            uint32_t v = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
            w[i] = (v << 1) | (v >> 31);
        }
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4];
        for (int i = 0; i < 80; i++) {
            uint32_t f, k;
            if (i < 20) { f = (b & c) | (~b & d); k = 0x5A827999; }
            else if (i < 40) { f = b ^ c ^ d; k = 0x6ED9EBA1; }
            else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC; }
            else { f = b ^ c ^ d; k = 0xCA62C1D6; }
            uint32_t t = ((a << 5) | (a >> 27)) + f + e + k + w[i];
            e = d; d = c; c = (b << 30) | (b >> 2); b = a; a = t;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e;
    }
    for (int i = 0; i < 5; i++) {
        out[4 * i] = (uint8_t)(h[i] >> 24);
        out[4 * i + 1] = (uint8_t)(h[i] >> 16);
        out[4 * i + 2] = (uint8_t)(h[i] >> 8);
        out[4 * i + 3] = (uint8_t)h[i];
    }
}

/* ---------- HMAC-SHA-1(RFC 2104, B=64, L=20) ---------- */
static void hmac_sha1(const uint8_t *key, size_t klen, const uint8_t *msg,
                      size_t mlen, uint8_t out[20]) {
    uint8_t k[64], ipad[64], opad[64], inner[20], m[64 + 64];
    memset(k, 0, 64);
    if (klen > 64)
        sha1(key, klen, k);
    else
        memcpy(k, key, klen);
    for (int i = 0; i < 64; i++) {
        ipad[i] = k[i] ^ 0x36;
        opad[i] = k[i] ^ 0x5c;
    }
    memcpy(m, ipad, 64);
    memcpy(m + 64, msg, mlen);
    sha1(m, 64 + mlen, inner);
    memcpy(m, opad, 64);
    memcpy(m + 64, inner, 20);
    sha1(m, 84, out);
}

/* ---------- HOTP(RFC 4226 §5.3) ---------- */
static long hotp(const uint8_t *key, size_t klen, uint64_t counter, int digits) {
    uint8_t c[8], hs[20];
    for (int i = 0; i < 8; i++)
        c[7 - i] = (uint8_t)(counter >> (8 * i)); /* 大端计数器 */
    hmac_sha1(key, klen, c, 8, hs);
    int off = hs[19] & 0xF; /* 动态截断 */
    uint32_t code = ((uint32_t)(hs[off] & 0x7F) << 24) | ((uint32_t)hs[off + 1] << 16) |
                    ((uint32_t)hs[off + 2] << 8) | (uint32_t)hs[off + 3];
    uint32_t mod = 1;
    for (int i = 0; i < digits; i++)
        mod *= 10;
    return (long)(code % mod);
}

static long totp(const uint8_t *key, size_t klen, int64_t unix_time, int digits) {
    return hotp(key, klen, (uint64_t)(unix_time / 30), digits);
}

/* RFC 6238 §5.2: ±1 步 resync 窗口内匹配即通过 */
static int verify_wide(const uint8_t *key, size_t klen, long code, int64_t unix_time,
                       int digits) {
    int64_t t = unix_time / 30;
    for (int64_t dt = -1; dt <= 1; dt++)
        if (hotp(key, klen, (uint64_t)(t + dt), digits) == code)
            return 1;
    return 0;
}

int main(void) {
    static const char *secret = "12345678901234567890";
    size_t klen = 20;
    int fail = 0;
    /* demo1: RFC 4226 附录 D */
    const int want[10] = {755224, 287082, 359152, 969429, 338314,
                          254676, 287922, 162583, 399871, 520489};
    for (int c = 0; c < 10; c++) {
        if (hotp((const uint8_t *)secret, klen, c, 6) != want[c]) {
            printf("FAIL demo1 HOTP(%d)\n", c);
            fail++;
        }
    }
    if (!fail)
        puts("demo1 RFC 4226 附录 D (count 0-9): PASS");
    /* demo2: RFC 6238 附录 B(SHA1 8 位) */
    struct tv { int64_t t; int v; } vecs[] = {
        {59, 94287082}, {1111111109, 7081804}, {1111111111, 14050471},
        {1234567890, 89005924}, {2000000000, 69279037}, {20000000000LL, 65353130},
    };
    for (size_t i = 0; i < sizeof(vecs) / sizeof(vecs[0]); i++) {
        if (totp((const uint8_t *)secret, klen, vecs[i].t, 8) != vecs[i].v) {
            printf("FAIL demo2 TOTP @%lld\n", (long long)vecs[i].t);
            fail++;
        }
    }
    if (!fail)
        puts("demo2 RFC 6238 附录 B (SHA1): PASS");
    /* demo3: §5.4 截断细节 */
    {
        uint8_t hs[20] = {0x1f, 0x86, 0x98, 0x69, 0x0e, 0x02, 0xca, 0x16, 0x61, 0x85,
                          0x50, 0xef, 0x7f, 0x19, 0xda, 0x8e, 0x94, 0x5b, 0x55, 0x5a};
        int off = hs[19] & 0xF;
        uint32_t dbc = ((uint32_t)(hs[off] & 0x7F) << 24) | ((uint32_t)hs[off + 1] << 16) |
                       ((uint32_t)hs[off + 2] << 8) | (uint32_t)hs[off + 3];
        if (off != 0xA || dbc != 0x50EF7F19 || dbc % 1000000 != 872921) {
            puts("FAIL demo3 截断细节");
            fail++;
        } else {
            puts("demo3 §5.4 动态截断细节(offset=0xa -> 872921): PASS");
        }
    }
    /* demo4: 时间步边界 + resync 窗口(±1 步)
     * T(59)=1, T(60)=2 -> 跨步即变; 快 1 步(在 89s 验 59s 的码)应通过, 快 2 步拒绝 */
    {
        long c59 = totp((const uint8_t *)secret, klen, 59, 8);
        long c60 = totp((const uint8_t *)secret, klen, 60, 8);
        if (c59 == c60 ||
            !verify_wide((const uint8_t *)secret, klen, c59, 89, 8) ||
            verify_wide((const uint8_t *)secret, klen, c59, 119, 8)) {
            puts("FAIL demo4 步进/窗口");
            fail++;
        } else {
            puts("demo4 时间步边界 + ±1 步 resync 窗口: PASS");
        }
    }
    /* demo5: 密钥雪崩 */
    {
        uint8_t key2[20];
        memcpy(key2, secret, 20);
        key2[7] ^= 1;
        int same = 0;
        for (int c = 0; c < 10; c++)
            if (hotp(key2, 20, c, 6) == want[c])
                same++;
        if (same) {
            puts("FAIL demo5 密钥雪崩");
            fail++;
        } else {
            puts("demo5 密钥雪崩(变 1 bit 输出全变): PASS");
        }
    }
    if (fail) {
        puts("SOME FAILED");
        return 1;
    }
    puts("ALL PASS");
    return 0;
}
