"""Protocol Buffers 线格式的最小可运行编解码器。

口径来源（本轮实读原文）：
  - protobuf.dev/programming-guides/encoding/（98 885 字节）
      * Base 128 Varints：每字节最高位是 continuation bit，低 7 位是 payload，
        **小端序**拼接；1~10 字节可表示 uint64。
      * Message Structure：`tag = (field_number << 3) | wire_type`，低 3 位是线类型，
        其余是字段号。
      * 六种线类型：0 VARINT / 1 I64 / 2 LEN / 3 SGROUP / 4 EGROUP / 5 I32。
      * bool 与 enum 按 int32 编码，bool 只会是 `00` 或 `01`。
      * intN 负数按**二进制补码**编码，因此必然占满 10 字节；
        sintN 用 ZigZag：`(n << 1) ^ (n >> 31)`（32 位）或 `(n << 1) ^ (n >> 63)`（64 位），
        表格：0→0、-1→1、1→2、-2→3。
      * I32 / I64 是**小端**定长；float / double 是 IEEE 754。
      * LEN：紧跟 tag 的 varint 是长度；string 与 bytes 上限 2GB。
      * packed：原始数值类型的 repeated 字段默认打包成**单个 LEN 记录**；
        解析器必须接受多个键值对形式并把载荷拼接起来。
      * oneof 与"不在 oneof 里"编码完全相同。
      * Last One Wins：标量/字符串取最后一个；**嵌入消息是合并**；
        repeated 拼接；因此「拼接两段编码再解析」==「分别解析再 MergeFrom」。
      * map 等价于 `repeated Entry{key=1; value=2}`，序列化顺序不保证。
      * group 用 SGROUP / EGROUP 成对界定，**字段号必须配对**。
      * 序列化顺序是实现细节：字段号声明顺序与写出顺序都**不保证**，
        默认序列化**不是确定性的**。
  - protobuf.dev/programming-guides/proto3/（191 146 字节）
      * 字段号范围 1 .. 536,870,911；**19,000 ~ 19,999 保留给实现**；
        字段号 1~15 的 tag 占 1 字节，16~2047 占 2 字节；字段号上限是 29 位
        （另 3 位给线类型）。
      * 删除字段必须 reserved 字段号（区间**闭区间**）；
        字段号与字段名**不能写在同一条 reserved 语句里**。

运行：python wire.py
"""

import struct

VARINT = 0
I64 = 1
LEN = 2
SGROUP = 3
EGROUP = 4
I32 = 5

WIRE_TYPE_NAMES = {0: "VARINT", 1: "I64", 2: "LEN", 3: "SGROUP", 4: "EGROUP", 5: "I32"}

MAX_UINT64 = (1 << 64) - 1
MAX_LEN = 2 * 1024 * 1024 * 1024  # 2GB，string/bytes 的上限


def varint_encode(value):
    """Base 128 varint：低 7 位一组，**小端**在前，MSB 作 continuation bit。"""
    if value < 0:
        value += 1 << 64
    if value > MAX_UINT64:
        raise ValueError("varint 超出 uint64")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def varint_decode(buf, pos=0):
    """返回 (值, 新位置)。最多读 10 字节。"""
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("varint 提前耗尽")
        if shift > 63:
            raise ValueError("varint 超过 10 字节")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7


def varint_size(value):
    return len(varint_encode(value))


def zigzag_encode(value, bits=64):
    """sint32 用 `(n << 1) ^ (n >> 31)`，sint64 用 `(n << 1) ^ (n >> 63)`。"""
    return (value << 1) ^ (value >> (bits - 1))


def zigzag_decode(value, bits=64):
    return (value >> 1) ^ -(value & 1)


def to_signed64(value):
    """把 uint64 解释成 int64 的二进制补码。"""
    return value - (1 << 64) if value >= (1 << 63) else value


def tag_byte(field_number, wire_type):
    """`tag = (field_number << 3) | wire_type`。"""
    return varint_encode((field_number << 3) | wire_type)


def split_tag(value):
    return value >> 3, value & 0x07


def encode_varint_field(field_number, value):
    return tag_byte(field_number, VARINT) + varint_encode(value)


def encode_sint_field(field_number, value, bits=64):
    return tag_byte(field_number, VARINT) + varint_encode(zigzag_encode(value, bits))


def encode_len_field(field_number, payload):
    return tag_byte(field_number, LEN) + varint_encode(len(payload)) + payload


def encode_string_field(field_number, text):
    return encode_len_field(field_number, text.encode("utf-8"))


def encode_fixed32_field(field_number, value):
    return tag_byte(field_number, I32) + struct.pack("<I", value & 0xFFFFFFFF)


def encode_fixed64_field(field_number, value):
    return tag_byte(field_number, I64) + struct.pack("<Q", value & MAX_UINT64)


def encode_float_field(field_number, value):
    return tag_byte(field_number, I32) + struct.pack("<f", value)


def encode_double_field(field_number, value):
    return tag_byte(field_number, I64) + struct.pack("<d", value)


def encode_packed(field_number, values):
    """packed repeated：一个 LEN 记录，载荷是各元素编码的拼接。"""
    payload = b"".join(varint_encode(v) for v in values)
    return encode_len_field(field_number, payload)


def encode_group(field_number, body):
    """group：SGROUP 与 EGROUP 的字段号必须相同。"""
    return tag_byte(field_number, SGROUP) + body + tag_byte(field_number, EGROUP)


def decode_records(buf):
    """无 schema 的记录级解析，返回 [(字段号, 线类型, 值)]。

    LEN 的值是原始字节；SGROUP / EGROUP 的值是 None（载荷为空）。
    """
    out = []
    pos = 0
    while pos < len(buf):
        tag, pos = varint_decode(buf, pos)
        field_number, wire_type = split_tag(tag)
        if wire_type == VARINT:
            value, pos = varint_decode(buf, pos)
            out.append((field_number, wire_type, value))
        elif wire_type in (SGROUP, EGROUP):
            out.append((field_number, wire_type, None))
        elif wire_type == I64:
            out.append((field_number, wire_type, buf[pos:pos + 8]))
            pos += 8
        elif wire_type == I32:
            out.append((field_number, wire_type, buf[pos:pos + 4]))
            pos += 4
        elif wire_type == LEN:
            length, pos = varint_decode(buf, pos)
            out.append((field_number, wire_type, buf[pos:pos + length]))
            pos += length
        else:
            raise ValueError("未知线类型 %d" % wire_type)
    return out


def group_pairs(records):
    """检查 group 的 SGROUP / EGROUP 字段号是否配对，返回不匹配的位置列表。"""
    bad = []
    stack = []
    for index, (field_number, wire_type, _value) in enumerate(records):
        if wire_type == SGROUP:
            stack.append((index, field_number))
        elif wire_type == EGROUP:
            if not stack:
                bad.append((index, "多余的 EGROUP"))
            elif stack[-1][1] != field_number:
                bad.append((index, "EGROUP 字段号 %d 与 SGROUP %d 不符"
                            % (field_number, stack[-1][1])))
                stack.pop()
            else:
                stack.pop()
    for index, field_number in stack:
        bad.append((index, "SGROUP %d 未闭合" % field_number))
    return bad


def concat_packed(chunks):
    """解析器必须接受多个 packed 键值对，把载荷**拼接**起来。"""
    payload = b"".join(chunks)
    values = []
    pos = 0
    while pos < len(payload):
        value, pos = varint_decode(payload, pos)
        values.append(value)
    return values
