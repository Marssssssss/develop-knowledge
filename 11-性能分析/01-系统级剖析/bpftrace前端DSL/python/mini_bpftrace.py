#!/usr/bin/env python3
"""mini_bpftrace.py — bpftrace 语言前端最小实现（解析 + 语义 + 执行 + 输出）

复刻 bpftrace 的四件事:
  1. 探针说明符展开(短名 / 通配符)与谓词过滤
  2. 关联数组(map)与聚合函数(count/sum/min/max/avg/stats/hist/lhist)
  3. hist()/lhist()/stats() 的输出格式(列宽按官方样例逐字符对齐)
  4. 事件驱动执行引擎:用 sys_enter + sys_exit 配对给 syscall 延迟打直方图

不依赖任何第三方库。权威来源见 ../README.md「参考资料」。
运行: python mini_bpftrace.py
"""
from __future__ import annotations

import re

from mini_bpftrace_engine import Engine, Unset, printf
from mini_bpftrace_hist import (Aggregator, BAR_WIDTH, expand_probe, hist_index, hist_label,
                                lhist_index, lhist_label, probe_matches, render_rows)
from mini_bpftrace_parse import parse

# ---------------------------------------------------------------- 自检
def check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  <- {detail}" if detail else ""))
    return bool(cond)


def synthetic_events():
    """合成「应用线程被上游队列顶住」的 syscall 序列(单位 ns),确定性、无随机数。"""
    ev = [("read", d) for d in (4000, 5200, 6800, 8000, 9500, 11000, 13000, 14500)]
    ev += [("openat", d) for d in (16_000_000, 20_000_000, 24_000_000, 31_000_000)]
    ev += [("write", d) for d in (700, 1300, 2600)]
    out, ts = [], 0
    for i, (name, d) in enumerate(ev):
        tid = 100 + (i % 2)
        ts += 1000
        out.append((f"tracepoint:syscalls:sys_enter_{name}",
                    {"tid": tid, "pid": tid, "comm": "app", "nsecs": ts}))
        ts += d
        out.append((f"tracepoint:syscalls:sys_exit_{name}",
                    {"tid": tid, "pid": tid, "comm": "app", "nsecs": ts, "ret": 1024}))
    return out


SRC = """
BEGIN { printf("Tracing syscall latency... Hit Ctrl-C to end.\\n"); }
tracepoint:syscalls:sys_enter_* /comm == "app"/ { @start[tid] = nsecs; }
tracepoint:syscalls:sys_exit_* /comm == "app" && @start[tid]/
  { @ns[comm] = hist(nsecs - @start[tid]); delete(@start, tid); }
tracepoint:raw_syscalls:sys_enter { @[comm] = count(); }
"""


