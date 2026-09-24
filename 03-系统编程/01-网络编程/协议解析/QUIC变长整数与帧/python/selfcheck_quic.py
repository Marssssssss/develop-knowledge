"""自检:QUIC 变长整数与帧层。纯计算断言。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quic_varint import (                                 # noqa: E402
    QuicError, MAX_1, MAX_2, MAX_4, MAX_8, PREFIX_TO_LEN,
    PADDING, PING, FRAME_ENCODING_ERROR, PROTOCOL_VIOLATION,
    varint_encode, varint_decode, varint_len_for, is_shortest_encoding,
    decode_ack_ranges, gap_packet_count, parse_frames,
)

N = 0


def ok(cond, msg):
    global N
    N += 1
    if not cond:
        raise AssertionError("FAIL #%d: %s" % (N, msg))


def eq(got, want, msg):
    ok(got == want, "%s -> got %r want %r" % (msg, got, want))


def raises(fn, kind, msg):
    global N
    N += 1
    try:
        fn()
    except QuicError as e:
        if e.kind != kind:
            raise AssertionError("FAIL #%d: %s -> kind %r want %r" % (N, msg, e.kind, kind))
        return
    raise AssertionError("FAIL #%d: %s -> 没有抛异常" % (N, msg))


# --------------------------------------------------------------- 长度表
eq(PREFIX_TO_LEN, {0: 1, 1: 2, 2: 4, 3: 8}, "length = 1 << prefix")
eq(MAX_1, 63, "1 字节上限 63")
eq(MAX_2, 16383, "2 字节上限 16383")
eq(MAX_4, 1073741823, "4 字节上限 2^30-1")
eq(MAX_8, 4611686018427387903, "8 字节上限 2^62-1")

# ------------------------------------------- RFC 9000 附录 A.1 的四个例子
eq(varint_decode(bytes([0x25]))[0], 37, "0x25 -> 37")
eq(varint_decode(bytes([0x7b, 0xbd]))[0], 15293, "0x7bbd -> 15293")
eq(varint_decode(bytes([0x9d, 0x7f, 0x3e, 0x7d]))[0], 494878333, "0x9d7f3e7d -> 494878333")
eq(varint_decode(bytes([0xc2, 0x19, 0x7c, 0x5e, 0xff, 0x14, 0xe8, 0x8c]))[0],
   151288809941952652, "0xc2197c5eff14e88c -> 151288809941952652")
# 「the two-byte sequence 0x4025 decodes to 37」
eq(varint_decode(bytes([0x40, 0x25]))[0], 37, "0x4025 -> 37(非最短编码)")

# ------------------------------------------------------------- 编码往返
for v in (0, 1, 37, 63, 64, 1000, 15293, 16383, 16384, 494878333,
          MAX_4, MAX_4 + 1, MAX_8):
    raw = varint_encode(v)
    eq(varint_decode(raw)[0], v, "往返 %d" % v)
    eq(len(raw), varint_len_for(v), "最短长度 %d" % v)
eq(varint_encode(37), bytes([0x25]), "37 -> 0x25")
eq(varint_encode(15293), bytes([0x7b, 0xbd]), "15293 -> 0x7bbd")
eq(varint_encode(494878333), bytes([0x9d, 0x7f, 0x3e, 0x7d]), "494878333 -> 0x9d7f3e7d")
eq(varint_encode(151288809941952652),
   bytes([0xc2, 0x19, 0x7c, 0x5e, 0xff, 0x14, 0xe8, 0x8c]), "附录例子反向编码")
eq(varint_len_for(63), 1, "63 -> 1 字节")
eq(varint_len_for(64), 2, "64 -> 2 字节")
eq(varint_len_for(16383), 2, "16383 -> 2 字节")
eq(varint_len_for(16384), 4, "16384 -> 4 字节")
eq(varint_len_for(MAX_4), 4, "2^30-1 -> 4 字节")
eq(varint_len_for(MAX_4 + 1), 8, "2^30 -> 8 字节")

# -------------------------------------------- 显式指定更长的长度(非最短)
eq(varint_encode(37, 2), bytes([0x40, 0x25]), "显式 2 字节")
eq(varint_encode(37, 4), bytes([0x80, 0x00, 0x00, 0x25]), "显式 4 字节")
eq(varint_encode(37, 8)[0], 0xC0, "显式 8 字节首位前缀 11")
raises(lambda: varint_encode(64, 1), FRAME_ENCODING_ERROR, "64 装不进 1 字节")
raises(lambda: varint_encode(MAX_8 + 1), FRAME_ENCODING_ERROR, "超出 2^62-1")
raises(lambda: varint_encode(-1), FRAME_ENCODING_ERROR, "负数")

# ------------------------------------------------------------ 解码错误
raises(lambda: varint_decode(b""), FRAME_ENCODING_ERROR, "空输入")
raises(lambda: varint_decode(bytes([0x40])), FRAME_ENCODING_ERROR, "2 字节前缀但只有 1 字节")
raises(lambda: varint_decode(bytes([0x80, 0x00])), FRAME_ENCODING_ERROR, "4 字节前缀但只有 2 字节")
raises(lambda: varint_decode(bytes([0xC0, 0x00])), FRAME_ENCODING_ERROR, "8 字节前缀但只有 2 字节")

# ------------------------------------------------- 最短编码判定(帧类型要求)
ok(is_shortest_encoding(bytes([0x25])), "0x25 是最短的")
ok(not is_shortest_encoding(bytes([0x40, 0x25])), "0x4025 不是最短(成对用例)")
ok(is_shortest_encoding(bytes([0x7b, 0xbd])), "0x7bbd 是最短的")
ok(not is_shortest_encoding(bytes([0x80, 0x00, 0x3b, 0xbd])), "4 字节写 15293 不是最短")

# --------------------------------------------------------- ACK Range 解码
r = decode_ack_ranges(10, 2, [(1, 1)])
eq(r, [(8, 10), (4, 5)], "largest=10, first=2, gap=1/len=1")
eq(gap_packet_count(1), 2, "gap=1 表示有 2 个未确认的包(7 和 6)")
r = decode_ack_ranges(10, 2, [(0, 1)])
eq(r, [(8, 10), (5, 6)], "gap=0:largest = 8-0-2 = 6,smallest = 6-1 = 5")
eq(gap_packet_count(0), 1, "gap=0 表示有 1 个未确认的包(7)")
r = decode_ack_ranges(100, 0, [])
eq(r, [(100, 100)], "只确认一个包")
# 负数 -> FRAME_ENCODING_ERROR(gap 稍微大一点就会越界)
r = decode_ack_ranges(2, 0, [(0, 0)])
eq(r, [(2, 2), (0, 0)], "largest=2, gap=0 -> 下一区间最大是 0")
raises(lambda: decode_ack_ranges(2, 0, [(1, 0)]), FRAME_ENCODING_ERROR,
       "largest = 2-1-2 = -1 -> 负数")
raises(lambda: decode_ack_ranges(0, 1, []), FRAME_ENCODING_ERROR,
       "smallest = 0-1 = -1")
# 多段
r = decode_ack_ranges(20, 1, [(1, 0), (2, 3)])
eq(r, [(19, 20), (16, 16), (9, 12)],
   "19..20; gap=1 -> 最大 17? 实为 19-1-2=16;再 gap=2 -> 16-2-2=12,smallest=12-3=9")

# ------------------------------------------------------------- 帧解析
raises(lambda: parse_frames(b""), PROTOCOL_VIOLATION, "空负载 = PROTOCOL_VIOLATION")
eq(parse_frames(bytes([PADDING])), [(PADDING, b"")], "一帧 PADDING")
eq(parse_frames(bytes([PADDING] * 5)), [(PADDING, b"")] * 5, "5 个 PADDING")
eq(parse_frames(bytes([PING, PADDING])), [(PING, b""), (PADDING, b"")], "PING + PADDING")
raises(lambda: parse_frames(bytes([0x40, 0x00])), FRAME_ENCODING_ERROR,
       "帧类型 0x00 用了 2 字节编码 -> 非最短")
raises(lambda: parse_frames(bytes([0x2a])), FRAME_ENCODING_ERROR, "未知帧类型 0x2a")
raises(lambda: parse_frames(bytes([0x01, 0x02])), FRAME_ENCODING_ERROR,
       "PING 之后跟了一个 ACK(本 demo 未建模其字段)")

print("selfcheck OK: %d assertions" % N)
