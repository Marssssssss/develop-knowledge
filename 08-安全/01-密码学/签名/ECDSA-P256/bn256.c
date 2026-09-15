/* bn256.c — 256-bit(4x64 小端 limb)大数运算: 加减乘/模归约(移位-比较-相减)/二元扩展欧几里得求逆 */
#include <stdio.h>
#include <string.h>
#include "bn256.h"

typedef unsigned __int128 u128;

int bn_is_zero(const uint64_t a[4]) { return !(a[0] | a[1] | a[2] | a[3]); }

int bn_is_one(const uint64_t a[4]) { return a[0] == 1 && !(a[1] | a[2] | a[3]); }

int bn_cmp(const uint64_t a[4], const uint64_t b[4]) {
    for (int i = 3; i >= 0; i--) {
        if (a[i] < b[i]) return -1;
        if (a[i] > b[i]) return 1;
    }
    return 0;
}

/* 带借位减(a>=b 前提), 返回借位恒 0 */
void bn_sub(const uint64_t a[4], const uint64_t b[4], uint64_t r[4]) {
    uint64_t borrow = 0, t;
    for (int i = 0; i < 4; i++) {
        t = a[i] - borrow;
        borrow = (a[i] < borrow) + (t < b[i]);
        r[i] = t - b[i];
    }
}

/* 学校簿乘法: 4x4 limb -> 8 limb, u128 累加防溢出 */
void bn_mul(const uint64_t a[4], const uint64_t b[4], uint64_t r[8]) {
    memset(r, 0, 64);
    for (int i = 0; i < 4; i++) {
        u128 carry = 0;
        for (int j = 0; j < 4; j++) {
            u128 cur = (u128)a[i] * b[j] + carry + r[i + j];
            r[i + j] = (uint64_t)cur;
            carry = cur >> 64;
        }
        r[i + 4] = (uint64_t)carry; /* 该位置此前必为 0 */
    }
}

static int cmp8(const uint64_t a[8], const uint64_t b[8]) {
    for (int i = 7; i >= 0; i--) {
        if (a[i] < b[i]) return -1;
        if (a[i] > b[i]) return 1;
    }
    return 0;
}

static void sub8(uint64_t a[8], const uint64_t b[8]) {
    uint64_t borrow = 0, t;
    for (int i = 0; i < 8; i++) {
        t = a[i] - borrow;
        borrow = (a[i] < borrow) + (t < b[i]);
        a[i] = t - b[i];
    }
}

/* 512-bit c mod m(256-bit): 对 s=255..0 依次条件减去 m<<s —— 慢但显然正确 */
void bn_mod_reduce(const uint64_t c[8], const uint64_t m[4], uint64_t r[4]) {
    uint64_t t[8], sm[8];
    memcpy(t, c, 64);
    for (int s = 255; s >= 0; s--) {
        int w = s >> 6, b = s & 63;
        memset(sm, 0, 64);
        if (b == 0) {
            for (int i = 0; i < 4; i++)
                sm[i + w] = m[i];
        } else {
            for (int i = 0; i < 4; i++) {
                sm[i + w] |= m[i] << b;
                sm[i + w + 1] |= m[i] >> (64 - b);
            }
        }
        if (cmp8(t, sm) >= 0)
            sub8(t, sm);
    }
    memcpy(r, t, 32);
}

void bn_mod_add(const uint64_t a[4], const uint64_t b[4], const uint64_t m[4],
                uint64_t r[4]) {
    u128 s = 0;
    uint64_t carry = 0;
    for (int i = 0; i < 4; i++) {
        s += (u128)a[i] + b[i];
        r[i] = (uint64_t)s;
        s >>= 64;
    }
    carry = (uint64_t)s;
    if (carry || bn_cmp(r, m) >= 0)
        bn_sub(r, m, r); /* a,b < m => 和 < 2m, 减一次 m 即可 */
}

