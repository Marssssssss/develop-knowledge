/*
 * base64.h — RFC 4648 §4 standard Base64
 */
#ifndef WS_BASE64_H
#define WS_BASE64_H

#include <stddef.h>
#include <stdint.h>

/*
 * base64_encode：标准 base64（RFC 4648 §4），无换行；
 * out_cap 必须 ≥ ((in_len + 2) / 3) * 4 + 1。
 * 失败（容量不足）时写入 '\0' 终止符。
 */
void base64_encode(const uint8_t *in, size_t in_len,
                   char *out, size_t out_cap);

/*
 * base64_decode：解任意长度；返回写入 out 的字节数；
 * 输入含非法字符或填充错误时返回 (size_t)-1。
 * out_cap 必须 ≥ in_len * 3 / 4。
 */
size_t base64_decode(const char *in, size_t in_len,
                     uint8_t *out, size_t out_cap);

#endif