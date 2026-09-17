# Go benchmark 分配统计：b.ReportAllocs、B/op 与 allocs/op

## 简介

- `go test -bench` 的基准输出里，`B/op` 与 `allocs/op` 两列直接量化**每次迭代制造了多少垃圾**——对 GC 语言，这往往比 ns/op 更能预测性能（Go 官方博客的剖析案例：一轮优化 11 倍提速的收尾正是消灭内层循环的分配）。
- 核心价值：`b.ReportAllocs()` 让单个基准输出 malloc 统计；`B/op = MemBytes/N`、`allocs/op = MemAllocs/N` 都是**整数除法**，看的是平均每次迭代的分配量。
- 关键概念：
  - **b.N 自适应**：框架多次调用基准函数、逐轮调大 b.N，直到"持续足够久、可可靠计时"
  - **ReportAllocs vs -benchmem**：前者只影响调用它的基准，后者全局生效
  - **ResetTimer / StopTimer**：排除 setup 与收尾（分配计数器一并清零/暂停）
  - **SetBytes 的 MB/s ≠ B/op**：前者是吞吐量（处理的字节数），后者是分配量

## 原理详解

依据 pkg.go.dev/testing（Go 1.27 文档，本轮实读）：

1. **b.N 机制**（文档原文）："The benchmark function is called multiple times with b.N adjusted until the benchmark function lasts long enough to be timed reliably. This also means **any setup done before the loop may be run several times**"——所以 `NewBig()` 这类昂贵 setup 要么放循环外并 `b.ResetTimer()`，要么改用新的 `b.Loop()`（"New benchmarks should prefer using B.Loop, which is more robust and more efficient"，且只对循环体计时）。
2. **ReportAllocs**（文档原文）："enables malloc statistics for this benchmark. It is equivalent to setting -test.benchmem, but **it only affects the benchmark function that calls ReportAllocs**"——一个文件里 10 个基准只想让 1 个带分配列时用它。
3. **两个指标的定义**（BenchmarkResult 方法）：
   - `AllocedBytesPerOp`："returns the **B/op** metric, which is calculated as **r.MemBytes / r.N**"
   - `AllocsPerOp`："returns the **allocs/op** metric, which is calculated as **r.MemAllocs / r.N**"
   - 两者都是整型字段相除 → **整数除法**（1000 字节 / 3 次 = 333 B/op，不是 333.3）
4. **ResetTimer**（文档原文）："zeroes the elapsed benchmark time **and memory allocation counters** and deletes user-reported metrics. **It does not affect whether the timer is running**"——清的是账本，不动开关状态。
5. **StopTimer/StartTimer**：暂停/恢复计时，"pause the timer while performing steps that you don't want to measure"——StopTimer 期间的分配同样不入账。
6. **SetBytes**（文档原文）："records the number of bytes processed in a single operation. If this is called, the benchmark will report **ns/op and MB/s**"——MB/s = Bytes × N / T 是**吞吐量**。注意与 B/op 完全是两回事：一个是"处理了多少数据"，一个是"分配了多少内存"。
7. **输出行格式**：`BenchmarkRandInt-8\t68453040\t17.8 ns/op`——名字后缀 `-GOMAXPROCS`；带分配统计时追加 `B/op`、`allocs/op` 列（MemString 的格式与 go test 输出一致）。完整规范见 [go.dev/design/14313-benchmark-format](https://go.dev/design/14313-benchmark-format)。
8. **RunParallel 的计时口径**（文档原文）："reports ns/op values as **wall time for the benchmark as a whole**, not the sum of wall time or CPU time over each parallel goroutine"——并行基准的 ns/op 不会被 worker 数放大；body 里**禁止**再用 StartTimer/StopTimer/ResetTimer（它们是全局效果）。
9. **统计比较**：文档明确推荐 golang.org/x/perf/cmd/**benchstat** 做"statistically robust A/B comparisons"——两版代码的数字不能直接目测比大小。

```
go test -bench .
  └─ 对每个基准：n=1 起步
       b.N = n; 清零计时/分配; 计时开 → body(循环 b.N 次) → 计时停
       持续够久? ──否──▶ 按 per-op 预测更大的 n（本 demo 简化模型）
       └─是─▶ Result{N,T,Bytes,MemAllocs,MemBytes}
                B/op = MemBytes/N   allocs/op = MemAllocs/N（整数除法）
                SetBytes>0 ? → 追加 MB/s 列
```

## 对比 / 选型

| 手段 | 影响范围 | 典型用法 |
| --- | --- | --- |
| `b.ReportAllocs()` | 仅当前基准 | 单点关注分配的基准 |
| `go test -benchmem` | 全部基准 | 全面体检 |
| `go test -memprofile` | 产出 pprof 文件 | 下钻到分配点（配合 `list`） |

## 环境准备

- Python ≥ 3.10 / Go ≥ 1.21（demo 为纯逻辑模拟；Go 版本机无工具链，走人工审查 + 括号配平）

## 运行方式

```bash
python3 python/bench_allocs.py   # 10 组断言
# go run go/bench_allocs.go      # 同 10 组断言
```

## 关键代码片段

```python
while True:
    b.N = n
    b.reset_timer()            # 框架行为：每轮重跑前清零计时/分配
    b.start_timer()
    body(b)
    b.stop_timer()
    if b.elapsed >= target - 1e-9:   # 浮点容差：10×0.1 累加为 0.999…9
        break
    per_op = b.elapsed / b.N
    n = max(n + 1, min(100 * n, int(target / per_op)))  # 简化递增策略
```

## 性能与边界

- b.N 的具体递增策略（预测公式/封顶）本 demo 未复刻，只实现"逐轮调大直到够久"的语义；文档原话即"b.N adjusted until … long enough to be timed reliably"。
- B/op 是**平均值**：单次大分配 + 大量零分配迭代会互相稀释，看最大分配点要下钻 -memprofile。
- 分配计数覆盖计时开启期间的全部 malloc：StopTimer 里的准备代码不计（本 demo 断言 6 验证）。

## 注意事项与常见坑

- **setup 会被执行多次**：b.N 自适应意味着基准函数被反复调用，把 setup 放函数体内又不 ResetTimer，会让首轮分配污染统计（断言 5 的 10000B/500 次正演示这一点）。
- **B/op 是整数除法**：1000/3 打出来是 333，别当四舍五入 333.3 看。
- **MB/s 与 B/op 别混**：SetBytes 的语义是"单次操作处理的字节数"（如哈希了 1KB 输入），与分配无关；两列可能同时出现（本 demo 断言 8 里 MB/s 出现而 B/op 缺席，因为没 ReportAllocs）。
- **并行基准里别碰计时器**：RunParallel 的 body 里调 StartTimer/StopTimer/ResetTimer 是文档明令禁止的（全局效果会互相踩）。
- **比较结果用 benchstat**：手工目测两个 ns/op 的差异容易被噪声骗，官方推荐统计化的 A/B 比较。

## 参考资料（实际阅读过的权威来源）

- [testing - Go Packages](https://pkg.go.dev/testing) — b.N 机制原话、ReportAllocs/ResetTimer/StartTimer/StopTimer/SetBytes/RunParallel 语义、BenchmarkResult 四字段与两个 PerOp 方法、输出行示例
- [Profiling Go Programs - Go Blog（Russ Cox）](https://go.dev/blog/pprof) — "垃圾回收语言更要盯内层循环分配量"的官方案例（11× 提速、3.7× 省内存）
- [go.dev/design/14313-benchmark-format](https://go.dev/design/14313-benchmark-format) — 基准结果格式规范（testing 文档指定）
