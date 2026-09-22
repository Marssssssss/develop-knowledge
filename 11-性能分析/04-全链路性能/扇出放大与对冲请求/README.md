# 扇出放大与对冲请求：为什么单服务 p99 很漂亮，端到端却很难看

> 一个服务把自己的 p99 优化到极致，端到端 p99 仍然可能烂得离谱——因为**用户请求要等的不止一个下游**。这不是玄学，是纯算术。
>
> 本 demo 把《The Tail at Scale》给出的量化结论与 gRPC 官方 hedging 策略做成可运行实现，回答：**放大到什么程度**、**对冲要付多少代价**、**代价怎么被限流兜住**。

## 核心研究主题

- 扇出放大的闭式解 `1 − (1−p)ⁿ` 与论文的两个标定点
- 反问题：给定可容忍概率，最多能扇出多少个下游
- "等到多少算完成"：单个 leaf / 95% 完成 / 全部完成三种口径的差距
- 对冲（hedged requests）的延迟收益与负载代价，以及 `delay = p95` 这个选点
- gRPC 的 hedging 策略：`maxAttempts` 封顶、`hedgingDelay`、令牌桶限流、服务端 pushback

## 原理详解

### 1. 放大是乘法，不是加法

论文给的场景很具体：

> "consider a system where each server typically responds in 10ms but with a 99th-percentile latency of one second. If a user request is handled on just one such server, one user request in 100 will be slow."

一台服务器处理时，1% 的请求慢。但一个用户请求要**并行收集 100 台**的响应时：

> "then 63% of user requests will take more than one second"

因为要等的是**最慢的那个**，概率是

```
P(至少一个慢) = 1 − (1 − p)ⁿ
```

本 demo 实测：`p=0.01, n=100 → 0.6340`（论文 63%）。

更反直觉的是第二组数字：

> "Even for services with only one in 10,000 requests experiencing more than one-second latencies at the single-server level, a service with 2,000 such servers will see almost one in five user requests taking more than one second"

实测 `p=0.0001, n=2000 → 0.1813`。**把单机故障率压到万分之一也救不回来**——因为扇出基数涨了 2000 倍。这条直接否定了"让每个下游更快"这条路线：收益是线性的，而放大是指数的。

### 2. 反问题：最多能扇出几个

把上式反解：

```
n ≤ log(1 − target) / log(1 − p)
```

实测（容忍至少一个慢的概率 ≤ 5%）：

| 单机慢概率 | 最大扇出 |
| --- | --- |
| 1% | **5** |
| 0.01% | **512** |

这两个数就是"架构评审时该问的问题"的量化答案：扇出超过这个数，靠优化单机延迟是没用的，只能靠**减少扇出**、**降级**或**对冲**。

### 3. "等到多少算完成"是最大的杠杆

论文 Table 1 给了一组真实测量（在 root 节点测）：

| 口径 | p99 |
| --- | --- |
| 单个随机 leaf 请求完成 | **10 ms** |
| 95% 的请求完成 | **70 ms** |
| **全部**请求完成 | **140 ms** |

结论原文："waiting for the slowest 5% of the requests to complete is responsible for half of the total 99%-percentile latency."

本 demo 用一个**合成分布**（99% 落在 8–12 ms、1% 直接掉到 900–1100 ms，对应论文 §1 的 10ms / 1s 口径）复现了同样的**方向**，但幅度更极端：单个 leaf p99 = 8.2 ms、95% 完成 p99 = 12.0 ms、全部完成 p99 = 1097.5 ms——因为合成分布没有真实系统那种"厚中间尾巴"。

**这个差异本身就是结论**：放大效应有多严重，取决于 leaf 延迟分布的**中间段**，而不是 p99 那一个点。真实系统的 leaf 分布是连续的长尾，所以"等最后 5%"付出 p99 的一半；而只要存在哪怕是 1% 的"掉到秒级"，全部完成的 p99 就会被它单独决定。

### 4. 对冲：把负载换延迟，汇率是 p95

> "A simple way to curb latency variability is to issue the same request to multiple replicas and use the results from whichever replica responds first."

关键是**什么时候**发第二个请求。论文的方案：

> "defer sending a secondary request until the first request has been outstanding for more than the 95th-percentile expected latency for this class of requests. This approach limits the additional load to approximately 5% while substantially shortening the latency tail."

本 demo 实测：先量出这类请求的 p95 = 11.8 ms，用它做 `hedgingDelay`，得到 **额外负载 5.0%**，同时 p99.9 从 1075 ms 降到 23.5 ms、p99 从 911 ms 降到 19.9 ms。汇率完全对得上论文的"约 5% 负载换掉尾巴"。

论文给的 Google benchmark 更夸张：1000 个 key 分布在 100 台服务器，**10 ms 后对冲**，p99.9 从 **1800 ms 降到 74 ms**，而只多发了 **2%** 的请求。

`delay` 选点错了代价很清楚：本 demo 里 `delay = 500 ms` 时额外负载降到 1.0%，但 p99.9 仍有 511 ms——**省下的负载换不来尾部**。反之 `delay` 过小（甚至 0）会瞬间把后端负载翻倍。论文的配套建议是把对冲请求**标记成比 primary 更低的优先级**，进一步压低开销。

再进一步是 **tied requests**：同时排队给两台服务器，谁先开始执行就发取消消息给对方；客户端在两次发送之间插入 **2 倍平均网络消息延迟**（现代数据中心 ≤1 ms）。Table 2 隔离场景下 median −16%、p99.9 近 −40%，而磁盘开销 <1%。

