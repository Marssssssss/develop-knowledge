"""
RPKI 对象的最小 DER 编解码（ASN.1 Distinguished Encoding Rules）

RPKI 的 ROA 是「CMS SignedData 包裹的 DER 编码对象」：外层是 CMS（RFC 5652/6488），
内层 eContent 是 ASN.1 DER 编码的 RouteOriginAttestation（RFC 6482 §3）。
要读懂 ROA 就必须能读 DER，而 DER 的规则很简单、却有几个「不许」：

  - 长度必须用**最短形式**（<128 用短形式，否则 0x81/0x82… 加长度字节）
  - 整数必须最短补码：最高位为 1 时才补 0x00
  - **不许**用不定长（0x80 结尾 0x00 0x00）—— 那是 BER，DER 明确禁止
  - SEQUENCE / SET / 各原语只用构造式或原语式中的一种（DER 进一步收紧）

本文件只实现 RPKI 需要的子集：SEQUENCE / INTEGER / BIT STRING / OCTET STRING / OID。
"""

from __future__ import annotations


class DerError(ValueError):
    """DER 结构非法（长度非最短、越界、标签不符等）"""


# ------------------------------------------------------------
# 1. 编码
# ------------------------------------------------------------

def encode_length(n: int) -> bytes:
    """DER 的长度字段：<128 用 1 字节短形式，否则 0x81/0x82… + 大端长度。"""
    if n < 0:
        raise DerError("negative length")
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    if len(raw) > 4:
        raise DerError("length too large")
    return bytes([0x80 | len(raw)]) + raw


def tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + encode_length(len(content)) + content


def der_sequence(*parts: bytes) -> bytes:
    return tlv(0x30, b"".join(parts))


def der_integer(value: int) -> bytes:
    """最短补码：只有最高位为 1（会被误读成负数）时才补一个 0x00。"""
    if value == 0:
        return tlv(0x02, b"\x00")
    if value < 0:
        raise DerError("DER 中 RPKI 的整数都是有符号非负值，本实现不支持负数")
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return tlv(0x02, raw)


def der_octet_string(data: bytes) -> bytes:
    return tlv(0x04, data)


def der_bit_string(data: bytes, unused_bits: int = 0) -> bytes:
    """BIT STRING 的内容多一个「未使用位数」字节（DER 要求未使用位必须是 0）。"""
    if not 0 <= unused_bits <= 7:
        raise DerError("unused bits out of range")
    if unused_bits and (data[-1] & ((1 << unused_bits) - 1)):
        raise DerError("DER 要求未使用的位必须为 0")
    return tlv(0x03, bytes([unused_bits]) + data)


def der_oid(dotted: str) -> bytes:
    """OID：前两个分量的合并值用 base-128 编码，其余分量各自 base-128（首位是续位标志）。"""
    parts = [int(x) for x in dotted.split(".")]
    if len(parts) < 2 or parts[0] > 2 or (parts[0] < 2 and parts[1] > 39):
        raise DerError("invalid OID arcs")
    body = bytearray(_base128(parts[0] * 40 + parts[1]))
    for p in parts[2:]:
        body += _base128(p)
    return tlv(0x06, bytes(body))


def _base128(value: int) -> bytes:
    if value < 0:
        raise DerError("negative arc")
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def der_null() -> bytes:
    return tlv(0x05, b"")


# ------------------------------------------------------------
# 2. 解码（严格模式：违 DER 即报错，不学 BER 的宽容）
# ------------------------------------------------------------

class DerReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    @property
    def eof(self) -> bool:
        return self.pos >= len(self.data)

    def _read_length(self) -> int:
        first = self._byte()
        if first < 0x80:
            return first
        count = first & 0x7F
        if count == 0:
            raise DerError("DER 禁止不定长（0x80）")
        if count > 4:
            raise DerError("length too large")
        raw = self.take(count)
        if raw[0] == 0:
            raise DerError("长度不是最短形式")
        n = int.from_bytes(raw, "big")
        if n < 0x80:
            raise DerError("长度不是最短形式")
        return n

    def _byte(self) -> int:
        if self.eof:
            raise DerError("truncated")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise DerError("truncated")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def read_tlv(self, expect_tag: int | None = None) -> tuple[int, bytes]:
        tag = self._byte()
        if expect_tag is not None and tag != expect_tag:
            raise DerError(f"tag 0x{tag:02x} != 0x{expect_tag:02x}")
        return tag, self.take(self._read_length())

    def read_integer(self) -> int:
        _, content = self.read_tlv(0x02)
        if not content:
            raise DerError("empty INTEGER")
        if len(content) > 1 and content[0] == 0x00 and not content[1] & 0x80:
            raise DerError("INTEGER 不是最短编码")
        return int.from_bytes(content, "big", signed=True)

    def read_octet_string(self) -> bytes:
        return self.read_tlv(0x04)[1]

    def read_bit_string(self) -> tuple[bytes, int]:
        _, content = self.read_tlv(0x03)
        if not content:
            raise DerError("empty BIT STRING")
        unused = content[0]
        if unused > 7:
            raise DerError("unused bits out of range")
        return content[1:], unused

    def read_oid(self) -> str:
        _, content = self.read_tlv(0x06)
        if not content:
            raise DerError("empty OID")
        arcs, value = [], 0
        for i, b in enumerate(content):
            value = (value << 7) | (b & 0x7F)
            if not b & 0x80:
                if not arcs:
                    arcs += [min(value // 40, 2), value - min(value // 40, 2) * 40]
                else:
                    arcs.append(value)
                value = 0
            elif i == len(content) - 1:
                raise DerError("OID 以续位结束")
        return ".".join(str(a) for a in arcs)
