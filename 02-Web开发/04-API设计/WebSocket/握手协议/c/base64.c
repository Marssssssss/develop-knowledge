/*
 * base64.c — RFC 4648 §4 standard Base64
 *
 * 标准字母表（与 URL-safe variant 区别：无 '-_'、有 '+/'）。
 * 本 demo 用在 Sec-WebSocket-Key（客户端 nonce）与 Sec-WebSocket-Accept
 * （20 字节 SHA-1 → 28 字节 base64），都是标准 base64 而非 URL-safe。
 */
#include "base64.h"
#include <string.h>

static const char B64_ALPHABET[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

void base64_encode(const uint8_t *in, size_t in_len,
                   char *out, size_t out_cap) {
    size_t need = ((in_len + 2) / 3) * 4 + 1;
    if (out_cap < need) { if (out_cap > 0) out[0] = '\0'; return; }

    size_t i = 0, o = 0;
    while (i + 3 <= in_len) {
        uint32_t v = ((uint32_t)in[i] << 16)
                   | ((uint32_t)in[i+1] << 8)
                   |  (uint32_t)in[i+2];
        out[o++] = B64_ALPHABET[(v >> 18) & 0x3F];
        out[o++] = B64_ALPHABET[(v >> 12) & 0x3F];
        out[o++] = B64_ALPHABET[(v >>  6) & 0x3F];
        out[o++] = B64_ALPHABET[ v        & 0x3F];
        i += 3;
    }
    if (i < in_len) {
        uint32_t v = (uint32_t)in[i] << 16;
        if (i + 1 < in_len) v |= (uint32_t)in[i+1] << 8;
        out[o++] = B64_ALPHABET[(v >> 18) & 0x3F];
        out[o++] = B64_ALPHABET[(v >> 12) & 0x3F];
        out[o++] = (i + 1 < in_len) ? B64_ALPHABET[(v >> 6) & 0x3F] : '=';
        out[o++] = '=';
    }
    out[o] = '\0';
}

size_t base64_decode(const char *in, size_t in_len,
                     uint8_t *out, size_t out_cap) {
    /* 反查表（懒初始化） */
    static int8_t T[256];
    static int initialized = 0;
    if (!initialized) {
        for (int i = 0; i < 256; i++) T[i] = -1;
        for (int i = 0; i < 64; i++) T[(uint8_t)B64_ALPHABET[i]] = (int8_t)i;
        initialized = 1;
    }
    /* 去尾随 '=' */
    size_t end = in_len;
    while (end > 0 && in[end-1] == '=') end--;

    size_t o = 0, i = 0;
    while (i < end) {
        int v0 = T[(uint8_t)in[i++]]; if (v0 < 0) return (size_t)-1;
        if (i >= end) return (size_t)-1;
        int v1 = T[(uint8_t)in[i++]]; if (v1 < 0) return (size_t)-1;
        if (o >= out_cap) return (size_t)-1;
        out[o++] = (uint8_t)((v0 << 2) | (v1 >> 4));
        if (i >= end) break;
        int v2 = T[(uint8_t)in[i++]]; if (v2 < 0) return (size_t)-1;
        if (o >= out_cap) return (size_t)-1;
        out[o++] = (uint8_t)(((v1 & 0xF) << 4) | (v2 >> 2));
        if (i >= end) break;
        int v3 = T[(uint8_t)in[i++]]; if (v3 < 0) return (size_t)-1;
        if (o >= out_cap) return (size_t)-1;
        out[o++] = (uint8_t)(((v2 & 0x3) << 6) | v3);
    }
    return o;
}