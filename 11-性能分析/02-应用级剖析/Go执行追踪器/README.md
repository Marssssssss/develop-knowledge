# Go execution tracer：与 pprof 采样互补的确定性事件流

> pprof 回答「CPU 时间花在哪」，execution trace 回答「**为什么此刻没在跑**」。
> 一堆 goroutine 阻塞在同一个 channel 上这种并发瓶颈，CPU profile 里几乎没有样本可采——
> 因为没有执行可供采样；而 trace 里阻塞事件和阻塞栈一目了然。

## 一、采样剖析 vs 执行追踪

| 维度 | CPU profile（pprof） | execution trace |
| --- | --- | --- |
| 数据来源 | 定时采样程序计数器 | runtime 写出的**确定性事件流** |
| 能回答 | 哪些函数吃 CPU（统计近似） | 某个 goroutine 何时跑、何时阻塞、被谁唤醒 |
| 看不见 | 阻塞、调度延迟、锁等待（无执行可采） | 单条指令级热点、长跑期的全量数据 |
| 开销 | 低（可常开） | Go 1.21 前 **10–20% CPU**；优化 traceback 后 **1–2%** |
| 典型用途 | 找热点函数 | 找延迟毛刺、调度器/网络/锁的问题 |

开销下降的关键在 **traceback**：trace 里大量事件都带栈，栈展开曾是主要成本
（Felix Geisendörfer / Nick Ripley 的优化）。这也是为什么「能不能常开」到 Go 1.21 才成立。

## 二、事件是**乱序**落盘的（工具负责重排）

为了压低运行时开销，所有事件写进 **per-P / 线程本地缓冲**，因此落盘顺序 ≠ 真实时间顺序，
重排包袱全在工具侧。demo 里给了最小反例：

```text
落盘顺序: g1@100 running, g2@50 running, g1@120 waiting, g2@60 waiting
按落盘顺序算并发峰值 = 2   ← 错：两条 goroutine 其实根本不重叠
按真实时间排序     = 1   ← 对
```

并发峰值这类「同时有多少 goroutine 在跑」的判据，对顺序**极其敏感**。

## 三、Go 1.22 的 trace 拆分

旧格式要求工具**把整份 trace 的状态全留在内存里**：几百 MiB 的 trace 可能吃掉几 GiB 内存。
解法是让 runtime **随时切一刀**：

- 切点等价于「同时关闭再打开 tracing」；
- 切点之前的数据本身就是一份**完整、自包含的 trace**（可以独立解析）；
- 新数据从切点无缝续上 ⇒ 工具可以流式处理，不必一次装下全部状态。

demo 用「每个 goroutine 的状态迁移是否闭合」来判定自包含：只有 `GoRunning` 没有离开事件 =
半截 trace，不自包含。

> 注意：`go tool trace` 目前仍把整份 trace 读进内存，只是对 Go 1.22+ 产出的 trace
> 而言，去掉这个限制变得**可行**了。

## 四、Flight recorder（Go 1.25）：事后取证

场景：一个 RPC 突然很慢，等你发现时根因早就过去了——**来不及 Start**。
flight recorder 的做法是**常开 tracing，只在内存里留最近一段**，出事时 `WriteTo` 抓快照。

```go
fr := trace.NewFlightRecorder(trace.FlightRecorderConfig{
    MinAge:   200 * time.Millisecond,
    MaxBytes: 1 << 20, // 1 MiB
})
fr.Start()
// ... 发现慢请求时：fr.WriteTo(f)
```

两条官方用法要点：

1. **`MinAge` 建议取待观测事件窗口的 2 倍**。调试 5 秒超时就设 10 秒——
   否则「检测到的时刻」到「根因发生的时刻」这段会掉出窗口。
2. **`MaxBytes` 才是硬约束**。官方给的参考速率是**每秒几 MB**（繁忙服务约 **10 MB/s**），
   那么 1 MiB 只留得住 `1 MiB / 10 MB/s = 0.1 秒`——`MinAge` 设成 10 秒也**达不到**，
   想留 10 秒得给到 ~100 MiB。demo 里直接算了这个数。

## 五、三类用户标注（runtime/trace）

| 类型 | API | 语义 |
| --- | --- | --- |
| log | `trace.Log(ctx, category, message)` | 一次性消息；UI 可按 category 过滤/分组 goroutine |
| region | `trace.WithRegion(ctx, name, fn)` | goroutine **内**的时间区间，**可嵌套**；起止必须在**同一个 goroutine** |
| task | `ctx, task := trace.NewTask(ctx, name)` | 逻辑操作（一次 RPC），经 `context.Context` **跨 goroutine** 传播；`task.End()` |

- task 延迟 = `NewTask` → `End` 的时间差，trace 工具会按 task 类型给出**延迟分布**。
- 同一个 task 下的 region 可以落在不同 goroutine（官方 cappuccino 例子：steamMilk / extractCoffee
  各起一个 goroutine，mixMilkCoffee 等两者完成后才 `task.End()`）。
- region 起止跨 goroutine 是**不成立**的——这是 region 与 task 的分界线。

## 六、Trace reader API

Go 1.22 重写后有了可编程读取的 API（`golang.org/x/exp/trace`，Go 1.25 进标准库）。
官方示例的判据值得抄：

```go
if ev.Kind() == trace.EventStateTransition {
    st := ev.StateTransition()
    if st.Resource.Kind == trace.ResourceGoroutine {
        from, to := st.Goroutine()
        if from.Executing() && to == trace.GoWaiting { /* 记一次阻塞 */ }
    }
}
```

**「阻塞」的严格定义是「从执行态迁移到等待态」**——只看 `to` 会把「从 syscall 返回后等待」
之类的迁移也算进来。demo 里的 `IsBlock()` 就是这个判据，并用它复现了官方的
「网络阻塞占比」统计。

## 七、运行

```bash
python python/execution_tracer.py   # 23 条断言
cd go && go run .                   # 同语义 Go 版（本机无工具链，人工审查）
```

## 八、注意事项与常见坑

1. **别用 trace 找 CPU 热点**——它是事件流，不是采样；热点要看 pprof 的 flat/cum。
2. **别拿「阻塞时长」当「调度延迟」**：阻塞时长是「进入等待 → 被唤醒」，
   调度延迟是「被唤醒 → 真正开始跑」，两者在 trace 里是不同事件。
3. **乱序会造成假结论**（§二），任何跨 goroutine 的时序判据都要先确认事件已按真实时间排序。
4. **`MinAge` 不是「保留窗口」的上限而是下限语义**：它承诺的是「可靠保留这么久」，
   实际能留多久还要过 `MaxBytes` 这一关。
5. **常开 tracing 的前提是 1–2% 的开销**，老版本（Go 1.20 及更早）别拿来常开。
6. region 跨 goroutine 会静默失去意义；跨 goroutine 的聚合一律用 task + context。

## 参考资料（本轮实际读过）

- [Go 官方博客 — More powerful Go execution traces（2024-03-14，Michael Knyszek）](https://go.dev/blog/execution-traces-2024)
- [Go 官方博客 — Flight Recorder in Go 1.25](https://go.dev/blog/flight-recorder)
- [pkg.go.dev — runtime/trace（log / region / task 语义）](https://pkg.go.dev/runtime/trace)
- [pkg.go.dev — net/http/pprof（/debug/pprof/trace?seconds=N）](https://pkg.go.dev/net/http/pprof)
