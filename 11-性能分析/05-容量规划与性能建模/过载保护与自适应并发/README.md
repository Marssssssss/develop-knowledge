# 过载保护与自适应并发

> 前四个 demo 都在算「还剩多少余量」，这一个回答「余量用完之后怎么办」。三段可执行的机制：**关键性分级降级**（Google SRE 书的四个 criticality 值）、**客户端自适应限流**（`requests` 与 `accepts` 的 K 倍门限）、**自适应并发限制**（Envoy 梯度控制器 + Netflix VegasLimit）。

代码：`python/overload.py`（关键性 + 客户端限流 + Envoy）、`python/vegas.py`（Netflix VegasLimit 逐行复刻）、`python/selfcheck_overload.py`（150 条断言）、`python/main.py`、`go/`（同口径 Go 实现）。

## 一、关键性分级：拒绝必须有顺序

SRE 书给四个值（从低到高）：`SHEDDABLE` < `SHEDDABLE_PLUS` < `CRITICAL` < `CRITICAL_PLUS`。任务级过载保护基于 **utilization**（通常是 CPU 实际用量 / 预留 CPU，有时含内存），**关键性越高、拒绝阈值越高**。

```text
阈值：SHEDDABLE 0.6 / SHEDDABLE_PLUS 0.7 / CRITICAL 0.85 / CRITICAL_PLUS 0.95

util=0.50  全部服务
util=0.65  仅拒 SHEDDABLE
util=0.75  拒 SHEDDABLE、SHEDDABLE_PLUS
util=0.90  只剩 CRITICAL_PLUS
util=0.99  全部拒绝
```

书里的不变量被做成断言：**"只有当所有更低关键性的请求都已经在被拒绝时，才会拒绝某个关键性的请求"**。实现上只要保证阈值随关键性**严格递增**（构造时校验，否则直接报错），这条不变量就自动成立——自检在 8 个利用率点上验证「被拒集合恒为从最低开始的一段前缀」。

书里另一条容易被忽略的补充：**关键性与延迟要求是正交的**。搜索建议请求是高度 sheddable 的，但延迟要求很严——"能不能丢"和"多快返回"是两个独立维度。

## 二、客户端自适应限流：K 就是那个旋钮

每个客户端任务维护**两分钟窗口**内的两个计数：

- `requests`：应用层尝试的请求数（在自适应限流系统之上）
- `accepts`：后端接受的请求数

正常情况下二者相等；后端开始拒绝时 `accepts` 变小，**直到 `requests` 达到 `accepts` 的 K 倍，客户端开始本地拒绝**（请求根本不出网卡）。K 默认 2。

> 原文给出的拒绝概率曲线只有一张图（*Client request rejection probability*），本文读不到其中的式子，因此**不臆造该式**；只建模原文明确写出的门限条件与它给出的两个定量结论。

门限 `S = K · A`（S 为发往后端的速率、A 为后端接受速率）推出的稳态：

| 情形 | 发送 S | 后端接受 | 后端拒绝 | 本地拒绝 |
| --- | --- | --- | --- | --- |
| `G ≤ C` 未过载 | G | G | 0 | 0 |
| `C < G ≤ K·C` | G | C | G − C | 0 |
| `G > K·C` 客户端自限 | K·C | C | (K−1)C | G − K·C |

于是 **后端「拒绝/接受」= K − 1**，正好对上原文的两句话：

- `K = 2` → **每处理 1 个就拒绝 1 个**（"Even in large overload situations, backends end up rejecting one request for each request they actually process"）
- `K = 1.1` → **每接受 10 个才拒绝 1 个**（"Reducing the modifier to 1.1 means only one request will be rejected by the backend for every 10 requests accepted"）

实跑（`C = 200`、`G = 1000`）：

```text
K=1.1  发送 220.0  后端接受 200.0  后端拒绝  20.0  本地拒绝 780.0  拒:接 = 0.10
K=1.5  发送 300.0  后端接受 200.0  后端拒绝 100.0  本地拒绝 700.0  拒:接 = 0.50
K=2.0  发送 400.0  后端接受 200.0  后端拒绝 200.0  本地拒绝 600.0  拒:接 = 1.00
K=3.0  发送 600.0  后端接受 200.0  后端拒绝 400.0  本地拒绝 400.0  拒:接 = 2.00
```

