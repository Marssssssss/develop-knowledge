"""CPU 利用率口径与 IPC 归因 —— 自检（实跑）。

断言全部以《CPU Utilization is Wrong》原文给出的实测数字为锚：
cycles=1,433,972,173,374 / instructions=1,118,336,816,068 /
task-clock=641,398.723351 ms / elapsed=10.003794539 s /
IPC 0.78 / 2.236 GHz / 64.116 CPUs utilized。
"""

from cpu_util import (
    ProcStat,
    avg_hides_burst,
    busy_pct,
    clock_ghz,
    cpus_utilized,
    delta_pct,
    hyperthread_headroom,
    idle_thread_pct,
    ipc,
    is_spin_lock,
    parse_procstat,
    pct_of_peak,
    stall_split,
    summarize,
    verdict,
)

# 原文 perf stat 的实测值
CYCLES = 1_433_972_173_374
INSTR = 1_118_336_816_068
TASK_CLOCK_MS = 641_398.723351
ELAPSED_MS = 10_003.794539

_passed = 0
_failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"ok   {label} {detail}")
    else:
        _failed += 1
        print(f"FAIL {label} {detail}")


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


# --- 1. /proc/stat 解析与配平 -------------------------------------------------
st = parse_procstat("cpu  100 200 300 400 50 60 70 80 9 9")
check("parse 10 字段", (st.user, st.steal, st.guest, st.guest_nice) == (100, 80, 9, 9),
      f"user={st.user} steal={st.steal} guest={st.guest}")
check("total 不含 guest", st.total() == 1260, f"total={st.total()}（100+200+300+400+50+60+70+80）")

pcts = delta_pct(ProcStat(), st)
check("各态占比和为 100", close(sum(pcts.values()), 100.0, 1e-9), f"sum={sum(pcts.values()):.9f}")
check("idle 占比", close(pcts["idle"], 400 * 100.0 / 1260, 1e-9), f"idle={pcts['idle']:.4f}%")
check("busy = 100 − idle", close(busy_pct(pcts), 100.0 - pcts["idle"], 1e-9),
      f"busy={busy_pct(pcts):.4f}%")
check("busy 吞掉 iowait", busy_pct(pcts) > 100.0 - pcts["idle"] - pcts["iowait"],
      f"busy={busy_pct(pcts):.2f}% > 非 iowait 的 {100 - pcts['idle'] - pcts['iowait']:.2f}%")
# 两个口径**都**含 iowait：busy 把它算成忙、idle 线程口径把它算成闲，
# 于是两者相加不是 100 而是 100 + iowait —— 这就是口径冲突的量化证据。
check("busy 与真空闲口径重叠恰为 iowait",
      close(busy_pct(pcts) + idle_thread_pct(pcts), 100.0 + pcts["iowait"], 1e-9),
      f"{busy_pct(pcts):.4f} + {idle_thread_pct(pcts):.4f} = {busy_pct(pcts) + idle_thread_pct(pcts):.4f}")
check("扣掉 iowait 才是真在干活",
      close(busy_pct(pcts) - pcts["iowait"], 100.0 - idle_thread_pct(pcts), 1e-9),
      f"{busy_pct(pcts) - pcts['iowait']:.4f}%")

# --- 2. IPC 与定性 ------------------------------------------------------------
got_ipc = ipc(INSTR, CYCLES)
check("IPC 复现原文 0.78", close(got_ipc, 0.78, 5e-3), f"IPC={got_ipc:.6f}")
check("IPC<1.0 ⇒ 内存停顿", verdict(0.78) == "memory", f"verdict={verdict(0.78)}")
check("IPC>1.0 ⇒ 指令受限", verdict(1.44) == "instruction", f"verdict={verdict(1.44)}")
check("1.0 是开区间分界", verdict(1.0) == "instruction" and verdict(0.999) == "memory",
      "原文规则是 IPC < 1.0 才算 memory")
check("4-wide 上 0.78 = 峰值 19.5%", close(pct_of_peak(0.78, 4.0), 19.5, 1e-9),
      f"{pct_of_peak(0.78, 4.0):.4f}%")
check("峰值宽度换算", close(pct_of_peak(4.0, 4.0), 100.0, 1e-9), "IPC=width ⇒ 100%")

# --- 3. 停顿周期拆分 ----------------------------------------------------------
sp = stall_split(CYCLES, INSTR, 4.0)
check("retired + stalled = cycles", close(sp["retired_cycles"] + sp["stalled_cycles"], CYCLES, 1e-6),
      f"{sp['retired_cycles']:.0f} + {sp['stalled_cycles']:.0f}")
check("原文样例约 80.5% 停顿", close(sp["pct_stl"], 80.5, 1e-1), f"pct_stl={sp['pct_stl']:.4f}%")
check("%INS + %STL = 100", close(sp["pct_ins"] + sp["pct_stl"], 100.0, 1e-9),
      f"{sp['pct_ins']:.4f} + {sp['pct_stl']:.4f}")
sp0 = stall_split(1000, 4000, 4.0)
check("IPC=4 时无停顿", close(sp0["pct_stl"], 0.0, 1e-9), f"pct_stl={sp0['pct_stl']}")

# --- 4. 主频与占用核数 --------------------------------------------------------
check("反推主频 2.236 GHz", close(clock_ghz(CYCLES, TASK_CLOCK_MS), 2.236, 1e-3),
      f"{clock_ghz(CYCLES, TASK_CLOCK_MS):.6f} GHz")
check("64.116 CPUs utilized", close(cpus_utilized(TASK_CLOCK_MS, ELAPSED_MS), 64.116, 1e-2),
      f"{cpus_utilized(TASK_CLOCK_MS, ELAPSED_MS):.4f}")

# --- 5. 四类误导 --------------------------------------------------------------
check("自旋锁：高 busy + 高 IPC + 零前进", is_spin_lock(100.0, 3.9, False) is True, "")
check("同样高 busy 但 IPC 低 ⇒ 不是自旋", is_spin_lock(100.0, 0.4, False) is False, "内存停顿")
check("有前进就不是自旋", is_spin_lock(100.0, 3.9, True) is False, "")
burst = avg_hides_burst([100.0] * 48 + [0.0] * 12)
check("60 秒均值 80% 掩盖 100% 突发",
      close(burst["avg"], 80.0, 1e-9) and close(burst["peak"], 100.0, 1e-9)
      and close(burst["burst_frac"], 0.8, 1e-9),
      f"avg={burst['avg']} peak={burst['peak']} burst={burst['burst_frac']}")
check("超线程余量取最小停顿占比", close(hyperthread_headroom([0.8, 0.3]), 0.3, 1e-9),
      f"{hyperthread_headroom([0.8, 0.3])}")
check("超线程余量非空", hyperthread_headroom([0.05]) <= 0.05, "余量小 ⇒ 偷不到")

# --- 6. 结论串 ----------------------------------------------------------------
lines = summarize(pcts, 0.78)
check("结论三行且含 iowait", len(lines) == 3 and "iowait" in lines[0], lines[0])
check("内存停顿给内存建议", "内存" in lines[2], lines[2])
check("指令受限给代码建议", "代码" in summarize(pcts, 1.44)[2], summarize(pcts, 1.44)[2])

print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
