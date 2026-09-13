#!/usr/bin/env python3
"""TLV(Type-Length-Value)编解码器。

简化版 ASN.1 BER 风格:
  - Type: 2 字节大端 (uint16);universal 类(0) + primitive(0) + 数字 [0..65535]
  - Length: 可变长整数,首字节 < 0x80 表示短格式(直接是长度),否则首字节 & 0x7F 表示
            后续长度字节数(big-endian)。0x80 = 不定长(indefinite);本 demo 不实现
  - Value: 紧跟 Length 个字节

示例数据(每条记录):type=1(Name), type=2(Age), type=3(Email)
"""
import struct
import sys
from dataclasses import dataclass, field
from typing import List


class TLVDecodeError(Exception):
    pass


@dataclass
class TLV:
    tag: int
    value: bytes

    def encode(self) -> bytes:
        return encode_tlv(self.tag, self.value)

    def __repr__(self):
        return f"TLV(tag={self.tag}, value={self.value!r})"


def encode_length(n: int) -> bytes:
    """BER 长格式:< 0x80 短形式;>= 0x80 首字节低 7 位为长度字节数 + BE 字节。"""
    if n < 0:
        raise ValueError("length must be non-negative")
    if n < 0x80:
        return bytes([n])
    # 长形式:首字节 0x80|num_bytes,后续 num_bytes 个大端字节
    bs = []
    while n > 0:
        bs.insert(0, n & 0xFF)
        n >>= 8
    return bytes([0x80 | len(bs)]) + bytes(bs)


def decode_length(buf: bytes, pos: int) -> tuple[int, int]:
    """返回 (length, new_pos)。buf[pos] 是长度首字节。"""
    if pos >= len(buf):
        raise TLVDecodeError("decode_length: out of range")
    first = buf[pos]
    if first < 0x80:
        return first, pos + 1
    if first == 0x80:
        raise TLVDecodeError("indefinite length not supported")
    num_bytes = first & 0x7F
    if num_bytes == 0 or pos + 1 + num_bytes > len(buf):
        raise TLVDecodeError(f"bad long-form length byte count {num_bytes}")
    n = 0
    for i in range(1, num_bytes + 1):
        n = (n << 8) | buf[pos + i]
    return n, pos + 1 + num_bytes


def encode_tlv(tag: int, value: bytes) -> bytes:
    if tag < 0 or tag > 0xFFFF:
        raise ValueError("tag must fit in uint16")
    return struct.pack(">H", tag) + encode_length(len(value)) + value


def decode_tlv(buf: bytes, pos: int = 0) -> tuple[TLV, int]:
    """从 buf[pos] 解一条 TLV;返回 (TLV, new_pos)。"""
    if pos + 2 > len(buf):
        raise TLVDecodeError("decode_tlv: not enough bytes for tag")
    tag = struct.unpack(">H", buf[pos:pos + 2])[0]
    pos += 2
    length, pos = decode_length(buf, pos)
    if pos + length > len(buf):
        raise TLVDecodeError(f"decode_tlv: value of length {length} out of range")
    value = bytes(buf[pos:pos + length])
    return TLV(tag=tag, value=value), pos + length


def decode_all(buf: bytes) -> List[TLV]:
    out: List[TLV] = []
    pos = 0
    while pos < len(buf):
        tlv, pos = decode_tlv(buf, pos)
        out.append(tlv)
    return out


# ================= 自测 =================
def _hex(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b)


