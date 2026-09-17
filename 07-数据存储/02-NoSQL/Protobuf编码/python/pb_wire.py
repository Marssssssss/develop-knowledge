# -*- coding: utf-8 -*-
"""pb_wire.py — Protobuf 消息编解码:TLV 记录、packed 重复字段、未知字段跳过、合并语义。

权威来源(protobuf.dev 官方文档原文):
  programming-guides/encoding/
    * "each key-value pair is turned into a record consisting of the field number,
       a wire type and a payload" —— 即 TLV
    * "The wire type tells the parser how big the payload after it is. This allows
       old parsers to skip over new fields they don't understand."
    * LEN "has a dynamic length, specified by a varint immediately after the tag"
    * packed:"parsers must be able to parse repeated fields that were compiled as
       packed as if they were not packed, and vice versa"
    * SGROUP/EGROUP "records have empty payloads";"Group field numbers need to
       match up. If we encounter 7:EGROUP where we expect 8:EGROUP, the message
       is mal-formed."
    * Field Order:"there is no guaranteed order for how its known or unknown
       fields will be written ... parsers must be able to parse fields in any order"
    * Last One Wins:"if the same field appears multiple times, the parser accepts
       the last value it sees. For embedded message fields, the parser merges
       multiple instances of the same field"
"""

import struct
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional, Tuple

from pb_varint import (
    WIRE_EGROUP, WIRE_I32, WIRE_I64, WIRE_LEN, WIRE_NAMES, WIRE_SGROUP, WIRE_VARINT,
    ProtobufError, decode_tag, decode_varint, encode_tag, encode_varint,
    validate_field_number, zigzag_decode, zigzag_encode,
)

# 官方 wire type 表:每类字段固定映射到一种线格式
KIND_TO_WIRE = {
    "int32": WIRE_VARINT, "int64": WIRE_VARINT, "uint32": WIRE_VARINT,
    "uint64": WIRE_VARINT, "sint32": WIRE_VARINT, "sint64": WIRE_VARINT,
    "bool": WIRE_VARINT, "enum": WIRE_VARINT,
    "fixed64": WIRE_I64, "sfixed64": WIRE_I64, "double": WIRE_I64,
    "string": WIRE_LEN, "bytes": WIRE_LEN, "message": WIRE_LEN,
    "fixed32": WIRE_I32, "sfixed32": WIRE_I32, "float": WIRE_I32,
}


@dataclass
class FieldDef:
    number: int
    name: str
    kind: str
    repeated: bool = False
    packed: bool = True
    msg_def: Optional["MessageDef"] = None

    def wire_type(self) -> int:
        validate_field_number(self.number)
        return KIND_TO_WIRE[self.kind]

    def scalar_wire(self) -> bool:
        """VARINT / I64 / I32 是定长或自定界标量,可以 packed。"""
        return self.wire_type() in (WIRE_VARINT, WIRE_I32, WIRE_I64)


@dataclass
class MessageDef:
    name: str
    fields: List[FieldDef] = dc_field(default_factory=list)

    def by_number(self) -> Dict[int, FieldDef]:
        return {f.number: f for f in self.fields}

    def by_name(self) -> Dict[str, FieldDef]:
        return {f.name: f for f in self.fields}


# ------------------------------------------------------------------ 标量
def encode_scalar(fd: FieldDef, value: Any) -> bytes:
    k = fd.kind
    if k in ("int32", "int64"):
        return encode_varint(value)                     # 官方:负数用补码 → 十个字节
    if k in ("uint32", "uint64"):
        return encode_varint(value)
    if k == "sint32":
        return encode_varint(zigzag_encode(value, 32))
    if k == "sint64":
        return encode_varint(zigzag_encode(value, 64))
    if k in ("bool", "enum"):
        return encode_varint(1 if value else 0)
    if k == "fixed64":
        return int(value).to_bytes(8, "little")
    if k == "sfixed64":
        return int(value).to_bytes(8, "little", signed=True)
    if k == "double":
        return struct.pack("<d", value)
    if k == "fixed32":
        return int(value).to_bytes(4, "little")
    if k == "sfixed32":
        return int(value).to_bytes(4, "little", signed=True)
    if k == "float":
        return struct.pack("<f", value)
    if k == "string":
        return value.encode("utf-8")
    if k == "bytes":
        return bytes(value)
    if k == "message":
        return encode_message(fd.msg_def, value)
    raise ProtobufError("unsupported kind %r" % k)


