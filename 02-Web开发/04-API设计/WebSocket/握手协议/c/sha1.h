/*
 * sha1.h — RFC 3174 / FIPS 180-3 SHA-1
 *
 * 极简增量式 SHA-1 实现（参考 NIST FIPS 180-4 §6.1）：
 *   1) sha1_init   初始化状态字
 *   2) sha1_update 接受任意长度字节流
 *   3) sha1_final  输出 20 字节摘要
 *
 * 本 demo 仅在 handshake.c 中使用一次（计算 Sec-WebSocket-Accept），
 * 因此不追求性能、不支持 HMAC；保持代码逐行可读、便于交叉验证。
 */
#ifndef WS_SHA1_H
#define WS_SHA1_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t h[5];           /* H0..H4: a, b, c, d, e */
    uint64_t bit_count;      /* 已处理的总位数 */
    uint8_t  buf[64];        /* 未满 64 字节的缓冲 */
    size_t   buf_len;        /* buf 中有效字节数 [0, 64) */
} sha1_ctx;

void sha1_init  (sha1_ctx *ctx);
void sha1_update(sha1_ctx *ctx, const void *data, size_t len);
void sha1_final (sha1_ctx *ctx, uint8_t out[20]);

/* 一次性接口：等价于 init + update + final */
void sha1(const void *data, size_t len, uint8_t out[20]);

#endif