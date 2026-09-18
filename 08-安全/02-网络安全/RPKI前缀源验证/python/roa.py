"""
ROA（Route Origin Authorization）与 RFC 6811 前缀源验证

链路：持有者签发 ROA（RFC 6482，DER 编码的「AS + 前缀 + 最大长度」三元组列表）
  → 依赖方验证签名后把 ROA 展开成 VRP（Validated ROA Payload）
  → BGP 路由器用 VRP 库给每条路由打上 NotFound / Valid / Invalid（RFC 6811）

本文件不碰签名验证（那需要完整 CMS + X.509 路径验证），只实现**数据模型、DER 编解码、
VRP 展开与三态验证**——也就是「拿到已验签的 ROA 之后到底怎么判」的部分。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rpki_der import (DerError, DerReader, der_bit_string, der_integer,
                      der_octet_string, der_sequence, tlv)

# AFI：RFC 6482 §3.3 规定 addressFamily 只能是 0001（IPv4）或 0002（IPv6）
AFI_IPV4 = bytes([0x00, 0x01])
AFI_IPV6 = bytes([0x00, 0x02])
ADDR_BITS = {1: 32, 2: 128}

# RFC 6811 §2 的三个验证状态（另有「未做验证」时 SHOULD 初始化为 NotFound）
NOT_FOUND = "NotFound"
VALID = "Valid"
INVALID = "Invalid"


@dataclass(frozen=True)
class Prefix:
    """(地址, 前缀长度) —— 地址按整数保存，长度单位是位。"""

    afi: int
    address: int
    length: int

    def __post_init__(self) -> None:
        limit = ADDR_BITS[self.afi]
        if not 0 <= self.length <= limit:
            raise ValueError(f"prefix length {self.length} out of range for AFI {self.afi}")
        if self.address >> limit or self.address < 0:
            raise ValueError("address out of range")

    @property
    def bits(self) -> int:
        return ADDR_BITS[self.afi]

    def contains(self, other: "Prefix") -> bool:
        """RFC 6811 §2 的 Covered：VRP 前缀不更长，且其指定的所有位与路由前缀相同。"""
        if self.afi != other.afi or self.length > other.length:
            return False
        shift = self.bits - self.length
        return (self.address >> shift) == (other.address >> shift)

    def to_bytes(self) -> bytes:
        """BIT STRING 的内容：前缀位**左对齐**，低位补零到整字节。

        注意不能写成「右移 (位数-长度) 再取字节」—— 那只在长度是 8 的倍数时才巧合正确。
        例：203.0.113.0/26 的前 26 位是 `11001011 00000000 01110001 00`，
        BIT STRING 内容是 CB 00 71 00（4 字节、6 个未使用位），
        而右移 6 位会得到 03 2C 01 C4 —— 完全不同的字节串。
        """
        nbytes = (self.length + 7) // 8
        if self.length == 0:
            return b""
        masked = self.address & ~((1 << (self.bits - self.length)) - 1)
        return masked.to_bytes(self.bits // 8, "big")[:nbytes]

    def __str__(self) -> str:
        return f"{_fmt(self)}/{self.length}"


def _fmt(p: Prefix) -> str:
    if p.afi == 1:
        return ".".join(str((p.address >> s) & 0xFF) for s in (24, 16, 8, 0))
    groups = [(p.address >> s) & 0xFFFF for s in range(112, -1, -16)]
    best = -1
    for i in range(8):
        if groups[i] != 0:
            best = i
    return ":".join(f"{g:x}" for g in groups[:best + 1]) + "::" * (best < 7)


def parse_prefix(text: str) -> Prefix:
    """支持 203.0.113.0/24 与 2001:db8::/32 两种写法。"""
    addr_text, _, len_text = text.partition("/")
    length = int(len_text) if len_text else None
    if ":" in addr_text:
        address, afi = _parse_v6(addr_text), 2
    else:
        address, afi = _parse_v4(addr_text), 1
    return Prefix(afi, address, ADDR_BITS[afi] if length is None else length)


def _parse_v4(text: str) -> int:
    octets = [int(x) for x in text.split(".")]
    if len(octets) != 4 or any(not 0 <= o <= 255 for o in octets):
        raise ValueError(f"bad IPv4 address: {text}")
    value = 0
    for o in octets:
        value = value << 8 | o
    return value


def _parse_v6(text: str) -> int:
    head, sep, tail = text.partition("::")
    groups = [int(g, 16) for g in head.split(":") if g]
    back = [int(g, 16) for g in tail.split(":") if g]
    if sep:
        fill = 8 - len(groups) - len(back)
        if fill < 0:
            raise ValueError(f"bad IPv6 address: {text}")
        groups += [0] * fill
    groups += back
    if len(groups) != 8:
        raise ValueError(f"bad IPv6 address: {text}")
    value = 0
    for g in groups:
        value = value << 16 | g
    return value


# ------------------------------------------------------------
# 1. ROA 的数据模型与 DER 编解码
# ------------------------------------------------------------

@dataclass
class RoaAddress:
    prefix: Prefix
    max_length: int | None = None

    @property
    def effective_max_length(self) -> int:
        """RFC 6482 §3.3：缺省时只授权**精确**前缀，不授权任何更具体的。"""
        return self.prefix.length if self.max_length is None else self.max_length


@dataclass
class Roa:
    asn: int
    addresses: list[RoaAddress] = field(default_factory=list)
    version: int = 0

    def problems(self) -> list[str]:
        """检查 RFC 6482 对字段取值的硬约束（不合法 ROA 不能进 VRP 库）。"""
        issues = []
        if self.version != 0:
            issues.append(f"version 必须为 0，实际 {self.version}")
        if not 0 <= self.asn <= 0xFFFFFFFF:
            issues.append("asID 超出 4 字节范围")
        if not self.addresses:
            issues.append("ipAddrBlocks 不能为空")
        for a in self.addresses:
            limit = ADDR_BITS[a.prefix.afi]
            if a.max_length is not None:
                if a.max_length < a.prefix.length:
                    issues.append(f"{a.prefix} 的 maxLength 小于前缀长度")
                if a.max_length > limit:
                    issues.append(f"{a.prefix} 的 maxLength 超过 AFI 位宽 {limit}")
        return issues

    def encode_content(self) -> bytes:
        """eContent：DER 编码的 RouteOriginAttestation（RFC 6482 §3）。"""
        blocks = []
        for afi in (1, 2):
            addrs = [a for a in self.addresses if a.prefix.afi == afi]
            if not addrs:
                continue
            encoded = []
            for a in addrs:
                unused = (8 - a.prefix.length % 8) % 8
                parts = [der_bit_string(a.prefix.to_bytes(), unused)]
                if a.max_length is not None:
                    parts.append(der_integer(a.max_length))
                encoded.append(der_sequence(*parts))
            blocks.append(der_sequence(der_octet_string(AFI_IPV4 if afi == 1 else AFI_IPV6),
                                       der_sequence(*encoded)))
        fields = []
        # version 带 DEFAULT 0：DER 规定「等于默认值时必须省略」。
        # RFC 6482 附录 A 的模块声明是 `DEFINITIONS EXPLICIT TAGS`，
        # 所以 [0] 是**显式**标签：A0 03 02 01 01（外面套一层，而不是替换 INTEGER 的标签）。
        if self.version != 0:
            fields.append(tlv(0xA0, der_integer(self.version)))
        fields += [der_integer(self.asn), der_sequence(*blocks)]
        return der_sequence(*fields)

    @staticmethod
    def decode_content(data: bytes) -> "Roa":
        r = DerReader(data)
        _, content = r.read_tlv(0x30)
        r = DerReader(content)
        version = 0
        if not r.eof and r.data[r.pos] == 0xA0:
            _, inner = r.read_tlv(0xA0)
            version = DerReader(inner).read_integer()
        asn = r.read_integer()
        _, blocks_raw = r.read_tlv(0x30)
        br = DerReader(blocks_raw)
        addresses: list[RoaAddress] = []
        while not br.eof:
            _, fam_raw = br.read_tlv(0x30)
            fr = DerReader(fam_raw)
            # addressFamily 是 2 字节的 AFI 原文（0001/0002），不要当成填充字节砍掉
            afi_bytes = fr.read_octet_string()
            if afi_bytes not in (AFI_IPV4, AFI_IPV6):
                raise ValueError(f"不支持的 addressFamily: {afi_bytes.hex()}")
            afi = 1 if afi_bytes == AFI_IPV4 else 2
            _, addr_raw = fr.read_tlv(0x30)
            ar = DerReader(addr_raw)
            while not ar.eof:
                _, one_raw = ar.read_tlv(0x30)
                nr = DerReader(one_raw)
                bits, unused = nr.read_bit_string()
                length = len(bits) * 8 - unused
                # 内容左对齐：还原成完整位宽的地址要把整字节串推回高位
                shift = ADDR_BITS[afi] - 8 * len(bits)
                if shift < 0:
                    raise ValueError("BIT STRING 比地址族还长")
                address = int.from_bytes(bits, "big") << shift
                prefix = Prefix(afi, address, length)
                max_length = nr.read_integer() if not nr.eof else None
                addresses.append(RoaAddress(prefix, max_length))
        return Roa(asn=asn, addresses=addresses, version=version)

    def to_vrps(self) -> list["Vrp"]:
        """一个 ROAIPAddress 展开成恰好一条 VRP。"""
        if self.problems():
            raise ValueError("非法 ROA 不能展开为 VRP")
        return [Vrp(a.prefix, a.effective_max_length, self.asn) for a in self.addresses]


@dataclass(frozen=True)
class Vrp:
    prefix: Prefix
    max_length: int
    asn: int


# ------------------------------------------------------------
# 2. RFC 6811 源验证（三态）
# ------------------------------------------------------------

def validate(vrps: list[Vrp], route_prefix: Prefix, origin_asn: int | None) -> str:
    """route_prefix 是被验证的公告前缀；origin_asn 为 None 表示规范里的 NONE。

    直接照搬 RFC 6811 §2.1 的伪代码：遍历所有 Covered 的 VRP，
    只有「路由前缀长度 ≤ VRP 最大长度」且「源 AS 相等且双方都不为 0/NONE」才算 Matched。
    """
    covered = False
    for vrp in vrps:
        if not vrp.prefix.contains(route_prefix):
            continue
        covered = True
        if route_prefix.length <= vrp.max_length and origin_asn is not None and \
                vrp.asn != 0 and origin_asn == vrp.asn:
            return VALID
    return INVALID if covered else NOT_FOUND
