#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JFR 与 async-profiler：async 采样如何绕开 safepoint 偏差

被对照的官方资料（本轮实际读过）：
  * async-profiler README —— "does not suffer from Safepoint bias problem"、
    `--ttsp`（time-to-safepoint）、`-XX:+DebugNonSafepoints` 对内联方法的影响
  * docs/CpuSamplingEngines.md —— cpu/itimer/ctimer 三引擎对照表、每线程一个 perf 描述符、
    itimer 的「一次只能投递一个信号 / 不均分 / 受 jiffy 限制」、ctimer 与 HZ、
    「1 个样本 = 1 个核忙了 N 纳秒」
  * docs/StackWalkingModes.md —— FP / DWARF / VM Structs；4.2 起默认 vm；
    AsyncGetCallTrace 的 JDK-8307549 / JDK-8178287 与「可能崩 JVM」
  * JEP 328（JFR）—— ≤1% 开销、无开销（未启用时）、线程本地无锁缓冲 → 全局环形缓冲、
    自描述二进制（little endian base 128）、24 字节的 class load 事件实例
  * JEP 349（JFR Event Streaming）—— 线程本地缓冲**每秒**刷一次到磁盘仓库、
    只读取被订阅的事件、<1% 开销

运行：python asyncprof_safepoint.py
"""
from collections import defaultdict

# ------------------------------------------------- 一、safepoint 偏差模型

class Method:
    """一段被采样的方法：逐行给出耗时与「该行是否是 safepoint 轮询点」"""

    def __init__(self, lines):
        # lines: [(行号, 是否轮询点, 耗时)]
        self.lines = lines

    def timeline(self):
        t, out = 0, []
        for no, poll, cost in self.lines:
            out.append((t, t + cost, no, poll))
            t += cost
        return out, t

    def line_at(self, t):
        for a, b, no, poll in self.timeline()[0]:
            if a <= t < b:
                return no, poll
        return None, False

    def next_poll_after(self, t):
        for a, b, no, poll in self.timeline()[0]:
            if a >= t and poll:
                return no
        return None


def sample_async(method, interval, total):
    """async-profiler：信号来了就地取栈 ⇒ 样本落在**真正执行的那一行**"""
    hist = defaultdict(int)
    t = interval // 2
    while t < total:
        no, _ = method.line_at(t)
        if no is not None:
            hist[no] += 1
        t += interval
    return dict(hist)


def sample_at_safepoints(method, interval, total):
    """只能在 safepoint 取栈的剖析器：采样点不在轮询点上 ⇒ 推迟到**下一个**轮询点"""
    hist = defaultdict(int)
    t = interval // 2
    while t < total:
        no, poll = method.line_at(t)
        if no is None:
            break
        if poll:
            hist[no] += 1
        else:
            target = method.next_poll_after(t)
            if target is not None:
                hist[target] += 1
            # target is None ⇒ 这段之后再也没有 safepoint，样本彻底丢失（不记账）
        t += interval
    return dict(hist)


# --------------------------------------------- 二、三种 CPU 采样引擎

def cpu_engine_samples(cores, utilisation, interval_ns):
    """官方原文：2 核 × 30% × (1s / 10ms) = 60 样本/秒"""
    per_core = 1_000_000_000 // interval_ns
    return int(cores * utilisation * per_core)


def itimer_distribution(threads_busy_ns, interval_ns, pick_first=True):
    """ITIMER_PROF：一次只能给进程投递一个信号，且不按线程均分。
    demo 用「总是挑第一个可运行线程」模拟这种不均（口径见 README 表述）。"""
    ticks = sum(threads_busy_ns) // interval_ns
    dist = [0] * len(threads_busy_ns)
    for _ in range(ticks):
        for i, b in enumerate(threads_busy_ns):
            if b > 0:
                dist[i] += 1
                if pick_first:
                    break
    return dist


def min_interval_ms(hz):
    """ctimer/cpu 的分辨率受 jiffy 限制：HZ=100 ⇒ 10ms，HZ=250 ⇒ 4ms"""
    return 1000 // hz


def cpu_engine_caps():
    """docs/CpuSamplingEngines.md 的对照表（✅/❌/🆗 原样录入）"""
    return {
        "cpu":    dict(kernel_stacks=True,  high_res=True,  fair=True,  container=False, no_fd=False, mac=False),
        "itimer": dict(kernel_stacks=False, high_res=False, fair=False, container=True,  no_fd=True,  mac=True),
        "ctimer": dict(kernel_stacks=False, high_res=False, fair=None,  container=True,  no_fd=True,  mac=False),
    }


# ------------------------------------------------- 三、栈行走模式

def unwind_fp(frames, depth=64):
    """FP 行走：遇到没保留帧指针的帧就断（代码必须 -fno-omit-frame-pointer）"""
    out = []
    for f in frames[:depth]:
        out.append(f)
        if not f.has_fp:
            break
    return out


def unwind_dwarf(frames, depth=64):
    """DWARF 行走：靠 .eh_frame / .debug_frame，优化掉帧指针也能走通"""
    return frames[:depth]


class Frame:
    def __init__(self, name, kind="java", has_fp=True):
        self.name, self.kind, self.has_fp = name, kind, has_fp

    def __repr__(self):
        return self.name


# ------------------------------------------------- 四、JFR 编码与缓冲

def leb128(buf, i):
    """JEP 328：自描述二进制用 little endian base 128（文件头与少数段除外）"""
    val, shift, n = 0, 0, 0
    while True:
        b = buf[i + n]
        val |= (b & 0x7F) << shift
        n += 1
        if not (b & 0x80):
            break
        shift += 7
    return val, i + n


class ThreadLocalBuffer:
    """JEP 328：线程无锁写本地缓冲，满了晋升到全局**环形**缓冲"""

    def __init__(self, global_capacity):
        self.local, self.cap = [], global_capacity
        self.global_ring = []

    def emit(self, ev):
        self.local.append(ev)
        if len(self.local) >= 4:            # 满了 ⇒ 晋升
            self.global_ring.extend(self.local)
            self.local = []
            if len(self.global_ring) > self.cap:
                self.global_ring = self.global_ring[-self.cap:]   # 只留最近

    def flush(self):
        """JEP 349：线程本地缓冲**每秒**刷一次到磁盘仓库，订阅者才看得到"""
        out, self.local = self.local, []
        return out


# ---------------------------------------------------------------- 自检
PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1


HOT = Method([(10, False, 30),     # 0..30   热点循环，内部没有轮询点
              (11, True, 2),       # 30..32  轮询点
              (12, False, 28),     # 32..60  另一段热点
              (13, True, 2)])      # 60..62  轮询点


def check_safepoint_bias():
    total = HOT.timeline()[1]
    a = sample_async(HOT, 10, total)
    s = sample_at_safepoints(HOT, 10, total)
    ok(a == {10: 3, 12: 3}, "async 采样把样本落在真正执行的第 10/12 行")
    ok(s == {11: 3, 13: 3}, "只能在 safepoint 取栈 ⇒ 样本全被记到轮询点 11/13")
    ok(a != s, "两种采样给出**不同**的热点行 ⇒ 这就是 safepoint bias")
    ok(sum(a.values()) == sum(s.values()) == 6, "样本总数一样，只是归属被挪了")

    # 跑在原生代码里、整段都没有 safepoint 的线程
    native = Method([(1, False, 100)])
    ok(sample_async(native, 10, 100) == {1: 10}, "async 照样采到 10 个样本")
    ok(sample_at_safepoints(native, 10, 100) == {}, "safepoint 剖析器一个样本都拿不到")


def check_engines():
    ok(cpu_engine_samples(2, 0.30, 10_000_000) == 60,
       "2 核 × 30% × 100/秒 = 60 样本/秒（官方原文算例）")
    ok(cpu_engine_samples(1, 1.0, 10_000_000) == 100, "单核跑满 10ms 间隔 ⇒ 100 样本/秒")

    dist = itimer_distribution([1000, 1000, 1000], 10)
    ok(dist[0] == sum(dist), "itimer 的信号全落在一个线程上（不均分的极端情形）")

    ok(min_interval_ms(100) == 10, "HZ=100 ⇒ 最小采样间隔 10ms")
    ok(min_interval_ms(250) == 4, "HZ=250 ⇒ 最小采样间隔 4ms")

    caps = cpu_engine_caps()
    ok(caps["cpu"]["kernel_stacks"] and not caps["itimer"]["kernel_stacks"],
       "只有 perf_events 引擎能拿内核栈")
    ok(caps["itimer"]["container"] and caps["ctimer"]["container"] and not caps["cpu"]["container"],
       "cpu 引擎默认在容器里跑不起来（paranoid / seccomp）")
    ok(caps["itimer"]["mac"] and not caps["cpu"]["mac"], "macOS 上只有 itimer")
    ok(caps["cpu"]["no_fd"] is False, "cpu 引擎每线程一个描述符 ⇒ 可能撑爆 ulimit -n")


def check_stack_walking():
    frames = [Frame("javaMethod"), Frame("stub", "jvm", has_fp=False), Frame("native", "c")]
    ok([f.name for f in unwind_fp(frames)] == ["javaMethod", "stub"],
       "FP 行走在没保留帧指针的帧处断掉 ⇒ 丢掉 native 部分")
    ok(len(unwind_dwarf(frames)) == 3, "DWARF 行走能把整条栈走通")
    ok(len(unwind_dwarf(frames, depth=2)) == 2, "-j depth 限制栈深度（vm/vmx 模式）")


def check_jfr_encoding():
    """JEP 328 给的 24 字节 class load 事件实例"""
    raw = bytes([0x98, 0x80, 0x80, 0x00, 0x87, 0x02,
                 0x95, 0xAE, 0xE4, 0xB2, 0x92, 0x03,
                 0xA2, 0xF7, 0xAE, 0x9A, 0x94, 0x02,
                 0x02, 0x01, 0x8D, 0x11, 0x00, 0x00])
    ok(len(raw) == 24, "官方示例总长 24 字节")
    size, i = leb128(raw, 0)
    ok(size == 24, "首字段（事件大小）解出 24，与注释一致")
    ok(i == 4, "事件大小占 4 个字节（base 128 变长）")
    ev_id, i = leb128(raw, i)
    ok(ev_id == 263, "事件 ID = 263")
    ts, i = leb128(raw, i)
    ok(ts > 0, "时间戳解出正值（纳秒）")
    dur, i = leb128(raw, i)
    tid, i = leb128(raw, i)
    ok(tid == 2, "线程 ID = 2")
    sid, i = leb128(raw, i)
    ok(sid == 1, "栈轨迹 ID = 1")
    ok(i == len(raw) - 4, "剩下 4 字节是事件负载（loaded class + 两个 classloader）")


def check_jfr_buffers():
    b = ThreadLocalBuffer(global_capacity=6)
    ok(b.flush() == [], "还没写事件 ⇒ 刷新出来是空的")
    for n in range(8):                      # 每 4 条晋升一次
        b.emit("ev%d" % n)
    ok(b.global_ring == ["ev2", "ev3", "ev4", "ev5", "ev6", "ev7"],
       "全局环形缓冲只保留**最近** 6 条，最旧的 ev0/ev1 被丢弃")
    b.emit("ev8")
    b.emit("ev9")
    ok("ev8" not in b.global_ring and "ev9" not in b.global_ring,
       "本地缓冲没满 ⇒ 还没晋升，订阅者此刻看不到 ev8/ev9（JEP 349：每秒刷新一次）")
    ok(b.flush() == ["ev8", "ev9"], "刷新点到了才把本地缓冲推给订阅者")
    ok(len(b.global_ring) <= 6, "环形缓冲容量约束始终生效")


if __name__ == "__main__":
    for fn in (check_safepoint_bias, check_engines, check_stack_walking,
               check_jfr_encoding, check_jfr_buffers):
        fn()
    print("PASS %d assertions" % PASS)
