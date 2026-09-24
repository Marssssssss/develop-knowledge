"""倒排表压缩公共层：32 位截断与 vInt / vLong 编解码。

口径：Java int 固定 32 位、溢出回绕；vInt 每字节低 7 位存数据、最高位表「还有后续」。
"""

MASK32 = 0xFFFFFFFF


def _i32(x):
    """按 Java int 语义截断到 32 位（以无符号形式保存）。"""
    return x & MASK32


def _write_vint(out, v):
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _read_vint(buf, pos):
    b = buf[pos]
    pos += 1
    v = b & 0x7F
    shift = 7
    while b & 0x80:
        b = buf[pos]
        pos += 1
        v |= (b & 0x7F) << shift
        shift += 7
    return v, pos


def _read_vlong(buf, pos):
    """Lucene 的 readVLong 与 readVInt 同构（本 demo 只用到小值）。"""
    return _read_vint(buf, pos)
