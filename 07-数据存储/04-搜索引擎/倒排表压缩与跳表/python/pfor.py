"""Lucene PForUtil：带例外（patch）的定长位打包。

忠实转写自 apache/lucene@main
``core/src/java/org/apache/lucene/codecs/lucene104/PForUtil.java``。

一个块固定 256 个值。编码器先统计位宽直方图，从最高位宽往下试探：
只要「落到该位宽之上的值」累计不超过 MAX_EXCEPTIONS=7，就继续压低位数，
被压不下去的值作为例外单独存 (下标, 高 bits) 两个字节。
"""

from bitio import _i32, _read_vint, _read_vlong, _write_vint
from forutil import (BLOCK_SIZE, forutil_decode, forutil_encode_bytes,
                     num_bytes)

MAX_EXCEPTIONS = 7


def bits_required(v):
    """PackedInts.bitsRequired：非负整数的二进制位宽，0 需要 0 位。"""
    if v < 0:
        raise ValueError("bitsRequired 只接受非负数")
    return v.bit_length()


def all_equal(l):
    for i in range(1, BLOCK_SIZE):
        if l[i] != l[0]:
            return False
    return True


def pfor_encode(ints):
    """PForUtil.encode：返回 (字节串, 诊断字典)。不修改入参。"""
    work = list(ints)
    histogram = [0] * 32
    max_bits_required = 0
    for i in range(BLOCK_SIZE):
        b = bits_required(work[i])
        histogram[b] += 1
        max_bits_required = max(max_bits_required, b)

    # patch 只占 1 字节，故位数最多下调 8
    min_bits = max(0, max_bits_required - 8)
    cumulative = 0
    patched_bits = max_bits_required
    num_exceptions = 0
    b = max_bits_required
    while b >= min_bits:
        if cumulative > MAX_EXCEPTIONS:
            break
        patched_bits = b
        num_exceptions = cumulative
        cumulative += histogram[b]
        b -= 1

    max_unpatched = (1 << patched_bits) - 1
    exceptions = [0] * (num_exceptions * 2)
    all_equal_after = False
    if num_exceptions > 0:
        k = 0
        for i in range(BLOCK_SIZE):
            if work[i] > max_unpatched:
                exceptions[2 * k] = i
                exceptions[2 * k + 1] = (work[i] >> patched_bits) & 0xFF
                work[i] &= max_unpatched
                k += 1
        assert k == num_exceptions

    out = bytearray()
    if all_equal(work) and max_bits_required <= 8:
        all_equal_after = True
        # 落盘的 bitsPerValue 是 0，解码端按 `ints[i] |= hi << 0` 还原，
        # 所以这里必须先把高位抬回原值
        for i in range(num_exceptions):
            exceptions[2 * i + 1] = _i32(
                exceptions[2 * i + 1] << patched_bits) & 0xFF
        all_equal_after = True
        out.append((num_exceptions << 5) & 0xFF)
        _write_vint(out, work[0])
    else:
        token = (num_exceptions << 5) | patched_bits
        out.append(token & 0xFF)
        if patched_bits > 0:
            out += forutil_encode_bytes(work, patched_bits)
    out += bytes(exceptions)
    return bytes(out), {
        "max_bits": max_bits_required,
        "patched_bits": patched_bits,
        "num_exceptions": num_exceptions,
        "max_unpatched": max_unpatched,
        "all_equal": all_equal_after,
        "histogram": histogram,
    }


def pfor_decode(buf):
    """PForUtil.decode：返回 256 个值。"""
    token = buf[0]
    bpv = token & 0x1F
    pos = 1
    ints = [0] * BLOCK_SIZE
    if bpv == 0:
        v, pos = _read_vint(buf, pos)
        ints = [v] * BLOCK_SIZE
    else:
        words = []
        for _ in range(bpv << 3):
            words.append(int.from_bytes(buf[pos:pos + 4], "big"))
            pos += 4
        ints = forutil_decode(words, bpv)
    num_exceptions = token >> 5
    for _ in range(num_exceptions):
        idx = buf[pos]
        hi = buf[pos + 1]
        pos += 2
        ints[idx] |= hi << bpv
    return ints


def pfor_skip(buf, pos=0):
    """PForUtil.skip：不解码直接跳过一个块，返回新的读取位置。

    bitsPerValue 为 0 时正文是 vLong，否则是 numBytes(bpv) 个定长字节。
    """
    token = buf[pos]
    bpv = token & 0x1F
    num_exceptions = token >> 5
    pos += 1
    if bpv == 0:
        _, pos = _read_vlong(buf, pos)
        pos += num_exceptions << 1
    else:
        pos += num_bytes(bpv) + (num_exceptions << 1)
    return pos
