/* ECDSA(P-256) + RFC 6979 确定性签名 — 实现文件(仿射坐标点运算)。
 * 依据:
 *   - 曲线参数 secp256r1: SEC2 v2 §2.4.2 (https://www.secg.org/sec2-v2.pdf)
 *   - RFC 6979 §3.2 确定性 k 生成 + A.2.5 (P-256+SHA-256) 测试向量
 * 常量与测试向量一律运行时从 hex 字符串解析, 杜绝 limb 手抄错误。
 * 编译: cc ecdsa.c bn256.c hmacsha256.c ecdsa_test.c -o ecdsa  (需 __int128) */
#include <string.h>
#include "bn256.h"
#include "hmacsha256.h"
#include "ecdsa.h"

uint64_t P[4], A[4], B[4], Gx[4], Gy[4], N[4];

void p256_init(void) {
    bn_hex2limb("FFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF", P);
    bn_hex2limb("5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B", B);
    bn_hex2limb("6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296", Gx);
    bn_hex2limb("4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5", Gy);
    bn_hex2limb("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", N);
    memcpy(A, P, 32);
    uint64_t three[4] = {3, 0, 0, 0};
    bn_sub(A, three, A); /* a = p-3 */
}

void pt_add(const pt *p1, const pt *p2, pt *r) {
    if (p1->inf) { *r = *p2; return; }
    if (p2->inf) { *r = *p1; return; }
    uint64_t dx[4], dy[4], num[4], den[4], inv[4], lam[4], t[4];
    r->inf = 0;
    bn_mod_sub(p2->x, p1->x, P, dx);
    bn_mod_sub(p2->y, p1->y, P, dy);
    if (bn_is_zero(dx)) {
        if (bn_is_zero(dy)) { /* 同点: 倍点 lam=(3x²+a)/(2y) */
            uint64_t three[4] = {3, 0, 0, 0};
            bn_mod_mul(p1->x, p1->x, P, t);
            bn_mod_add(t, t, P, t);
            bn_mod_add(t, three, P, num);
            bn_mod_add(p1->y, p1->y, P, den);
        } else { /* x 同 y 反: 结果为无穷远点 */
            r->inf = 1;
            return;
        }
    } else {
        memcpy(num, dy, 32);
        memcpy(den, dx, 32);
    }
    bn_modinv(den, P, inv);
    bn_mod_mul(num, inv, P, lam);
    /* x3 = lam²-x1-x2; y3 = lam(x1-x3)-y1 —— 先入局部量, 防 r 与 p1/p2 别名 */
    {
        uint64_t x3[4], y3[4];
        bn_mod_mul(lam, lam, P, t);
        bn_mod_sub(t, p1->x, P, t);
        bn_mod_sub(t, p2->x, P, x3);
        bn_mod_sub(p1->x, x3, P, t);
        bn_mod_mul(lam, t, P, t);
        bn_mod_sub(t, p1->y, P, y3);
        memcpy(r->x, x3, 32);
        memcpy(r->y, y3, 32);
        r->inf = 0;
    }
}

void pt_mul(const uint64_t k[4], const pt *p, pt *r) {
    pt acc;
    acc.inf = 1;
    int start = 0;
    while (start < 4 && !k[start])
        start++;
    if (start == 4) { r->inf = 1; return; } /* k == 0 */
    for (int i = start; i < 4; i++)
        for (int b = 63; b >= 0; b--) {
            pt_add(&acc, &acc, &acc); /* 倍点 */
            if ((k[i] >> b) & 1)
                pt_add(&acc, p, &acc);
        }
    *r = acc;
}

int on_curve(const pt *p) {
    if (p->inf) return 1;
    uint64_t lhs[4], rhs[4], t[4];
    bn_mod_mul(p->y, p->y, P, lhs);              /* y² */
    bn_mod_mul(p->x, p->x, P, rhs);
    bn_mod_mul(rhs, p->x, P, rhs);               /* x³ */
    bn_mod_mul(A, p->x, P, t);
    bn_mod_add(rhs, t, P, rhs);
    bn_mod_add(rhs, B, P, rhs);                   /* x³+ax+b */
    return bn_cmp(lhs, rhs) == 0;
}

/* z mod n(单次条件减: 输入 < 2^256 < 2n) */
void mod_n(const uint64_t z[4], uint64_t r[4]) {
    if (bn_cmp(z, N) >= 0)
        bn_sub(z, N, r);
    else
        memcpy(r, z, 32);
}

