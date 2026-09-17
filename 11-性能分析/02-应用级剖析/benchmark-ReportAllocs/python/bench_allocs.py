# -*- coding: utf-8 -*-
"""Go benchmark 分配统计复刻：b.N 递增、ReportAllocs、B/op 与 allocs/op、SetBytes 的 MB/s。

口径（pkg.go.dev/testing，本轮实读）：
  - b.N 机制："The benchmark function is called multiple times with b.N adjusted
    until the benchmark function lasts long enough to be timed reliably"
    （多次调用基准函数、调整 b.N，直到持续足够久、可可靠计时；故循环前的 setup
    可能被执行多次——需要显式 ResetTimer）
  - ReportAllocs："enables malloc statistics for this benchmark. It is equivalent
    to setting -test.benchmem, but it only affects the benchmark function that
    calls ReportAllocs"（只影响调用它的那个基准函数）
  - ResetTimer："zeroes the elapsed benchmark time and memory allocation counters
    and deletes user-reported metrics. It does not affect whether the timer is
    running"（清零时间与分配计数器；**不改变计时器运行状态**）
  - StopTimer/StartTimer：暂停/恢复计时（不想计入的步骤）
  - BenchmarkResult：AllocedBytesPerOp = r.MemBytes / r.N（"B/op"），
    AllocsPerOp = r.MemAllocs / r.N（"allocs/op"）——整数除法
  - SetBytes："records the number of bytes processed in a single operation"，产出
    ns/op 与 MB/s（吞吐量列；与 B/op 的"分配字节数"是两回事）
  - 输出名形如 BenchmarkRandInt-8（-GOMAXPROCS）
  - RunParallel："reports ns/op values as wall time for the benchmark as a whole,
    not the sum of wall time or CPU time over each parallel goroutine"

注意：b.N 的递增策略在真实实现里有多轮预测/封顶细节，本 demo 用简化模型
（next = max(n+1, min(100n, target/per_op))），只忠实复刻"多次调整直到够久"的语义。
"""


class B:
    """testing.B 的最小复刻：计时 + 分配计数（只在计时开启期间累计）。"""

    def __init__(self):
        self.N = 0
        self.timer_on = False
        self.elapsed = 0.0            # 秒（模拟时钟）
        self.mem_allocs = 0
        self.mem_bytes = 0
        self.bytes_per_op = 0         # SetBytes
        self.report_allocs_on = False

    def start_timer(self):
        self.timer_on = True

    def stop_timer(self):
        self.timer_on = False

    def reset_timer(self):
        """清零时间与分配计数器；不改变 timer_on（官方原文）。"""
        self.elapsed = 0.0
        self.mem_allocs = 0
        self.mem_bytes = 0

    def report_allocs(self):
        self.report_allocs_on = True

    def set_bytes(self, n):
        self.bytes_per_op = n

    def op(self, dt, nbytes=0, nallocs=0):
        """模拟一次迭代：耗时 dt 秒、分配 nbytes 字节、nallocs 次 malloc。
        只有计时开启时才入账（StopTimer 期间的动作不计）。"""
        if self.timer_on:
            self.elapsed += dt
            self.mem_bytes += nbytes
            self.mem_allocs += nallocs


class Result:
    """testing.BenchmarkResult 的最小复刻。"""

    def __init__(self, b):
        self.N = b.N
        self.T = b.elapsed
        self.Bytes = b.bytes_per_op
        self.MemAllocs = b.mem_allocs
        self.MemBytes = b.mem_bytes

    def ns_per_op(self):
        return 1e9 * self.T / self.N if self.N else 0.0

    def alloced_bytes_per_op(self):
        """官方口径：r.MemBytes / r.N（整数除法）。"""
        return self.MemBytes // self.N if self.N else 0

    def allocs_per_op(self):
        """官方口径：r.MemAllocs / r.N（整数除法）。"""
        return self.MemAllocs // self.N if self.N else 0

    def mb_per_s(self):
        if self.Bytes <= 0 or self.T <= 0:
            return 0.0
        return self.Bytes * self.N / self.T / 1e6

    def mem_string(self):
        return "%d B/op\t%d allocs/op" % (self.alloced_bytes_per_op(), self.allocs_per_op())


def launch(name, body, target=1.0, gomaxprocs=8, benchmem=False):
    """go test 运行单个基准的简化模型：多次调整 b.N 直到持续足够久。"""
    b = B()
    n = 1
    while True:
        b.N = n
        b.reset_timer()                        # 框架行为：每轮重跑前清零计时/分配
        b.start_timer()
        body(b)
        b.stop_timer()
        if b.elapsed >= target - 1e-9:         # 浮点容差：10×0.1 累加为 0.999…9
            break
        per_op = b.elapsed / b.N if b.N else 0.0
        pred = int(target / per_op) if per_op > 0 else 100 * n
        n = max(n + 1, min(100 * n, pred))     # 简化递增策略
    r = Result(b)
    line = "%s-%d\t%d\t%.2f ns/op" % (name, gomaxprocs, r.N, r.ns_per_op())
    if b.report_allocs_on or benchmem:
        line += "\t" + r.mem_string()
    if r.Bytes > 0:
        line += "\t%.2f MB/s" % r.mb_per_s()
    return line, r


def alloc_body(dt, nbytes, nallocs):
    def body(b):
        for _ in range(b.N):
            b.op(dt, nbytes, nallocs)
    return body


