"""Off-Wake 调度延迟归因 —— 自检（实跑）。

锚点全部来自 Brendan Gregg《Off-CPU Analysis》。
"""

from offwake import (
    TASK_INTERRUPTIBLE,
    TASK_RUNNING,
    TASK_UNINTERRUPTIBLE,
    OffCpuTracer,
    Sample,
    elapsed_split,
    folded,
    parse_folded,
)

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


# --- 1. 原文伪代码的行为 ------------------------------------------------------
tr = OffCpuTracer()
r1 = tr.on_switch_finish(prev_tid=1, prev_state=TASK_UNINTERRUPTIBLE,
                         cur_tid=2, cur_stack="b;c", ts=100.0)
check("首次切换不给被切上的线程记账", r1 is None, "sleeptime[cur] 为 0 ⇒ return")
check("但给 prev 打了起点", tr.sleeptime[1] == 100.0, f"sleeptime[1]={tr.sleeptime[1]}")

tr.on_wakeup(1, ts=130.0, waker_stack="w1;w2")
r2 = tr.on_switch_finish(prev_tid=2, prev_state=TASK_RUNNING,
                         cur_tid=1, cur_stack="a;b", ts=140.0)
check("第二次切换结算 cur 的等待", r2 is not None and close(r2.off_cpu, 40.0),
      f"off_cpu={r2.off_cpu}")
check("结算后 sleeptime 清零而非删除", tr.sleeptime[1] == 0, f"{tr.sleeptime[1]}")
check("off-CPU 含调度延迟（时间膨胀）", close(r2.blocked, 30.0) and close(r2.sched_latency, 10.0),
      f"blocked={r2.blocked} sched={r2.sched_latency}")
check("off_cpu = blocked + sched_latency",
      close(r2.off_cpu, r2.blocked + r2.sched_latency), f"{r2.off_cpu}")
check("膨胀比 > 1", r2.inflation > 1.0, f"inflation={r2.inflation:.4f}")

# 没有唤醒点时，全部时间都算阻塞
s_plain = Sample(tid=9, start=0.0, end=25.0, state=2, stack="x")
check("无唤醒点 ⇒ sched_latency=0", close(s_plain.sched_latency, 0.0) and close(s_plain.blocked, 25.0),
      "")
check("无唤醒点 ⇒ 膨胀比=1", close(s_plain.inflation, 1.0), f"{s_plain.inflation}")

# --- 2. 时间膨胀：相同阻塞，不同调度延迟 --------------------------------------
tr2 = OffCpuTracer()
# 两个线程 t=1 同时阻塞、t=11 同时被唤醒（真阻塞都是 10），但上 CPU 的时刻差 70
for tid in (1, 2):
    tr2.sleeptime[tid] = 1.0          # 注意 0 在伪代码里表示「没在睡」
    tr2.state[tid] = TASK_UNINTERRUPTIBLE
    tr2.on_wakeup(tid, 11.0)
a = tr2.on_switch_finish(3, TASK_RUNNING, 1, "read", 21.0)     # 唤醒后立刻上 CPU
b = tr2.on_switch_finish(3, TASK_RUNNING, 2, "read", 91.0)     # 被压在运行队列 80
check("相同真阻塞时间", close(a.blocked, 10.0) and close(b.blocked, 10.0),
      f"{a.blocked} vs {b.blocked}")
check("调度延迟差 8 倍", close(b.sched_latency, 8 * a.sched_latency),
      f"{a.sched_latency} vs {b.sched_latency}")
check("off-CPU 被调度延迟放大", close(a.off_cpu, 20.0) and close(b.off_cpu, 90.0),
      f"{a.off_cpu} vs {b.off_cpu}")
check("膨胀比 2.0 vs 9.0", close(a.inflation, 2.0) and close(b.inflation, 9.0),
      f"{a.inflation:.2f} vs {b.inflation:.2f}")

