"""演示入口:QUIC 变长整数与帧层。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quic_varint import (                                 # noqa: E402
    QuicError, MAX_1, MAX_2, MAX_4, MAX_8,
    PADDING, PING, varint_encode, varint_decode,
    varint_len_for, is_shortest_encoding, decode_ack_ranges,
    gap_packet_count, parse_frames,
)


def hexs(b):
    return "0x" + b.hex()


def main():
    print("== ① 长度表:length = 1 << prefix ==")
    print("  %-8s %-8s %-12s %s" % ("2MSB", "长度", "可用位", "取值范围"))
    for p, n, bits, hi in ((0, 1, 6, MAX_1), (1, 2, 14, MAX_2),
                           (2, 4, 30, MAX_4), (3, 8, 62, MAX_8)):
        print("  %-8s %-8d %-12d 0-%d" % (format(p, "02b"), n, bits, hi))

    print("\n== ② RFC 9000 附录 A.1 的四个例子 ==")
    for raw in (b"\x25", b"\x7b\xbd", b"\x9d\x7f\x3e\x7d",
                b"\xc2\x19\x7c\x5e\xff\x14\xe8\x8c", b"\x40\x25"):
        v, used = varint_decode(raw)
        tag = "" if is_shortest_encoding(raw) else "  <- 非最短编码(允许,但帧类型不行)"
        print("  %-20s -> %-22d (占 %d 字节)%s" % (hexs(raw), v, used, tag))

    print("\n== ③ 最短编码长度的分界 ==")
    print("  %-16s %-8s %s" % ("值", "字节", "编码"))
    for v in (0, 63, 64, 16383, 16384, MAX_4, MAX_4 + 1, MAX_8):
        raw = varint_encode(v)
        print("  %-16d %-8d %s" % (v, len(raw), hexs(raw)))

    print("\n== ④ 同一个值的四种长度(除帧类型外都合法) ==")
    for n in (1, 2, 4, 8):
        try:
            print("  长度 %d -> %s" % (n, hexs(varint_encode(37, n))))
        except QuicError as e:
            print("  长度 %d -> %s" % (n, e))

    print("\n== ⑤ 帧类型必须最短 ==")
    for raw in (b"\x25", b"\x40\x25"):
        try:
            t, used = varint_decode(raw)
            print("  %-12s 解出类型 0x%02x 最短=%s"
                  % (hexs(raw), t, is_shortest_encoding(raw)))
        except QuicError as e:
            print("  %-12s %s" % (hexs(raw), e))

    print("\n== ⑥ ACK Range:largest = previous_smallest - gap - 2 ==")
    for largest, first, ranges in ((10, 2, [(1, 1)]), (10, 2, [(0, 1)]),
                                   (100, 0, []), (20, 1, [(1, 0), (2, 3)])):
        try:
            rs = decode_ack_ranges(largest, first, ranges)
            desc = "  ".join("[%d,%d]" % (lo, hi) for lo, hi in rs)
            print("  largest=%-5d first=%-3d ranges=%-14s -> %s"
                  % (largest, first, str(ranges), desc))
        except QuicError as e:
            print("  largest=%-5d first=%-3d ranges=%-14s -> %s"
                  % (largest, first, str(ranges), e))
    print("  gap 里的包数 = Gap 字段值 + 1:gap=0 -> %d 个,gap=1 -> %d 个"
          % (gap_packet_count(0), gap_packet_count(1)))

    print("\n== ⑦ 帧解析 ==")
    for raw, note in ((b"\x00", "1 个 PADDING"),
                      (b"\x00\x00\x00\x00\x00", "5 个 PADDING"),
                      (b"\x01\x00", "PING + PADDING"),
                      (b"", "空负载"),
                      (b"\x40\x00", "PADDING 用了 2 字节编码"),
                      (b"\x2a", "未知类型 0x2a")):
        try:
            frames = parse_frames(raw)
            print("  %-14s %-24s -> %s"
                  % (hexs(raw) if raw else "(空)", note, frames))
        except QuicError as e:
            print("  %-14s %-24s -> %s" % (hexs(raw) if raw else "(空)", note, e))

    print("\n== ⑧ 一句话 ==")
    print("  QUIC 把「长度」塞进前两位,换来 1/2/4/8 字节四档弹性;")
    print("  代价是同一个值有多种合法编码,所以帧类型专门要求最短编码。")


if __name__ == "__main__":
    main()
