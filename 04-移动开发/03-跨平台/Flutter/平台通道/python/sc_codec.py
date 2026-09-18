"""StandardMessageCodec 的二进制格式模型(纯标准库)。

格式依据:2026-09-18 实读 flutter/packages/flutter/lib/src/services/message_codecs.dart
的类注释与实现。完整逐条说明见 ../README.md「原理详解」;此处只留实现要点:

  * 首字节是类型判定字节,其后才是值本体;数字按宿主字节序(本模型按小端复现)。
  * null/true/false 无后续字节;可纳入 32 位的整数用 4 字节补码,更大的用 8 字节。
  * String 与大整数先写**字节长度**(expanding 格式)再写 UTF-8 字节。
  * List/Map 先写元素个数再递归写各元素(含类型字节)。
  * double 的值前补零字节,使其落在 64 位边界;TypedData 列表先写个数,
    再补**最少数量**零字节把元素区对齐到元素宽度,然后连续写元素(不带类型信息)。

  expanding 长度格式:0..253 单字节;254..0xFFFF 用 254+uint16;更大用 255+uint32。
  decodeMessage 读出值后缓冲区必须恰好读空,否则抛 FormatException('Message corrupted')。
"""

import struct

# ---- 类型判定字节(源码常量名与取值,原样对应) ----
TYPE_NULL = 0
TYPE_TRUE = 1
TYPE_FALSE = 2
TYPE_INT32 = 3
TYPE_INT64 = 4
TYPE_LARGE_INT = 5        # 源码注释:writeValue 自己从不产出 5,留给子类表达大整数
TYPE_FLOAT64 = 6
TYPE_STRING = 7
TYPE_UINT8_LIST = 8
TYPE_INT32_LIST = 9
TYPE_INT64_LIST = 10
TYPE_FLOAT64_LIST = 11
TYPE_LIST = 12
TYPE_MAP = 13
TYPE_FLOAT32_LIST = 14
# 0..127 保留给基类;>=128 供扩展使用;15..127 预留给未来

TYPE_NAMES = {
    TYPE_NULL: "null", TYPE_TRUE: "true", TYPE_FALSE: "false",
    TYPE_INT32: "int32", TYPE_INT64: "int64", TYPE_LARGE_INT: "largeInt",
    TYPE_FLOAT64: "float64", TYPE_STRING: "string",
    TYPE_UINT8_LIST: "uint8list", TYPE_INT32_LIST: "int32list",
    TYPE_INT64_LIST: "int64list", TYPE_FLOAT64_LIST: "float64list",
    TYPE_LIST: "list", TYPE_MAP: "map", TYPE_FLOAT32_LIST: "float32list",
}


class FormatException(Exception):
    """对应 Dart 的 FormatException(解码校验失败)。"""


# ---- 显式包装的定长列表(避免与普通 list 混淆:普通 list 走 TYPE_LIST) ----
class U8List:
    def __init__(self, data):
        self.data = bytes(data)


class F64List:
    def __init__(self, items):
        self.items = list(items)


class F32List:
    def __init__(self, items):
        self.items = list(items)


class I32List:
    def __init__(self, items):
        self.items = list(items)


class I64List:
    def __init__(self, items):
        self.items = list(items)


class WriteBuffer:
    """写缓冲。字节序固定小端(宿主字节序在本机与移动端主流 ABI 上均为小端)。"""

    def __init__(self):
        self.data = bytearray()

    def __len__(self):
        return len(self.data)

    def put_uint8(self, v):
        self.data += struct.pack("<B", v & 0xFF)

    def put_uint16(self, v):
        self.data += struct.pack("<H", v & 0xFFFF)

    def put_uint32(self, v):
        self.data += struct.pack("<I", v & 0xFFFFFFFF)

    def put_int32(self, v):
        self.data += struct.pack("<i", v)

    def put_int64(self, v):
        self.data += struct.pack("<q", v)

    def align_to(self, alignment):
        """补**最少数量**的零字节,使下一个字节的偏移是 alignment 的整数倍。"""
        pad = (-len(self.data)) % alignment
        self.data += b"\x00" * pad
        return pad


class ReadBuffer:
    def __init__(self, data):
        self.data = bytes(data)
        self.offset = 0

    @property
    def has_remaining(self):
        return self.offset < len(self.data)

    def get_uint8(self):
        v = self.data[self.offset]
        self.offset += 1
        return v

    def get_uint16(self):
        v = struct.unpack_from("<H", self.data, self.offset)[0]
        self.offset += 2
        return v

    def get_uint32(self):
        v = struct.unpack_from("<I", self.data, self.offset)[0]
        self.offset += 4
        return v

    def get_int32(self):
        v = struct.unpack_from("<i", self.data, self.offset)[0]
        self.offset += 4
        return v

    def get_int64(self):
        v = struct.unpack_from("<q", self.data, self.offset)[0]
        self.offset += 8
        return v

    def take(self, n):
        v = self.data[self.offset:self.offset + n]
        self.offset += n
        return v


def write_size(buf, value):
    """expanding 长度格式。value<254 单字节;<=0xFFFF 用 254+uint16;否则 255+uint32。"""
    assert 0 <= value <= 0xFFFFFFFF
    if value < 254:
        buf.put_uint8(value)
    elif value <= 0xFFFF:
        buf.put_uint8(254)
        buf.put_uint16(value)
    else:
        buf.put_uint8(255)
        buf.put_uint32(value)


