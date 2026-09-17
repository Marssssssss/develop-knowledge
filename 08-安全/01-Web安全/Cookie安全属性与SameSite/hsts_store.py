"""HSTS 策略存储(RFC 6797 §6.1 语法、§8.1–§8.3 处理模型)。

与 cookie 同属"浏览器侧状态",但规则完全不同:HSTS 是**按发出主机**索引的
策略库,且只在安全传输上接受 —— 这正是它容易实现错的地方。

§x.y 均指 RFC 6797。
"""
from __future__ import annotations

import re

DIRECTIVE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9\-]*)\s*(?:=\s*(\S+)\s*)?$")
IPV4_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){3}$")


def is_ip_literal(host: str) -> bool:
    """§8.1.1 要排除的是 RFC 3986 的 IPv4address 与 IP-literal(IPv6 用 [ ] 包裹)。"""
    h = host.strip("[]")
    return bool(IPV4_RE.match(h)) or ":" in h


def parse_sts_header(value: str) -> dict | None:
    """§6.1:指令名大小写不敏感、顺序无关、每个指令只能出现一次;
    出现非法指令或重复指令 → 整个头字段必须被忽略。无法识别的指令则忽略该指令本身。"""
    out: dict[str, str | bool] = {}
    seen: set[str] = set()
    for raw in value.split(";"):
        if raw.strip() == "":
            continue
        m = DIRECTIVE_RE.match(raw)
        if not m:
            return None
        name, val = m.group(1).lower(), m.group(2)
        if name in seen:
            return None                       # §6.1 规则 2:每个指令只能出现一次
        seen.add(name)
        if name == "max-age":
            if val is None:
                return None
            raw_val = val.strip('"')          # §6.1.1:max-age 可用 quoted-string
            if not raw_val.isdigit():
                return None
            out["max-age"] = int(raw_val)
        elif name == "includesubdomains":
            out["includeSubDomains"] = True
        else:
            continue                          # §6.1 规则 5:无法识别的指令被忽略
    return out if "max-age" in out else None   # max-age 是 REQUIRED


def domain_label_match(known: str, requested: str) -> str:
    """§8.2:自最右标签起逐标签、ASCII 大小写不敏感比较。
    返回 congruent(完全一致)/ superdomain(已知者是请求者的父域)/ none。"""
    k = known.lower().rstrip(".").split(".")
    r = requested.lower().rstrip(".").split(".")
    if k == r:
        return "congruent"
    if len(k) < len(r) and r[-len(k):] == k:
        return "superdomain"
    return "none"


class HstsStore:
    def __init__(self, known=None):
        # host -> {"expiry": t, "include_sub": bool};模拟"预加载列表"可预置
        self.policies: dict[str, dict] = dict(known or {})

    def note(self, header_values: list[str], host: str, secure_transport: bool, now=0) -> None:
        """§8.1 处理模型。header_values 是同一响应里出现的所有 STS 头。"""
        if not secure_transport:
            return                            # §8.1:非安全传输上的 STS 头必须完全忽略
        if is_ip_literal(host):
            return                            # §8.1.1:IP 字面量不得被标记
        value = header_values[0]              # §8.1:多于一个头字段时只处理第一个
        parsed = parse_sts_header(value)
        if parsed is None:
            return
        h = host.lower().rstrip(".")
        if parsed["max-age"] == 0:
            # §6.1.1 / §8.1:max-age=0 既删策略,也清掉 includeSubDomains(后者被忽略)
            self.policies.pop(h, None)
            return
        # §8.1:max-age 是相对"接收时刻"的 TTL;§8.1.1 只更新自己这一条,不动父域条目
        self.policies[h] = {
            "expiry": now + parsed["max-age"],
            "include_sub": bool(parsed.get("includeSubDomains")),
        }

    def applies(self, host: str, now=0) -> bool:
        """§5.4 / §8.2:父域匹配须带 includeSubDomains;否则只有一致匹配才算。"""
        h = host.lower().rstrip(".")
        active = {k: v for k, v in self.policies.items() if v["expiry"] > now}
        for known, pol in active.items():
            kind = domain_label_match(known, h)
            if kind == "congruent":
                return True
            if kind == "superdomain" and pol["include_sub"]:
                return True
        return False

    def pruned(self, now=0) -> int:
        """§8.1.1:缓存中存在过期条目时必须全部清除。"""
        stale = [k for k, v in self.policies.items() if v["expiry"] <= now]
        for k in stale:
            del self.policies[k]
        return len(stale)

    def upgrade(self, scheme: str, host: str, port: int | None, now=0) -> tuple[str, int | None]:
        """§8.3:命中策略时把 http 改 https;显式 80 → 443;其他端口保留;无端口不添加。"""
        if scheme != "http" or not self.applies(host, now):
            return scheme, port
        if port == 80:
            return "https", 443
        return "https", port


def meta_http_equiv_ignored() -> bool:
    """§8.5:UA 必须不理会 <meta http-equiv="Strict-Transport-Security">。
    这里用文档化返回值表达该规则(真正的浏览器行为无法在纯 Python 里复现)。"""
    return True
