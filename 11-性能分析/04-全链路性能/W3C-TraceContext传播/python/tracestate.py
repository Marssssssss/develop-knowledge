#!/usr/bin/env python3
"""W3C ``tracestate``（含配套的 ``baggage`` 限额）的解析、变更与截断。

口径来自 <https://www.w3.org/TR/trace-context/> §3.3–3.5 与 <https://www.w3.org/TR/baggage/> §3.3：

- 值是逗号分隔的 ``key=value`` 列表，**最多 32 个 list-member**
  （ABNF：``list = list-member 0*31(OWS "," OWS list-member)``）；空成员合法；
- key 两种形态：``simple-key``（1–256 字符）与 ``multi-tenant-key = tenant-id "@" system-id``，
  tenant-id ≤ 241、system-id ≤ 14，**必须以小写字母或数字开头**，只能含 a-z 0-9 ``_ - * /``；
- value 最多 256 个可打印 ASCII（0x20–0x7E），**排除逗号与等号**；
- 变更规则：新增/修改的 key 要**移到最左**（"Modified keys SHOULD be moved to the beginning"）；
  同一 key 只能有一条（"Only one entry per key is allowed"），重进系统要**覆写**而不是追加；
  未修改的 key 必须保持原有相对顺序；
- 截断顺序：先删 **长度 > 128 字符** 的条目，仍不够再从**尾部**删
  （"Entries larger than 128 characters long SHOULD be removed first.
  Then entries SHOULD be removed starting from the end"）；
- 关键联动：**``traceparent`` 没改动时不得改 ``tracestate``**
  （"If the value of the traceparent field wasn't changed before propagation,
  tracestate MUST NOT be modified as well."）——透传代理靠这条保持零成本；
- ``baggage`` 的传播下限是 **64 个成员 / 8192 字节**，超出才能丢；且**不允许传播半个成员**。
"""

MAX_MEMBERS = 32
MAX_VALUE_LEN = 256
BIG_ENTRY_LEN = 128
PROPAGATE_AT_LEAST_CHARS = 512
BAGGAGE_MAX_MEMBERS = 64
BAGGAGE_MAX_BYTES = 8192

_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789_-*/")
_LCALPHA = set("abcdefghijklmnopqrstuvwxyz")


def valid_key(key: str) -> bool:
    if not key or len(key) > 256:
        return False
    if key[0] not in _LCALPHA and key[0] not in "0123456789":
        return False
    if "@" in key:
        tenant, _, system = key.partition("@")
        if "@" in system:
            return False
        if not (1 <= len(tenant) <= 241) or not (1 <= len(system) <= 14):
            return False
        return all(c in _ALLOWED for c in tenant) and all(c in _ALLOWED for c in system)
    return all(c in _ALLOWED for c in key)


def valid_value(value: str) -> bool:
    if len(value) > MAX_VALUE_LEN:
        return False
    return all(0x20 <= ord(c) <= 0x7E for c in value) and "," not in value and "=" not in value


def parse_tracestate(header: str) -> list[tuple[str, str]]:
    """返回 ``[(key, value), ...]``，左→右。空成员被丢弃；重复的 key 保留**最左**那条。"""
    if not header:
        return []
    members: list[tuple[str, str]] = []
    for raw in header.split(","):
        m = raw.strip()
        if not m:
            continue
        if "=" not in m:
            continue
        k, _, v = m.partition("=")
        k, v = k.strip(), v.strip()
        if not valid_key(k) or not valid_value(v):
            continue
        if any(k == kk for kk, _ in members):
            continue
        members.append((k, v))
    return members


def update(members: list[tuple[str, str]], key: str, value: str) -> list[tuple[str, str]]:
    """写入（新增或覆写）一个 key，并把它**移到最左**；其余成员相对顺序不变。"""
    rest = [(k, v) for k, v in members if k != key]
    return [(key, value)] + rest


def _entry_len(member: tuple[str, str]) -> int:
    """一条 ``key=value`` 的字符数。"""
    k, v = member
    return len(k) + 1 + len(v)


def truncate(members: list[tuple[str, str]], budget: int = PROPAGATE_AT_LEAST_CHARS) -> list[tuple[str, str]]:
    """把组合后的 ``tracestate`` 压进 ``budget`` 个字符：先删 >128 的长条目，再从尾部删整条。"""
    def size(ms):
        # 逗号数量 = 成员数 - 1
        return sum(len(k) + len(v) + 1 for k, v in ms) + max(0, len(ms) - 1)

    kept = [m for m in members if _entry_len(m) <= BIG_ENTRY_LEN]
    if size(kept) <= budget:
        return kept
    # 2) 仍超限则从尾部整条删除
    while kept and size(kept) > budget:
        kept.pop()
    return kept


def format_tracestate(members: list[tuple[str, str]]) -> str:
    return ",".join(f"{k}={v}" for k, v in members)


def can_propagate_baggage(members: int, payload_bytes: int) -> bool:
    """W3C baggage §3.3.2：64 个成员以内且 8192 字节以内时平台 MUST 全部传播。"""
    return members <= BAGGAGE_MAX_MEMBERS and payload_bytes <= BAGGAGE_MAX_BYTES
