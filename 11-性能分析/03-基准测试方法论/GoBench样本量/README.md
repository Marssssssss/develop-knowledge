# Go `testing.B` 的迭代标定与样本量

> `-benchtime=1000000x` 看起来"跑了一百万次,样本量巨大",其实它只给了你 **1 个**测量值。本 demo 把 `go test -bench` 里"迭代次数怎么定"和"样本量怎么来"这两件事彻底分开——前者由 `predictN` 自动标定,后者只能靠 `-count`。

## 1. 两个旋钮不是一回事

| 旋钮 | 决定什么 | 产物 | 典型误用 |
| --- | --- | --- | --- |
| `-benchtime` | **一次测量内部**跑多少遍 `b.N` 循环 | 1 个 `ns/op` | 以为调大它就能做统计推断 |
| `-count` | 这个 benchmark **被独立测量几次** | N 个 `ns/op` | 只跑 1 次就下"快了 3%"的结论 |

`pkg.go.dev/testing` 的原文把 `b.N` 的标定说得很清楚:

> the benchmark function is called multiple times with `b.N` adjusted until the benchmark function lasts long enough to be timed reliably. This also means any setup done before the loop may be run several times.

注意后半句——**循环前的 setup 会被执行多遍**。Go 1.24 起的 `B.Loop` 正是为此而存在:"runs each benchmark function only once per measurement"。

## 2. `-benchtime` 的解析:没有 `parseBenchTime`

读源码前容易以为有个专门函数。实际 `src/testing/benchmark.go` 里 flag 是这样注册的:

```go
benchTime = durationOrCountFlag{d: 1 * time.Second} // 默认值就写在变量初始化里
flag.Var(&benchTime, "test.benchtime",
    "run each benchmark for duration `d` or N times if `d` is of the form Nx")
```

`Set` 的逻辑是:**看结尾是不是 `"x"`**——是就 `strconv.ParseInt` 拿次数,否则 `time.ParseDuration`。于是:

- `0x`、`-1x` → `invalid count`(不是"跑零次");
- `0s`、`-1s`、`abc` → `invalid duration`;
- `1s` 与 `1000000x` 是两种**互斥**的模式,同一个 flag 里 `d` 和 `n` 至多一个有意义。

自检 `[1]` 逐条复现了这条解析(含五种非法输入)。

## 3. `b.N` 不是 1,2,5,10,20,50… 那个序列

流传很广的说法是 `b.N` 按 1/2/5/10/20/50 的固定档位递增。**当前源码里没有这个序列**。真实逻辑在 `launch` + `predictN`:

```go
n := goalns * prevIters / prevns   // 先乘后除
n += n / 5                         // 多跑 20%(1.2x)
n = min(n, 100*last)               // 别因上次计时误差而暴涨
n = max(n, last+1)                 // 至少比上次多一次
n = min(n, maxBenchPredictIters)   // 1e9 上限
```

三处值得记住:

1. **"先乘后除"不是风格问题**。源码注释写得很直白:"For very fast benchmarks, prevIters ~= prevns. If you divide first, you get 0 or 1, which can hide an order of magnitude in execution time."
2. **`100*last` 是真正的收敛速度决定项**。极快的 benchmark 每轮最多只能涨 100 倍,所以 1 ns/op 的基准要 **6 轮**才爬到 N=1e9(自检 `[4]`)。
3. **`1.2x` 会超调**。`-benchtime=10s` + 1 µs/op 实测标定出 N=12,000,000,实际墙钟 **12.000 s**——比要求的多 2 秒。想严格控制总时长只能用 `Nx` 形式。

自检 `[3]` 给出 1 µs/op 在 `-benchtime=1s` 下的完整序列:

```
[1, 100, 10000, 1000000]     # 4 轮;不是 1,2,5,10,...,1000000
```

`-benchtime=Nx` 则完全跳过这套自适应:`run1` 先跑 `runN(1)`,若 `N > 1` 再 `runN(N)`,结束。而 **`1x` 时连这次 `runN` 都不调**,直接复用 `run1` 的结果(源码注释指向 golang.org/issue/32051)——于是 N=1,`ns/op` 就是单次墙钟。

## 4. 几个容易被忽略的实现细节

