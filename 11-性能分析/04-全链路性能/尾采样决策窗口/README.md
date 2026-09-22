# 尾采样：决策窗口、不可回头的决策与过载淘汰

> 头采样（head sampling）在请求入口就掷骰子，代价低但**注定看不到"慢请求"与"错请求"**；尾采样把决策推迟到 trace 攒齐之后，代价是**必须把整条 trace 在内存里按住一段时间**。
>
> 本 demo 把 OpenTelemetry Collector Contrib 的 `tailsamplingprocessor` 状态机逐行转写成可运行实现，回答三个工程问题：**决策到底等多久**、**决策能不能改**、**内存满了丢的是谁**。

## 核心研究主题

- `decision_wait` 与"批管道"的换算关系：为什么决策延迟 ≈ `decision_wait`
- `decision_wait_after_root_received` 的加速区间与失效边界
- **决策不回头**（`FinalDecision != Unspecified` 即跳过）
- `num_traces` 满时的两种形态：阻塞 vs 淘汰最老待决 trace
- 决策缓存（LRU）与"晚到 span"的处理
- 策略组合律与 drop 策略的**前缀**语义

## 原理详解

### 1. 决策窗口 = 批管道的槽位数

处理器起了一个叫 `idbatcher` 的定长批管道，长度直接由决策等待时间决定：

```go
numDecisionBatches := math.Max(1, tsp.cfg.DecisionWait.Seconds())
idBatcher, err := idbatcher.New(uint64(numDecisionBatches), tsp.cfg.ExpectedNewTracesPerSec)
```

批 id 是**单调递增的绝对编号**，不是数组下标。`AddToCurrentBatch` 返回 `takeID + len(batches)`——即"正在建的那批"永远比"即将取出的那批"大 `numBatches`。`CloseCurrentAndTakeFirstBatch` 每秒被 tick 调一次，取 `batches[takeID % numBatches]` 并把当前批补进该槽位。

于是：**一个 id 进管道后，要经历 `numBatches + 1` 次 Close 才会被决策**。实测 `decision_wait=30` 时是第 31 次 tick。工程上把它读成"决策点在 `decision_wait` 到 `decision_wait + 1` 秒之间"，与默认 30 s 的直觉一致。

这里有个容易误判的点：**管道里的槽位是环形复用的**，但不会产生冲突——因为取出的一定是比写入的小 `numBatches` 的那一批。本 demo 用 40 次连续 Close 验证：吐出 36 个 id（管道里仍压着 4 批），无重复、顺序即插入顺序。

### 2. 根 span 加速只在区间内有效

`decision_wait_after_root_received` 让根 span 一到就把决策提前：

```go
if containsRootSpan && tsp.cfg.DecisionWaitAfterRootReceived > 0 {
    actualData.batchID = tsp.decisionBatcher.MoveToEarlierBatch(
        id, actualData.batchID, uint64(tsp.cfg.DecisionWaitAfterRootReceived.Seconds()))
}
```

`MoveToEarlierBatch` 的实现是 `proposed := takeID + batchesFromNow; if proposed >= traceCurrentBatch { return traceCurrentBatch }`——**只会往前搬**。所以：

| `decision_wait` | `after_root` | 是否搬移 | 实际决策延迟 |
| --- | --- | --- | --- |
| 30 s | 5 s | 是 | ≈ 5 s |
| 5 s | 30 s | **否** | ≈ 5 s |
| 10 s | 10 s | **否**（相等不小于） | ≈ 10 s |

也就是说，把"加速等待"配成**大于等于** `decision_wait` 是纯无效配置，不会报错也不会生效。

### 3. 决策不回头

tick 上遍历批里的每个 id 时，第一件事是：

```go
if trace.FinalDecision != samplingpolicy.Unspecified {
    continue // "A decision was already made, no need to do it again."
}
```

这条注释解释了一个真实场景：**根 span 触发的早决**与 **`decision_wait` 到期的定时决策**可能指向同一条 trace，第二次必须被跳过，否则同一条 trace 会被导出两次。副作用是——决策一旦落下，之后到达的 span **不会**改变这条 trace 的命运，哪怕它们带来了 `ERROR` 状态。

那晚到的 span 怎么办？靠 `decision_cache`（两套 LRU：`sampled_cache_size` / `non_sampled_cache_size`，默认都是 **0，即关闭**）。命中缓存就按旧结论放行或丢弃；**没开缓存时，晚到 span 会重新在 `idToTrace` 里建一条 trace，重新走一遍完整的决策窗口**——这是"为什么我的 trace 被切成两截"的常见原因之一。

> 配置建议来自官方 README 原文：缓存容量要配得**远大于 `num_traces`**，这样 trace 数据已经从内存释放之后，决策还能保留得更久。

### 4. 内存满了：淘汰的是最老的"待决"trace，且不给它决策

span 首次到达时除了入批，还会把 id 压进 `deleteTraceQueue` 队尾。`idToTrace` 满了以后走 `waitForSpace`：

