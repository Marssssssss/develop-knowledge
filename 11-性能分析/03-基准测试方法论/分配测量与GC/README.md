# 分配测量与 GC:`B/op` 与 `allocs/op` 的那些坑

> `0 B/op` **不等于**没有分配。Go 的 `B/op`、`allocs/op` 是"净值 ÷ N"的**整数除法**,任何每操作不足 1 字节的分配都会被截断成 0。本 demo 把这个截断、净值语义和 GC 摊销全部程序化。

## 1. 净值是**差值**,不是绝对值

`src/testing/benchmark.go` 里的实现:

```go
func (b *B) StartTimer() {
	if !b.timerOn {
		runtime.ReadMemStats(&memStats)
		b.startAllocs = memStats.Mallocs
		b.startBytes = memStats.TotalAlloc
		...
	}
}
func (b *B) StopTimer() {
	if b.timerOn {
		b.duration += highPrecisionTimeSince(b.start)
		runtime.ReadMemStats(&memStats)
		b.netAllocs += memStats.Mallocs - b.startAllocs
		b.netBytes += memStats.TotalAlloc - b.startBytes
		b.timerOn = false
	}
}
```

之所以必须取差,`runtime.MemStats` 的字段文档说得很清楚:

> **TotalAlloc** is cumulative bytes allocated for heap objects. ... unlike Alloc and HeapAlloc, it **does not decrease** when objects are freed.
>
> **Mallocs** is the cumulative count of heap objects allocated. The number of live objects is Mallocs - Frees.

自检 `[1][2]` 固化这两条:分配 1000 → **停表期间**分配 5 → 再分配 2000,净值 = **3000** 而不是 3005;释放两次后 `TotalAlloc` 仍是 500,存活对象数 = `Mallocs − Frees` = 3。

推论: **`StopTimer()` 之后做的清理工作,其分配不会被计入** —— 这既是特性(可以排除 setup/cleanup)也是坑(把分配挪到计时器外面就会"消失")。

## 2. 整数除法:0 不代表零

```go
func (r BenchmarkResult) AllocedBytesPerOp() int64 {
	if v, ok := r.Extra["B/op"]; ok { return int64(v) }
	if r.N <= 0 { return 0 }
	return int64(r.MemBytes) / int64(r.N)      // 整数除法,直接截断
}
```

`AllocsPerOp` 同理。于是:

| 总分配(固定 500 KB) | N | 报出的 `B/op` |
| --- | --- | --- |
| 500,000 B | 1 | **500,000** |
| 500,000 B | 100 | 5,000 |
| 500,000 B | 10,000 | **50** |
| 500,000 B | 1,000,000 | **0** |

自检 `[3][4]` 断言了整行。**同一个 benchmark,`-benchtime` 越大反而越容易看到 `0 B/op`** —— 稀疏分配(缓存未命中才分配、切片扩容)在摊销之后就是会落到 0。

## 3. GC 怎么混进 `ns/op`

`ns/op` 用的是**墙钟**,本轮内发生的 STW 全部摊在里面。但 `runN` 每轮开头有一次不计入的 GC:

```go
// Try to get a comparable environment for each run
// by clearing garbage from previous runs.
runtime.GC()
```

注意它发生在 `ResetTimer()` **之前**。因此:

- **GC 时间不计入 ns/op**(好消息);
- **每轮开始时堆是干净的**,所以"堆持续增长触发 GC"这类真实服务的成本模式在微基准里被系统性抹掉(坏消息);
- **`NumGC` 随 `-count` 增长**:自检 `[6]` 用 `-count=10` 断言 `NumGC` 至少 +10。

自检 `[5]` 跑了一个每 op 分配 1 KB、存活 5% 的基准(单次 STW 2 ms):

| N | GC 次数 | 报出的 `ns/op` |
| --- | --- | --- |
| 1e3 | 0 | **100** |
| 1e5 | 2 | **120** |
| 1e6 | 5 | **108** |

**非单调**。原因是 GC 次数按对数增长(每次 GC 后 `next_gc` 翻倍),而每次 STW 是固定开销,所以 N 继续增大时摊销反而回落。结论很实际:**同一个含分配的 benchmark,换 `-benchtime` 会得到不同的 `ns/op`,而且不是单调的**。

