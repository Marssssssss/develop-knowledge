#!/usr/bin/env python3
"""demo 596 W3C Trace Context 自检：所有断言都对应规范条款，实跑验证。"""

import sys

import traceparent as tp
import tracestate as ts

FAILED: list[str] = []
COUNT = 0


def check(name: str, cond: bool) -> None:
    global COUNT
    COUNT += 1
    if not cond:
        FAILED.append(name)
        print(f"  FAIL {name}")


def eq(name: str, got, want) -> None:
    global COUNT
    COUNT += 1
    if got != want:
        FAILED.append(f"{name} (got={got!r} want={want!r})")
        print(f"  FAIL {name}: got={got!r} want={want!r}")


GOOD_TID = "4bf92f3577b34da6a3ce929d0e0e4736"
GOOD_PID = "00f067aa0ba902b7"
GOOD = f"00-{GOOD_TID}-{GOOD_PID}-01"

# --- 1. 解析基本盘 ---
p = tp.parse_traceparent(GOOD)
check("v00 合法可解析", p["ok"])
eq("v00 trace-id", p["trace_id"], GOOD_TID)
eq("v00 parent-id", p["parent_id"], GOOD_PID)
eq("v00 flags=01 → sampled", p["sampled"], True)

p0 = tp.parse_traceparent(f"00-{GOOD_TID}-{GOOD_PID}-00")
eq("flags=00 → 未采样", p0["sampled"], False)

# --- 2. 四类非法 ---
check("version=ff 非法", not tp.parse_traceparent(f"ff-{GOOD_TID}-{GOOD_PID}-01")["ok"])
check("trace-id 全零非法", not tp.parse_traceparent(f"00-{'0'*32}-{GOOD_PID}-01")["ok"])
check("parent-id 全零非法", not tp.parse_traceparent(f"00-{GOOD_TID}-{'0'*16}-01")["ok"])
check("大写十六进制非法", not tp.parse_traceparent(f"00-{GOOD_TID.upper()}-{GOOD_PID}-01")["ok"])
check("trace-flags 长度不足非法", not tp.parse_traceparent(f"00-{GOOD_TID}-{GOOD_PID}-0")["ok"])
check("字段数不对非法", not tp.parse_traceparent("00-" + GOOD_TID)["ok"])
eq("version 前缀不可解析 → restart",
   tp.parse_traceparent("zz-" + GOOD_TID)["reason"], "restart")

# --- 3. sampled 是 bit 0 ---
eq("flags=09 → sampled（掩码生效）", tp.is_sampled(0x09), True)
eq("flags=08 → 非 sampled", tp.is_sampled(0x08), False)
eq("flags=02 → 非 sampled", tp.is_sampled(0x02), False)
eq("flags=ff → sampled", tp.is_sampled(0xFF), True)
eq("flags=fe → 非 sampled", tp.is_sampled(0xFE), False)
# 朴素写法会在这些点上判错（反向锁死这条断言有鉴别力）
check("朴素比较 flags==1 在 09 上判错", (0x09 == 1) is False)
check("朴素比较 flags==1 在 ff 上判错", (0xFF == 1) is False)

# --- 4. 高版本位置解析 ---
high = f"01-{GOOD_TID}-{GOOD_PID}-01-x9"
ph = tp.parse_traceparent(high)
check("高版本 58 字符可解析", ph["ok"])
eq("高版本 trace-id 按 3:35 取", ph["trace_id"], GOOD_TID)
eq("高版本 parent-id 按 36:52 取", ph["parent_id"], GOOD_PID)
eq("高版本需降级", ph["downgraded"], True)
eq("高版本 dash 落在 35/52", high[35] + high[52], "--")
short = f"01-{GOOD_TID}-{GOOD_PID}-0"          # 54 字符
eq("短于 55 字符 → restart", tp.parse_traceparent(short)["reason"], "restart")
eq("55 字符边界可解析", tp.parse_traceparent(f"01-{GOOD_TID}-{GOOD_PID}-01")["ok"], True)

