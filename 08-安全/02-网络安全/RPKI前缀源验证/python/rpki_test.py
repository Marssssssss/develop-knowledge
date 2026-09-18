"""
RPKI 前缀源验证自检：期望值来自 RFC 与可推导的 DER 编码规则。

对照点：
  RFC 6482 §3     ROA 的 ASN.1 结构（version [0] DEFAULT 0 / asID / ipAddrBlocks）
  RFC 6482 §3.3   maxLength 的取值范围与「缺省即只授权精确前缀」
  RFC 6482 §3.3   例子：203.0.113/24 + maxLength 26 授权 /24 与 /25，不授权 /27
  RFC 6811 §2     VRP / Covered / Matched 的定义与 NotFound/Valid/Invalid 三态
  RFC 6811 §2.1   验证伪代码
  RFC 6811 §2     Route Origin ASN 的四种来源（含 NONE）
  RFC 6483 之外的 OID 用法见 RFC 6482 §4（id-ct-routeOriginAuthz）

DER 部分的期望值是**按规则手推**的（下面逐字节注明来源），不是抄来的：
  - OID 1.2.840.113549.1.9.16.1.24 的编码 = 06 0B 2A 86 48 86 F7 0D 01 09 10 01 18
  - 203.0.113/24 的 BIT STRING = 03 04 00 CB 00 71（unused=0，地址 CB 00 71）
"""

from __future__ import annotations

import sys

from bgp_origin import (AS_CONFED_SEQUENCE, AS_SEQUENCE, AS_SET, NONE,
                        as_path_text, asn_problems, origin_asn)
from roa import (AFI_IPV4, INVALID, NOT_FOUND, VALID, Roa, RoaAddress,
                 parse_prefix, validate)
from rpki_der import (DerError, DerReader, der_bit_string, der_integer,
                      der_octet_string, der_oid, der_sequence, encode_length)

#  RFC 6482 §4 的 ROA 内容类型 OID
ROA_CONTENT_TYPE_OID = "1.2.840.113549.1.9.16.1.24"
OID_DER = "060b2a864886f70d0109100118"


def check_der() -> int:
    checks = 0
    assert der_oid(ROA_CONTENT_TYPE_OID).hex() == OID_DER
    # 1.2 → 42；840 → 86 48；113549 → 86 F7 0D；然后每个分量 1 字节
    assert der_oid("2.5.29.24").hex() == "0603551d18"
    # 常见 X.509 算法 OID：1.2.840.113549.1.1.11 → 06 09 2A 86 48 86 F7 0D 01 01 0B
    assert der_oid("1.2.840.113549.1.1.11").hex() == "06092a864886f70d01010b"
    # 大分量要用 base-128 续位：2.999 → 2*40+999 = 1079 = 0b10000110111
    #   → 低 7 位 0x37，余 8 → 0x88，故为 88 37
    assert der_oid("2.999").hex() == "06028837"
    checks += 3

    assert der_integer(0).hex() == "020100"
    assert der_integer(26).hex() == "02011a"
    assert der_integer(64496).hex() == "020300fbf0"      # 0xFB 最高位为 1 → 补 0x00
    assert der_integer(127).hex() == "02017f"            # 0x7F 不必补
    assert der_integer(128).hex() == "02020080"
    assert DerReader(der_integer(64496)).read_integer() == 64496
    checks += 6

    # /24 的 BIT STRING：unused = 0，内容即 203.0.113 = CB 00 71
    assert der_bit_string(parse_prefix("203.0.113.0/24").to_bytes(), 0).hex() == \
        "030400cb0071"
    # /26：4 字节 + 6 个未使用位（BIT STRING 内容首字节就是「未使用位数」= 06）
    assert der_bit_string(parse_prefix("203.0.113.0/26").to_bytes(), 6).hex() == \
        "030506cb007100"
    # 203.0.113.192/26 → 末字节 C0，低 6 位为 0，才满足 DER 对未使用位的要求
    assert der_bit_string(parse_prefix("203.0.113.192/26").to_bytes(), 6).hex() == \
        "030506cb0071c0"
    # 非 8 倍数位宽的前缀必须左对齐（曾误写为「右移后取字节」）
    assert parse_prefix("203.0.113.0/26").to_bytes().hex() == "cb007100"
    assert parse_prefix("203.0.113.128/25").to_bytes().hex() == "cb007180"
    checks += 4
    try:
        der_bit_string(b"\x01", 7)          # 末位为 1 却不是 unused 位
        raise AssertionError("非零 unused 位必须被拒绝")
    except DerError:
        checks += 1

    # 长度形式：127 用短形式，128 起用 0x81，256 起用 0x82
    assert encode_length(127).hex() == "7f"
    assert encode_length(128).hex() == "8180"
    assert encode_length(256).hex() == "820100"
    checks += 3

    # 解码侧必须拒绝 BER 的宽松写法
    for bad, why in ((bytes.fromhex("308003000000"), "不定长"),
                     (bytes.fromhex("02810101"), "长度非最短"),
                     (bytes.fromhex("02820001 01".replace(" ", "")), "长度非最短(长形式)")):
        try:
            r = DerReader(bad)
            r.read_tlv(0x30) if why == "不定长" else r.read_integer()
            raise AssertionError(f"{why} 必须被拒绝")
        except DerError:
            checks += 1
    try:
        DerReader(bytes.fromhex("02020001")).read_integer()   # 0x00 0x01 不是最短
        raise AssertionError("非最短 INTEGER 必须被拒绝")
    except DerError:
        checks += 1
    return checks