## 4. `GCCPUFraction` 的分母不是墙钟

官方文档原文:

> A program's available CPU time is defined as **the integral of GOMAXPROCS** since the program started. That is, if GOMAXPROCS is 2 and a program has been running for 10 seconds, its "available CPU" is 20 seconds. GCCPUFraction **does not include CPU time used for write barrier activity**.

自检 `[7]` 用文档原例断言:GOMAXPROCS=2 跑 10 秒 → 可用 CPU = 20 秒,GC 用掉 1 秒 → `0.05`。

两条易错点:① 分母是 GOMAXPROCS 的积分,不是墙钟,所以多核机器上这个分数会"变小";② 它**不含写屏障**,而 Go 的写屏障是并发标记期间的主要开销来源之一,所以 `GCCPUFraction` 会**低估** GC 的真实成本。

## 5. 怎么判断"你把分配当逻辑测了"

如果一个改动让 `B/op` 涨了 10 倍而 `ns/op` 只涨 10%,那你测到的主要是**分配**而不是逻辑。自检 `[9]` 把这个判据写成函数:

```python
ratio_b >= 3.0 and ratio_t < 1.5   # → 判为「测的是分配」
```

处理办法:输入在 `Setup` 里造好(`b.ResetTimer()`),或用 `b.ReportAllocs()` 明确把分配作为**独立指标**看,而不是让它混进 `ns/op`。

## 6. 运行与自检

```bash
cd python && python allocstats.py                     # 9 组断言,全部实跑通过
cd ../go    && go run allocstats_core.go allocstats_check.go
```

## 7. 注意事项与常见坑

1. **`0 B/op` 要复验**。想确认"真的零分配",用 `-benchtime=1x`(N=1)或 `testing.AllocsPerRun` 看原始计数,别只看摊销后的列。
2. **`Extra` 会盖掉内建列**。`BenchmarkResult` 的三个取值方法都**优先**读 `r.Extra[...]`,`b.ReportMetric("B/op", v)` 写进去的值会覆盖真实计算值(自检 `[8]`)。自定义指标不要占用这四个保留名。
3. **`ReportAllocs()` 只影响调用它的那个 benchmark**,等价于 `-benchmem` 但作用域更小。
4. **每轮 `runtime.GC()` 让"内存随时间增长"类问题在微基准里隐身**。这类问题要用长跑 + `runtime.ReadMemStats` 自己采样。
5. **`heap profile` 与 `ReadMemStats` 的时间点不同**。文档原文:"The returned memory allocator statistics are up to date as of the call to ReadMemStats. This is in contrast with a heap profile, which is a snapshot as of the most recently completed garbage collection cycle."

## 参考资料(实际阅读过的来源)

- [`golang/go` — `src/testing/benchmark.go`(master 分支源码)](https://raw.githubusercontent.com/golang/go/master/src/testing/benchmark.go) — `StartTimer`/`StopTimer`/`ResetTimer` 的 `runtime.ReadMemStats` 采样点、`netAllocs`/`netBytes` 的累加、`runN` 里 `runtime.GC()` 的位置与注释、`BenchmarkResult` 的 `NsPerOp`/`AllocsPerOp`/`AllocedBytesPerOp` 整数除法与 `Extra` 优先逻辑
- [`pkg.go.dev/runtime#MemStats`](https://pkg.go.dev/runtime#MemStats) — `TotalAlloc` / `Mallocs` / `Frees` / `HeapAlloc` / `PauseTotalNs` / `NumGC` / `NextGC` / `GCCPUFraction` 的字段文档原文(含"does not decrease when objects are freed"、"integral of GOMAXPROCS"、"does not include CPU time used for write barrier activity")
- [`pkg.go.dev/testing`](https://pkg.go.dev/testing) — `B.ReportAllocs`、`B.SetBytes`、`B.ReportMetric` 的官方说明
- [`pkg.go.dev/golang.org/x/perf/cmd/benchstat`](https://pkg.go.dev/golang.org/x/perf/cmd/benchstat) — `-filter "/format:json goos:linux .unit:(ns/op OR B/op)"` 的用法,说明 `B/op` 与 `ns/op` 是可以作为独立 unit 分别比较的
