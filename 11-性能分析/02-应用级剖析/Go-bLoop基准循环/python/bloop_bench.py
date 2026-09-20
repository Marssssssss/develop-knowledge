#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go 1.24 的 testing.B.Loop：与 b.N 风格的差异（对照 src/testing/benchmark.go 与 Go 1.24 release notes）

被对照的官方实现（本轮实际读过）：
  * go.dev/doc/go1.24 —— 「Benchmarks may now use the faster and less error-prone
    testing.B.Loop ... 两个显著优势：① 每个 -count 只执行一次基准函数，昂贵的 setup/cleanup
    只做一次；② 函数调用的实参和结果保持存活，编译器无法把循环体整体优化掉」
  * src/testing/benchmark.go —— Loop() 快路径、loopPoisonTimer/loopPoisonMask 常量、
    loopSlowPath、stopOrScaleBLoop、predictN，以及 Loop 的文档注释
  * benchTime = durationOrCountFlag{d: 1 * time.Second}（默认 1 秒）

运行：python bloop_bench.py
"""
MAX_BENCH_PREDICT_ITERS = 1_000_000_000
LOOP_POISON_TIMER = 1 << 63
LOOP_POISON_MASK = (~((1 << (63 - (1 - 1))) - 1)) & 0xFFFFFFFFFFFFFFFF


# --------------------------------------------------------- predictN

def predict_n(goalns, prev_iters, prev_ns, last):
    """benchmark.go 的 predictN：先乘后除（避免快速基准丢掉数量级），再 1.2x 与多重夹紧"""
    if prev_ns == 0:
        prev_ns = 1                       # 官方注释：绕过除零（issue 70709）
    n = goalns * prev_iters // prev_ns    # 先乘后除
    n += n // 5                           # 多跑 20%
    n = min(n, 100 * last)                # 增长不要太猛
    n = max(n, last + 1)                  # 至少比上次多一个
    n = min(n, MAX_BENCH_PREDICT_ITERS)   # 上限 1e9（也保证 32 位平台不溢出）
    return n


# --------------------------------------------------------- B 的最小复刻

class FakeClock:
    def __init__(self):
        self.now = 0

    def advance(self, ns):
        self.now += ns


class B:
    """testing.B 里与 Loop 相关的那部分状态"""

    def __init__(self, bench_ns=1_000_000_000, bench_count=0, per_iter_ns=1_000):
        self.bench_ns, self.bench_count, self.per_iter_ns = bench_ns, bench_count, per_iter_ns
        self.N = 0
        # testing 在进入基准函数之前就把 timerOn 置位了，否则 Loop 的首次慢路径
        # 会立刻撞上 "B.Loop called with timer stopped"
        self.timer_on = True
        self.timer_start = None
        self.elapsed = 0
        self.i = 0
        self.n = 0
        self.done = False
        self.body_calls = 0        # 循环体真正执行的次数
        self.fn_calls = 0          # 基准函数被调用的次数
        self.fatal = None
        self.clock = FakeClock()

    # ---- 计时器
    def reset_timer(self):
        self.elapsed = 0
        self.timer_on = True

    def stop_timer(self):
        if self.timer_on:
            self.timer_on = False

    def tick(self):
        """跑一次循环体：推进时钟并把耗时计入（仅计时开启时）"""
        self.body_calls += 1
        self.clock.advance(self.per_iter_ns)
        if self.timer_on:
            self.elapsed += self.per_iter_ns

    def elapsed_ns(self):
        return self.elapsed

    # ---- Loop
    def loop(self):
        if self.i < self.n:
            self.i += 1
            return True
        return self.loop_slow_path()

    def loop_slow_path(self):
        if not self.timer_on:
            self.fatal = "B.Loop called with timer stopped"
            raise RuntimeError(self.fatal)
        if self.i & LOOP_POISON_MASK:
            raise RuntimeError("unknown loop stop condition: %#x" % self.i)
        if self.n == 0:
            # 第一次调用：固定次数用 -benchtime=Nx，否则从 1 起步
            self.n = self.bench_count if self.bench_count > 0 else 1
            self.N = 0                      # 循环内不使用 b.N
            self.reset_timer()
            self.i += 1
            return True
        if self.bench_count > 0:
            # 固定次数模式：跑够就停，不再按时间标定
            if self.i != self.bench_count:
                raise RuntimeError("iteration count %d < fixed target %d"
                                   % (self.i, self.bench_count))
            more = False
        else:
            more = self.stop_or_scale_bloop()
        if not more:
            self.stop_timer()
            self.N = int(self.n)
            self.done = True
            return False
        self.i += 1
        return True

    def stop_or_scale_bloop(self):
        t = self.elapsed_ns()
        if t >= self.bench_ns:
            return False                    # 到目标时长
        prev_iters = self.n
        self.n = predict_n(self.bench_ns, prev_iters, t, prev_iters)
        return prev_iters < self.n

    # ---- 两种风格
    def run_bloop_style(self, setup_ns=0, cleanup_ns=0):
        """for b.Loop() { ... }：基准函数只跑一次，setup/cleanup 各一次且不计入"""
        self.fn_calls += 1
        self.clock.advance(setup_ns)        # setup 在 loop 之前 ⇒ 计时还没开始
        while self.loop():
            self.tick()
        self.clock.advance(cleanup_ns)      # cleanup 在 loop 之后 ⇒ 计时已停
        return self.N, self.elapsed_ns()

    def run_bn_style(self, setup_ns=0):
        """for range b.N：基准函数会被**多次**调用（每次都要重新标定 N）"""
        rounds = 0
        self.n = 0
        while True:
            rounds += 1
            self.fn_calls += 1
            self.clock.advance(setup_ns)    # 每轮都要重做 setup，且这次会计入计时
            self.i, self.n = 0, predict_n(self.bench_ns, max(self.n, 1),
                                          max(self.elapsed, 1), max(self.n, 1))
            self.N = self.n
            self.reset_timer()
            for _ in range(self.N):
                self.tick()
            self.stop_timer()
            if self.elapsed_ns() >= self.bench_ns:
                break
            if rounds > 50:
                break
        return self.N, self.elapsed_ns()


# ------------------------------------------------- KeepAlive 的适用条件

def keepalive_applies(cond_text, body_text, stmt_in_braces):
    """Loop 文档：必须是**字面** `b.Loop()`，且语句要真的写在花括号内"""
    return cond_text.strip() == "b.Loop()" and stmt_in_braces and body_text.strip() != ""


# ---------------------------------------------------------------- 自检
PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1


def check_poison_constants():
    ok(LOOP_POISON_TIMER == 1 << 63, "loopPoisonTimer = 1<<63（用最高位把 i 打毒）")
    ok(LOOP_POISON_MASK == 1 << 63,
       "loopPoisonMask = ^((1<<(63-(iota-1)))-1)，iota=1 时正好只剩最高位")


def check_predict_n():
    """predictN 的四条夹紧规则，逐条断言"""
    ok(predict_n(1_000_000_000, 1, 1_000, 1) == 100,
       "1 次耗时 1µs ⇒ 理论 1e6，但被 100*last 夹到 100")
    ok(predict_n(1_000_000_000, 100, 1_000_000, 100) == 10_000,
       "理论 1e5 ×1.2 = 1.2e5，被 100*last=1e4 夹住")
    ok(predict_n(1_000_000_000, 1_000, 100_000_000, 1_000) == 12_000,
       "理论 1e4 ×1.2 = 1.2e4，未被 100*last 夹 ⇒ 取 1.2e4")
    ok(predict_n(1_000_000_000, 100_000_000, 1_000_000, 100_000_000)
       == MAX_BENCH_PREDICT_ITERS, "超大预测值被 1e9 上限夹住")
    ok(predict_n(1_000_000_000, 1, 0, 1) == 100, "prevns=0 被当成 1，绕开除零（issue 70709）")
    ok(predict_n(1_000_000_000, 5, 1_000_000_000, 5) == 6,
       "已达标但仍在增长时，至少比上次多一个（max(n, last+1)）")


def check_once_per_count():
    """核心差异：Loop 风格下基准函数只执行一次 ⇒ setup/cleanup 只做一次"""
    bloop = B(bench_ns=1_000_000, per_iter_ns=1_000)
    n1, el1 = bloop.run_bloop_style(setup_ns=500_000, cleanup_ns=500_000)
    ok(bloop.fn_calls == 1, "Loop 风格：基准函数只被调用一次")
    ok(n1 > 0 and el1 >= 1_000_000, "跑够 1ms 目标时长，N=%d" % n1)
    ok(el1 == n1 * 1_000, "计时只包含循环体：setup 与 cleanup 都没算进去")
    ok(bloop.clock.now == 500_000 + n1 * 1_000 + 500_000,
       "墙钟时间包含 setup/cleanup，但被测时间不含")

    bn = B(bench_ns=1_000_000, per_iter_ns=1_000)
    bn.run_bn_style(setup_ns=500_000)
    ok(bn.fn_calls > 1, "b.N 风格：基准函数被反复调用（每轮重新标定）")
    ok(bn.fn_calls > bloop.fn_calls, "昂贵的 setup 在 b.N 风格下会被重复执行")


def check_timer_lifecycle():
    b = B(bench_ns=100_000, per_iter_ns=1_000)
    ok(b.n == 0 and b.N == 0, "初始状态 n=0、N=0")
    b.loop()                       # 第一次调用
    ok(b.n == 1 and b.N == 0, "首次调用：n 置 1，且 **循环内 b.N 强制为 0**")
    ok(b.timer_on, "首次调用里 ResetTimer ⇒ setup 不计入")

    b.loop()                       # i==n=1 ⇒ 走慢路径重新标定
    ok(b.n > 1, "标定后 n 被放大：%d" % b.n)

    stopped = B()
    stopped.stop_timer()
    try:
        stopped.loop()
        ok(False, "计时器关闭时调用 Loop 应当 Fatal")
    except RuntimeError as e:
        ok("timer stopped" in str(e), "B.Loop called with timer stopped（源码里是 b.Fatal）")

    poisoned = B()
    poisoned.n, poisoned.i, poisoned.timer_on = 10, LOOP_POISON_TIMER, True
    try:
        poisoned.loop()
        ok(False, "被打毒的 i 应当 panic")
    except RuntimeError as e:
        ok("unknown loop stop condition" in str(e), "未知停止条件直接 panic")


def check_fixed_count():
    """-benchtime=5x：固定次数模式，跑够就停，不再按时间标定"""
    b = B(bench_count=5, per_iter_ns=1_000)
    calls = 0
    while b.loop():
        calls += 1
        b.tick()
    ok(calls == 5, "固定次数模式正好跑 5 次")
    ok(b.N == 5, "Loop 返回 false 后 b.N = 总迭代次数（源码：b.N = int(b.loop.n)）")
    ok(b.done and not b.timer_on, "结束时 StopTimer ⇒ cleanup 不计入")


def check_keepalive():
    ok(keepalive_applies("b.Loop()", "sink(f(x))", True),
       "条件必须**字面**写成 b.Loop()，语句写在花括号内 ⇒ 保活生效")
    ok(not keepalive_applies("b.Loop() && extra", "f(x)", True),
       "条件不是纯 b.Loop() ⇒ 编译期变换不生效")
    ok(not keepalive_applies("b.Loop()", "f(x)", False),
       "语句不在花括号内 ⇒ 不保活")
    ok(not keepalive_applies("b.Loop()", "", True), "空循环体无从保活")


if __name__ == "__main__":
    for fn in (check_poison_constants, check_predict_n, check_once_per_count,
               check_timer_lifecycle, check_fixed_count, check_keepalive):
        fn()
    print("PASS %d assertions" % PASS)