def main() -> int:
    ok = True

    print("== 1. 探针说明符:短名展开 ==")
    ok &= check("t:syscalls:sys_enter_read -> tracepoint:...",
                expand_probe("t:syscalls:sys_enter_read") == "tracepoint:syscalls:sys_enter_read")
    ok &= check("k:f -> kprobe:f", expand_probe("k:f") == "kprobe:f")
    ok &= check("kr:vfs_read -> kretprobe:vfs_read",
                expand_probe("kr:vfs_read") == "kretprobe:vfs_read")
    ok &= check("i:s:5 -> interval:s:5", expand_probe("i:s:5") == "interval:s:5")
    ok &= check("BEGIN 是内置事件、不是 provider(统一成小写 begin)",
                expand_probe("BEGIN") == "begin" and expand_probe("begin") == "begin")
    ok &= check("未知 provider 原样保留(交给 bpftrace 报错)",
                expand_probe("nope:foo") == "nope:foo")

    print("== 2. 通配符匹配 ==")
    ok &= check("tracepoint:sched:sched* 命中 sched_wakeup",
                probe_matches("tracepoint:sched:sched*", "tracepoint:sched:sched_wakeup"))
    ok &= check("同一模式不命中 sys_enter_read",
                not probe_matches("tracepoint:sched:sched*", "tracepoint:syscalls:sys_enter_read"))
    ok &= check("? 只匹配单字符(tcp_? 命中 tcp_a)",
                probe_matches("kprobe:tcp_?", "kprobe:tcp_a"))
    ok &= check("? 不匹配多字符", not probe_matches("kprobe:tcp_?", "kprobe:tcp_abc"))

    print("== 3. hist() 分桶(每 2 的幂一个桶) ==")
    got = [hist_index(v) for v in (0, 1, 2, 3, 4, 7, 8, 127, 128, 1023)]
    ok &= check("0/1->0 2..3->1 4..7->2 8..15->3 127->6 128->7 1023->9",
                got == [0, 0, 1, 1, 2, 2, 3, 6, 7, 9], f"got {got}")
    ok &= check("首桶标签是 [0, 1](右括号为方括号,同时收纳 0 与 1)", hist_label(0) == "[0, 1]")
    ok &= check("后续桶左闭右开", hist_label(7) == "[128, 256)")
    ok &= check(">=1024 的边界用 k/M/G 刻度(tutorial Lesson 7 的 [2k, 4k) / [512k, 1M))",
                hist_label(11) == "[2k, 4k)" and hist_label(19) == "[512k, 1M)"
                and hist_label(20) == "[1M, 2M)", f"{hist_label(11)} {hist_label(19)}")

    print("== 4. 输出列宽与官方 tutorial 样例逐字符一致 ==")
    rows = render_rows([("[0, 1]", 12), ("[2, 4)", 18), ("[128, 256)", 1)])
    ok &= check("`[0, 1]` 行前 24 列 == '[0, 1]'+16 空格+'12'",
                rows[0][:24] == "[0, 1]" + " " * 16 + "12", repr(rows[0][:24]))
    ok &= check("`[2, 4)` 行前 24 列 == '[2, 4)'+16 空格+'18'",
                rows[1][:24] == "[2, 4)" + " " * 16 + "18", repr(rows[1][:24]))
    ok &= check("`[128, 256)` 行前 24 列 == '[128, 256)'+13 空格+'1'",
                rows[2][:24] == "[128, 256)" + " " * 13 + "1", repr(rows[2][:24]))
    ok &= check("柱区总宽恒为 52", all(len(r.split("|")[1]) == BAR_WIDTH for r in rows))
    ok &= check("最大桶占满 52 个 @", rows[1].count("@") == BAR_WIDTH, str(rows[1].count("@")))

    print("== 5. lhist(): 桶数 = (max-min)/step + 2,越界值各占一桶 ==")
    ag = Aggregator("lhist", (0, 0, 2000, 200))
    ok &= check("lhist(_,0,2000,200) 共 12 桶(M=10 + 2)", len(ag.buckets) == 12,
                str(len(ag.buckets)))
    ok &= check("低于下界 -> (...,0]", lhist_label(-1, 0, 2000, 200) == "(...,0]")
    ok &= check("高于上界 -> [2000,...)", lhist_label(10, 0, 2000, 200) == "[2000,...)")
    ok &= check("区间标签 -> [1800, 2000)", lhist_label(9, 0, 2000, 200) == "[1800, 2000)")
    ok &= check("-5 与 0 同落 (...,0] 桶",
                lhist_index(-5, 0, 2000, 200) == lhist_index(0, 0, 2000, 200) == -1)
    ok &= check("2000 与 99999 同落 [2000,...) 桶",
                lhist_index(2000, 0, 2000, 200) == lhist_index(99999, 0, 2000, 200) == 10)

    print("== 6. 解析 + 执行:sys_enter/sys_exit 配对 ==")
    prog = parse(SRC)
    ok &= check("解析出 4 个动作块", len(prog) == 4, str(len(prog)))
    ok &= check("第 3 块带谓词", prog[2][1] is not None)
    ok &= check("第 2 块探针通配符已展开",
                prog[1][0] == ["tracepoint:syscalls:sys_enter_*"], str(prog[1][0]))
    eng = Engine(prog)
    eng.feed("begin", {})
    for name, ctx in synthetic_events():
        eng.feed(name, ctx)
    ok &= check("BEGIN 打印出表头", bool(eng.out) and "Tracing" in eng.out[0], repr(eng.out))
    ns = eng.agg[("ns", ("app",))]
    ok &= check("15 条 syscall 延迟全部配对成功", sum(ns.buckets.values()) == 15,
                str(sum(ns.buckets.values())))
    ok &= check("呈双峰: 4k~8k 段有人、16ms~32ms 段也有人",
                ns.buckets.get(hist_index(6800), 0) > 0
                and ns.buckets.get(hist_index(16_000_000), 0) > 0)
    ok &= check("delete(@start, tid) 已清空 15 个 @start 键",
                sum(1 for (n, _k) in eng.maps if n == "start") == 0)
    rpt = "\n".join(eng.report())
    ok &= check("报告里出现 @ns[app]: 与直方图行", "@ns[app]:" in rpt and "[4k, 8k)" in rpt, rpt[:80])

    print("== 7. 未配对时不应记账 ==")
    e2 = Engine(parse(SRC))
    e2.feed("tracepoint:syscalls:sys_exit_read", {"tid": 7, "pid": 7, "comm": "other", "nsecs": 5})
    ok &= check("comm 不匹配 -> 不建 @ns", ("ns", ("other",)) not in e2.agg)
    e3 = Engine(parse(SRC))
    e3.feed("tracepoint:syscalls:sys_exit_read", {"tid": 7, "pid": 7, "comm": "app", "nsecs": 5})
    ok &= check("comm 匹配但缺 @start[tid] -> 谓词挡住,不建 @ns",
                not any(n == "ns" for (n, _k) in e3.agg))
    e4 = Engine(parse("kretprobe:vfs_read { @b = hist(retval); }"))
    e4.feed("kretprobe:vfs_read", {"retval": 100})
    ok &= check("真值 100 落进 [64, 128) 桶且只计 1 次",
                e4.agg[("b", ())].buckets == {0: 0, 6: 1}, str(e4.agg[("b", ())].buckets))

    print("== 8. count() 与 stats() 的输出格式 ==")
    e5 = Engine(parse("tracepoint:raw_syscalls:sys_enter { @[comm] = count(); }"
                      "kprobe:vfs_read { @bytes[comm] = stats(arg2); }"))
    for c in ("bpftrace", "systemd", "snmp-pass", "snmp-pass", "sshd"):
        e5.feed("tracepoint:raw_syscalls:sys_enter", {"comm": c})
    for n in (7, 832, 886):
        e5.feed("kprobe:vfs_read", {"comm": "bash", "arg2": n})
    rep = "\n".join(e5.report())
    ok &= check("无名 map 渲染为 @[snmp-pass]: 2(与官方 tutorial 一致)",
                "@[snmp-pass]: 2" in rep, rep)
    ok &= check("stats() 渲染为 count/average/total 三件套",
                "@bytes[bash]: count 3, average 575, total 1725" in rep, rep)

    print("== 9. 作用域与 map 生命周期 ==")
    e6 = Engine(parse("kprobe:vfs_read { $x = 1; @g = 2; } kprobe:vfs_write { @g = @g + 1; }"))
    e6.feed("kprobe:vfs_read", {})
    ok &= check("$scratch 出块即不可见", isinstance(e6.get(("scratch", "x"), {}), Unset))
    e6.feed("kprobe:vfs_write", {})
    ok &= check("@map 跨块存活并被累加(@g: 2 -> 3)", e6.maps.get(("g", ())) == 3, str(e6.maps))

    print("== 10. 三元运算符与算术 ==")
    e7 = Engine(parse("BEGIN { @m = avg(arg0); }"))
    for v in (10, 20, 33):
        e7.feed("begin", {"arg0": v})
    ok &= check("avg(10,20,33) = 63//3 = 21", e7.agg[("m", ())].render() == ["21"],
                str(e7.agg[("m", ())].render()))
    bt = Engine(parse('BEGIN { @r = count(); }'))
    bt.feed("begin", {})
    ok &= check("count() 无参时计 1 次", bt.agg[("r", ())].render() == ["1"])

    print("\n" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