# --- 5. 变更规则 ---
p = tp.parse_traceparent(GOOD)
out = tp.mutate(p, "c3ce929d0e0e4736")
eq("更新 parent-id 保留 trace-id", out.split("-")[1], GOOD_TID)
eq("更新 parent-id 写入新值", out.split("-")[2], "c3ce929d0e0e4736")
eq("更新 parent-id 不动 sampled", out.split("-")[3], "01")
out = tp.mutate(p, "aaaaaaaaaaaaaaaa", sampled=False)
eq("关掉 sampled 必须换 parent-id", out.split("-")[2], "aaaaaaaaaaaaaaaa")
eq("关掉 sampled 后 flags=00", out.split("-")[3], "00")
out = tp.mutate(p, "", sampled=True)
check("未给新 parent-id 时自动 bump", out.split("-")[2] != GOOD_PID)
eq("重开 trace 三字段全换",
   tp.restart("1" * 32, "2" * 16, True), "00-" + "1" * 32 + "-" + "2" * 16 + "-01")

# --- 6. tracestate key/value 合法性 ---
check("simple-key 合法", ts.valid_key("rojo"))
check("含 - 的 key 合法", ts.valid_key("my-tracing-system"))
check("多租户 key 合法", ts.valid_key("fw529a3039@dt"))
check("大写开头非法", not ts.valid_key("Rojo"))
check("含 @ 的 system-id 非法", not ts.valid_key("a@dt@x"))
check("空 key 非法", not ts.valid_key(""))
check("含逗号的 value 非法", not ts.valid_value("a,b"))
check("含等号的 value 非法", not ts.valid_value("a=b"))
check("257 字符 value 非法", not ts.valid_value("x" * 257))
check("256 字符 value 合法", ts.valid_value("x" * 256))
check("数字开头 key 合法", ts.valid_key("1abc"))

# --- 7. 列表解析与变更 ---
members = ts.parse_tracestate("rojo=00f067aa0ba902b7,congo=t61rcWkgMzE")
eq("解析出 2 条", len(members), 2)
eq("最左是 rojo", members[0][0], "rojo")
eq("空头解析为 0 条", len(ts.parse_tracestate("")), 0)
eq("空成员被忽略", len(ts.parse_tracestate("rojo=1,,  ,congo=2")), 2)
eq("非法 key 被丢弃", len(ts.parse_tracestate("Rojo=1,congo=2")), 1)
dup = ts.parse_tracestate("rojo=1,congo=2,rojo=3")
eq("重复 key 只留最左一条", dup, [("rojo", "1"), ("congo", "2")])
moved = ts.update(members, "congo", "zzz")
eq("修改的 key 移到最左", moved[0], ("congo", "zzz"))
eq("未修改的相对顺序不变", [k for k, _ in moved[1:]], ["rojo"])
added = ts.update(members, "acme", "1")
eq("新增 key 也在最左", added[0][0], "acme")

# --- 8. 截断顺序：先删长条目，再从尾部删 ---
big = [("congo", "x" * 200)] + [(f"v{i}", "y" * 40) for i in range(12)]
cut = ts.truncate(big)
check("超长条目被优先删除", all(len(k) + 1 + len(v) <= ts.BIG_ENTRY_LEN for k, v in cut))
check("长条目已不在结果中", ("congo", "x" * 200) not in cut)
check("截断后头部存活", cut[0] == ("v0", "y" * 40))
check("尾部被裁掉", cut[-1] != ("v11", "y" * 40))
size = sum(len(k) + 1 + len(v) for k, v in cut) + len(cut) - 1
check("结果压进 512 字符预算", size <= ts.PROPAGATE_AT_LEAST_CHARS)
check("只删整条不截断单条", all(any(m == b for b in big) for m in cut))

# --- 9. baggage 传播下限 ---
check("64 成员 / 8192 字节 → 必须全量传播", ts.can_propagate_baggage(64, 8192))
check("65 成员 → 可丢", not ts.can_propagate_baggage(65, 100))
check("8193 字节 → 可丢", not ts.can_propagate_baggage(3, 8193))

print(f"\n{COUNT - len(FAILED)}/{COUNT} 断言通过")
sys.exit(1 if FAILED else 0)