/* RFC 6979 DRBG 初始化: bx = int2octets(x) || bits2octets(h1) */
void drbg_init(const uint64_t x[4], const uint8_t h1[32], uint8_t K[32],
               uint8_t V[32]) {
    uint8_t bx[64], m[97];
    uint64_t z[4];
    bn_limb2be(x, bx);
    bn_be2limb(h1, z);
    mod_n(z, z);
    bn_limb2be(z, bx + 32);
    memset(V, 0x01, 32);
    memset(K, 0x00, 32);
    memcpy(m, V, 32);
    m[32] = 0x00;
    memcpy(m + 33, bx, 64);
    hmac_sha256(K, 32, m, 97, K);
    hmac_sha256(K, 32, V, 32, V);
    memcpy(m, V, 32);
    m[32] = 0x01;
    memcpy(m + 33, bx, 64);
    hmac_sha256(K, 32, m, 97, K);
    hmac_sha256(K, 32, V, 32, V);
}

/* 产出下一个候选 k; 拒绝后调用 drbg_update 再调用本函数 */
void drbg_gen(uint8_t K[32], uint8_t V[32], uint64_t k[4]) {
    uint8_t T[32];
    hmac_sha256(K, 32, V, 32, V); /* V = HMAC(K,V); T = V(qlen=256) */
    memcpy(T, V, 32);
    bn_be2limb(T, k);
}

void drbg_update(uint8_t K[32], uint8_t V[32]) {
    uint8_t m[33];
    memcpy(m, V, 32);
    m[32] = 0x00;
    hmac_sha256(K, 32, m, 33, K);
    hmac_sha256(K, 32, V, 32, V);
}

int sign(const uint64_t x[4], const uint8_t *msg, size_t mlen,
         uint64_t r_out[4], uint64_t s_out[4]) {
    uint8_t h1[32], K[32], V[32];
    uint64_t k[4], e[4], z[4], t[4], inv[4], R[4];
    pt G, Rp;
    sha256(msg, mlen, h1);
    drbg_init(x, h1, K, V);
    bn_be2limb(h1, z);
    mod_n(z, e);
    memcpy(G.x, Gx, 32);
    memcpy(G.y, Gy, 32);
    G.inf = 0;
    for (;;) {
        drbg_gen(K, V, k);
        if (bn_is_zero(k) || bn_cmp(k, N) >= 0) {
            drbg_update(K, V);
            continue;
        }
        pt_mul(k, &G, &Rp);
        mod_n(Rp.x, R);
        if (bn_is_zero(R)) {
            drbg_update(K, V);
            continue;
        }
        /* s = k⁻¹(e + r·x) mod n */
        bn_mod_mul(R, x, N, t);
        bn_mod_add(t, e, N, t);
        bn_modinv(k, N, inv);
        bn_mod_mul(inv, t, N, t);
        if (bn_is_zero(t)) {
            drbg_update(K, V);
            continue;
        }
        memcpy(r_out, R, 32);
        memcpy(s_out, t, 32);
        return 0;
    }
}

int verify(const pt *Q, const uint8_t *msg, size_t mlen,
           const uint64_t r[4], const uint64_t s[4]) {
    uint64_t e[4], z[4], w[4], u1[4], u2[4], Rn[4];
    uint8_t h1[32];
    pt G, Rp1, Rp2, Rp;
    if (bn_is_zero(r) || bn_cmp(r, N) >= 0 || bn_is_zero(s) || bn_cmp(s, N) >= 0)
        return 0;
    if (!on_curve(Q) || Q->inf)
        return 0;
    sha256(msg, mlen, h1);
    bn_be2limb(h1, z);
    mod_n(z, e);
    bn_modinv(s, N, w);
    bn_mod_mul(e, w, N, u1);
    bn_mod_mul(r, w, N, u2);
    memcpy(G.x, Gx, 32);
    memcpy(G.y, Gy, 32);
    G.inf = 0;
    pt_mul(u1, &G, &Rp1);
    pt_mul(u2, Q, &Rp2);
    pt_add(&Rp1, &Rp2, &Rp);
    if (Rp.inf)
        return 0;
    mod_n(Rp.x, Rn);
    return bn_cmp(Rn, r) == 0;
}

int hexeq(const uint64_t a[4], const char *s) {
    uint64_t w[4];
    bn_hex2limb(s, w);
    return bn_cmp(a, w) == 0;
}