- **每轮 `runN` 前都 `runtime.GC()`**。源码注释:"Try to get a comparable environment for each run by clearing garbage from previous runs"。它发生在 `ResetTimer()` 之前,所以 GC 时间不计入 `ns/op`,但**它也抹掉了"堆持续增长触发 GC"这类真实服务的成本模式**。
- **`ns/op` 与 `B/op` 都是整数除法、直接截断**:`NsPerOp = T.Nanoseconds()/N`。所以 1501 ns / 2 op 报 **750** 而不是 750.5;500 KB 摊到 1e6 op 报 **0 B/op**(详见 [分配测量与GC/](../分配测量与GC/))。
- **`RunParallel` 的 grain 是 ~100 µs**,钳制在 `[1, 1e4]`:`grain = 1e5 * previousN / previousDuration`。1 ns/op 会顶到上限 1e4。它报告的 `ns/op` 是**整体墙钟**,不是各 goroutine 墙钟之和。
- **`B.Loop`(Go 1.24+)**:首次调用自动 `ResetTimer`,返回 false 时自动 `StopTimer`,循环体内变量被插入 `runtime.KeepAlive` 以防被优化掉;循环条件必须**严格**写成 `b.Loop()`。

## 5. 样本量:`-count` 该选多少

`golang.org/x/perf/cmd/benchstat` 的文档给了明确要求:

> Each benchmark should be run at least 10 times to gather a statistically significant sample of results.

并在 Tips 里补充:

> Pick a number of benchmark runs (at least 10, ideally 20) and stick to it.

理由在 [MDE与样本量估算/](../MDE与样本量估算/) 里被量化成公式:给定变异系数 CoV 与想要的功效,所需每侧样本量是 `n = 2(z_{1-α/2}+z_{1-β})²(CoV/MDE)²`。用 benchstat 建议的 n=10、α=0.05、power=0.8 反解,能检出的最小相对回归约为 **`1.25 × CoV`**——噪声 1% 时约 1.25%,噪声 5% 时约 6.3%。**先降噪,再谈判据**。

`-count=1` 的死穴不是"不精确",而是**根本不存在分布**:自检 `[6]` 用"中位数置信区间在 n<2 时无法给出"把这一点固化下来。

## 6. 运行与自检

```bash
cd python && python gobench_sample.py      # 9 组断言,全部实跑通过
cd ../go    && go run gobench_sample.go    # 同一组断言的 Go 镜像
```

自检覆盖:benchtime 解析(默认/两种形态/五种非法值)、`predictN` 四处钳制、1 µs/op 与 1 ns/op 的标定序列、`Nx` 与 `1x` 的分支、`-count=1` 无分布、整数截断、`RunParallel` grain、`-benchtime=10s` 的 1.2× 超调。**无第三方依赖**。

## 7. 注意事项与常见坑

1. **别把 `-benchtime=1x` 当"快速跑一遍"**——它给出的是 N=1 的 `ns/op`,噪声等于时钟抖动,且 `B/op` 会被整数截断成离谱的值。
2. **循环里的 `b.N` 次迭代不是 N 个样本**。它们是同一次计时窗口内的连续执行,彼此不独立(缓存、分支预测、GC 都在里面)。
3. **`b.N` 循环外的 setup 会被重复执行**。昂贵 setup 要么 `b.ResetTimer()`,要么改用 `B.Loop`。
4. **`go test` 会缓存结果**。`-benchtime` 属于"可缓存标志",改它不会绕过缓存;官方给的惯用禁用方式是 `-count=1`——而这恰好又把样本量打回 1,所以正式采样要用 `-count=10`(它同样不属于缓存键,但 >1 即不可缓存)。
5. **`Nx` 形式牺牲了自适应**。被测代码变慢 10 倍后,`1000000x` 的总时长也变慢 10 倍,而 `1s` 形式会自动把 N 降下来保持总时长恒定。

## 参考资料(实际阅读过的来源)

- [`golang/go` — `src/testing/benchmark.go`(master 分支源码)](https://raw.githubusercontent.com/golang/go/master/src/testing/benchmark.go) — `durationOrCountFlag.Set` 的解析分支、`benchTime = durationOrCountFlag{d: 1 * time.Second}` 默认值、`predictN` 的先乘后除与四处钳制、`launch`/`runN`/`run1` 的调用关系、`runtime.GC()` 的位置、`RunParallel` 的 grain 计算、`BenchmarkResult` 的 `NsPerOp`/`AllocedBytesPerOp` 整数除法
- [`pkg.go.dev/testing`](https://pkg.go.dev/testing) — `b.N` 标定语义原文、`B.Loop` 的计时与 `KeepAlive` 语义、`ReportAllocs`/`SetBytes`/`SetParallelism` 说明
- [`pkg.go.dev/golang.org/x/perf/cmd/benchstat`](https://pkg.go.dev/golang.org/x/perf/cmd/benchstat) — "at least 10 times"、"at least 10, ideally 20"、默认 `assume=nothing` 走非参数统计(中位数 + Mann-Whitney U)、`assume=exact` 分支
- [`pkg.go.dev/cmd/go`(Test packages 与 Testing flags 索引)](https://pkg.go.dev/cmd/go) — 可缓存测试标志集合与"显式禁用缓存的惯用方式是 `-count=1`"
