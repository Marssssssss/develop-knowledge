"""SSRF 防护策略(OWASP SSRF Prevention Cheat Sheet 的可执行版)。

区分规范给的两种场景:
  Case 1 目标集合已知 → 允许列表 + "用匹配到的条目自行重建请求"
  Case 2 目标任意(Webhook)→ 只能阻止列表,且必须逐个校验 DNS 解析结果

两处最容易漏的:① 解析器分歧(同一个字符串被不同库读出不同 host)直接拒绝;
② 禁止自动跟随重定向 —— 校验过 host 之后再被 302 带走,等于没校验。
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from url_ipv4 import host_parser, whatwg_authority_host

# OWASP 列出的"最后手段"阻止列表(生产应优先允许列表)
METADATA_HOSTS = {"metadata.google.internal", "metadata.amazonaws.com",
                  "metadata.goog", "instance-data"}
BLOCKED_NETS = [ipaddress.ip_network(n) for n in (
    "169.254.0.0/16",     # 链路本地 / 云元数据 169.254.169.254
    "127.0.0.0/8",        # 回环
    "0.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",      # CGNAT
    "224.0.0.0/4",        # 组播
    "::1/128",
    "ff00::/8",
    "fc00::/7",           # 唯一本地地址
    "fe80::/10",          # 链路本地
)]
NAT64 = ipaddress.ip_network("64:ff9b::/96")
SIX_TO_FOUR = ipaddress.ip_network("2002::/16")
ALLOWED_SCHEMES = {"http", "https"}


class SsrfBlocked(Exception):
    pass


def unwrap(addr):
    """把"套着 v4 的 v6"(IPv4-mapped / NAT64 / 6to4)还原成 v4 —— 否则 is_global 会判错。"""
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return addr.ipv4_mapped
        if addr.sixtofour:
            return addr.sixtofour
        if addr in NAT64:
            return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return addr


def is_public(addr) -> bool:
    addr = unwrap(addr)
    if any(addr in net for net in BLOCKED_NETS):
        return False
    return addr.is_global


def classify(host: str) -> tuple[str, str]:
    """返回 (kind, 规范化值);无法解析则抛 SsrfBlocked。"""
    got = host_parser(host)
    if got is None:
        raise SsrfBlocked(f"host 非法或 IPv4 越界: {host}")
    return got


def parser_disagreement(url: str) -> tuple[str, str] | None:
    """把两种解析姿势的 host 摆在一起:不一致 → 直接拒绝(不做调和)。"""
    whatwg = whatwg_authority_host(url)
    try:
        py = urlsplit(url).hostname
    except ValueError:
        py = None
    if whatwg is None or py is None:
        return None
    return (whatwg, py.lower())


class Policy:
    def __init__(self, allow_domains=(), allow_ips=(), callback_tokens=("token123",),
                 resolver=None):
        self.allow_domains = tuple(allow_domains)     # 大小写敏感精确比对(OWASP 要求)
        self.allow_ips = tuple(allow_ips)
        self.callback_tokens = tuple(callback_tokens)
        self.resolver = resolver or (lambda d: [])
        self.follow_redirects = False                 # 永远不跟随

    def resolve(self, domain: str) -> list[str]:
        return list(self.resolver(domain))

    def target(self, url: str, dns=None) -> dict:
        """校验并**重建**请求目标:只保留 scheme/host/port/path 中允许的部分。"""
        d = parser_disagreement(url)
        if d and d[0] != d[1]:
            raise SsrfBlocked(f"解析器分歧:{d[0]} vs {d[1]}")
        parts = urlsplit(url)
        if parts.scheme not in ALLOWED_SCHEMES:
            raise SsrfBlocked(f"scheme 不在允许列表:{parts.scheme}")
        raw_host = parts.hostname or ""
        kind, value = classify(raw_host)

        if kind in ("ipv4", "ipv6"):
            addr = unwrap(ipaddress.ip_address(value))
            if str(addr) not in self.allow_ips or not is_public(addr):
                raise SsrfBlocked(f"IP 不在允许列表或非公网:{addr}")
            return {"scheme": parts.scheme, "host": str(addr), "port": parts.port or 443,
                    "path": parts.path or "/"}
        if value in METADATA_HOSTS:
            raise SsrfBlocked(f"云元数据主机:{value}")

        # Case 1:允许列表命中 → 用允许列表里的那条自行重建(不复制用户 URL 的其他部分)
        if self.allow_domains:
            if value not in self.allow_domains:
                raise SsrfBlocked(f"域名不在允许列表:{value}")
            host = value
        else:
            host = value
        records = dns if dns is not None else self.resolve(host)
        if not records:
            raise SsrfBlocked(f"DNS 无解析结果:{host}")
        for rec in records:                            # 防 DNS pinning:全部 A/AAAA 都要查
            addr = unwrap(ipaddress.ip_address(rec))
            if not is_public(addr):
                raise SsrfBlocked(f"DNS 指向非公网地址:{host} -> {addr}")
        return {"scheme": parts.scheme, "host": host, "port": parts.port or 443,
                "path": parts.path or "/"}

    def maybe_redirect(self, location: str) -> None:
        """收到 3xx 时的正确姿势:不跟随(而不是"跟随后再校验")。"""
        if not self.follow_redirects:
            raise SsrfBlocked(f"策略禁止跟随重定向:{location}")