### 5. gRPC 的 hedging 策略与三道保险

| 配置项 | 语义 |
| --- | --- |
| `maxAttempts` | 必填；**>5 时按 5 处理**（本 demo 实测 9→5、100→5） |
| `hedgingDelay` | 可选；**不填则所有副本同时发出**（等于没有延迟控制） |
| `nonFatalStatusCodes` | 命中即刻发下一个对冲请求，跳过 `hedgingDelay` |

三道保险：

1. **deadline 是总闸门**："gRPC call deadlines apply to the entire chain of hedged requests"——deadline 一到，无论还有几个在飞都会失败。这与 [延迟预算与 deadline 传播](../延迟预算与deadline传播/) 是同一套机制。
2. **令牌桶限流**：`maxTokens` / `tokenRatio`，失败 −1、成功 `+token_ratio`；**对冲请求只在 `token_count > maxTokens/2` 时才发出**（严格大于）。实测：初始 10、连续 6 次失败后降到 4，立刻停发对冲；20 次成功后回到 6 恢复。而且**只有 non-fatal 状态码或"pushback 不要重试"才算失败**——这条是刻意的，避免把 `INVALID_ARGUMENT`（请求本身有问题）误判成服务故障而触发限流。
3. **服务端 pushback**：metadata `grpc-retry-pushback-ms`，给一个正数表示"多久之后再发下一个"；**负值或不可解析一律视为"不要重试"**。实测 `'150'→150ms`、`'-1'/'abc'/缺失→不重试`。

## 代码结构

| 文件 | 说明 |
| --- | --- |
| `python/fanout.py` | 放大闭式解、反解、合成 leaf 分布、三种完成口径、对冲模拟、令牌桶、pushback 解析 |
| `python/main.py` | 四组现象驱动（全部固定随机种子，可复现） |
| `python/selfcheck_fanout.py` | 49 条断言（实跑全绿） |
| `go/fanout.go` | 同口径 Go 实现；`Amplify` 走 `Expm1/Log1p` 避免大 n 下 `Pow` 精度损失 |

运行：

```bash
cd python && python main.py && python selfcheck_fanout.py
```

## 实测现象摘录

```text
p=0.01   扇出=100  -> 0.6340        p=0.0001 扇出=2000 -> 0.1813
容忍 5% 时 p=1% 最多扇出 5 个；p=0.01% 最多扇出 512 个
单个 leaf p99=8.2ms   95% 完成 p99=12.0ms   全部完成 p99=1097.5ms
delay=11.8ms(p95) -> p99.9 1075.2 -> 23.5 ms | 额外负载 5.0%
delay=500.0ms     -> p99.9 1075.2 -> 511.7 ms | 额外负载 1.0%   <- 省了负载，没救尾部
maxAttempts=9 -> 5      6 次失败后 tokens=4 可对冲=False
pushback='-1'/'abc'/缺失 -> 不要重试
```

## 工程启示

1. **别只报单服务 p99**。端到端的目标必须由**扇出后的联合概率**定义，否则两边数字永远对不上。
2. **先算反问题再谈优化**。扇出超过 `log(1−target)/log(1−p)` 时，优化单机延迟的收益是线性的，而放大是指数的——该改架构。
3. **对冲的 delay 必须按实测 p95 设**，而且要随流量漂移定期重算；拍脑袋设一个值，要么负载翻倍，要么救不了尾部。
4. **对冲必须配限流与 pushback**。没有令牌桶的对冲在后端故障时会自动放大成 DDoS；pushback 是后端唯一的自卫手段。
5. **deadline 要盖住整条对冲链**。否则"对冲帮我把 p99.9 从 1800 ms 降到 74 ms"会在 deadline 之外变成一次彻底失败。

## 参考资料（实际阅读过的来源）

- [Jeff Dean & Luiz André Barroso — The Tail at Scale（CACM）](https://cacm.acm.org/research/the-tail-at-scale/) — 10ms/p99=1s 与扇出 100 → 63%；1/10000 与 2000 台 → 几乎 1/5；Table 1 的 10/70/140 ms 与"等最慢 5% 占 p99 一半"；对冲"推迟到 p95 之后、额外负载约 5%"与 1000 keys/100 servers/10ms → p99.9 1800ms→74ms、仅多 2%；tied requests 的 2× 网络延迟、Table 2 的 median −16% / p99.9 近 −40% / 磁盘开销 <1%；canary requests、micro-partitions（20 partitions/machine → 5% 增量）、good-enough 结果（单 leaf 拥有最佳结果的几率 <1/1000）
- [gRPC — Request Hedging 官方指南](https://grpc.io/docs/guides/request-hedging/) — `maxAttempts` 必填且 >5 按 5 处理；`hedgingDelay` 不填则同时发出；`nonFatalStatusCodes` 命中即跳过 delay；deadline 作用于整条对冲链；`maxTokens`/`tokenRatio` 令牌桶与"仅 `token_count > maxTokens/2` 才发对冲"、只有 non-fatal 或 pushback 才计失败；`grpc-retry-pushback-ms` 负值/不可解析 = 不要重试
- [gRPC — Deadlines 官方指南](https://grpc.io/docs/guides/deadlines/) — deadline 作为对冲链总闸门的机制说明

> 口径声明：本 demo 的 leaf 延迟分布是**合成**的，只标定到论文 §1 的"10ms 典型 / 1% 到 1s"，不声称复现 Table 1 的具体数值；Table 1 的 10/70/140 ms 以引用原文的方式给出，并在 README 中说明了两者的差异来源。