def _sample_roa() -> Roa:
    return Roa(asn=64496, addresses=[
        RoaAddress(parse_prefix("203.0.113.0/24"), 26),
    ])


def check_roa_der() -> int:
    checks = 0
    roa = _sample_roa()
    assert roa.problems() == []
    checks += 1

    # 逐字节手推：30 1A | 02 03 00 FB F0 | 30 13 | 30 11 | 04 02 00 01 | 30 0B |
    #            30 09 | 03 04 00 CB 00 71 | 02 01 1A
    expect = ("301a" "020300fbf0" "3013" "3011" "04020001" "300b"
              "3009" "030400cb0071" "02011a")
    assert roa.encode_content().hex() == expect
    checks += 1

    back = Roa.decode_content(bytes.fromhex(expect))
    assert back.asn == 64496 and back.version == 0
    assert len(back.addresses) == 1
    got = back.addresses[0]
    assert str(got.prefix) == "203.0.113.0/24" and got.max_length == 26
    assert got.prefix.afi == 1 and got.prefix.length == 24
    checks += 5
    assert back.encode_content() == roa.encode_content()      # 编解码往返一致
    checks += 1

    # 缺省 maxLength：DER 里就该没有 INTEGER，且只能授权精确前缀
    exact = Roa(asn=64496, addresses=[RoaAddress(parse_prefix("198.51.100.0/24"), None)])
    assert "02011a" not in exact.encode_content().hex()
    assert exact.addresses[0].effective_max_length == 24
    assert Roa.decode_content(exact.encode_content()).addresses[0].max_length is None
    checks += 3

    # 双 AFI：v4 与 v6 各自成块，v4 块在前
    both = Roa(asn=64496, addresses=[
        RoaAddress(parse_prefix("2001:db8::/32"), 48),
        RoaAddress(parse_prefix("203.0.113.0/24"), 26),
    ])
    assert both.problems() == []
    both_der = both.encode_content().hex()
    assert both_der.index("04020001") < both_der.index("04020002")
    assert len(Roa.decode_content(bytes.fromhex(both_der)).addresses) == 2
    assert [str(a.prefix) for a in Roa.decode_content(bytes.fromhex(both_der)).addresses] == \
        ["203.0.113.0/24", "2001:db8::/32"]
    checks += 4

    # 非法取值的 ROA 必须拒绝展开：version != 0、maxLength 越界、空 ipAddrBlocks
    bad_version = Roa(asn=1, addresses=[RoaAddress(parse_prefix("10.0.0.0/8"), 8)],
                      version=1)
    assert bad_version.problems() and len(bad_version.problems()) == 1
    # RFC 6482 附录 A 是 EXPLICIT TAGS：version [0] INTEGER 编码成 A0 03 02 01 01
    # （外层是 30 + 1 字节长度 + 内容，所以内容从 hex 的第 5 个字符开始）
    assert bad_version.encode_content().hex()[4:14] == "a003020101"
    assert Roa.decode_content(bad_version.encode_content()).version == 1
    checks += 3
    too_long = Roa(asn=1, addresses=[RoaAddress(parse_prefix("10.0.0.0/8"), 33)])
    too_short = Roa(asn=1, addresses=[RoaAddress(parse_prefix("10.0.0.0/24"), 16)])
    empty = Roa(asn=1, addresses=[])
    assert too_long.problems() and too_short.problems() and empty.problems()
    for bad in (too_long, too_short, empty):
        try:
            bad.to_vrps()
            raise AssertionError("非法 ROA 不得展开为 VRP")
        except ValueError:
            checks += 1
    return checks


def check_origin_asn() -> int:
    checks = 0
    assert origin_asn([(AS_SEQUENCE, [65001, 65002])], 65000) == 65002
    assert origin_asn([], 65000) == 65000                       # AS_PATH 为空 → 本机 AS
    assert origin_asn([(AS_CONFED_SEQUENCE, [64512])], 65000) == 65000
    assert origin_asn([(AS_SEQUENCE, [65001]), (AS_SET, [65002, 65003])], 65000) is NONE
    assert origin_asn([(AS_SEQUENCE, [4200000000])], 65000) == 4200000000  # 4 字节 AS
    checks += 5
    assert as_path_text([(AS_SEQUENCE, [65001, 65002]), (AS_SET, [65003, 65004])]) == \
        "65001 65002 {65003 65004}"
    checks += 1
    assert asn_problems(0)                      # 保留值
    assert asn_problems(0xFFFFFFFF + 1)         # 越界
    assert asn_problems(64496) == []            # 正常 AS
    assert asn_problems(23456)                  # AS_TRANS 不该是有效源 AS
    checks += 4
    return checks


