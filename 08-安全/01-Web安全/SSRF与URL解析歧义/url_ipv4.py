"""WHATWG URL 规范里的 IPv4 主机解析(ssrf 绕过的核心机制)。

为什么单独一个文件:`127.0.0.1` 至少有十几种等价写法(`0x7f.1`、`2130706433`、
`0177.0.0.1`、`127.1` …)。只做字符串黑名单的实现,必然被这些写法绕过。
本文件按规范逐步复刻,来源:WHATWG URL Standard §3.5(IPv4 number parser / IPv4 parser /
ends in a number checker)、§4.4 host parser。
"""
from __future__ import annotations

import ipaddress

HEX = set("0123456789abcdef")


def ipv4_number_parser(text: str) -> tuple[int, bool] | None:
    """§3.5 IPv4 number parser。返回 (值, 是否出现非十进制写法) 或 None(failure)。"""
    if text == "":
        return None
    validation_error = False
    radix = 10
    if len(text) >= 2 and text[:2] in ("0x", "0X"):
        validation_error = True           # 十六进制写法:规范要求报验证错误
        text, radix = text[2:], 16
    elif len(text) >= 2 and text[0] == "0":
        validation_error = True           # 前导 0 = 八进制
        text, radix = text[1:], 8
    if text == "":
        return (0, True)                  # "0x" / "0" 自身
    allowed = set("0123456789") if radix == 10 else (HEX if radix == 16 else set("01234567"))
    if any(ch.lower() not in allowed for ch in text):
        return None
    return (int(text, radix), validation_error)


def ipv4_parser(text: str) -> ipaddress.IPv4Address | None:
    """§3.5 IPv4 parser:点分部分 ≤4 且**最后一部分**可覆盖剩余字节。"""
    parts = text.split(".")
    if parts and parts[-1] == "":
        if len(parts) > 1:
            parts.pop()                   # 尾点被忽略(IPv4-empty-part)
    if len(parts) > 4:
        return None
    numbers = []
    for part in parts:
        got = ipv4_number_parser(part)
        if got is None:
            return None
        numbers.append(got[0])
    if any(n > 255 for n in numbers[:-1]):
        return None
    # 最后一部分允许"吃掉"剩余字节:1 段 ≤2^32-1,2 段 ≤2^24-1,3 段 ≤2^16-1
    if numbers[-1] >= 256 ** (5 - len(numbers)):
        return None
    value = numbers[-1]
    for counter, n in enumerate(numbers[:-1]):
        value += n * 256 ** (3 - counter)
    return ipaddress.IPv4Address(value)


def ends_in_a_number(text: str) -> bool:
    """§3.5 ends in a number checker:最后一段是纯数字或能当 IPv4 number 解析。"""
    if text == "":
        return False
    parts = text.split(".")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    if not parts:
        return False
    last = parts[-1]
    if last and last.isdigit() and last.isascii():
        return True
    if last in ("0x", "0X"):
        return True
    return ipv4_number_parser(last) is not None


def host_parser(host: str) -> tuple[str, str] | None:
    """§4.4 host parser 的"特殊 scheme"分支:IPv6 / IPv4 / 域名 三选一。
    成功返回 (kind, 规范化值);失败返回 None(浏览器会判为非法 URL)。"""
    if host.startswith("["):
        inner = host[1:-1] if host.endswith("]") else host[1:]
        try:
            return ("ipv6", str(ipaddress.IPv6Address(inner)))
        except ValueError:
            return None
    if ends_in_a_number(host):
        ip = ipv4_parser(host)
        return ("ipv4", str(ip)) if ip else None
    return ("domain", host.lower())


def whatwg_authority_host(url: str) -> str | None:
    """按 WHATWG 语义取 authority 里的主机名:反斜杠与斜杠都终止 authority。"""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return None
    authority = rest
    for stop in ("/", "\\", "?", "#"):
        idx = authority.find(stop)
        if idx >= 0:
            authority = authority[:idx]
    authority = authority.rpartition("@")[2]          # 去 userinfo(取最后一个 @)
    if authority.startswith("["):
        return authority.split("]")[0] + "]"
    return authority.split(":")[0].lower()


def bypass_table() -> dict[str, str]:
    """一组真实出现过的等价写法 → 规范化后的 IPv4。用于自检与对照实验。"""
    return {
        "127.0.0.1": "127.0.0.1",
        "127.1": "127.0.0.1",
        "2130706433": "127.0.0.1",
        "0x7f000001": "127.0.0.1",
        "0x7f.1": "127.0.0.1",
        "0177.0.0.1": "127.0.0.1",
        "0x7f.0x0.0x0.0x1": "127.0.0.1",
        "169.254.169.254": "169.254.169.254",
        "2852039166": "169.254.169.254",
        "0xa9fea9fe": "169.254.169.254",
        "127.0.0.1.": "127.0.0.1",
    }
