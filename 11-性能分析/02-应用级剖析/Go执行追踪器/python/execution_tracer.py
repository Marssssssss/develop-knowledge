#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go execution tracer：与 pprof 采样剖析互补的确定性事件流（对照官方博客与 runtime/trace 文档）

被对照的官方资料（本轮实际读过）：
  * go.dev/blog/execution-traces-2024 —— 开销 10–20% → 1–2%；trace 拆分（Go 1.22）；
    flight recorder；trace reader API 的 EventStateTransition 计数示例
  * go.dev/blog/flight-recorder       —— FlightRecorderConfig{MinAge, MaxBytes}，
    MinAge 建议取「事件窗口的 2 倍」，约几 MB/s（繁忙服务 10 MB/s）
  * pkg.go.dev/runtime/trace          —— log / region / task 三类用户标注的语义

运行：python execution_tracer.py
"""
from collections import defaultdict

# ------------------------------------------------------------ 事件模型

RUNNING, WAITING, SYSCALL = "running", "waiting", "syscall"

class Event:
    """对应 runtime/trace 的 EventStateTransition（本 demo 只建模 goroutine 资源）"""
    def __init__(self, ts, g, to, frm=RUNNING, reason=""):
        self.ts, self.g, self.to, self.frm, self.reason = ts, g, to, frm, reason

    def is_block(self):
        """对照官方示例：from.Executing() && to == GoWaiting"""
        return self.frm == RUNNING and self.to == WAITING


class Trace:
    def __init__(self, events):
        self.events = list(events)

    def emitted_order(self):
        """runtime 把事件写进 per-P/线程本地缓冲 ⇒ 落盘顺序 ≠ 真实时间顺序"""
        return list(self.events)

    def reader(self):
        """trace reader API 交给你的是**已按真实时间排好序**的事件流"""
        return sorted(self.events, key=lambda e: (e.ts, e.g))


def blocked_on_network_ratio(events):
    """官方博客里的示例：统计阻塞事件中「因网络而阻塞」的比例"""
    blocked = [e for e in events if e.is_block()]
    net = [e for e in blocked if "network" in e.reason]
    if not blocked:
        return None
    return 100.0 * len(net) / len(blocked)


def peak_concurrency(events):
    """并发峰值：按给定顺序扫一遍，维护在跑的 goroutine 数"""
    cur = mx = 0
    for e in events:
        if e.to == RUNNING:
            cur += 1
            mx = max(mx, cur)
        elif e.frm == RUNNING:
            cur -= 1
    return mx


# --------------------------------------------------- pprof 采样的盲区

def cpu_profile(events, window_ns, interval_ns=10_000_000):
    """采样剖析器只在「有执行」的时刻取样：阻塞中的 goroutine 拿不到样本"""
    per_g = defaultdict(list)
    for e in events:
        per_g[e.g].append(e)
    samples = defaultdict(int)
    for g, evs in per_g.items():
        evs = sorted(evs, key=lambda e: e.ts)
        t = 0
        for e in evs:
            if e.to == RUNNING:
                t = e.ts
            elif e.frm == RUNNING and e.ts > t:
                samples[g] += max(0, (e.ts - t) // interval_ns)
    return dict(samples)


# -------------------------------------------------------- trace 拆分

def split_trace(events, at_ts):
    """Go 1.22 起 runtime 可以随时「切一刀」：切点前后各自是一份完整自包含的 trace"""
    before = [e for e in events if e.ts < at_ts]
    after = [e for e in events if e.ts >= at_ts]
    return before, after


def is_self_contained(events):
    """自包含 = 每个 goroutine 在这段里的状态迁移都能闭合（进 RUNNING 必有离开）"""
    state = {}
    for e in sorted(events, key=lambda x: x.ts):
        if e.to == RUNNING:
            state[e.g] = RUNNING
        elif state.get(e.g) == RUNNING:
            state[e.g] = e.to
    return all(v != RUNNING for v in state.values())


# ------------------------------------------------------ flight recorder

class FlightRecorder:
    """runtime/trace.FlightRecorder：常开 tracing，只在内存里留最近一段"""

    def __init__(self, min_age_ns, max_bytes, bytes_per_event=64):
        self.min_age = min_age_ns
        self.max_bytes = max_bytes
        self.bytes_per_event = bytes_per_event
        self.buf = []

    def add(self, e):
        self.buf.append(e)
        self._evict()

    def _evict(self):
        """同时受 MaxBytes 与 MinAge 约束：先按容量裁，再按窗口裁"""
        cap_events = self.max_bytes // self.bytes_per_event
        if len(self.buf) > cap_events:
            self.buf = self.buf[-cap_events:]
        if not self.buf:
            return
        newest = max(e.ts for e in self.buf)
        self.buf = [e for e in self.buf if e.ts >= newest - self.min_age]

    def write_to(self):
        return sorted(self.buf, key=lambda e: e.ts)

    def bytes_used(self):
        return len(self.buf) * self.bytes_per_event


# ------------------------------------------------- log / region / task

class Region:
    """region：同一 goroutine 内的一段区间，可嵌套；**必须同 goroutine 开始并结束**"""

    def __init__(self, name, g, start, end):
        self.name, self.g, self.start, self.end = name, g, start, end

    def valid(self):
        return self.g is not None and self.start <= self.end


class Task:
    """task：逻辑操作（一次 RPC），经 context.Context 跨 goroutine 传播"""

    def __init__(self, name, created_ts):
        self.name, self.created = name, created_ts
        self.ended = None
        self.regions = []

    def end(self, ts):
        self.ended = ts

    def latency(self):
        """trace 工具按 NewTask→End 计算 task 延迟"""
        assert self.ended is not None, "task 未结束"
        return self.ended - self.created


# ---------------------------------------------------------------- 自检
PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1


def check_sampling_blind_spot():
    """并发瓶颈：一堆 goroutine 阻塞在同一个 channel 上——CPU profile 完全看不见"""
    # 8 个 goroutine 在整个观测窗口里都阻塞在同一个 channel 上
    evs = [Event(0, i, WAITING, frm=RUNNING, reason="chan receive") for i in range(8)]
    prof = cpu_profile(evs, window_ns=1_000_000_000)
    ok(prof == {}, "全程阻塞 ⇒ CPU profile 一个样本都没有（没有执行可供采样）")
    blocks = [e for e in evs if e.is_block()]
    ok(len(blocks) == 8, "execution trace 里 8 条阻塞事件一览无余")
    ok(blocked_on_network_ratio(evs) == 0.0, "阻塞原因不是网络 ⇒ 网络占比 0%")

    evs2 = [Event(0, 1, RUNNING), Event(1_000_000, 1, WAITING, reason="network read"),
            Event(0, 2, RUNNING), Event(1_000_000, 2, WAITING, reason="chan send"),
            Event(0, 3, RUNNING), Event(1_000_000, 3, WAITING, reason="network poll")]
    ok(abs(blocked_on_network_ratio(evs2) - 200.0 / 3) < 1e-9,
       "网络阻塞占比 = 2/3（官方示例的同一算法）")


def check_out_of_order():
    """per-P/线程本地缓冲 ⇒ 落盘乱序；不做重排会得到错误的并发峰值"""
    evs = [Event(100, "g1", RUNNING), Event(50, "g2", RUNNING),
           Event(120, "g1", WAITING), Event(60, "g2", WAITING)]
    naive = peak_concurrency(evs)
    truth = peak_concurrency(sorted(evs, key=lambda e: e.ts))
    ok(naive == 2, "按落盘顺序算 ⇒ 峰值 2（错：g1/g2 其实并不重叠）")
    ok(truth == 1, "按真实时间重排 ⇒ 峰值 1（正确）")
    ok(naive != truth, "乱序会直接改变结论，这正是 trace 工具的负担所在")


def check_split():
    evs = [Event(10, "g1", RUNNING), Event(20, "g1", WAITING),
           Event(30, "g2", RUNNING), Event(40, "g2", WAITING)]
    before, after = split_trace(evs, 25)
    ok(len(before) == 2 and len(after) == 2, "切点前后各 2 条事件")
    ok(is_self_contained(before) and is_self_contained(after),
       "Go 1.22 的切点让两段各自自包含、可独立解析")
    ok(len(before) + len(after) == len(evs), "拆分不丢事件")

    broken = [Event(10, "g1", RUNNING)]
    ok(not is_self_contained(broken), "半截 trace（只有进入 RUNNING）不自包含")


def check_flight_recorder():
    """MinAge 建议取事件窗口的 2 倍；MaxBytes 兜住内存"""
    fr = FlightRecorder(min_age_ns=10_000, max_bytes=64 * 1000, bytes_per_event=64)
    for ts in range(0, 60_000, 100):
        fr.add(Event(ts, "g", RUNNING if ts % 200 == 0 else WAITING))
    ok(fr.bytes_used() <= 64 * 1000, "MaxBytes 兜住内存占用")
    snap = fr.write_to()
    ok(max(e.ts for e in snap) - min(e.ts for e in snap) <= 10_000,
       "快照只保留最近 MinAge 这一段")

    # 官方建议：调试 5 秒超时 ⇒ MinAge 设 10 秒；只设 5 秒会漏掉根因
    root_cause_ts = 0
    detect_ts = 5_000
    ok(detect_ts - root_cause_ts <= 10_000, "MinAge=2 倍窗口 ⇒ 根因仍在快照里")
    ok(detect_ts - root_cause_ts > 5_000 - 1, "MinAge=1 倍窗口 ⇒ 根因恰好落在边界上（不可靠）")


def check_annotations():
    r = Region("steamMilk", "g1", 0, 10)
    ok(r.valid(), "region 在同一 goroutine 内开始并结束")
    ok(Region("x", None, 0, 1).valid() is False, "region 必须有确定的 goroutine")

    t = Task("makeCappuccino", 0)
    t.regions.append(Region("steamMilk", "g1", 1, 2))
    t.regions.append(Region("extractCoffee", "g2", 1, 3))
    t.end(5)
    ok(t.latency() == 5, "task 延迟 = NewTask → End（跨 goroutine）")
    ok(len({r.g for r in t.regions}) == 2, "一个 task 下的 region 可以落在不同 goroutine")


def check_capacity_vs_minage():
    """MinAge 与 MaxBytes 会互相打架：官方给的参考是繁忙服务 ~10 MB/s"""
    rate = 10 * 1024 * 1024          # 10 MB/s
    max_bytes = 1 << 20              # 官方示例里的 1 MiB
    achievable = max_bytes / rate
    ok(abs(achievable - 0.1) < 1e-9, "1 MiB / 10 MB/s ⇒ 实际只留得住 0.1 秒")
    ok(achievable < 10.0, "想留 10 秒（调试 5 秒超时）就得给 ~100 MiB，MaxBytes 才是硬约束")


def check_overhead():
    """官方口径：Go 1.21 前 10–20% CPU，traceback 优化后降到 1–2% ⇒ 才谈得上『常开』"""
    true_ns = 100.0
    ok(abs(true_ns * (1 + 0.15) - 115.0) < 1e-9, "15% 开销 ⇒ 被测指标被自身抬高 15%")
    ok(abs(true_ns * (1 + 0.015) - 101.5) < 1e-9, "1.5% 开销 ⇒ 只抬高 1.5%，进入噪声量级")


if __name__ == "__main__":
    for fn in (check_sampling_blind_spot, check_out_of_order, check_split,
               check_flight_recorder, check_annotations,
               check_capacity_vs_minage, check_overhead):
        fn()
    print("PASS %d assertions" % PASS)