**K 越小越激进**：保护后端更狠，但状态传播变慢（后端恢复时客户端感知更迟）。原文偏向 `K = 2` 的理由正是"更多请求到达后端 → 后端状态变化能更快传播到所有客户端"。另外断言了**后端接受量永不超出 C**——这是整套机制的目的。

## 三、Envoy 梯度控制器：用 RTT 比值调并发

```text
gradient  = (minRTT + B) / sampleRTT,   B = minRTT × buffer_pct
limit_new = gradient × limit_old + headroom,   headroom = √(limit_old)  （不可配置）
```

`minRTT` 通过把并发**钉在 `min_concurrency`** 上周期性测得（`min_concurrency` 与 `min_concurrency_limit` 可分开配，前者是测量时的并发，后者是正常运行时允许压到的下限）。

**buffer 的全部意义在于"容忍正常抖动"**——这一条用**成对断言**钉死（`minRTT = 10 ms`）：

| sampleRTT | 无 buffer | 10% buffer |
| --- | --- | --- |
| 10.0 | 1.0000（扩张） | 1.1000（扩张） |
| 10.5 | 0.9524（**收缩**） | 1.0476（**仍扩张**） |
| 11.0 | 0.9091 | 1.0000 |
| 20.0 | 0.5000 | 0.5500 |

同样的 5% 抖动，有没有 buffer 会让控制器走向**相反方向**。

**headroom 不是可选项**：文档明确说它"迫使并发上限一直增长直到出现偏离"，因为 `sampleRTT ≈ minRTT` 时 `gradient ≈ 1`，没有 headroom 就会停滞在一个过小的值上。断言 `envoy_update(1, 1.0) > 1.0`。

## 四、Netflix VegasLimit：把 TCP Vegas 搬进服务端

逐行对照 `VegasLimit.java` 与 `Log10RootIntFunction.java`（master 分支）：

```text
LOG10(t)  = max(1, (int)log10(t))        # t < 1000 查预计算表
alpha     = 3 × LOG10(limit)
beta      = 6 × LOG10(limit)
threshold =     LOG10(limit)
queueSize = ceil(limit × (1 − rtt_noload/rtt))       # 这就是 TCP Vegas 的排队估计
```

判定顺序（**顺序本身就是语义，不能调换**）：

```text
1. rtt <= 0                      -> 抛异常
2. 探针到期                       -> 刷新 jitter/计数、更新 rtt_noload、直接返回
3. rtt_noload == 0 或 rtt 更小    -> 更新基线、直接返回
4. didDrop                       -> limit − LOG10(limit)
5. inflight × 2 < limit          -> 原样返回（防止未贴近上限时向上漂移）
6. queueSize ≤ threshold         -> limit + beta      （激进扩张）
   queueSize <  alpha            -> limit + LOG10(limit)
   queueSize >  beta             -> limit − LOG10(limit)
   其余（alpha ≤ queueSize ≤ beta）-> 原样返回（甜蜜区）
7. 夹到 [1, maxConcurrency]；(1 − smoothing)·old + smoothing·new
```

`limit = 100` 时 `LOG10 = 2`，于是 `alpha/beta/threshold = 6/12/2`。四个分支各自有独立用例（用 `nl/rtt = 15/16` 这类**二进制精确**的比值构造 `queueSize`，见下）：

| 构造 | queueSize | 分支 | 结果 |
| --- | --- | --- | --- |
| limit=32, nl/rtt=16/16 | 0 | ≤ threshold | 32 → 38（+beta 6） |
| limit=32, nl/rtt=31/32 | 1 | **== threshold** | 32 → 38（边界含等号） |
| limit=32, nl/rtt=15/16 | 2 | threshold < q < alpha | 32 → 33（+LOG10 1） |
| limit=96, nl/rtt=15/16 | 6 | **== beta** | 96（不变，甜蜜区上界含等号） |
| limit=320, nl/rtt=15/16 | 20 | > beta | 320 → 318（−LOG10 2） |

另外两条容易被漏掉的行为：`didDrop`（超时/丢弃）**优先于所有队列分支**；`inflight × 2 < limit` 时**即使 RTT 已经爆表也原样返回**——自检用同一 RTT（100）配 `inflight=10` 与 `inflight=60` 两个用例成对验证。

