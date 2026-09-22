# 延迟预算：从 SLO 到每一跳的 deadline

> "p99 ≤ 300 ms" 这种 SLO 只有落到**每一跳各自的 deadline**上才可执行。难点在于：预算是个**时长**，而跨进程能安全传递的是另一个东西。
>
> 本 demo 把 gRPC 官方的 deadline 语义做成可运行实现，并给出三种把总预算拆给各跳的策略，回答：**为什么线路上传的是 timeout 而不是 deadline**、**为什么客户端和服务端报的状态不一样**、**预算不够分时该怎么降级**。

## 核心研究主题

- deadline（时刻）vs timeout（时长）：两个概念、一次互换
- 传播时**扣掉已耗时**与预留收尾时间
- **时钟偏移**：为什么直接传绝对时刻会出事，传 timeout 为什么没事
- 客户端 `DEADLINE_EXCEEDED` 与服务端 `CANCELLED` 的分工
- 预算分配三策略：等分 / 按历史成本加权 / 可压缩空间注水
- 逐跳对账与"预算不可行"的判定

## 原理详解

### 1. deadline 是时刻，timeout 是时长

官方定义得很直接：

> "A deadline is used to specify a point in time past which a client is unwilling to wait for a response from a server."

而 timeout 是"这个调用最长能持续多久"，二者可以互换（timeout + 当前时刻 = deadline）。文档里还有一句必须记住的：

> "By default, gRPC does not set a deadline which means it is possible for a client to end up waiting for a response effectively forever."

**不设 deadline 就是无限等**。这是分布式系统里"一个慢下游拖垮整条链路"最常见的起点——客户端线程一直挂着，上游的连锁超时全部失效。

### 2. 传播时要把已耗时扣掉

服务端在转发给下游时，不能把上游给的 deadline 原样传下去，而要先扣掉自己已经花掉的时间。官方给的时序是：

```text
13:00:00  客户端发起，要求 2s 内完成   → deadline = 13:00:02
13:00:00  用户服务收到，花了 0.5s
13:00:00  用户服务调账单服务           → timeout = 1.5s   （不是 2s）
13:00:02  到点：客户端报 DEADLINE_EXCEEDED，服务端报 CANCELLED
```

本 demo 在 `propagate()` 里额外加了一个 `reserve` 参数：除了已耗时，还要给**自己收尾**留一点（回包序列化、日志、以及"下游超时后我还要能把 `CANCELLED` 返回给上游"）。`reserve` 吃光剩余时间时**必须截断到 0 而不是返回负数**——业务代码里负 timeout 经常被误解成"不限时"，那正好是它要防的事故。

### 3. 为什么线路上传 timeout

这是整个机制里最容易被忽略的一条：

> "Since a deadline is set point in time, propagating it as-is to a server can be problematic as the clocks on the two servers might not be synchronized. To address this gRPC converts the deadline to a timeout from which the already elapsed time is already deduced. This shields your system from any clock skew issues."

于是形成了清晰的分工：**进程内部用 deadline（便于统一比较），线路上用 timeout（免疫时钟偏移）**。本 demo 实测（预算 2 s）：

| 服务端时钟偏移 | 传 timeout 后剩余 | 直传绝对 deadline 会算出 |
| --- | --- | --- |
| −30 s | 2.00 s | +32.00 s |
| 0 s | 2.00 s | +2.00 s |
| +30 s | 2.00 s | −28.00 s |

两种错误方向都致命：算成 32 s 会让服务端白干 30 秒；算成 −28 s 会让服务端**立刻放弃**一个其实还有整 2 秒预算的请求。

### 4. 两侧状态为什么不一样

- 客户端等到 deadline 之后放弃 → **`DEADLINE_EXCEEDED`**；
- 服务端在客户端设的 deadline 过后**自动取消**这次调用 → **`CANCELLED`**。

注意 `CANCELLED` 是服务端侧的状态，而且文档特别提醒：**"the server application is responsible for stopping any activity it has spawned to service the RPC"**——框架只会把调用标记为取消，你在 RPC 里派生出去的goroutine/线程/查询要自己去检查取消信号并停掉。否则"取消"只是账面取消，资源还占着。

本 demo 的一个细节：判定是**闭区间**——恰好等于 deadline 算成功（客户端），服务端也只有在**越过** deadline 后才转 `CANCELLED`。边界写反会在压测里制造大量"刚好卡线"的假故障。

### 5. 预算怎么拆：三种策略

