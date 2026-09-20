"""LEB128 与 MUTF-8：DEX 的两套变长编码（见 AOSP《Dalvik 可执行文件格式》）。"""

# ---------- LEB128 ----------

def uleb128_encode(value):
    """无符号 LEB128：1-5 字节，除最后一字节外最高位均置 1。"""
    assert 0 <= value <= 0xFFFFFFFF, "uleb128 只用于 32 位数"
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def uleb128_decode(buf, pos=0):
    """返回 (值, 新位置)。未明确表示的位解译为 0。"""
    result = 0
    shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result & 0xFFFFFFFF, pos
        shift += 7


def sleb128_encode(value):
    """有符号 LEB128：最后一字节的最高载荷位做符号扩展。"""
    assert -0x80000000 <= value <= 0x7FFFFFFF
    out = bytearray()
    more = True
    while more:
        byte = value & 0x7F
        value >>= 7                       # Python 的 >> 是算术右移，天然符号扩展
        if (value == 0 and not (byte & 0x40)) or (value == -1 and (byte & 0x40)):
            more = False
        else:
            byte |= 0x80
        out.append(byte)
    return bytes(out)


def sleb128_decode(buf, pos=0):
    result = 0
    shift = 0
    byte = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not (byte & 0x80):
            break
    if byte & 0x40:                        # 符号扩展：最后一字节的第 6 位为符号位
        result -= 1 << shift
    return result, pos


def uleb128p1_encode(value):
    """uleb128p1 = uleb128(v + 1)，让 -1（无符号 0xffffffff）编码成单字节。"""
    return uleb128_encode((value + 1) & 0xFFFFFFFF)


def uleb128p1_decode(buf, pos=0):
    raw, pos = uleb128_decode(buf, pos)
    return (raw - 1) & 0xFFFFFFFF, pos


# ---------- MUTF-8（修改后的 UTF-8） ----------

def mutf8_encode(s):
    """U+0000 编成 0xc0 0x80；增补平面字符按代理对逐半代 3 字节。"""
    out = bytearray()
    for ch in s:
        cp = ord(ch)
        if cp == 0x00:
            out += b"\xc0\x80"
        elif cp <= 0x7F:
            out.append(cp)
        elif cp <= 0x7FF:
            out.append(0xC0 | (cp >> 6))
            out.append(0x80 | (cp & 0x3F))
        elif 0xD800 <= cp <= 0xDFFF:
            out.append(0xE0 | (cp >> 12))
            out.append(0x80 | ((cp >> 6) & 0x3F))
            out.append(0x80 | (cp & 0x3F))
        else:
            for half in _utf16_units(cp):
                out.append(0xE0 | (half >> 12))
                out.append(0x80 | ((half >> 6) & 0x3F))
                out.append(0x80 | (half & 0x3F))
    out.append(0x00)                       # 以值为 0 的字节结尾
    return bytes(out)


def _utf16_units(cp):
    if cp < 0x10000:
        return (cp,)
    cp -= 0x10000
    return (0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF))


def utf16_size(s):
    """官方 string_data_item.utf16_size：以 UTF-16 代码单元计数，不是字节数。"""
    return sum(len(_utf16_units(ord(ch))) for ch in s)


def mutf8_decode(buf):
    """逐字节解码，遇 0 终止；不做排序语义，仅还原码元序列。"""
    units = []
    i = 0
    while i < len(buf) and buf[i] != 0x00:
        b0 = buf[i]
        if b0 < 0x80:
            units.append(b0)
            i += 1
        elif b0 & 0xE0 == 0xC0:
            units.append(((b0 & 0x1F) << 6) | (buf[i + 1] & 0x3F))
            i += 2
        elif b0 & 0xF0 == 0xE0:
            units.append(((b0 & 0x0F) << 12) | ((buf[i + 1] & 0x3F) << 6) | (buf[i + 2] & 0x3F))
            i += 3
        else:
            raise ValueError("非法 MUTF-8 起始字节 0x%02x" % b0)
    return units


def mutf8_to_str(buf):
    out = []
    units = mutf8_decode(buf)
    i = 0
    while i < len(units):
        u = units[i]
        if 0xD800 <= u <= 0xDBFF and i + 1 < len(units) and 0xDC00 <= units[i + 1] <= 0xDFFF:
            lo = units[i + 1]
            out.append(chr(0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00)))
            i += 2
        else:
            out.append(chr(u))
            i += 1
    return "".join(out)


def string_sort_key(s):
    """string_ids 必须「使用 UTF-16 码位值按字符串内容排序」（非语言区域敏感）。"""
    return tuple(_utf16_units(ord(ch)) for ch in s)

