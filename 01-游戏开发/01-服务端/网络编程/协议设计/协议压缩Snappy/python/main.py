# -*- coding: utf-8 -*-
"""Snappy 块格式:完整解码器 + 贪心编码器(纯标准库)。

依据 google/snappy 官方 format_description.txt:
  preamble = 小端 varint 未压缩长度;元素 = literal(00) / copy1(01)
  / copy2(10) / copy4(11);copy 逐字节回引,RLE 语义允许 len > offset。
"""
import struct


class SnappyError(ValueError):
    pass


# ---------------- preamble 变长整数 ----------------

def encode_varint(n):
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def decode_varint(buf, pos):
    n, shift = 0, 0
    while True:
        if pos >= len(buf):
            raise SnappyError("varint 截断")
        b = buf[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, pos
        shift += 7


# ---------------- 解码器(全格式) ----------------

def decompress(data):
    n, pos = decode_varint(data, 0)
    out = bytearray()
    while pos < len(data):
        tag = data[pos]
        t = tag & 3
        pos += 1
        if t == 0:                                   # literal
            h = tag >> 2
            if h < 60:
                length = h + 1                        # 高 6 位存 len-1
            else:
                extra = h - 59                        # 60/61/62/63 -> 1~4 字节
                if pos + extra > len(data):
                    raise SnappyError("literal 长度截断")
                length = 1 + int.from_bytes(data[pos:pos + extra], "little")
                pos += extra
            if pos + length > len(data):
                raise SnappyError("literal 数据截断")
            out += data[pos:pos + length]
            pos += length
        else:                                         # copy
            if t == 1:                                # offset 高 3 位在 tag 内
                length = 4 + ((tag >> 2) & 7)
                offset = ((tag >> 5) << 8) | data[pos]
                pos += 1
            else:
                length = 1 + (tag >> 2)
                size = 2 if t == 2 else 4
                if pos + size > len(data):
                    raise SnappyError("copy offset 截断")
                offset = int.from_bytes(data[pos:pos + size], "little")
                pos += size
            if offset == 0 or offset > len(out):
                raise SnappyError("非法回引 offset=%d(已输出 %d 字节)"
                                  % (offset, len(out)))
            for _ in range(length):                   # 逐字节回引 = RLE
                out.append(out[-offset])
    if len(out) != n:
        raise SnappyError("声明长度 %d != 实际 %d" % (n, len(out)))
    return bytes(out)


# ---------------- 贪心编码器(教学级) ----------------

def _emit_literal(out, data, start, end):
    length = end - start
    if length <= 0:
        return
    if length <= 60:
        out.append((length - 1) << 2)
    else:
        for extra, tag in ((1, 60), (2, 61), (3, 62), (4, 63)):
            if length - 1 < (1 << (8 * extra)):
                out.append(tag << 2)
                out += (length - 1).to_bytes(extra, "little")
                break
    out += data[start:end]


def _emit_copy(out, offset, length):
    while length:
        if 4 <= length <= 11 and offset <= 2047:     # copy1 最省
            out.append(0x01 | ((length - 4) << 2) | ((offset >> 8) << 5))
            out.append(offset & 0xFF)
            return
        chunk = min(length, 64)
        if offset <= 0xFFFF:                          # copy2
            out.append(0x02 | ((chunk - 1) << 2))
            out += struct.pack("<H", offset)
        else:                                         # copy4(长回引)
            out.append(0x03 | ((chunk - 1) << 2))
            out += struct.pack("<I", offset)
        length -= chunk


def compress(data):
    out = bytearray(encode_varint(len(data)))
    table = {}
    i = lit_start = 0
    n = len(data)
    while i + 4 <= n:
        key = bytes(data[i:i + 4])
        cand = table.get(key)
        table[key] = i
        if cand is not None:
            offset = i - cand
            m = 4
            while i + m < n and data[cand + m] == data[i + m] and m < 64:
                m += 1
            _emit_literal(out, data, lit_start, i)
            _emit_copy(out, offset, m)
            i += m
            lit_start = i
        else:
            i += 1
    _emit_literal(out, data, lit_start, n)
    return bytes(out)


# ---------------- 断言 ----------------

def check(label, cond, detail=""):
    assert cond, "%s %s" % (label, detail)
    print("[ok] %s" % label)


def _lcg(n):
    x, out = 1, bytearray()
    for _ in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(x & 0xFF)
    return bytes(out)


def walk_elements(enc):
    _, pos = decode_varint(enc, 0)
    while pos < len(enc):
        tag = enc[pos]
        t = tag & 3
        start = pos
        pos += 1
        if t == 0:
            h = tag >> 2
            if h < 60:
                length = h + 1
            else:
                extra = h - 59
                length = 1 + int.from_bytes(enc[pos:pos + extra], "little")
                pos += extra
            pos += length
        elif t == 1:
            pos += 1
        else:
            pos += 2 if t == 2 else 4
        yield start, tag


def main():
    # ---- 1. preamble varint(规范原文数值) ----
    check("varint(64) == 0x40", encode_varint(64) == b"\x40")
    check("varint(2097150) == FE FF 7F",
          encode_varint(2097150) == b"\xfe\xff\x7f")
    for v in (0, 1, 127, 128, 300, 2 ** 31 - 1):
        n, pos = decode_varint(encode_varint(v), 0)
        assert n == v and pos == len(encode_varint(v))
    check("varint 往返 0..2^31-1 抽样", True)

    # ---- 2. 规范原文示例:'xababab' = literal 'xab' + copy(offset=2,len=4) ----
    enc = compress(b"xababab")
    check("规范示例字节串逐字节复现",
          enc == b"\x07\x08xab\x01\x02", "got %s" % enc.hex())
    check("规范示例解压还原", decompress(enc) == b"xababab")

    # ---- 3. RLE:len > offset ----
    raw = b"a" * 100
    check("RLE 游程往返", decompress(compress(raw)) == raw)
    check("RLE 压缩产物小于原文", len(compress(raw)) < len(raw))
    check("手工 copy2(len=1) 解压 'aa'",
          decompress(b"\x02\x00a\x02\x01\x00") == b"aa")
    check("手工 copy4(len=3,offset=3) 解压 'xyzxyz'",
          decompress(b"\x06\x08xyz\x0b\x03\x00\x00\x00") == b"xyzxyz")

    # ---- 4. copy4 长回引(70000 字节距离) ----
    a = _lcg(70000)
    data = a + a[:200]
    enc4 = compress(data)
    check("70000 长回引往返", decompress(enc4) == data)
    copies4 = [(p, tg) for p, tg in walk_elements(enc4) if tg & 3 == 3]
    check("编码流中确实出现 copy4 元素", len(copies4) >= 1)
    first_pos = copies4[0][0]
    off = int.from_bytes(enc4[first_pos + 1:first_pos + 5], "little")
    check("首个 copy4 的 offset == 70000", off == 70000, "got %d" % off)

    # ---- 5. 长字面量(61 号扩展长度,2 字节) ----
    import random
    rand300 = random.Random(42).randbytes(300)   # 固定种子,可复现
    enc5 = compress(rand300)
    check("300 字节字面量 tag=0xF4(61<<2)", enc5[2] == 0xF4, "got %#x" % enc5[2])
    check("300 字节随机往返", decompress(enc5) == rand300)

    # ---- 6. 混合数据往返(多种尺寸) ----
    sample = (b"hp=100 mp=50 pos=1,2,3 " * 40 + _lcg(64)
              + b"\x00" * 128 + b"attack skill=95 cd=1.5 ")
    for n in (0, 1, 2, 3, 4, 60, 61, 64, 100, 1000, 5000):
        blob = (sample * 3)[:n] if n else b""
        check("往返 n=%d" % n, decompress(compress(blob)) == blob)

    # ---- 7. 解码器守卫 ----
    for label, bad in (("声明长度不符", b"\x05\x08ab"),
                       ("回引越界(offset>已输出)",
                        b"\x04\x00a\x01\x02"),
                       ("offset=0 非法", b"\x04\x00a\x01\x00"),
                       ("literal 数据截断", b"\x05\x0bab")):
        try:
            decompress(bad)
            check(label + " 应报错", False)
        except SnappyError:
            check(label + " 报错", True)

    print("\n全部断言通过")


if __name__ == "__main__":
    main()