def check_rfc6811() -> int:
    checks = 0
    # RFC 6482 §3.3 的例子：203.0.113/24 + maxLength 26
    roa = _sample_roa()
    vrps = roa.to_vrps()
    assert len(vrps) == 1 and vrps[0].max_length == 26 and vrps[0].asn == 64496
    checks += 1

    # 授权：精确前缀、任意 /25、任意 /26（maxLength 26 允许 /24 内**所有** /26）
    for ok in ("203.0.113.0/24", "203.0.113.0/25", "203.0.113.128/25",
               "203.0.113.0/26", "203.0.113.64/26", "203.0.113.192/26"):
        assert validate(vrps, parse_prefix(ok), 64496) == VALID, ok
        checks += 1
    # 过长（被覆盖但不被授权）→ Invalid
    for bad in ("203.0.113.0/27", "203.0.113.64/27", "203.0.113.0/32"):
        assert validate(vrps, parse_prefix(bad), 64496) == INVALID, bad
        checks += 1
    # 未被任何 VRP 覆盖 → NotFound
    for nf in ("203.0.114.0/24", "203.0.112.0/24", "10.0.0.0/8"):
        assert validate(vrps, parse_prefix(nf), 64496) == NOT_FOUND, nf
        checks += 1
    # 覆盖但源 AS 不对 → Invalid
    assert validate(vrps, parse_prefix("203.0.113.0/24"), 65000) == INVALID
    # 源 AS 为 NONE → 永远不可能 Matched
    assert validate(vrps, parse_prefix("203.0.113.0/24"), NONE) == INVALID
    # AS 0 的 VRP 永远不匹配（RFC 6811 §2 的明文观察）
    zero_vrp = Roa(asn=0, addresses=[RoaAddress(parse_prefix("203.0.113.0/24"), 26)]).to_vrps()
    assert validate(zero_vrp, parse_prefix("203.0.113.0/24"), 0) == INVALID
    checks += 4

    # maxLength 缺省 → 只授权精确前缀
    exact = Roa(asn=64496, addresses=[
        RoaAddress(parse_prefix("198.51.100.0/24"), None)]).to_vrps()
    assert validate(exact, parse_prefix("198.51.100.0/24"), 64496) == VALID
    assert validate(exact, parse_prefix("198.51.100.0/25"), 64496) == INVALID
    checks += 2

    # 一条 ROA 内前缀互相包含（RFC 6482 §3.3 明确允许）：判定仍然是确定的
    multi = Roa(asn=64496, addresses=[
        RoaAddress(parse_prefix("203.0.113.0/24"), 26),
        RoaAddress(parse_prefix("203.0.113.0/28"), 28)]).to_vrps()
    assert len(multi) == 2
    assert validate(multi, parse_prefix("203.0.113.0/28"), 64496) == VALID
    assert validate(multi, parse_prefix("203.0.113.0/27"), 64496) == INVALID
    checks += 3

    # 多条 ROA 的合并效果：只要有一条 Matched 就是 Valid，与遍历顺序无关
    a = Roa(asn=64496, addresses=[RoaAddress(parse_prefix("203.0.113.0/24"), 24)]).to_vrps()
    b = Roa(asn=65000, addresses=[RoaAddress(parse_prefix("203.0.113.0/24"), 26)]).to_vrps()
    assert validate(a + b, parse_prefix("203.0.113.0/25"), 65000) == VALID
    assert validate(b + a, parse_prefix("203.0.113.0/25"), 65000) == VALID
    assert validate(a + b, parse_prefix("203.0.113.0/25"), 64496) == INVALID
    checks += 3

    # IPv6 与「前缀长度跨族比较」：v6 的 VRP 不可能覆盖 v4 的路由
    v6 = Roa(asn=64496, addresses=[
        RoaAddress(parse_prefix("2001:db8::/32"), 48)]).to_vrps()
    assert validate(v6, parse_prefix("2001:db8:1::/48"), 64496) == VALID
    assert validate(v6, parse_prefix("2001:db8:1::/49"), 64496) == INVALID
    assert validate(v6, parse_prefix("2001:db9::/32"), 64496) == NOT_FOUND
    assert validate(v6, parse_prefix("203.0.113.0/24"), 64496) == NOT_FOUND
    checks += 4

    # Covered 的位比较：/25 的两个半区互不覆盖
    p = parse_prefix("203.0.113.0/25")
    assert p.contains(parse_prefix("203.0.113.0/26"))
    assert not p.contains(parse_prefix("203.0.113.128/26"))
    assert not p.contains(parse_prefix("203.0.113.0/24"))
    checks += 3
    return checks


def main() -> int:
    total = 0
    for name, fn in (("der 编解码", check_der),
                     ("roa 结构与 VRP 展开", check_roa_der),
                     ("AS_PATH → 源 AS", check_origin_asn),
                     ("RFC 6811 三态验证", check_rfc6811)):
        got = fn()
        total += got
        print(f"  {name}: {got} checks")
    print(f"rpki_test: {total} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