def main():
    # 1. b.N 递增：0.1s/次、目标 1s → N 序列 1 → 10，第二轮达标停止
    seq = []

    def rec_body(b):
        seq.append(b.N)
        for _ in range(b.N):
            b.op(0.1, 24, 1)
    line, r = launch("BenchmarkStep", rec_body)
    assert seq == [1, 10], seq                 # 多次调整直到"持续足够久"
    assert r.N == 10 and abs(r.ns_per_op() - 1e8) < 1e-6
    assert "BenchmarkStep-8" in line           # 名字-GOMAXPROCS 后缀

    # 2. 单次就够久 → N 不再增长（setup 只跑一次）
    seq2 = []
    line, r = launch("BenchmarkSlow", lambda b: (seq2.append(b.N),
                                                 [b.op(1.5) for _ in range(b.N)]))
    assert seq2 == [1] and r.N == 1

    # 3. B/op 与 allocs/op：官方整数除法（MemBytes/N、MemAllocs/N）
    class FakeB:
        N, elapsed, bytes_per_op = 3, 0.001, 0
        mem_allocs, mem_bytes = 10, 1000
    rr = Result(FakeB())
    assert rr.alloced_bytes_per_op() == 333    # 1000/3 截断，不是 333.3
    assert rr.allocs_per_op() == 3
    assert rr.mem_string() == "333 B/op\t3 allocs/op"

    # 4. ReportAllocs：只影响调用它的那个基准（官方原文），全局 benchmem 影响所有
    line_on, r_on = launch("BenchmarkA", lambda b: (b.report_allocs(),
                                                    [b.op(0.5, 24, 1) for _ in range(b.N)]))
    assert "24 B/op" in line_on and "1 allocs/op" in line_on
    line_off, _ = launch("BenchmarkB", lambda b: [b.op(0.5, 24, 1) for _ in range(b.N)])
    assert "B/op" not in line_off              # 未调用 ReportAllocs → 无分配列
    line_glob, _ = launch("BenchmarkC", lambda b: [b.op(0.5, 24, 1) for _ in range(b.N)],
                          benchmem=True)
    assert "24 B/op" in line_glob              # -benchmem 全局生效

    # 5. ResetTimer：清零时间与分配计数器（排除 setup 的分配），但 N 已递增的事实不变
    def setup_body(b):
        b.op(0.4, 10000, 500)                  # setup：耗 0.4s、分配 500 次
        b.reset_timer()                        # 关键：清零
        for _ in range(b.N):
            b.op(0.6, 24, 1)
    line, r = launch("BenchmarkSetup", setup_body, target=0.6)
    assert r.N == 1                            # 第一轮 setup+循环已 1.0s ≥ 0.6s
    assert r.T == 0.6 and r.MemBytes == 24 and r.MemAllocs == 1
    # setup 的 10000 字节 / 500 次分配没有入账

    # 6. StopTimer/StartTimer：不想计入的收尾步骤
    def stop_body(b):
        for _ in range(b.N):
            b.op(0.6, 24, 1)
        b.stop_timer()
        b.op(0.3, 999, 99)                     # 收尾：不计
        b.start_timer()
    line, r = launch("BenchmarkTail", stop_body, target=0.6)
    assert r.T == 0.6 and r.MemBytes == 24 and r.MemAllocs == 1

    # 7. ResetTimer 不改变计时器运行状态（官方原文）
    b = B()
    b.stop_timer()
    b.reset_timer()
    assert b.timer_on is False
    b.start_timer()
    b.reset_timer()
    assert b.timer_on is True

    # 8. SetBytes → MB/s 吞吐列；与 B/op（分配字节）是两回事
    line, r = launch("BenchmarkIO", lambda b: (b.set_bytes(1024 * 1000),
                                               [b.op(0.1, 0, 0) for _ in range(b.N)]))
    assert abs(r.mb_per_s() - 10.24) < 1e-9   # 1024000B × 10 / 1s = 10.24 MB/s
    assert "10.24 MB/s" in line and "B/op" not in line   # 未 ReportAllocs → 无 B/op

    # 9. RunParallel 口径：ns/op 是整体墙钟时间，不是各 goroutine 之和
    workers = 4
    line_par, r_par = launch("BenchmarkPar",
                             lambda b: [b.op(0.1 / workers, 24, 1) for _ in range(b.N)])
    line_ser, r_ser = launch("BenchmarkSer",
                             lambda b: [b.op(0.1, 24, 1) for _ in range(b.N)])
    assert abs(r_par.ns_per_op() * workers - r_ser.ns_per_op()) < 1e-6
    # 4 个 goroutine 分摊同一批迭代：墙钟 per-op = 串行 per-op / 4

    # 10. B/op 的语义边界：分配计数按"计时期间的 malloc 次数"累计，
    #     若 body 里同一个 op 多次小分配 → allocs/op > 1
    line, r = launch("BenchmarkChatty", lambda b: (b.report_allocs(),
                                                   [b.op(1.0, 40, 3) for _ in range(b.N)]))
    assert r.allocs_per_op() == 3 and r.alloced_bytes_per_op() == 40
    assert "40 B/op" in line and "3 allocs/op" in line

    print("bench_allocs: 10 组断言全部通过")


if __name__ == "__main__":
    main()
