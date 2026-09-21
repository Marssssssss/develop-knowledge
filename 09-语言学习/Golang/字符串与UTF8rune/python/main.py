"""Go 字符串与 UTF-8 演示：len 是字节数，range 才解码 rune。"""

from utf8model import (
    RUNE_ERROR, decode_rune, encode_rune, rune_len, rune_count_in_string,
    rune_start, valid, FIRST, ACCEPT_RANGES,
)


def show(raw):
    r, size = decode_rune(raw)
    name = "U+%04X" % r if r != RUNE_ERROR else "RuneError"
    print("  %-24s -> %-12s size=%d" % (
        " ".join("%02X" % b for b in raw), name, size))


def main():
    print("== 1. len(s) 是字节数，不是字符数 ==")
    s = "世界"
    raw = s.encode("utf-8")
    print("  s = %r" % s)
    print("  len(s) = %d（字节）" % len(raw))
    print("  rune 数 = %d" % rune_count_in_string(raw))
    print("  逐字节：%s" % " ".join("%02X" % b for b in raw))

    print("\n== 2. 三类靠 acceptRanges 拦住的非法序列 ==")
    print("  acceptRanges[1] = %s（E0 之后必须 >= A0，挡 overlong）" % (ACCEPT_RANGES[1],))
    print("  acceptRanges[2] = %s（ED 之后必须 <= 9F，挡代理区）" % (ACCEPT_RANGES[2],))
    print("  acceptRanges[4] = %s（F4 之后必须 <= 8F，挡超上限）" % (ACCEPT_RANGES[4],))
    show([0xE0, 0x80, 0x80])        # overlong
    show([0xED, 0xA0, 0x80])        # U+D800 代理区
    show([0xF4, 0x90, 0x80, 0x80])  # U+110000 超上限
    show([0xED, 0x9F, 0xBF])        # U+D7FF 边界内侧，合法
    show([0xF4, 0x8F, 0xBF, 0xBF])  # U+10FFFF 边界内侧，合法

    print("\n== 3. 非法输入：Go 返回 (RuneError, 1)，不是抛异常 ==")
    for raw in ([0x80], [0xFF], [0xC2], [0xE4, 0xB8]):
        show(raw)
    print("  空输入才是 size=0：", decode_rune([]))

    print("\n== 4. RuneLen 的代理区陷阱 ==")
    for cp in (0x7F, 0x80, 0x7FF, 0x800, 0xD7FF, 0xD800, 0xDFFF, 0xE000, 0xFFFF, 0x10000):
        print("  U+%04X -> RuneLen=%d" % (cp, rune_len(cp)))

    print("\n== 5. 越界 rune 编码成 RuneError ==")
    for cp in (-1, 0xD800, 0x110000):
        print("  %-10s -> %s" % (cp, " ".join("%02X" % b for b in encode_rune(cp))))

    print("\n== 6. RuneStart：续字节高两位恒为 10 ==")
    print("  0x41 -> %-5s ; 0x80 -> %-5s ; 0xBF -> %-5s ; 0xC2 -> %s"
          % (rune_start(0x41), rune_start(0x80), rune_start(0xBF), rune_start(0xC2)))
    print("  Valid(\\xff) = %s ; Valid(中文) = %s"
          % (valid(b"\xff"), valid("世界".encode("utf-8"))))


if __name__ == "__main__":
    main()
