#!/usr/bin/env python3
"""把 ``traceparent`` / ``tracestate`` 的规范条款跑成可观察的现象。

运行：``python main.py``
"""

import traceparent as tp
import tracestate as ts

GOOD = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def demo_parse():
    print("== 1. traceparent 解析与四类非法 ==")
    cases = [
        (GOOD, "规范里的标准示例（sampled）"),
        ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00", "未采样"),
        ("ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", "version=ff（禁止值）"),
        ("00-00000000000000000000000000000000-00f067aa0ba902b7-01", "trace-id 全零"),
        ("00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01", "parent-id 全零"),
        ("00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01", "大写十六进制"),
        ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-0", "trace-flags 只有 1 位"),
    ]
    for value, note in cases:
        p = tp.parse_traceparent(value)
        flag = "ok  " if p["ok"] else f"{p['reason']}"
        print(f"  [{flag:7}] sampled={p['sampled']!s:5} {note}")


def demo_flag_mask():
    print("\n== 2. sampled 是 bit 0，不是「等于 1」==")
    for flags in (0x00, 0x01, 0x02, 0x08, 0x09, 0x0F, 0xFE, 0xFF):
        print(f"  trace-flags={flags:02x} -> sampled={tp.is_sampled(flags)!s:5}"
              f"  (朴素比较 flags==1 会给出 {flags == 1})")


def demo_versioning():
    print("\n== 3. 更高版本按位置解析，短于 55 字符直接重开 ==")
    high = "01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01-x9"
    short = "01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-0"
    for value in (high, short):
        p = tp.parse_traceparent(value)
        if p["ok"]:
            print(f"  len={len(value)} -> 解析成功 trace-id={p['trace_id']} "
                  f"parent-id={p['parent_id']} downgraded={p['downgraded']}")
        else:
            print(f"  len={len(value)} -> {p['reason']}（重开 trace，并清掉 tracestate）")


def demo_mutation():
    print("\n== 4. 四种允许的变更 ==")
    p = tp.parse_traceparent(GOOD)
    print(f"  入站           : {GOOD}")
    print(f"  更新 parent-id : {tp.mutate(p, 'c3ce929d0e0e4736')}")
    print(f"  更新 sampled=0 : {tp.mutate(p, 'aaaaaaaaaaaaaaaa', sampled=False)}"
          "   <- 必须同时换 parent-id")
    print(f"  重开 trace     : {tp.restart('11111111111111111111111111111111', '2222222222222222', True)}")


def demo_tracestate():
    print("\n== 5. tracestate：左=最新、同 key 覆写、超限先删长条目再删尾部 ==")
    header = "rojo=00f067aa0ba902b7,congo=t61rcWkgMzE"
    members = ts.parse_tracestate(header)
    print(f"  入站   : {ts.format_tracestate(members)}")
    members = ts.update(members, "congo", "00f067aa0ba902b8")
    print(f"  改 congo: {ts.format_tracestate(members)}   <- 修改过的 key 移到最左，未改的相对顺序不变")
    big = [("congo", "x" * 200)] + [(f"v{i}", "y" * 40) for i in range(12)]
    cut = ts.truncate(big)
    print(f"  原始 {len(big)} 条 -> 截断后 {len(cut)} 条，"
          f"最长条目 {max((len(k) + 1 + len(v)) for k, v in cut)} 字符")


def demo_baggage():
    print("\n== 6. baggage 的传播下限：64 成员 / 8192 字节 ==")
    for n, b in ((ts.BAGGAGE_MAX_MEMBERS, ts.BAGGAGE_MAX_BYTES),
                 (ts.BAGGAGE_MAX_MEMBERS + 1, 1024),
                 (10, ts.BAGGAGE_MAX_BYTES + 1)):
        print(f"  members={n:3} bytes={b:5} -> MUST 全量传播: {ts.can_propagate_baggage(n, b)}")


if __name__ == "__main__":
    demo_parse()
    demo_flag_mask()
    demo_versioning()
    demo_mutation()
    demo_tracestate()
    demo_baggage()