# --- 3. 状态过滤 --------------------------------------------------------------
tr3 = OffCpuTracer(state_filter=2)
tr3.sleeptime[1] = 1.0
tr3.sleeptime[2] = 1.0
tr3.sleeptime[3] = 1.0
tr3.state[1] = TASK_UNINTERRUPTIBLE
tr3.state[2] = TASK_INTERRUPTIBLE
tr3.state[3] = TASK_RUNNING
tr3.on_switch_finish(9, TASK_RUNNING, 1, "io", 100.0)
tr3.on_switch_finish(9, TASK_RUNNING, 2, "sleep", 100.0)
tr3.on_switch_finish(9, TASK_RUNNING, 3, "spin", 100.0)
check("样本全被记录", len(tr3.samples) == 3, f"{len(tr3.samples)}")
check("--state 2 只留 UNINTERRUPTIBLE",
      [s.stack for s in tr3.filtered()] == ["io"], f"{[s.stack for s in tr3.filtered()]}")
check("非自愿切换(TASK_RUNNING)被排除",
      all(s.state != TASK_RUNNING for s in tr3.filtered()), "")

# --- 4. 按栈聚合 + 线程池过滤 --------------------------------------------------
tr4 = OffCpuTracer()
for i, (stk, dur, state) in enumerate([
    ("mysqld;do_nanosleep;hrtimer_nanosleep", 900.0, TASK_INTERRUPTIBLE),
    ("mysqld;futex_wait_queue_me", 600.0, TASK_INTERRUPTIBLE),
    ("mysqld;read_events;do_io_getevents", 400.0, TASK_INTERRUPTIBLE),
    ("mysqld;do_command;dispatch_command;read", 80.0, TASK_UNINTERRUPTIBLE),
    ("mysqld;do_command;dispatch_command;futex", 20.0, TASK_UNINTERRUPTIBLE),
]):
    tid = 100 + i
    tr4.sleeptime[tid] = 0.0
    tr4.state[tid] = state
    tr4.samples.append(Sample(tid=tid, start=0.0, end=dur, state=state, stack=stk))

agg = tr4.by_stack()
total = sum(agg.values())
check("总 off-CPU 时间", close(total, 2000.0), f"{total}")
pool = sum(v for k, v in agg.items() if "do_command" not in k)
check("等待工作的栈占 95%", close(pool / total, 0.95), f"{pool / total:.4f}")
req = tr4.request_sync("do_command")
check("grep do_command 只剩 2 条", len(req) == 2, f"{len(req)}")
check("请求同步路径只占 5%", close(sum(s.off_cpu for s in req) / total, 0.05),
      f"{sum(s.off_cpu for s in req) / total:.4f}")
check("过滤后不含等待工作栈", all("do_command" in s.stack for s in req), "")

# --- 5. off-wake 联合归因 ------------------------------------------------------
tr5 = OffCpuTracer()
for tid, waker, waiter, dur in [
    (1, "irq;net_rx_action;tcp_data", "app;read", 50.0),
    (2, "worker;disk_done;blk_complete", "app;read", 70.0),
    (3, "irq;net_rx_action;tcp_data", "app;write", 30.0),
]:
    tr5.samples.append(Sample(tid=tid, start=0.0, end=dur, state=2,
                              stack=waiter, waker_stack=waker))
ow = tr5.off_wake()
check("同一阻塞栈因唤醒者不同被拆开",
      sum(1 for (w, s) in ow if s == "app;read") == 2, f"{sorted(ow)}")
check("off-wake 的键是 (waker, waiter) 二元组",
      ("irq;net_rx_action;tcp_data", "app;read") in ow, "")
check("off-wake 总宽度仍是 off-CPU 时间", close(sum(ow.values()), 150.0), f"{sum(ow.values())}")
check("单纯按阻塞栈聚合会丢信息", len(tr5.by_stack()) < len(ow),
      f"by_stack={len(tr5.by_stack())} < off_wake={len(ow)}")

# --- 6. 折叠格式 --------------------------------------------------------------
line = folded("a;b;c", 31)
check("折叠格式一行一栈", line == "a;b;c 31", line)
check("折叠格式可解析回来", parse_folded([line])["a;b;c"] == 31.0, "")
check("同名栈累加", parse_folded(["a;b;c 31", "a;b;c 2"])["a;b;c"] == 33.0, "")

# --- 7. 时间拆分输出 ----------------------------------------------------------
sp = elapsed_split(r2)
check("拆分四字段齐全", set(sp) == {"off_cpu", "blocked", "sched_latency", "inflation"}, f"{sorted(sp)}")

print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