def decode_scalar(fd: FieldDef, payload: bytes) -> Any:
    k = fd.kind
    if k in ("int32", "int64", "uint32", "uint64", "bool", "enum"):
        v, _ = decode_varint(payload, 0)
        return v
    if k in ("sint32", "sint64"):
        v, _ = decode_varint(payload, 0)
        return zigzag_decode(v)
    if k == "fixed64":
        return int.from_bytes(payload, "little")
    if k == "sfixed64":
        return int.from_bytes(payload, "little", signed=True)
    if k == "double":
        return struct.unpack("<d", payload)[0]
    if k == "fixed32":
        return int.from_bytes(payload, "little")
    if k == "sfixed32":
        return int.from_bytes(payload, "little", signed=True)
    if k == "float":
        return struct.unpack("<f", payload)[0]
    if k == "string":
        return payload.decode("utf-8")
    if k == "bytes":
        return payload
    if k == "message":
        return decode_message(fd.msg_def, payload)
    raise ProtobufError("unsupported kind %r" % k)


# ------------------------------------------------------------------ 记录
def encode_record(field_number: int, wire_type: int, payload: bytes) -> bytes:
    tag = encode_tag(field_number, wire_type)
    if wire_type == WIRE_VARINT:
        return tag + payload
    if wire_type in (WIRE_I64, WIRE_I32):
        return tag + payload
    if wire_type == WIRE_LEN:
        return tag + encode_varint(len(payload)) + payload
    if wire_type in (WIRE_SGROUP, WIRE_EGROUP):
        return tag                        # 官方:group 记录payload 为空
    raise ProtobufError("cannot build record for wire type %d" % wire_type)


def iter_records(data: bytes):
    """逐条吐出 (field_number, wire_type, payload)。未知字段也能定位。"""
    pos = 0
    while pos < len(data):
        number, wire, pos = decode_tag(data, pos)
        if wire == WIRE_VARINT:
            start = pos
            _, pos = decode_varint(data, pos)
            payload = data[start:pos]
        elif wire == WIRE_I64:
            payload, pos = data[pos:pos + 8], pos + 8
        elif wire == WIRE_I32:
            payload, pos = data[pos:pos + 4], pos + 4
        elif wire == WIRE_LEN:
            n, pos = decode_varint(data, pos)
            payload, pos = data[pos:pos + n], pos + n
        else:
            raise ProtobufError("group wire type %d at %d" % (wire, pos))
        if len(payload) == 0 and wire in (WIRE_I64, WIRE_I32, WIRE_LEN):
            raise ProtobufError("truncated record for field %d" % number)
        yield number, wire, payload


def skip_field(data: bytes, pos: int, wire_type: int) -> int:
    """按 wire type 跳过一条未知记录 —— 这就是"旧解析器能读新消息"的机制。"""
    if wire_type == WIRE_VARINT:
        _, pos = decode_varint(data, pos)
        return pos
    if wire_type == WIRE_I64:
        return pos + 8
    if wire_type == WIRE_I32:
        return pos + 4
    if wire_type == WIRE_LEN:
        n, pos = decode_varint(data, pos)
        return pos + n
    if wire_type == WIRE_SGROUP:
        return pos
    raise ProtobufError("EGROUP without SGROUP at %d" % pos)