- `blockOnOverflow=true` → 阻塞等 tick 腾地方（实测：A/B 通过正常决策被释放，不是被丢）；
- 否则 → **取 `deleteTraceQueue` 队首**（最老的待决 trace）直接 `dropTrace`。

关键点：`dropTrace` 只清 `idToTrace` 与队列，**不把它从批管道里摘掉**。于是这个 id 之后仍会在某个 tick 的批里出现，命中 `if !ok { idNotFoundOnMapCount++; continue }` 被跳过。实测：5 条 trace 挤 3 个名额时，淘汰 `T0/T1`、存活 `T2/T3/T4`，后续 6 次 tick 中 `id_not_found = 2`。

**被淘汰的 trace 从来没有决策**——它既不会进导出管道，也不会进 `late_span` 统计，只是消失。这是尾采样在过载下最需要监控却最不容易被看见的丢数据形态。

### 5. 策略组合律与 drop 的前缀语义

README 的 §Policy Decision Flow 是**有序**的：

1. 有 drop → 不采样；
2. 有 inverted not sample → 不采样（已废弃）；
3. 有 sample → 采样；
4. 有 inverted sample **且没有** not sample → 采样（已废弃）；
5. 其余一律不采样（含空列表）。

把 `sample` 和 `inverted_sample` 当成同一个值，会让第 4 条退化成"永远采样"——本 demo 特意把五个值分开建模来锁住这个差别。

另外 `numDropPolicies` 只数**前缀**：

```go
for _, p := range tsp.policies { if !p.isDrop { break }; numDropPolicies++ }
```

所以把 drop 策略写在列表中间时，它**不参与前缀短路**，会在 `sample_on_first_match` 之类的路径上表现出与写在开头不同的行为。

## 代码结构

| 文件 | 说明 |
| --- | --- |
| `python/idbatcher.py` | `id_batcher.go` 逐行转写（批号、`MoveToEarlierBatch`、环形复用） |
| `python/processor.py` | 决策组合律、LRU 决策缓存、淘汰与"不回头"状态机 |
| `python/main.py` | 五组现象驱动 |
| `python/selfcheck_tailsampling.py` | 58 条断言（实跑全绿） |
| `go/tailsampling.go` | 同口径 Go 实现；两套 LRU 各自独立淘汰链（共用一条会串味） |

运行：

```bash
cd python && python main.py && python selfcheck_tailsampling.py
```

## 实测现象摘录

```text
decision_wait= 30s -> numDecisionBatches=30, 首次入批 id=30, 实际 31 次 tick 后被决策
decision_wait=30s  after_root=5s  -> 搬移=True   实际决策延迟≈5s
decision_wait=5s   after_root=30s -> 搬移=False  实际决策延迟≈5s
内存中 trace: ['T2','T3','T4']   被淘汰: ['T0','T1']   id_not_found=2
[inverted_sample, not_sample]  -> not_sampled
[inverted_not_sample, sample]  -> not_sampled
```

## 工程启示

1. **`decision_wait` 直接等于内存占用 × 延迟**。它同时决定了"决策多晚落下"与"内存里按住多少条 trace"，调大要有内存预算。
2. **加速配置必须小于 `decision_wait` 才有意义**，且它是**按整秒**取的（`DecisionWaitAfterRootReceived.Seconds()` 转 uint64），亚秒配置会被截断。
3. **一定开决策缓存**，否则晚到 span 会重开决策窗口、把 trace 切成两截；容量要远大于 `num_traces`。
4. **监控 `id_not_found` 与淘汰计数**。前者上涨 = 淘汰在发生；被淘汰的 trace 是**零决策**的，既不算丢采样也不算丢 span，只会在总量对账时表现为"少了一批"。
5. **drop 策略写在列表开头**才能享受前缀短路；写在中间会得到不同行为。

## 参考资料（实际阅读过的来源）

- [opentelemetry-collector-contrib — `processor/tailsamplingprocessor/processor.go`](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/tailsamplingprocessor/processor.go) — `numDecisionBatches = math.Max(1, DecisionWait.Seconds())`；根 span 的 `MoveToEarlierBatch` 调用；`waitForSpace` 的阻塞/淘汰两条分支与"淘汰队首"实现；tick 上 `FinalDecision != Unspecified` 即 `continue` 及其注释；`numDropPolicies` 的前缀 `break`
- [opentelemetry-collector-contrib — `internal/idbatcher/id_batcher.go`](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/tailsamplingprocessor/internal/idbatcher/id_batcher.go) — 批号为绝对编号（`takeID + len(batches)`）、`CloseCurrentAndTakeFirstBatch` 的环形替换、`MoveToEarlierBatch` 的 `proposed >= traceCurrentBatch` 空操作条件、`Stop` 后 `lastBatchID = takeID + numBatches`
- [opentelemetry-collector-contrib — Tail Sampling Processor README](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/tailsamplingprocessor/README.md) — 全部配置项与默认值（`decision_wait` 30s、`num_traces` 50000、`decision_cache` 默认 0 关闭、`num_shards` 1–256 且配额按分片均分而 `burst_capacity` 不分）；§Policy Decision Flow 五条有序组合律；缓存容量应远大于 `num_traces` 的建议
