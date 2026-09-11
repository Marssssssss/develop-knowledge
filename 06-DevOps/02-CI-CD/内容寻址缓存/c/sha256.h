/* sha256.h — FIPS 180-4 SHA-256(供 cache_demo.c 计算 hashFiles 键) */
#ifndef SHA256_H
#define SHA256_H

#include <stddef.h>
#include <stdint.h>

/* 计算数据的 SHA-256,输出 64 个小写十六进制字符(不含结尾 NUL) */
void sha256_hex(const uint8_t *data, size_t len, char out[65]);

#endif