探针：`probeJitter × 30 × limit ≤ probeCount`，`jitter ∈ [0.5, 1)`。Java 用 `ThreadLocalRandom`，本 demo **强制注入确定性抖动源**（不注入就抛错），否则断言不可复现。实测 `limit=100`、`jitter=0.5` → 第 1500 次触发，触发后 `probeCount` 归零、`rtt_noload` 刷新。

## 五、踩过的坑

- **`LOG10(100) = 2` 不是 3**。`(int)log10(100)` 截断了小数部分，只有到 1000 才是 3；`999` 也是 2。首版断言按 3/6/1... 错了四条。
- **`queueSize` 的浮点灰尘会改变分支**。`ceil(30 × (1 − 10/30))` 数学上是 20，实际算得 `20.000000000000004`，`ceil` 之后是 **21**（Java 同一套算术，结果一致）。构造分支用例时改用 `15/16`、`31/32` 这类可精确表示的比值，否则 `queueSize` 会整体偏 1 而落进隔壁分支。
- **`K·C` 也会带浮点灰尘**：`1.1 × 200 = 220.00000000000003`。稳态量是连续值，断言必须用容差而不是 `==`。
- **javadoc 与实现不一致**：`VegasLimit` 的类注释写 `alpha = Max(3, 10% of the current limit)`，而代码是 `3 × LOG10(limit)`；`limit = 1000` 时前者给 100、后者给 9。**以源码为准**（本 demo 断言 9），并在注释里记录这处差异。
- **不要照抄原书图的公式**：自适应限流的拒绝概率曲线在原书中只有图片，读不到文本。本 demo 只实现原文明确写出的门限与两个定量结论（1:1 与 1:10），把"公式未知"这件事显式写在 README 里，比补一个看起来合理的式子更诚实。

## 参考资料（实际阅读过的来源）

- [Chapter 21 — Handling Overload, *Site Reliability Engineering*（Google SRE Book）](https://sre.google/sre-book/handling-overload/) — 四个 criticality 值的定义与排序、**"只有当所有更低关键性的请求都已被拒绝时才拒绝某个关键性"**、utilization 口径的任务级过载保护、客户端自适应限流的 `requests`/`accepts` 两分钟窗口与 K 倍门限、K 默认 2 与 1.1 的取舍理由、**"后端最终每处理一个就拒绝一个"**、**"K=1.1 意味着每接受 10 个才拒绝 1 个"**、关键性与延迟要求正交、自适应限流按关键性分别计数、RPC 系统自动传播 criticality
- [Adaptive Concurrency — Envoy 文档](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/adaptive_concurrency_filter) — `gradient = (minRTT + B)/sampleRTT`、`B = minRTT × buffer_pct`、`limit_new = gradient × limit_old + headroom`、**headroom 不可配置且取并发上限的平方根**、minRTT 通过把并发钉在 `min_concurrency` 上测得、`min_concurrency` 与 `min_concurrency_limit` 的分工、minRTT 窗口会造成 503 上升因此建议开重试
- [Netflix/concurrency-limits — `VegasLimit.java`](https://raw.githubusercontent.com/Netflix/concurrency-limits/master/concurrency-limits-core/src/main/java/com/netflix/concurrency/limits/limit/VegasLimit.java) — `queue_use = limit − BWE×RTTnoLoad = limit × (1 − RTTnoLoad/RTTactual)`、默认值 `initialLimit=20 / maxConcurrency=1000 / smoothing=1.0 / probeMultiplier=30`、`alphaFunc = 3*LOG10`、`betaFunc = 6*LOG10`、`thresholdFunc = LOG10`、increase/decrease 的形式、判定顺序、`didDrop` 优先、`inflight*2 < estimatedLimit` 防漂移、`shouldProbe` 判据与 `nextDouble(0.5, 1)` 抖动、以及 **javadoc 写 "alpha=Max(3, 10%)" 与实现为 `3*log10` 的不一致**
- [Netflix/concurrency-limits — `Log10RootIntFunction.java`](https://raw.githubusercontent.com/Netflix/concurrency-limits/master/concurrency-limits-core/src/main/java/com/netflix/concurrency/limits/limit/functions/Log10RootIntFunction.java) — `lookup[i] = max(1, (int)log10(i))` 的预计算表与 `t < 1000` 查表 / 否则直接算的分支