规范只定义了传播机制，**没有**定义拆分策略。以下是本 demo 给出的三种（口径已标注）：

| 策略 | 做法 | 适用 |
| --- | --- | --- |
| 等分 | `B / N` | 各跳同质、无历史数据时的起点 |
| 按成本加权 | `B × cᵢ / Σc` | 有历史耗时分布，按现状分配 |
| 可压缩空间注水 | 先给下限 `fᵢ`，余量按 `(cᵢ − fᵢ)` 比例分，封顶于 `cᵢ` | 有**不可压缩部分**（必需计算）与**上限**（再给也没用）时 |

注水法实测（200 ms 分给三跳，下限 40/20/10、上限 150/60/30）得到 **124.1 / 50.6 / 25.3 ms**：可压缩空间大的第一跳拿到最多余量，三跳都落在 `[floor, cap]` 内，且刚好分完。当预算充裕到超过总上限时（1000 ms），分配停在 150/60/30，余量 760 ms 留作 slack——**再多的预算对这条链路没有意义**。

最关键的是**不可行判定**：`Σ floors > B` 时（如 50 ms 预算对 40/20/10 的下限和 70 ms），注水法返回 `feasible = false`、`slack = −20 ms`。此时只有三条路：**砍依赖**（去掉一跳）、**降级**（改成异步或返回兜底）、**放宽 SLO**。靠"让下游再快一点"是解决不了的——因为下限是不可压缩的。

## 代码结构

| 文件 | 说明 |
| --- | --- |
| `python/deadline.py` | 时刻/时长互换、`propagate`、两侧状态、三种分配、逐跳对账 |
| `python/main.py` | 五组现象驱动 |
| `python/selfcheck_deadline.py` | 51 条断言（实跑全绿） |
| `go/deadline.go` | 同口径 Go 实现；含 `WorstHops` 的 TopN 排序与 +Inf 零预算处理 |

运行：

```bash
cd python && python main.py && python selfcheck_deadline.py
```

## 实测现象摘录

```text
now=+ 2.5s  timeout=-0.50s  client=DEADLINE_EXCEEDED  server=CANCELLED
2s 预算、本跳花 0.5s -> 下游 timeout=1.50s；留 0.2s 收尾 -> 1.30s
时钟偏 +30.0s -> 正确剩余=2.00s（直传时刻会算出 -28.00s）
注水: [124.1, 50.6, 25.3] 余量=0.0ms；下限和>预算: feasible=False slack=-20ms
跳1: 预算=120.0ms 实际=140.0ms 差=+20.0ms 超支
```

## 工程启示

1. **每个 RPC 都要显式设 deadline**，尤其是"内部调用"。默认值是无限等，这是事故不是配置。
2. **传播链路上的每一跳都要扣掉自己的已耗时**。只给入口设一次 deadline、中间原样转发，等于没设——下游会各自拿到完整的 2 秒，端到端变成 6 秒。
3. **服务端要主动检查取消信号**。框架只负责把状态改成 `CANCELLED`，你派生的查询/线程要自己停。
4. **监控两个不同的状态码**。`DEADLINE_EXCEEDED` 上涨说明客户端等不下去了，`CANCELLED` 上涨说明服务端在收拾残局，二者的行动完全不同。
5. **预算不可行时走降级，不要走"优化"**。`Σ floors > B` 是结构性问题，砍依赖或异步化才有效。

## 参考资料（实际阅读过的来源）

- [gRPC — Deadlines 官方指南](https://grpc.io/docs/guides/deadlines/) — deadline 是"不愿意再等的时刻"而 timeout 是时长；默认不设 deadline 等于无限等待；客户端 `DEADLINE_EXCEEDED` 与服务端 `CANCELLED` 的分工；服务端应用需自行停止派生的活动；deadline 传播要"扣除已耗时"以屏蔽时钟偏移；13:00:00 / 2s / 0.5s / 1.5s 的完整时序示例；各语言对传播的支持差异（Java/Go 默认开启、C++ 需显式启用）
- [gRPC — Request Hedging 指南](https://grpc.io/docs/guides/request-hedging/) — "gRPC call deadlines apply to the entire chain of hedged requests"，即 deadline 是对冲/重试的**总闸门**（与 [扇出放大与对冲请求](../扇出放大与对冲请求/) 配套阅读）

> 口径声明：三种预算分配策略（等分 / 按成本加权 / 可压缩空间注水）以及 `reserve` 收尾预留均为本 demo 的定义，gRPC 规范只定义传播机制，不定义拆分策略。
