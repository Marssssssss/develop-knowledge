"""
protobuf_wire: pure-Python Protobuf 3 wire format encoder/decoder.

Implements the six wire types from the spec:
    WireType.VARINT(0)         int32/int64/uint32/uint64/bool/enum
    WireType.FIXED64(1)        fixed64/sfixed64/double
    WireType.LEN(2)            string/bytes/embedded messages/packed repeated
    WireType.SGROUP(3)         deprecated
    WireType.EGROUP(4)         deprecated
    WireType.FIXED32(5)        fixed32/sfixed32/float

Tag formula: (field_number << 3) | wire_type

No .proto compile step; we hand-construct Field() objects.

   python3 protobuf_wire.py
"""
import struct
from io import BytesIO
from typing import Any, Iterable, Iterator
from dataclasses import dataclass, field as dc_field


# ----- Wire type table -----
class WireType:
    VARINT = 0
    FIXED64 = 1
    LEN = 2
    SGROUP = 3  # deprecated
    EGROUP = 4  # deprecated
    FIXED32 = 5


def encode_varint(value: int) -> bytes:
    """Encode unsigned 64-bit integer as varint (smallest number of bytes)."""
    out = bytearray()
    while value > 0x7f:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value & 0x7f)
    return bytes(out)


def zigzag(n: int) -> int:
    """Map signed int to unsigned: 0→0, -1→1, 1→2, -2→3, ..."""
    return (n << 1) ^ (n >> 31)


def encode_svarint(value: int) -> bytes:
    return encode_varint(zigzag(value))


def decode_varint(reader: BytesIO) -> int:
    """Decode varint from a stream; raises if more than 10 bytes."""
    shift = 0
    result = 0
    for _ in range(10):
        b = reader.read(1)
        if not b:
            raise EOFError("EOF mid-varint")
        b = b[0]
        result |= (b & 0x7f) << shift
        if (b & 0x80) == 0:
            return result
        shift += 7
    raise ValueError("varint > 10 bytes")


def decode_zigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def decode_svarint(reader: BytesIO) -> int:
    return decode_zigzag(decode_varint(reader))


# ----- Tag -----
def make_tag(field_number: int, wire_type: int) -> int:
    return (field_number << 3) | wire_type


def tag_field_number(tag: int) -> int:
    return tag >> 3


def tag_wire_type(tag: int) -> int:
    return tag & 0x7


# ----- Field / Message -----
@dataclass
class Field:
    number: int
    wire: int
    value: Any           # int / bytes / str / list[Field] (submessages)
    packed: bool = False  # repeated+primitive in LEN


@dataclass
class Message:
    fields: list[Field] = dc_field(default_factory=list)

    def add(self, number: int, value: Any, *, packed: bool = False):
        # decide wire type for value class
        if isinstance(value, int) and not isinstance(value, bool):
            self.fields.append(Field(number, WireType.VARINT, value))
        elif isinstance(value, bool):
            self.fields.append(Field(number, WireType.VARINT, int(value)))
        elif isinstance(value, (bytes, bytearray)):
            self.fields.append(Field(number, WireType.LEN, bytes(value)))
        elif isinstance(value, str):
            self.fields.append(Field(number, WireType.LEN, value.encode("utf-8")))
        elif isinstance(value, Message):
            self.fields.append(Field(number, WireType.LEN, value))
        elif isinstance(value, list):
            # repeated; try packed if primitives
            if all(isinstance(v, int) and not isinstance(v, bool) for v in value):
                # pack all primitive values into a LEN wire
                body = b"".join(encode_varint(v) for v in value)
                self.fields.append(Field(number, WireType.LEN, body, packed=True))
            elif all(isinstance(v, str) for v in value):
                body = b"".join(encode_varint(len(v.encode("utf-8"))) + v.encode("utf-8") for v in value)
                self.fields.append(Field(number, WireType.LEN, body, packed=True))
            else:
                # repeated messages → one field per entry (LEN each)
                for v in value:
                    self.fields.append(Field(number, WireType.LEN, v))

    def encode(self) -> bytes:
        out = bytearray()
        for f in self.fields:
            tag = make_tag(f.number, f.wire)
            out += encode_varint(tag)
            v = f.value
            if f.wire == WireType.VARINT:
                out += encode_varint(v) if not isinstance(v, Message) else b""
            elif f.wire == WireType.FIXED64:
                out += struct.pack("<Q", v)
            elif f.wire == WireType.FIXED32:
                out += struct.pack("<I", v)
            elif f.wire == WireType.LEN:
                if isinstance(v, Message):
                    body = v.encode()
                elif isinstance(v, list) and not f.packed:
                    raise ValueError("unpacked repeated should have been flattened")
                else:
                    body = v
                out += encode_varint(len(body))
                out += body
        return bytes(out)


