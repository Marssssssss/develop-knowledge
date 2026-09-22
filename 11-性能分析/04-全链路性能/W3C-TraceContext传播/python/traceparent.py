#!/usr/bin/env python3
"""W3C Trace Context ``traceparent`` 的解析、校验与变更。

口径全部来自逐句实读的 <https://www.w3.org/TR/trace-context/>（抽象为 ``is_invalid`` 而非抛异常，
这样调用方能区分「该丢弃并忽略 tracestate」与「该重开一条 trace」两类失败）：

1. ``version = 2HEXDIGLC``；``ff`` 被明令禁止（"Version ff is invalid"）；
2. ``trace-id = 32HEXDIGLC``、``parent-id = 16HEXDIGLC``、``trace-flags = 2HEXDIGLC``，
   ``HEXDIGLC`` 只含小写十六进制字符，所以 **大写一律非法**（"non-lowercase hex characters"）；
3. trace-id 全零、parent-id 全零都是 invalid，vendor MUST ignore 整个 ``traceparent``；
4. sampled 是 **bit 0**，必须用掩码取，不能把 ``trace-flags`` 当整数比大小 ——
   规范原文点名了这个坑："a flag 00000001 could be encoded as 01 in hex, or 09 in hex
   if present with the flag 00001000"；
5. 高版本按**位置**解析：先看总长，**短于 55 字符直接重开 trace**；dash 分别落在下标
   2 / 35 / 52，trace-flags 之后的第 56 个字符必须是串尾或 dash。
   位置算法由 "the second dash at the 35th position" 与 55 字符下限反推：
   ``00-``(3) + 32 = 35 → dash，+ 16 = 52 → dash，+ 2 = 55。

允许的变更只有四种（§3.4）：更新 parent-id、更新 sampled（**必须同时换 parent-id**）、
重开 trace、降级版本。"Vendors MUST NOT make any other mutations"。
"""

HEXLC = "0123456789abcdef"
VERSION_00 = "00"
FORBIDDEN_VERSION = "ff"
ZERO_TRACE_ID = "0" * 32
ZERO_PARENT_ID = "0" * 16


def _is_hexlc(s: str) -> bool:
    return len(s) > 0 and all(c in HEXLC for c in s)


def parse_traceparent(value: str) -> dict:
    """返回 ``{ok, reason, version, trace_id, parent_id, trace_flags, sampled, downgraded}``。

    ``ok`` 为 True 时字段可用；为 False 时 ``reason`` 说明是 ``ignore``（丢弃头、
    且**不得**去解析 tracestate）还是 ``restart``（重开一条 trace 并清掉 tracestate）。
    """
    out = {
        "ok": False, "reason": "", "version": "", "trace_id": "", "parent_id": "",
        "trace_flags": "", "sampled": False, "downgraded": False,
    }
    if value is None:
        out["reason"] = "ignore"
        return out
    v = value.strip()
    if len(v) < 2 or v[2:3] != "-":
        # "When the version prefix cannot be parsed ... the implementation should restart the trace."
        out["reason"] = "restart"
        return out
    version = v[0:2]
    if not _is_hexlc(version):
        out["reason"] = "restart"
        return out
    if version == FORBIDDEN_VERSION:
        out["reason"] = "ignore"
        return out
    out["version"] = version

    if version == VERSION_00:
        parts = v.split("-")
        if len(parts) != 4:
            out["reason"] = "ignore"
            return out
        _, trace_id, parent_id, flags = parts
        if len(trace_id) != 32 or not _is_hexlc(trace_id) or trace_id == ZERO_TRACE_ID:
            out["reason"] = "ignore"
            return out
        if len(parent_id) != 16 or not _is_hexlc(parent_id) or parent_id == ZERO_PARENT_ID:
            out["reason"] = "ignore"
            return out
        if len(flags) != 2 or not _is_hexlc(flags):
            out["reason"] = "ignore"
            return out
    else:
        # 高版本：按位置解析，并用本规范（00）的语义重建
        if len(v) < 55:
            out["reason"] = "restart"
            return out
        if v[35:36] != "-" or v[52:53] != "-":
            out["reason"] = "restart"
            return out
        trace_id, parent_id, flags = v[3:35], v[36:52], v[53:55]
        if not _is_hexlc(trace_id) or trace_id == ZERO_TRACE_ID:
            out["reason"] = "restart"
            return out
        if not _is_hexlc(parent_id) or parent_id == ZERO_PARENT_ID:
            out["reason"] = "restart"
            return out
        if not _is_hexlc(flags) or v[55:56] not in ("", "-"):
            out["reason"] = "restart"
            return out
        out["downgraded"] = True

    out.update(
        ok=True, trace_id=trace_id, parent_id=parent_id, trace_flags=flags,
        sampled=(int(flags, 16) & 0x01) == 0x01,
    )
    return out


def format_traceparent(version: str, trace_id: str, parent_id: str, flags: int) -> str:
    return "-".join([version, trace_id, parent_id, format(flags & 0xFF, "02x")])


def is_sampled(flags: int) -> bool:
    """sampled 是 bit 0，不是「flags 等于 1」。"""
    return (flags & 0x01) == 0x01


def mutate(parsed: dict, new_parent_id: str, sampled: bool | None = None, downgrade: bool = False) -> str:
    """按 §3.4 的四种允许变更产出新的 ``traceparent``。

    - 更新 sampled 时**必须**同时换一个新的 parent-id（规范原文：
      "The parent-id field MUST be set to a new value with the sampled flag update."）；
    - 高版本进来时先降级到 ``00``；
    - 不在列表里的任何改法（例如只改 trace-id 而保留 parent-id）都是被禁止的。
    """
    if not parsed.get("ok"):
        raise ValueError("cannot mutate an invalid traceparent")
    version = "00" if downgrade or parsed["downgraded"] else parsed["version"]
    flags = int(parsed["trace_flags"], 16)
    if sampled is not None:
        flags = (flags | 0x01) if sampled else (flags & 0xFE)
        # 未指定新 parent-id 时也要换——否则违反 MUST
        new_parent_id = new_parent_id or _bump(parsed["parent_id"])
    return format_traceparent(version, parsed["trace_id"], new_parent_id, flags)


def _bump(parent_id: str) -> str:
    return format((int(parent_id, 16) + 1) & 0xFFFFFFFFFFFFFFFF, "016x")


def restart(trace_id: str, parent_id: str, sampled: bool) -> str:
    """重开 trace：三个字段全部重新生成（安全网关入口的典型用法）。"""
    return format_traceparent("00", trace_id, parent_id, 0x01 if sampled else 0x00)
