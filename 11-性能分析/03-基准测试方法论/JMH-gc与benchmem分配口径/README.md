# JMH `-prof gc` 与 Go `-benchmem` 的分配口径对比

> 待研究项落地：`JMH -prof gc 与 Go -benchmem 的分配口径对比（一次分配的对象数 vs 字节数 vs GC 次数）`。两侧口径都来自**逐行实读**的官方源码（`GCProfiler.java` 与 `testing/benchmark.go`），不是凭经验归纳。

## 简介

"这个 benchmark 分配了多少"这个问题，JMH 和 Go 给出的**不是同一个量**，而且差得很有规律：

| 维度 | JMH `-prof gc` | Go `-benchmem` |
| --- | --- | --- |
| 字节口径 | `gc.alloc.rate.norm`，**浮点** B/op | `B/op`，**整数除法**截断 |
| 对象数 | **没有**（只有字节） | `allocs/op`（`Mallocs` 差值） |
| GC 次数 | `gc.count`（所有 GC bean 求和） | 不直接报（`NumGC` 另取） |
| 计量窗口 | 整个 JVM 进程（`beforeIteration`→`afterIteration`） | 仅 `StartTimer`→`StopTimer` |
| 零分配时 | 该列**根本不出现** | 打印 `0 B/op` |

## 原理详解

### 1. JMH：`gc.alloc.rate.norm` 是浮点，且零分配时整列消失

`GCProfiler.afterIteration` 里，只有在 `allocated != 0` 时才把 norm 加进结果：

```java
results.add(new ScalarResult("gc.alloc.rate", ..., "MB/sec", AggregationPolicy.AVG));
if (allocated != 0) {                       // ← 关键分支
    results.add(new ScalarResult("gc.alloc.rate.norm",
            1.0 * allocated / allOps, "B/op", AggregationPolicy.AVG));
}
```

于是"报告里没有 `gc.alloc.rate.norm` 这一列"**不代表 0 B/op**，只代表这一轮测到的净分配是 0——而 0 也可能是测量失败（`getTotalThreadAllocatedBytes` 返回 `-1` 时 rate 被写成 `Double.NaN`、norm 连生成都不生成）。

对照 Go 侧：无论有没有分配，`MemString()` 都会打印 `%8d B/op`，于是**亚字节分配一律显示成 0**：

```go
func (r BenchmarkResult) AllocedBytesPerOp() int64 {
	if r.N <= 0 { return 0 }
	return int64(r.MemBytes) / int64(r.N)     // 整数除法，直接截断
}
```

同一份真实分配量喂给两个报告器（自检 E21）：

- 真实 0.5 B/op → JMH `0.5`、Go `0`
- 真实 24 B/op → 两者都是 24
- 真实 24 B/op + 8 KB 基础设施分配 → JMH `32.0`、Go 仍 `24`

### 2. 窗口差了一个量级：进程级 vs 计时窗口级

JMH 优先用 `com.sun.management.ThreadMXBean.getTotalThreadAllocatedBytes()`，那是**整个 JVM 进程**的累计分配；回退到 `getThreadAllocatedBytes(long[])` 时，注释明确说明两个偏差：

- **当前线程被刻意排除**（"believed to execute jmh infrastructure code only"）
- **两次快照之间创建又死亡的线程的分配会被整段漏掉**

Go 侧只统计计时窗口：

```go
func (b *B) StartTimer() {
	if !b.timerOn {
		runtime.ReadMemStats(&memStats)
		b.startAllocs = memStats.Mallocs
		b.startBytes  = memStats.TotalAlloc
		...
	}
}
func (b *B) StopTimer() {
	if b.timerOn {
		b.netAllocs += memStats.Mallocs - b.startAllocs
		b.netBytes  += memStats.TotalAlloc - b.startBytes
		...
	}
}
```

所以**停表之后做的分配完全不计**（自检 E12）。另外 `runN` 的顺序是 `runtime.GC() → ResetTimer → StartTimer → benchFunc → StopTimer`，每轮开头那次 GC **计入 NumGC 但不进 ns/op**（自检 E13）——`-count` 越大，`NumGC` 越多，而 `ns/op` 完全看不到它。

### 3. 聚合策略不同，"两个数字相除"是错的

`gc.count` 用 `AggregationPolicy.SUM`，`gc.alloc.rate*` 用 `AVG`。多轮之后：

- `gc.count` = 各轮次数**之和**
- `gc.alloc.rate.norm` = 各轮 B/op 的**算术平均**

自检 E6 构造了两轮：轮 1 分配 1000 字节 / 100 ops（10 B/op），轮 2 分配 3000 字节 / 1000 ops（3 B/op）。聚合结果是 `count = 2`、`norm = 6.5`，而"总字节 ÷ 总操作"的真值是 `4000/1100 = 3.636`。**用聚合后的 count 除聚合后的 rate 得到的数没有物理意义**——想算总账必须用未聚合的分轮数据。

