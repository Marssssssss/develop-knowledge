/*
 * ech_buf_impl.h —— 序列化缓冲与带边界检查的读取器
 *
 * ECH 全部报文都是「长度前缀 + 变长向量」的嵌套结构（RFC 9849 §4-§5 沿用 RFC 8446 的
 * 表示法），所以每个实现都需要同一对工具：
 *   ech_buf     只向前追加，长度前缀用「先占位、后回填」避免序列化两遍；
 *   ech_reader  顺序读取，任何越界都返回 -1 —— 这一层解析的是 DNS 记录与对端
 *               ClientHello，越界读就是可得的内存安全问题。
 *
 * 作为实现头被 ech_hpke_impl.h / ech_wire_impl.h / ech_demo.c 文本级包含。
 */
#ifndef ECH_BUF_IMPL_H
#define ECH_BUF_IMPL_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

typedef struct {
    uint8_t *buf;
    size_t cap;
    size_t len;
} ech_buf;

static size_t buf_mark(const ech_buf *b);
static int buf_put(ech_buf *b, const void *data, size_t n);
static int buf_u8(ech_buf *b, uint8_t v);
static int buf_u16(ech_buf *b, uint16_t v);
static int buf_fill_vec16(ech_buf *b, size_t at);

static void buf_init(ech_buf *b, uint8_t *buf, size_t cap)
{
    b->buf = buf;
    b->cap = cap;
    b->len = 0;
}

static size_t buf_mark(const ech_buf *b)
{
    return b->len;
}

static int buf_put(ech_buf *b, const void *data, size_t n)
{
    if (b->len + n > b->cap) {
        return -1;
    }
    if (n > 0 && data != NULL) {
        memcpy(b->buf + b->len, data, n);
    }
    b->len += n;
    return 0;
}

static int buf_u8(ech_buf *b, uint8_t v)
{
    return buf_put(b, &v, 1);
}

static int buf_u16(ech_buf *b, uint16_t v)
{
    uint8_t t[2];
    t[0] = (uint8_t)(v >> 8);
    t[1] = (uint8_t)(v & 0xff);
    return buf_put(b, t, 2);
}

/* 占位一个 2 字节长度前缀，序列化完内容再回填 —— 免去「先算长度、再写一遍」 */
static size_t buf_begin_vec16(ech_buf *b)
{
    size_t at = b->len;
    if (buf_u16(b, 0) != 0) {
        return (size_t)-1;
    }
    return at;
}

static int buf_fill_vec16(ech_buf *b, size_t at)
{
    size_t n;
    if (at == (size_t)-1 || b->len < at + 2) {
        return -1;
    }
    n = b->len - at - 2;
    if (n > 0xffffu) {
        return -1;
    }
    b->buf[at] = (uint8_t)(n >> 8);
    b->buf[at + 1] = (uint8_t)(n & 0xff);
    return 0;
}

/* ---------------------------------------------------------------- 读取器 */

typedef struct {
    const uint8_t *data;
    size_t len;
    size_t pos;
} ech_reader;

static void rd_init(ech_reader *r, const uint8_t *data, size_t len);
static int rd_take(ech_reader *r, size_t n, const uint8_t **out);
static int rd_u8(ech_reader *r, uint8_t *out);
static int rd_u16(ech_reader *r, uint16_t *out);
static int rd_vec8(ech_reader *r, const uint8_t **out, size_t *out_len);
static int rd_vec16(ech_reader *r, const uint8_t **out, size_t *out_len);
static int rd_end(const ech_reader *r);

static void rd_init(ech_reader *r, const uint8_t *data, size_t len)
{
    r->data = data;
    r->len = len;
    r->pos = 0;
}

static int rd_take(ech_reader *r, size_t n, const uint8_t **out)
{
    if (r->pos + n > r->len) {
        return -1;
    }
    if (out != NULL) {
        *out = r->data + r->pos;
    }
    r->pos += n;
    return 0;
}

static int rd_u8(ech_reader *r, uint8_t *out)
{
    const uint8_t *p = NULL;
    if (rd_take(r, 1, &p) != 0) {
        return -1;
    }
    *out = p[0];
    return 0;
}

static int rd_u16(ech_reader *r, uint16_t *out)
{
    const uint8_t *p = NULL;
    if (rd_take(r, 2, &p) != 0) {
        return -1;
    }
    *out = (uint16_t)(((uint16_t)p[0] << 8) | (uint16_t)p[1]);
    return 0;
}

static int rd_vec8(ech_reader *r, const uint8_t **out, size_t *out_len)
{
    uint8_t n = 0;
    if (rd_u8(r, &n) != 0) {
        return -1;
    }
    if (rd_take(r, (size_t)n, out) != 0) {
        return -1;
    }
    *out_len = (size_t)n;
    return 0;
}

static int rd_vec16(ech_reader *r, const uint8_t **out, size_t *out_len)
{
    uint16_t n = 0;
    if (rd_u16(r, &n) != 0) {
        return -1;
    }
    if (rd_take(r, (size_t)n, out) != 0) {
        return -1;
    }
    *out_len = (size_t)n;
    return 0;
}

static int rd_end(const ech_reader *r)
{
    return r->pos == r->len ? 0 : -1;
}

/* 把 [start, r->pos) 这一段作为「已经解析过的 ClientHello」切出来 */
static size_t rd_consumed(const ech_reader *r)
{
    return r->pos;
}

#endif /* ECH_BUF_IMPL_H */
