"""RLE / Bit-Packing 混合编码与数据页布局 —— 从 main.py 拆出的独立模块。

转写对象：apache/parquet-format 的 Encodings.md（RLE = 3）与 README.md 的 Data Pages 一节。
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple


# --------------------------------------------- RLE / Bit-Packing Hybrid
def uleb128(value: int) -> bytes:
    """ULEB-128（官方：varint-encode() 就是它）。"""
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def read_uleb128(data: bytes, pos: int) -> Tuple[int, int]:
    result, shift = 0, 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def bit_width(n: int) -> int:
    return max(1, n.bit_length())


def pack_lsb_first(values: Sequence[int], width: int) -> bytes:
    """官方：值从每个字节的**最低位**往高位打包，位序本身仍是 MSB→LSB。"""
    out = bytearray()
    cur, bits = 0, 0
    for v in values:
        cur |= (v & ((1 << width) - 1)) << bits
        bits += width
        while bits >= 8:
            out.append(cur & 0xFF)
            cur >>= 8
            bits -= 8
    if bits:
        out.append(cur & 0xFF)
    return bytes(out)


def unpack_lsb_first(data: bytes, width: int, count: int) -> List[int]:
    out, cur, bits, pos = [], 0, 0, 0
    mask = (1 << width) - 1
    while len(out) < count:
        while bits < width and pos < len(data):
            cur |= data[pos] << bits
            bits += 8
            pos += 1
        out.append(cur & mask)
        cur >>= width
        bits -= width
    return out


def rle_encode(levels: Sequence[int], width: int) -> bytes:
    """极简 RLE 混合编码器：连续相同值走 rle-run，其余按 8 个一组走 bit-packed-run。

    官方文法：
        rle-run         := varint(rle-run-len << 1) <repeated-value>
        bit-packed-run  := varint((len/8) << 1 | 1) <bit-packed-values>
    """
    out = bytearray()
    i, n = 0, len(levels)
    while i < n:
        j = i
        while j < n and levels[j] == levels[i]:
            j += 1
        run_len = j - i
        if run_len >= 8:                      # 够长才值得走 RLE run
            out += uleb128(run_len << 1)
            out += pack_lsb_first([levels[i]], max(1, ((width + 7) // 8) * 8))
            i = j
            continue
        group = list(levels[i:i + 8])
        while len(group) < 8:                 # 官方：总是按 8 的倍数打包，不足补 0
            group.append(0)
        out += uleb128((len(group) // 8) << 1 | 1)
        out += pack_lsb_first(group, width)
        i += 8
    return bytes(out)


def rle_decode(data: bytes, width: int, count: int) -> List[int]:
    out: List[int] = []
    pos = 0
    width_bytes = max(1, (width + 7) // 8)
    while len(out) < count and pos < len(data):
        header, pos = read_uleb128(data, pos)
        if header & 1:                                     # bit-packed run
            run_len = (header >> 1) * 8
            nbytes = (run_len * width + 7) // 8
            vals = unpack_lsb_first(data[pos:pos + nbytes], width, run_len)
            pos += nbytes
            out.extend(vals[: min(run_len, count - len(out))])
        else:                                              # rle run
            run_len = header >> 1
            val = int.from_bytes(data[pos:pos + width_bytes], "little")
            pos += width_bytes
            out.extend([val] * min(run_len, count - len(out)))
    return out[:count]


# ------------------------------------------------------------ 数据页布局
def data_page(rep_levels: Optional[Sequence[int]], def_levels: Optional[Sequence[int]],
              values: Sequence[bytes], rep_width: int, def_width: int) -> bytes:
    """官方：数据页里 rep → def → values 三段背靠背，**无填充**；
    非嵌套列不写 rep，required 列不写 def。
    """
    page = bytearray()
    if rep_levels is not None:
        page += b"R" + rle_encode(list(rep_levels), rep_width)
    if def_levels is not None:
        page += b"D" + rle_encode(list(def_levels), def_width)
    page += b"V" + b"".join(values)
    return bytes(page)
