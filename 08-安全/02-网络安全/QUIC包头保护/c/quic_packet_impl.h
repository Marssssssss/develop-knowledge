/*
 * quic_packet_impl.h —— QUIC 包格式与包号编解码（RFC 9000 §16/§17.1/§17.2 与附录 A.2·A.3）
 *
 * 作为实现头被 quic_demo.c 文本级 #include（同一翻译单元，只用 .c 编译）。
 * 只放「不涉及密钥」的部分；头保护与 AEAD 在 quic_protect_impl.h。
 */
#ifndef QUIC_PACKET_IMPL_H
#define QUIC_PACKET_IMPL_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define QUIC_VARINT_MAX ((((uint64_t)1) << 62) - 1)
#define QUIC_SAMPLE_LEN 16
#define QUIC_HP_OFFSET_FROM_PN 4        /* 采样从包号字段起点往后跳 4 字节 */

/* ---------------------------------------------------------------- varint */

static size_t quic_varint_len(uint64_t v)
{
    if (v < (1ULL << 6)) return 1;
    if (v < (1ULL << 14)) return 2;
    if (v < (1ULL << 30)) return 4;
    return 8;
}

static size_t quic_varint_encode(uint64_t v, uint8_t *out)
{
    size_t n = quic_varint_len(v), i;
    if (n == 1) { out[0] = (uint8_t)v; return 1; }
    for (i = 0; i < n; i++) {
        out[n - 1 - i] = (uint8_t)((v >> (8 * i)) & 0xff);
    }
    out[0] |= (uint8_t)((n == 8 ? 3 : n / 2) << 6);
    return n;
}

/* 返回消耗字节数；非法或截断返回 0（调用方必须检错，C 没有异常） */
static size_t quic_varint_decode(const uint8_t *in, size_t len, size_t off,
                                 uint64_t *out)
{
    size_t width, i;
    uint64_t v = 0;
    if (off >= len) return 0;
    width = (size_t)1 << (in[off] >> 6);
    if (off + width > len) return 0;
    for (i = 0; i < width; i++) {
        v = (v << 8) | in[off + i];
    }
    /* 前缀位不属于值：1 字节编码去掉高 2 位，更宽的编码去掉最高 2 位 */
    if (width == 1) {
        v &= 0x3f;
    } else if (width == 8) {
        v &= QUIC_VARINT_MAX;
    } else {
        v &= ((1ULL << (8 * width - 2)) - 1);
    }
    *out = v;
    return width;
}

/* ------------------------------------------------------- 头部布局与 pn_offset */

/*
 * 长头首字节到包号字段起点的字节数。Length 与 Token Length 都是 varint，
 * **它们自身占几个字节也参与计算**，所以不能按固定偏移切包号。
 */
static size_t quic_pn_offset_initial(size_t dcid_len, size_t scid_len,
                                     size_t token_len, size_t length_field_len)
{
    return 7 + dcid_len + scid_len + quic_varint_len(token_len) + token_len +
           length_field_len;
}

static size_t quic_pn_offset_short(size_t dcid_len)
{
    return 1 + dcid_len;
}

static unsigned quic_pn_length_from_first_byte(uint8_t first)
{
    return (unsigned)(first & 0x03) + 1;
}

/* 长头类型由首字节 bit5..4 决定：0 Initial / 1 0-RTT / 2 Handshake / 3 Retry */
static unsigned quic_long_header_type(uint8_t first)
{
    return (unsigned)((first & 0x30) >> 4);
}

/* --------------------------------------------------------------- 包号编解码 */

/*
 * RFC 9000 附录 A.2 的伪代码用**实数** log：min_bits = log(n,2) + 1。
 * n 恰为 2 的幂时 min_bits 落在整数上、不再多一位，直接照抄成
 * `bit_length + 1` 会多算一个字节（n=128 应是 1 字节而不是 2 字节）。
 */
static unsigned quic_pn_min_bits(uint64_t n)
{
    unsigned b = 0;
    uint64_t x = n;
    while (x) { b++; x >>= 1; }
    if (b == 0) return 1;
    if (n == (1ULL << (b - 1))) return b;
    return b + 1;
}

static size_t quic_pn_encode_bytes(uint64_t full_pn, int has_largest,
                                   uint64_t largest_acked)
{
    uint64_t unacked = has_largest ? (full_pn - largest_acked) : (full_pn + 1);
    size_t n = (quic_pn_min_bits(unacked) + 7) / 8;
    if (n < 1) n = 1;
    if (n > 4) n = 4;
    return n;
}

static void quic_pn_encode(uint64_t full_pn, size_t nbytes, uint8_t *out)
{
    size_t i;
    for (i = 0; i < nbytes; i++) {
        out[nbytes - 1 - i] = (uint8_t)((full_pn >> (8 * i)) & 0xff);
    }
}

/*
 * 附录 A.3 的解码窗口。**这里有 C 特有的坑**：`expected_pn - pn_hwin` 在
 * expected_pn 很小时（重放旧包、首包）会无符号下溢成巨大值，使第一个分支
 * 恒真、把包号算错。Python 的任意精度整数不会暴露这个问题，所以必须在
 * int64 里做比较。
 */
static uint64_t quic_pn_decode(uint64_t largest_pn, uint64_t truncated,
                               unsigned nbits)
{
    int64_t expected = (int64_t)largest_pn + 1;
    int64_t win = (int64_t)1 << nbits;
    int64_t hwin = win / 2;
    int64_t cand = (int64_t)((uint64_t)expected & ~(uint64_t)(win - 1)) |
                   (int64_t)truncated;
    if (cand <= expected - hwin && (uint64_t)cand < (1ULL << 62) - (uint64_t)win) {
        return (uint64_t)(cand + win);
    }
    if (cand > expected + hwin && cand >= win) {
        return (uint64_t)(cand - win);
    }
    return (uint64_t)cand;
}

/* ----------------------------------------------------------- nonce / 采样 */

/* nonce = IV ⊕ (62 位包号左填零)，逐字节异或，网络字节序 */
static void quic_aead_nonce(const uint8_t *iv, size_t iv_len, uint64_t pn,
                            uint8_t *out)
{
    size_t i;
    for (i = 0; i < iv_len; i++) {
        out[i] = iv[i];
    }
    for (i = 0; i < iv_len && i < 8; i++) {
        out[iv_len - 1 - i] ^= (uint8_t)((pn >> (8 * i)) & 0xff);
    }
}

/* 约束：pn_len + 认证标签 + 帧数据 ≥ 4 + 16，缺一字节就取不到样本 */
static size_t quic_min_frame_length(unsigned pn_len, size_t expansion)
{
    size_t need = QUIC_HP_OFFSET_FROM_PN + QUIC_SAMPLE_LEN;
    if ((size_t)pn_len + expansion >= need) return 0;
    return need - pn_len - expansion;
}

static int quic_sample_ok(size_t pkt_len, size_t pn_offset)
{
    return pn_offset + QUIC_HP_OFFSET_FROM_PN + QUIC_SAMPLE_LEN <= pkt_len;
}

#endif /* QUIC_PACKET_IMPL_H */
