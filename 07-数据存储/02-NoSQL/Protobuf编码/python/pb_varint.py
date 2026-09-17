# -*- coding: utf-8 -*-
"""pb_varint.py — Protobuf 线格式的最底层:varint / ZigZag / tag。

权威来源(protobuf.dev 官方文档原文,逐句落在注释里):
  programming-guides/encoding/
    * "Each byte in the varint has a continuation bit ... This is the most
       significant bit (MSB) of the byte. The lower 7 bits are a payload"
    * "These 7-bit payloads are in little-endian order."
    * 150 编码为 ``9601``;1 编码为 ``01``
    * "There are six wire types: VARINT, I64, LEN, SGROUP, EGROUP, and I32"
    * "The 'tag' of a record is encoded as a varint formed from the field number
       and the wire type via the formula (field_number << 3) | wire_type"
    * intN 的负数用补码 → 占满 **十个字节**;sintN 用 ZigZag
    * ZigZag:正整数 p → 2p;负整数 n → 2|n| - 1;即 (n << 1) ^ (n >> 31|63)
  programming-guides/proto3/
    * "You must give each field in your message definition a number between
       1 and 536,870,911"
    * "Field numbers 19,000 to 19,999 are reserved for the Protocol Buffers
       implementation."
"""

from typing import Tuple

WIRE_VARINT = 0
WIRE_I64 = 1
WIRE_LEN = 2
WIRE_SGROUP = 3
WIRE_EGROUP = 4
WIRE_I32 = 5

WIRE_NAMES = {
    WIRE_VARINT: "VARINT",
    WIRE_I64: "I64",
    WIRE_LEN: "LEN",
    WIRE_SGROUP: "SGROUP",
    WIRE_EGROUP: "EGROUP",
    WIRE_I32: "I32",
}

MAX_VARINT_BYTES = 10           # 官方:unsigned 64-bit 最多用十个字节
MIN_FIELD_NUMBER = 1
MAX_FIELD_NUMBER = 536_870_911  # 官方:2^29 - 1
RESERVED_FIELD_LO = 19_000
RESERVED_FIELD_HI = 19_999

U64_MASK = (1 << 64) - 1


class ProtobufError(ValueError):
    """线格式违规。"""


def validate_field_number(number: int) -> None:
    """官方两条限制:必须在 [1, 2^29-1],且不能落在 19000..19999。"""
    if not MIN_FIELD_NUMBER <= number <= MAX_FIELD_NUMBER:
        raise ProtobufError("field number out of range: %d" % number)
    if RESERVED_FIELD_LO <= number <= RESERVED_FIELD_HI:
        raise ProtobufError("field number reserved: %d" % number)


def encode_varint(value: int) -> bytes:
    """base-128 变长整数。负数先按 64 位补码转成无符号(官方:intN 负数占十个字节)。"""
    if value < 0:
        value &= U64_MASK
    if value > U64_MASK:
        raise ProtobufError("value exceeds 64 bits: %d" % value)
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
            continue
        out.append(b)
        return bytes(out)


def varint_length(value: int) -> int:
    """编码后的字节数(不实际编码)。"""
    if value < 0:
        return MAX_VARINT_BYTES          # 负数被补码成 64 位全宽
    n = 1
    while value > 0x7F:
        value >>= 7
        n += 1
    return n


def decode_varint(data: bytes, pos: int = 0) -> Tuple[int, int]:
    """返回 (无符号值, 新位置)。第 10 个字节的最高位必须是 0,否则是损坏数据。"""
    start = pos
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ProtobufError("truncated varint at %d" % start)
        if pos - start >= MAX_VARINT_BYTES:
            raise ProtobufError("varint longer than 10 bytes at %d" % start)
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def as_signed64(value: int) -> int:
    """把无符号 64 位解释回有符号(int64 的补码语义)。"""
    return value - (1 << 64) if value >= (1 << 63) else value


def as_signed32(value: int) -> int:
    return value - (1 << 32) if value >= (1 << 31) else value


def zigzag_encode(n: int, bits: int = 32) -> int:
    """官方:每个值 n 用 (n << 1) ^ (n >> 31)(sint32)或 (n >> 63)(sint64)编码。"""
    return ((n << 1) ^ (n >> (bits - 1))) & ((1 << bits) - 1)


def zigzag_decode(v: int) -> int:
    """ZigZag 逆变换:最低位是符号位。"""
    return (v >> 1) ^ -(v & 1)


def encode_tag(field_number: int, wire_type: int) -> bytes:
    """官方公式:(field_number << 3) | wire_type,整体按 varint 编码。"""
    validate_field_number(field_number)
    if wire_type not in WIRE_NAMES:
        raise ProtobufError("unknown wire type %r" % (wire_type,))
    return encode_varint((field_number << 3) | wire_type)


def decode_tag(data: bytes, pos: int = 0) -> Tuple[int, int, int]:
    """返回 (field_number, wire_type, 新位置)。低 3 位是 wire type,其余是字段号。"""
    raw, pos = decode_varint(data, pos)
    wire_type = raw & 0x07
    field_number = raw >> 3
    if wire_type not in WIRE_NAMES:
        raise ProtobufError("unknown wire type %d" % wire_type)
    if field_number < MIN_FIELD_NUMBER:
        raise ProtobufError("field number 0 is not allowed")
    return field_number, wire_type, pos


def tag_size(field_number: int, wire_type: int) -> int:
    return varint_length((field_number << 3) | wire_type)