void bn_mod_sub(const uint64_t a[4], const uint64_t b[4], const uint64_t m[4],
                uint64_t r[4]) {
    if (bn_cmp(a, b) >= 0) {
        bn_sub(a, b, r);
    } else {
        uint64_t t[4];
        bn_sub(m, b, t); /* m-b */
        bn_mod_add(a, t, m, r); /* a + (m-b) < m (一步归约足够) */
    }
}

void bn_mod_mul(const uint64_t a[4], const uint64_t b[4], const uint64_t m[4],
                uint64_t r[4]) {
    uint64_t t[8];
    bn_mul(a, b, t);
    bn_mod_reduce(t, m, r);
}

/* 跨 limb 右移 1 位; carry 是溢出的第 257 位(放在最高位前) */
static void shr1(uint64_t a[4], uint64_t carry) {
    uint64_t prev = carry; /* 进位占 bit 256, 右移后落在 limb3 的最高位 */
    for (int i = 3; i >= 0; i--) {
        uint64_t cur = a[i];
        a[i] = (cur >> 1) | (prev << 63);
        prev = cur;
    }
}

/* 二元扩展欧几里得: a^-1 mod m(m 为奇素数, gcd(a,m)=1) */
void bn_modinv(const uint64_t a[4], const uint64_t m[4], uint64_t r[4]) {
    uint64_t u[4], v[4], x1[4], x2[4], t[4], carry;
    memcpy(u, a, 32);
    memcpy(v, m, 32);
    x1[0] = 1;
    memset(x1 + 1, 0, 24);
    memset(x2, 0, 32);
    while (!bn_is_one(u) && !bn_is_one(v)) {
        while (!(u[0] & 1)) { /* u 偶: u/=2; x1 = (x1 偶? x1 : x1+m)/2 */
            shr1(u, 0);
            if (x1[0] & 1) {
                u128 s = 0;
                for (int i = 0; i < 4; i++) {
                    s += (u128)x1[i] + m[i];
                    t[i] = (uint64_t)s;
                    s >>= 64;
                }
                carry = (uint64_t)s;
            } else {
                memcpy(t, x1, 32);
                carry = 0;
            }
            memcpy(x1, t, 32);
            shr1(x1, carry);
        }
        while (!(v[0] & 1)) {
            shr1(v, 0);
            if (x2[0] & 1) {
                u128 s = 0;
                for (int i = 0; i < 4; i++) {
                    s += (u128)x2[i] + m[i];
                    t[i] = (uint64_t)s;
                    s >>= 64;
                }
                carry = (uint64_t)s;
            } else {
                memcpy(t, x2, 32);
                carry = 0;
            }
            memcpy(x2, t, 32);
            shr1(x2, carry);
        }
        if (bn_cmp(u, v) >= 0) {
            bn_sub(u, v, u);
            bn_mod_sub(x1, x2, m, x1);
        } else {
            bn_sub(v, u, v);
            bn_mod_sub(x2, x1, m, x2);
        }
    }
    memcpy(r, bn_is_one(u) ? x1 : x2, 32);
}

void bn_hex2limb(const char *s, uint64_t r[4]) {
    uint8_t b[32] = {0};
    size_t n = strlen(s) / 2;
    for (size_t i = 0; i < n; i++)
        sscanf(s + 2 * i, "%2hhx", b + i);
    bn_be2limb(b, r);
}

void bn_limb2be(const uint64_t a[4], uint8_t out[32]) {
    for (int i = 0; i < 4; i++)
        for (int j = 0; j < 8; j++)
            out[31 - 8 * i - j] = (uint8_t)(a[i] >> (8 * j));
}

void bn_be2limb(const uint8_t in[32], uint64_t r[4]) {
    for (int i = 0; i < 4; i++) {
        uint64_t v = 0;
        for (int j = 0; j < 8; j++)
            v = (v << 8) | in[31 - 8 * i - j];
        r[i] = v;
    }
}

void bn_print(const uint64_t a[4]) {
    for (int i = 3; i >= 0; i--)
        printf("%016llx", (unsigned long long)a[i]);
    printf("\n");
}