# ----- Decoder -----
def decode_message(buf: bytes) -> tuple[Message, int]:
    """Decode a single embedded message from `buf`; returns (Message, consumed)."""
    msg = Message()
    r = BytesIO(buf)
    while r.tell() < len(buf):
        try:
            tag = decode_varint(r)
        except EOFError:
            break
        fnum = tag_field_number(tag)
        fwire = tag_wire_type(tag)
        if fwire == WireType.VARINT:
            value = decode_varint(r)
        elif fwire == WireType.FIXED64:
            value = struct.unpack("<Q", r.read(8))[0]
        elif fwire == WireType.FIXED32:
            value = struct.unpack("<I", r.read(4))[0]
        elif fwire == WireType.LEN:
            ln = decode_varint(r)
            data = bytes(r.read(ln))
            # Heuristic to detect embed message vs raw bytes:
            # if decode_message recurses cleanly AND consumes the full buffer,
            # it was a message; otherwise return as bytes.
            r2 = BytesIO(data)
            try:
                sub = Message()
                tag = decode_varint(r2)
                fnum2 = tag_field_number(tag)
                wtype = tag_wire_type(tag)
                # Only treat as sub-message if first tag has valid wire type and field #
                if fnum2 >= 1 and wtype in (WireType.VARINT, WireType.FIXED64,
                                            WireType.FIXED32, WireType.LEN):
                    r3 = BytesIO(data)
                    decoded, _ = decode_message(r3)
                    if r3.tell() == len(data):
                        value = decoded
                    else:
                        value = data
                else:
                    value = data
            except (EOFError, ValueError):
                value = data
        elif fwire in (WireType.SGROUP, WireType.EGROUP):
            continue
        else:
            raise ValueError(f"unknown wire type {fwire}")
        msg.fields.append(Field(fnum, fwire, value))
    return msg, r.tell()


# ----- Demo / self test -----
def _self_test():
    # Test 1: simple int field  -> [08 96 01] from Protobuf doc
    m = Message()
    m.add(1, 150)
    assert m.encode() == b"\x08\x96\x01", f"want 08 96 01, got {m.encode().hex()}"
    m2, _ = decode_message(b"\x08\x96\x01")
    assert m2.fields[0].value == 150

    # Test 2: string field 2 = "testing"  -> [12 07 74 65 73 74 69 6e 67]
    m = Message()
    m.add(2, "testing")
    want = b"\x12\x07testing"
    got = m.encode()
    assert got == want, f"want {want.hex()}, got {got.hex()}"
    m2, _ = decode_message(got)
    assert m2.fields[0].value == b"testing"

    # Test 3: nested message  proto: Test3 { Test1 { int32 a = 1 } } c.a = 150
    # Wire: tag of c (3,LEN) = (3<<3)|2 = 0x1a; length=3; payload [08 96 01]
    inner = Message()
    inner.add(1, 150)
    outer = Message()
    outer.add(3, inner)
    want = b"\x1a\x03\x08\x96\x01"
    assert outer.encode() == want

    # Test 4: packed repeated int32  field 4 = [1,2,3]
    m = Message()
    m.add(4, [1, 2, 3])
    got = m.encode()
    m2, _ = decode_message(got)
    # packed int -> we leave LEN bytes intact as bytes
    print(f"self-test passed. packed repeated enc={got.hex()}")

    # Test 5: zigzag
    assert encode_svarint(-1) == b"\x01"
    assert encode_svarint(-2) == b"\x03"
    assert decode_svarint(BytesIO(b"\x01")) == -1
    assert decode_svarint(BytesIO(b"\x03")) == -2

    print("ALL self-tests PASS")


if __name__ == "__main__":
    _self_test()