def read_size(buf):
    value = buf.get_uint8()
    if value == 254:
        return buf.get_uint16()
    if value == 255:
        return buf.get_uint32()
    return value


def size_encoded_len(value):
    """仅用于断言:给定长度值在 expanding 格式下占几个字节。"""
    if value < 254:
        return 1
    if value <= 0xFFFF:
        return 3
    return 5


def _write_list_body(buf, count, elem_bytes, pack_elems):
    write_size(buf, count)
    # 元素区起点对齐到"每元素字节数"
    padding = buf.align_to(elem_bytes)
    for item in pack_elems:
        buf.data += item
    return padding


def write_value(buf, value):
    """按 StandardMessageCodec 写入一个值,返回本次写入消耗的字节数。"""
    start = len(buf)
    if value is None:
        buf.put_uint8(TYPE_NULL)
    elif value is True:
        buf.put_uint8(TYPE_TRUE)
    elif value is False:
        buf.put_uint8(TYPE_FALSE)
    elif isinstance(value, int):
        if -(2 ** 31) <= value < 2 ** 31:
            buf.put_uint8(TYPE_INT32)
            buf.put_int32(value)
        else:
            buf.put_uint8(TYPE_INT64)
            buf.put_int64(value)
    elif isinstance(value, float):
        buf.put_uint8(TYPE_FLOAT64)
        buf.align_to(8)
        buf.data += struct.pack("<d", value)
    elif isinstance(value, str):
        buf.put_uint8(TYPE_STRING)
        raw = value.encode("utf-8")
        write_size(buf, len(raw))
        buf.data += raw
    elif isinstance(value, U8List):
        buf.put_uint8(TYPE_UINT8_LIST)
        _write_list_body(buf, len(value.data), 1, [bytes([b]) for b in value.data])
    elif isinstance(value, I32List):
        buf.put_uint8(TYPE_INT32_LIST)
        _write_list_body(buf, len(value.items), 4, [struct.pack("<i", i) for i in value.items])
    elif isinstance(value, I64List):
        buf.put_uint8(TYPE_INT64_LIST)
        _write_list_body(buf, len(value.items), 8, [struct.pack("<q", i) for i in value.items])
    elif isinstance(value, F32List):
        buf.put_uint8(TYPE_FLOAT32_LIST)
        _write_list_body(buf, len(value.items), 4, [struct.pack("<f", i) for i in value.items])
    elif isinstance(value, F64List):
        buf.put_uint8(TYPE_FLOAT64_LIST)
        _write_list_body(buf, len(value.items), 8, [struct.pack("<d", i) for i in value.items])
    elif isinstance(value, list):
        buf.put_uint8(TYPE_LIST)
        write_size(buf, len(value))
        for item in value:
            write_value(buf, item)
    elif isinstance(value, dict):
        buf.put_uint8(TYPE_MAP)
        write_size(buf, len(value))
        for k, v in value.items():
            write_value(buf, k)
            write_value(buf, v)
    else:
        raise TypeError("不支持的参数类型:%r" % type(value))
    return len(buf) - start


def read_value(buf):
    type_byte = buf.get_uint8()
    if type_byte == TYPE_NULL:
        return None
    if type_byte == TYPE_TRUE:
        return True
    if type_byte == TYPE_FALSE:
        return False
    if type_byte == TYPE_INT32:
        return buf.get_int32()
    if type_byte == TYPE_INT64:
        return buf.get_int64()
    if type_byte in (TYPE_LARGE_INT, TYPE_STRING):
        length = read_size(buf)
        return buf.take(length).decode("utf-8")
    if type_byte == TYPE_FLOAT64:
        while buf.offset % 8 != 0:            # 跳过写侧补的零字节
            buf.get_uint8()
        value = struct.unpack_from("<d", buf.data, buf.offset)[0]
        buf.offset += 8
        return value
    if type_byte in (TYPE_UINT8_LIST, TYPE_INT32_LIST, TYPE_INT64_LIST,
                     TYPE_FLOAT64_LIST, TYPE_FLOAT32_LIST):
        elem_bytes, fmt = {
            TYPE_UINT8_LIST: (1, "B"), TYPE_INT32_LIST: (4, "i"),
            TYPE_INT64_LIST: (8, "q"), TYPE_FLOAT64_LIST: (8, "d"),
            TYPE_FLOAT32_LIST: (4, "f"),
        }[type_byte]
        n = read_size(buf)
        while buf.offset % elem_bytes != 0:      # 跳过写侧补的零字节
            buf.get_uint8()
        out = []
        for _ in range(n):
            if elem_bytes == 1:
                out.append(buf.get_uint8())
            else:
                out.append(struct.unpack_from("<" + fmt, buf.data, buf.offset)[0])
                buf.offset += elem_bytes
        return out
    if type_byte == TYPE_LIST:
        n = read_size(buf)
        return [read_value(buf) for _ in range(n)]
    if type_byte == TYPE_MAP:
        n = read_size(buf)
        return {read_value(buf): read_value(buf) for _ in range(n)}
    raise FormatException("unknown type byte %d" % type_byte)


def decode_message(data):
    """对应 StandardMessageCodec.decodeMessage:读完一个值后必须恰好读空。"""
    buf = ReadBuffer(data)
    result = read_value(buf)
    if buf.has_remaining:
        raise FormatException("Message corrupted")
    return result