def self_test() -> None:
    print("=== TLV codec self-test ===")

    # Case 1: 单条短格式
    e = encode_tlv(0x0001, b"hello")
    # type=00 01, len=05, value='hello'  → 00 01 05 68 65 6c 6c 6f
    assert e == b"\x00\x01\x05hello", f"got {_hex(e)}"
    t, p = decode_tlv(e)
    assert t.tag == 1 and t.value == b"hello" and p == len(e)
    print(f"  [1] short-form OK  hex={_hex(e)}  parsed={t}")

    # Case 2: 多条拼接 + 大 value 触发长格式 length
    records = [
        TLV(tag=1, value=b"alice"),                       # short len=5
        TLV(tag=2, value=struct.pack(">H", 30)),          # age 30, len=2
        TLV(tag=3, value=b"alice@example.com"),           # len=17
        TLV(tag=4, value=b"x" * 200),                     # len=200 → 0x81 0xC8
    ]
    blob = b"".join(r.encode() for r in records)
    # 第 4 条的 length 是 0x81 0xC8
    head = blob[:5 + 7 + 6 + 21]  # 跳过前 3 条 = 5+7+6+21 字节
    print(f"  [2] four TLVs total bytes={len(blob)}  blob-head={_hex(blob[:30])}")
    # decode round-trip
    decoded = decode_all(blob)
    assert len(decoded) == 4
    assert decoded[0] == records[0]
    assert decoded[1].value == struct.pack(">H", 30)
    assert decoded[2] == records[2]
    assert decoded[3] == records[3]
    print(f"  [3] round-trip OK  parsed={len(decoded)} items")

    # Case 3: 极长 length 触发 2 字节长度字节
    big = TLV(tag=0x1234, value=b"y" * 300)
    enc = big.encode()
    # type=12 34, len=0x82 0x01 0x2C(300), value='y'*300
    assert enc[:4] == b"\x12\x34\x82\x01\x2c"[:4] + b""
    # 完整: 12 34 82 01 2C 79...
    print(f"  [4] long-form length OK  first6={_hex(enc[:6])}  total={len(enc)}B")

    # Case 4: 解析空 value
    e = encode_tlv(7, b"")
    assert e == b"\x00\x07\x00"
    t, p = decode_tlv(e)
    assert t.value == b""
    print(f"  [5] empty value OK  hex={_hex(e)}")

    # Case 5: 错位 / 截断检测
    try:
        decode_tlv(b"\x00\x01\xff")
        raise AssertionError("应抛异常")
    except TLVDecodeError as ex:
        print(f"  [6] truncated length detected: {ex}")

    print("all self-tests passed.\n")


# ================= 业务示例:Person 序列化 =================
@dataclass
class Person:
    name: str = ""
    age: int = 0
    email: str = ""

    _TAG_NAME = 1
    _TAG_AGE = 2
    _TAG_EMAIL = 3

    def encode(self) -> bytes:
        return b"".join([
            encode_tlv(self._TAG_NAME, self.name.encode("utf-8")),
            encode_tlv(self._TAG_AGE, struct.pack(">H", self.age)),
            encode_tlv(self._TAG_EMAIL, self.email.encode("utf-8")),
        ])

    @classmethod
    def decode(cls, buf: bytes) -> "Person":
        p = cls()
        for tlv in decode_all(buf):
            if tlv.tag == cls._TAG_NAME:
                p.name = tlv.value.decode("utf-8")
            elif tlv.tag == cls._TAG_AGE:
                p.age = struct.unpack(">H", tlv.value)[0]
            elif tlv.tag == cls._TAG_EMAIL:
                p.email = tlv.value.decode("utf-8")
            else:
                print(f"  unknown tag={tlv.tag}, skipped (forward compat)")
        return p


def demo_person() -> None:
    print("=== Person serialization demo ===")
    p1 = Person(name="张三", age=28, email="zhang@example.com")
    blob = p1.encode()
    print(f"  encoded {len(blob)} bytes: {_hex(blob)}")
    p2 = Person.decode(blob)
    assert p1.name == p2.name and p1.age == p2.age and p1.email == p2.email
    print(f"  decoded: name={p2.name!r} age={p2.age} email={p2.email!r}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "test":  # type: ignore
        self_test()
        demo_person()
    else:
        print("usage: python3 main.py test")
        sys.exit(1)