# ------------------------------------------------------------------ 消息
def encode_message(defn: MessageDef, values: Dict[str, Any],
                   field_order: Optional[List[str]] = None) -> bytes:
    """按 field_order(默认声明顺序)编码。顺序不影响语义,只影响字节。"""
    named = defn.by_name()
    order = field_order if field_order is not None else [f.name for f in defn.fields]
    out = bytearray()
    for name in order:
        fd = named[name]
        if name not in values or values[name] is None:
            continue
        val = values[name]
        if fd.repeated:
            items = list(val)
            if fd.packed and fd.scalar_wire():
                payload = b"".join(encode_scalar(fd, v) for v in items)
                out += encode_record(fd.number, WIRE_LEN, payload)
            else:
                for v in items:
                    payload = encode_scalar(fd, v)
                    out += encode_record(fd.number, fd.wire_type(), payload)
            continue
        payload = encode_scalar(fd, val)
        out += encode_record(fd.number, fd.wire_type(), payload)
    return bytes(out)


def merge_message(dst: Dict[str, Any], src: Dict[str, Any], defn: MessageDef) -> Dict[str, Any]:
    """官方 Last One Wins:标量后者覆盖;嵌入消息递归合并;repeated 拼接。"""
    named = defn.by_name()
    for k, v in src.items():
        fd = named[k]
        if fd.repeated:
            dst[k] = list(dst.get(k, [])) + list(v)
        elif fd.kind == "message":
            dst[k] = merge_message(dst.get(k, {}), v, fd.msg_def)
        else:
            dst[k] = v
    return dst


def decode_message(defn: MessageDef, data: bytes) -> Dict[str, Any]:
    """解析消息。未知字段按 wire type 跳过;重复标量取最后一个值。"""
    named = defn.by_number()
    out: Dict[str, Any] = {}
    pos = 0
    while pos < len(data):
        number, wire, pos = decode_tag(data, pos)
        fd = named.get(number)
        if fd is None:
            pos = skip_field(data, pos, wire)      # 前向兼容:未知字段直接跳过
            continue
        if wire == WIRE_VARINT:
            start = pos
            _, pos = decode_varint(data, pos)
            payload = data[start:pos]
        elif wire == WIRE_I64:
            payload, pos = data[pos:pos + 8], pos + 8
        elif wire == WIRE_I32:
            payload, pos = data[pos:pos + 4], pos + 4
        elif wire == WIRE_LEN:
            n, pos = decode_varint(data, pos)
            payload, pos = data[pos:pos + n], pos + n
            if fd.repeated and fd.scalar_wire():
                # 官方:packed 与非 packed 形式解析器都必须接受
                fixed = 8 if fd.kind in ("fixed64", "sfixed64", "double") else (
                    4 if fd.kind in ("fixed32", "sfixed32", "float") else 0)
                sub: List[Any] = []
                p = 0
                while p < len(payload):
                    if fixed:
                        sub.append(decode_scalar(fd, payload[p:p + fixed]))
                        p += fixed
                    else:
                        _, q = decode_varint(payload, p)
                        sub.append(decode_scalar(fd, payload[p:q]))
                        p = q
                out[fd.name] = list(out.get(fd.name, [])) + sub
                continue
        else:
            raise ProtobufError("group records must be handled by decode_with_groups")
        if fd.repeated:
            out[fd.name] = list(out.get(fd.name, [])) + [decode_scalar(fd, payload)]
        elif fd.kind == "message":
            out[fd.name] = merge_message(out.get(fd.name, {}), decode_scalar(fd, payload), fd.msg_def)
        else:
            out[fd.name] = decode_scalar(fd, payload)   # Last One Wins
    return out


def parse_group(data: bytes, pos: int, field_number: int) -> Tuple[bytes, int]:
    """官方:SGROUP/EGROUP 的字段号必须配对,否则消息 mal-formed。"""
    start = pos
    while pos < len(data):
        number, wire, next_pos = decode_tag(data, pos)
        if wire == WIRE_EGROUP:
            if number != field_number:
                raise ProtobufError("mal-formed group: expected %d:EGROUP, got %d:EGROUP"
                                    % (field_number, number))
            return data[start:pos], next_pos
        pos = skip_field(data, next_pos, wire)
    raise ProtobufError("group %d:EGROUP missing" % field_number)


def wire_name(wire_type: int) -> str:
    return WIRE_NAMES[wire_type]