### 4. churn 不是分配量，是净回收量

`churn` 默认关闭（`-prof gc:churn=true`，`churnWait` 默认 500 ms）。它统计的是每次 GC 通知里各 space 的 `before.getUsed() - after.getUsed()`，**只在 `c > 0` 时累加**：

```java
long c = before.getUsed() - after.getUsed();
if (c > 0) { CHURN.add(name, c); }
```

语义是"这一轮被回收掉的字节量"，与 `gc.alloc.rate` 的"分配总量"是两回事：`alloc ≥ churn`，只看 churn 会严重低估分配压力。另外通知是异步的，所以 `finishChurnProfile` 要 `Thread.sleep(churnWait)` 等通知到齐再摘监听器。

### 5. 对象数：JMH 缺一门

JMH 只有字节口径的 `gc.alloc.rate.norm`，**没有任何"每次操作分配多少个对象"的列**（自检 E18）。要对象数只能用 `-prof gc` 之外的手段。Go 的 `allocs/op = MemAllocs / N` 是有的，但注意 `Mallocs` 是**堆分配次数**：逃逸分析成功、留在栈上的分配不计数，所以 `allocs/op = 0` 也不等于"没分配"。

## 环境依赖

- Python ≥ 3.9（仅标准库）；Go ≥ 1.21（`go run .`，本机无工具链时按 §运行方式 走静态检查）

## 运行方式

```bash
cd 11-性能分析/03-基准测试方法论/JMH-gc与benchmem分配口径
python python/main.py              # 冒烟：打印两个报告器对同一份数据的输出
python python/selfcheck_alloc.py   # 40 条断言，全绿
cd go && go run .                  # Go 版同模型输出
```

## 关键代码

| 文件 | 职责 |
| --- | --- |
| `python/main.py` | `JmhSnapshot`/`ChurnEvent` + `jmh_gc_profile()` 复刻 `afterIteration`；`GoBench` 复刻 `StartTimer/StopTimer/ResetTimer/runN`；`compare_true_vs_reported()` 双报告对拍 |
| `python/selfcheck_alloc.py` | 40 条断言：列消失、亚字节截断、条件发射、负差钳制、per-thread 排除与漏计、churn 正负、窗口语义、聚合策略、Extra 覆盖 |
| `go/allocprof.go` | 同模型的 Go 版（298 行，含 `Aggregate`/`Lookup`/`MemString`） |

## 性能边界与注意事项

- **两个数字不可直接比较**：JMH 是进程级、Go 是计时窗口级。跨语言结论必须先把口径对齐（Go 侧把基础设施分配也放进窗口，JMH 侧开 per-thread 模式）。
- **`0 B/op ≠ 零分配`**：Go 侧整数截断，JMH 侧是列消失——同一个"0"在两边含义相反。
- **GC 次数不可跨工具比**：JMH 的 `gc.count` 是所有 `GarbageCollectorMXBean` 计数之和（young + old 一起加），Go 的 `runtime.MemStats.NumGC` 是聚合次数且每轮 `runN` 强制 +1。
- **`gc.time` 是有条件发射的**：某一轮既没发生 GC、时间也没变，这一轮就完全没有 `gc.time` 行；按"列一定存在"去解析 JMH 的 JSON 输出会踩空。
- **`gc.cpuTime` 单位换算用整除**：`(after - before) / 1_000_000`，2 499 999 ns 报 `2 ms`（截断而非四舍五入）。

## 参考资料（实际阅读过的来源）

- [`openjdk/jmh` — `jmh-core/src/main/java/org/openjdk/jmh/profile/GCProfiler.java`](https://github.com/openjdk/jmh/blob/master/jmh-core/src/main/java/org/openjdk/jmh/profile/GCProfiler.java) — 本文第 1/3/4/5 节的直接依据：`gc.count` 遍历所有 GC bean、`gc.time` 的条件发射、`gc.cpuTime` 的 `/1_000_000`、`gc.alloc.rate.norm` 的 `allocated != 0` 分支、全局与 per-thread 两条快照路径、负差钳成 0、churn 的 `c > 0` 与 `churnWait=500`
- [`golang/go` — `src/testing/benchmark.go`](https://github.com/golang/go/blob/master/src/testing/benchmark.go) — `StartTimer`/`StopTimer`/`ResetTimer`/`runN` 的语句顺序、`runtime.GC()` 的位置、`BenchmarkResult` 的三个整数除法、`MemString` 的 `%8d B/op\t%8d allocs/op` 模板、`Extra` 优先覆盖
- [`pkg.go.dev/runtime#MemStats`](https://pkg.go.dev/runtime#MemStats) — `Mallocs`/`TotalAlloc` 的累计语义（释放不减少）与 `GCCPUFraction` 的分母口径

> 上一批的 [分配测量与GC/](../分配测量与GC/) 只覆盖了 Go 侧 `-benchmem` 本身；本 demo 补的是**两侧口径的差异**，两者配合读。
