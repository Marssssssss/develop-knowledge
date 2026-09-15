/* ecdsa.h — ECDSA(P-256) + RFC 6979 接口声明 */
#ifndef ECDSA_H
#define ECDSA_H

#include <stdint.h>
#include <stddef.h>

extern uint64_t P[4], A[4], B[4], Gx[4], Gy[4], N[4];

typedef struct {
    uint64_t x[4], y[4];
    int inf; /* 1 = 无穷远点 */
} pt;

void p256_init(void);                     /* 运行时从 hex 解析全部曲线参数 */
void pt_add(const pt *p1, const pt *p2, pt *r);
void pt_mul(const uint64_t k[4], const pt *p, pt *r);
int on_curve(const pt *p);
void mod_n(const uint64_t z[4], uint64_t r[4]);
void drbg_init(const uint64_t x[4], const uint8_t h1[32], uint8_t K[32], uint8_t V[32]);
void drbg_gen(uint8_t K[32], uint8_t V[32], uint64_t k[4]);
void drbg_update(uint8_t K[32], uint8_t V[32]);
int sign(const uint64_t x[4], const uint8_t *msg, size_t mlen,
         uint64_t r_out[4], uint64_t s_out[4]);
int verify(const pt *Q, const uint8_t *msg, size_t mlen,
           const uint64_t r[4], const uint64_t s[4]);
int hexeq(const uint64_t a[4], const char *s);

#endif
