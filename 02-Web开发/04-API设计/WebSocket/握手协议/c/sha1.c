/*
 * sha1.c — FIPS 180-4 §6.1 SHA-1
 *
 * 实现要点（与 RFC 3174 一致；本 demo 不使用 HMAC）：
 *   - 初始 H0..H4 = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0
 *   - 80 轮：前 16 轮用消息块 W[0..15]，后 64 轮用 W[i] = ROL(W[i-3] ^ W[i-8] ^
 *     W[i-14] ^ W[i-16], 1)
 *   - 四段非线性函数 f1..f4 + 常量 K1..K4
 *   - 填充：消息后追加 0x80，按大端追加 64-bit 长度，凑齐 512-bit 整数倍
 *
 * 验证用例（来自 RFC 3174 Appendix A）：
 *   sha1("abc") =
 *     a9993e36 4706816a ba3e2571 7850c26c 9cd0d89d
 */
#include "sha1.h"
#include <string.h>

#define ROL(x, n)  (((x) << (n)) | ((x) >> (32 - (n))))

/* FIPS 180-4 §4.1.1：四段非线性函数 */
static uint32_t f1(uint32_t b, uint32_t c, uint32_t d) { return (b & c) | ((~b) & d); }
static uint32_t f2(uint32_t b, uint32_t c, uint32_t d) { return b ^ c ^ d; }
static uint32_t f3(uint32_t b, uint32_t c, uint32_t d) { return (b & c) | (b & d) | (c & d); }
static uint32_t f4(uint32_t b, uint32_t c, uint32_t d) { return b ^ c ^ d; }

/* FIPS 180-4 §4.2.1：四段常量 */
static uint32_t K(int t) {
    if (t < 20) return 0x5A827999u;
    if (t < 40) return 0x6ED9EBA1u;
    if (t < 60) return 0x8F1BBCDCu;
    return 0xCA62C1D6u;
}

void sha1_init(sha1_ctx *ctx) {
    ctx->h[0] = 0x67452301u;
    ctx->h[1] = 0xEFCDAB89u;
    ctx->h[2] = 0x98BADCFEu;
    ctx->h[3] = 0x10325476u;
    ctx->h[4] = 0xC3D2E1F0u;
    ctx->bit_count = 0;
    ctx->buf_len = 0;
}

/*
 * process_block：处理一个 64 字节块
 * 步骤：
 *   1) 拆成 16 个大端 32-bit 字 W[0..15]
 *   2) 扩展到 W[16..79]（每项 = ROL(W[i-3] ^ W[i-8] ^ W[i-14] ^ W[i-16], 1)）
 *   3) a..e = H0..H4
 *   4) 80 轮：t = ROL(a,5) + f_t(b,c,d) + e + K(t) + W[t]; e=d; d=c; c=ROL(b,30); b=a; a=t
 *   5) H0..H4 += a..e
 */
static void process_block(sha1_ctx *ctx, const uint8_t block[64]) {
    uint32_t W[80];
    /* 1) 大端拆 16 个字 */
    for (int i = 0; i < 16; i++) {
        W[i] = ((uint32_t)block[i*4]     << 24) |
               ((uint32_t)block[i*4 + 1] << 16) |
               ((uint32_t)block[i*4 + 2] <<  8) |
               ((uint32_t)block[i*4 + 3]);
    }
    /* 2) 扩展 W[16..79] */
    for (int i = 16; i < 80; i++) {
        W[i] = ROL(W[i-3] ^ W[i-8] ^ W[i-14] ^ W[i-16], 1);
    }
    /* 3) 装载工作变量 */
    uint32_t a = ctx->h[0], b = ctx->h[1], c = ctx->h[2],
             d = ctx->h[3], e = ctx->h[4];
    /* 4) 80 轮主循环 */
    for (int t = 0; t < 80; t++) {
        uint32_t f;
        if      (t < 20) f = f1(b, c, d);
        else if (t < 40) f = f2(b, c, d);
        else if (t < 60) f = f3(b, c, d);
        else             f = f4(b, c, d);
        uint32_t T = ROL(a, 5) + f + e + K(t) + W[t];
        e = d; d = c; c = ROL(b, 30); b = a; a = T;
    }
    /* 5) 写回状态字 */
    ctx->h[0] += a; ctx->h[1] += b; ctx->h[2] += c;
    ctx->h[3] += d; ctx->h[4] += e;
}

void sha1_update(sha1_ctx *ctx, const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    ctx->bit_count += (uint64_t)len * 8;
    /* 情形 A：缓冲未满 → 先填缓冲 */
    if (ctx->buf_len > 0) {
        size_t need = 64 - ctx->buf_len;
        size_t take = (len < need) ? len : need;
        memcpy(ctx->buf + ctx->buf_len, p, take);
        ctx->buf_len += take;
        p += take; len -= take;
        if (ctx->buf_len == 64) {
            process_block(ctx, ctx->buf);
            ctx->buf_len = 0;
        }
    }
    /* 情形 B：剩余按 64 字节一批整处理 */
    while (len >= 64) {
        process_block(ctx, p);
        p += 64; len -= 64;
    }
    /* 情形 C：尾随不足 64 字节的部分留到 buf */
    if (len > 0) {
        memcpy(ctx->buf, p, len);
        ctx->buf_len = len;
    }
}

void sha1_final(sha1_ctx *ctx, uint8_t out[20]) {
    uint64_t bits = ctx->bit_count;
    /* 步骤 1：追加 0x80 */
    ctx->buf[ctx->buf_len++] = 0x80;
    /* 步骤 2：若剩余 < 8 字节放长度，则补一个满块再做 */
    if (ctx->buf_len > 56) {
        memset(ctx->buf + ctx->buf_len, 0, 64 - ctx->buf_len);
        process_block(ctx, ctx->buf);
        ctx->buf_len = 0;
    }
    /* 步骤 3：补 0 直到 buf_len == 56 */
    memset(ctx->buf + ctx->buf_len, 0, 56 - ctx->buf_len);
    /* 步骤 4：大端写 64-bit 位长度 */
    for (int i = 0; i < 8; i++) {
        ctx->buf[56 + i] = (uint8_t)(bits >> (56 - i * 8));
    }
    process_block(ctx, ctx->buf);
    /* 步骤 5：大端写 20 字节摘要 */
    for (int i = 0; i < 5; i++) {
        out[i*4]     = (uint8_t)(ctx->h[i] >> 24);
        out[i*4 + 1] = (uint8_t)(ctx->h[i] >> 16);
        out[i*4 + 2] = (uint8_t)(ctx->h[i] >>  8);
        out[i*4 + 3] = (uint8_t)(ctx->h[i]);
    }
}

void sha1(const void *data, size_t len, uint8_t out[20]) {
    sha1_ctx ctx;
    sha1_init(&ctx);
    sha1_update(&ctx, data, len);
    sha1_final(&ctx, out);
}