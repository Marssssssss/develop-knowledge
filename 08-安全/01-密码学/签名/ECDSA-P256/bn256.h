/* bn256.h — 256-bit(4x64 小端 limb)大数运算: 加减乘/模归约/求逆 */
#ifndef BN256_H
#define BN256_H

#include <stdint.h>

int bn_is_zero(const uint64_t a[4]);
int bn_is_one(const uint64_t a[4]);
int bn_cmp(const uint64_t a[4], const uint64_t b[4]);
void bn_sub(const uint64_t a[4], const uint64_t b[4], uint64_t r[4]);
void bn_mul(const uint64_t a[4], const uint64_t b[4], uint64_t r[8]);
void bn_mod_reduce(const uint64_t c[8], const uint64_t m[4], uint64_t r[4]);
void bn_mod_add(const uint64_t a[4], const uint64_t b[4], const uint64_t m[4], uint64_t r[4]);
void bn_mod_sub(const uint64_t a[4], const uint64_t b[4], const uint64_t m[4], uint64_t r[4]);
void bn_mod_mul(const uint64_t a[4], const uint64_t b[4], const uint64_t m[4], uint64_t r[4]);
void bn_modinv(const uint64_t a[4], const uint64_t m[4], uint64_t r[4]);
void bn_hex2limb(const char *s, uint64_t r[4]);
void bn_limb2be(const uint64_t a[4], uint8_t out[32]); /* limb -> 大端 32 字节 */
void bn_be2limb(const uint8_t in[32], uint64_t r[4]);
void bn_print(const uint64_t a[4]);

#endif
