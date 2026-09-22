# 全链路性能

> 用户感知到的"慢"发生在**整条链路**上：DNS → TCP → TLS → CDN/网关 → 服务 → 缓存 → DB → 回程渲染。本子领域研究如何把"端到端慢"拆成**可归因的段落**，并让每一段都有预算。
>
> 与前三个子领域的分工：[01-系统级剖析/](../01-系统级剖析/) 回答"内核/硬件层慢在哪"、[02-应用级剖析/](../02-应用级剖析/) 回答"进程内慢在哪"、[03-基准测试方法论/](../03-基准测试方法论/) 回答"这个快慢结论可信吗"，**这里回答"用户端的这几百毫秒分别花在哪一段"**。前三者是**单机视角**，本子领域是**跨服务、跨端到端视角**。

## 核心研究主题

- **用户体验指标**：Core Web Vitals（LCP 加载 / INP 交互响应 / CLS 视觉稳定）、TTFB/FCP/TBT 等诊断指标
- **测量口径**：field（真实用户监测 RUM / CrUX）vs lab（合成监测 Synthetic / Lighthouse），以及 p75 聚合口径
- **端到端追踪**：OpenTelemetry 的 trace/span 数据模型、上下文传播（context propagation）、span kind 拓扑语义、关键路径分析
- **延迟预算**：把 SLO 目标（如 p99 ≤ 300 ms）逐段下发成子预算，每段单独监控与门禁
- **依赖图与扇出放大**：一次用户请求触发的 N 次下游调用，尾部延迟如何叠加（fan-out amplification）
- **采样与成本**：head sampling vs tail sampling、trace 采样率与"只对慢请求全采"策略

## 原理详解

### 1. 为什么是 p75，而不是均值

Core Web Vitals 用**第 75 百分位**作为判定口径：一个页面/站点在某个指标上，至少 75% 的访问达到"良好"阈值才算通过；反之若至少 25% 的访问落到"较差"阈值以下则判为"欠佳"。

官方给出了选 p75 的两条理由：① 要保证**大多数**访问达到目标水平，百分位越高越能满足；② 但百分位越高越容易**被离群值主导**——若用 p95 评估一个只有 100 次访问的站点，只需 5 个离群样本就能决定分类结果。p75 落在两条冲突目标的平衡点上：4 次访问中有 3 次达标，同时需要 25 个离群样本才可能污染结论。

> 工程含义：**别用均值报延迟**。均值天然把长尾抹平，而用户抱怨的恰恰是长尾；p99 单看又容易被少数极端样本带偏。

### 2. field 与 lab 不是同一个分数

同一 URL 丢进 PageSpeed Insights 会得到两组不一致的数字，这不是工具坏了：

| | field（实测） | lab（实验室） |
| --- | --- | --- |
| 来源 | CrUX：真实 Chrome 用户、真实设备、**滚动 28 天**聚合 | Lighthouse：**单次**模拟加载 |
| 环境 | 五花八门（中低端安卓 + 蜂窝网占大头） | 受控（限速、模拟中端机、空缓存、无扩展） |
| 用途 | **判定**（Search Console 通过与否看它） | **诊断**（能指名道姓指出是哪张图、哪个脚本） |
| 时效 | 修复上线后约 4 周才完全滚出窗口 | 立即反映改动 |

结论：**用 lab 找原因、改代码；用 field 确认效果**。看到"改完 field 没动"先别慌，是 28 天窗口的滞后。

### 3. 端到端追踪的数据模型

OpenTelemetry 把一次请求建模为一棵 **span 树**：

- **trace_id**：整条链路的唯一标识，跨进程、跨服务共享
- **span_id / parent_id**：`parent_id` 为空的是根 span；`parent_id` 指向另一 span 的 `span_id` 即构成父子关系（同一 trace 内）
- **start_time / end_time**：span 的起止时间戳 → 父 span 时长 − 子 span 时长 = **self time**，这正是"慢在哪一段"的可计算形式
- **span kind**：`Client` / `Server` / `Internal` / `Producer` / `Consumer`，给出了**跨进程的配对约定**——按规范，server span 的父通常是对端 client span，client span 的子通常是 server span；consumer span 的父**总是** producer span。后端靠这个语义把散落在不同服务里的 span 拼回一棵树。
- **span context**：span 中"被序列化并随请求传播"的那部分（trace_id、span_id、trace flags、trace state），是**上下文传播（context propagation）** 的载体，也是分布式追踪能成立的前提
- **span events**：带时间戳的**单点**标注（"页面变为可交互"这类瞬间），区别于有起止的 span；同时 span 还带 attributes（结构化键值）与 status

> 归因要点：跨服务的一跳在父子两侧**各有一个 span**（client 侧 / server 侧），二者时长之差就是**网络 + 排队**开销；这部分在任一侧的 profiler 里都看不见。

### 4. 延迟预算：TTFB 是 LCP 的地板

LCP 的计时起点是"用户请求 URL"，因此服务端首字节时间（TTFB）直接构成 LCP 的**下界**——服务端 800 ms 才吐首字节，前端再快也不可能做出 2.5 s 以内的 LCP。

反过来说，阈值不是拍出来的。LCP 的"良好"阈值 2.5 s 来自两路交叉验证：一是人类感知研究（Newell / Card / Miller 关于"1 秒即时响应"的讨论，原始描述其实是一个 **0.3–3 s 的区间**，再结合已有 FCP=1 s 收紧到 1–3 s）；二是**可实现性**——用 CrUX 数据验证候选阈值下至少 10% 的源能达标，且优化良好的站点能**持续**达标（实测 1.5 s / 2 s 无法持续达成，2.5 s 可以）。

INP 的 200 ms 阈值同理，来自"因果知觉"这类感知实验：延迟 ≤ 100 ms 时受试者认为反馈由自己的操作引起，100–200 ms 出现分歧，> 200 ms 则不再归因于操作。100 ms 是理想值，但按可达性放宽到 200 ms。

> 这一节的方法论价值大于数字本身：**性能阈值应当是"感知研究 + 可实现性"的交集**，而不是"我们上次跑到了 X"。

### 5. 扇出与尾部放大

服务 A 处理一次请求要并发调 10 个下游。即使每个下游只有 1% 的请求超过 1 s，A 的单次请求里"至少一个下游超时"的概率是 1 − 0.99¹⁰ ≈ 9.6%。再套一层（每层 ×10 扇出）就是 63%。

这就是"单服务 p99 很漂亮，端到端 p99 很难看"的算术来源。对策不是"让每个下游更快"，而是：给下游设**独立的超时与预算**（预算用尽就降级）、对非关键依赖做**异步化或裁剪**、把尾部采样打开以便真的能看见这些慢 trace。

## 已完成 demo（2026-09-22 首批 5 个，ID 596-600）

| demo | 核心机制 | 自检 |
| --- | --- | --- |
| [W3C-TraceContext传播/](./W3C-TraceContext传播/) | `traceparent` 四字段定长布局与"忽略 vs 重开"两类失败；sampled 是 **bit 0** 不是"等于 1"；高版本按位置解析（dash 在 2/35/52，短于 55 字符重开）；tracestate 左移规则与两阶段截断；baggage 的 64 成员 / 8192 字节下限 | 62 条 |
| [尾采样决策窗口/](./尾采样决策窗口/) | 批数 = `decision_wait` 秒数，决策延迟 = 批数 + 1 次 tick；根 span 加速只在 `after_root < decision_wait` 时生效；**决策不回头**；`num_traces` 满时淘汰最老待决 trace 且**不给决策**；LRU 决策缓存兜住晚到 span | 58 条 |
| [关键路径与self-time归因/](./关键路径与self-time归因/) | `self_time = duration − Σ 子 span 时长`，并发时为负 → 切 `parallel_wait`；关键路径以 self time 为点权的根到叶最长路径；`(CLIENT, SERVER)` 配对差值 = 网络 + 排队；多根 / 孤儿 span 检测 | 38 条 |
| [延迟预算与deadline传播/](./延迟预算与deadline传播/) | deadline 是时刻、timeout 是时长；传播要扣已耗时与收尾预留；**线路上传 timeout 才免疫时钟偏移**（±30 s 实测）；客户端 `DEADLINE_EXCEEDED` vs 服务端 `CANCELLED`；等分 / 按成本加权 / 可压缩空间注水三种拆法与不可行判定 | 51 条 |
| [扇出放大与对冲请求/](./扇出放大与对冲请求/) | `1 − (1−p)ⁿ`：p=1% 扇出 100 → 63%；反解最大可容忍扇出（5 / 512）；对冲 delay 取 **p95** 时额外负载 ≈5% 而 p99.9 从 1075 ms 降到 23.5 ms；gRPC `maxAttempts` 封顶 5、令牌桶限流、`grpc-retry-pushback-ms` | 49 条 |

## 待研究

- [x] 端到端追踪的传播机制：W3C Trace Context 的 `traceparent` 字段布局与采样标志（[W3C-TraceContext传播/](./W3C-TraceContext传播/)，2026-09-22，ID 596）
- [x] 尾采样（tail sampling）的判定窗口与"决策不回头"约束（[尾采样决策窗口/](./尾采样决策窗口/)，2026-09-22，ID 597）
- [x] 关键路径分析（critical path）与 span 树的差值归因算法（[关键路径与self-time归因/](./关键路径与self-time归因/)，2026-09-22，ID 598）
- [x] 延迟预算的拆分母法：SLO → 每跳预算的分配策略（等分 / 按成本 / 按可压缩空间）（[延迟预算与deadline传播/](./延迟预算与deadline传播/)，2026-09-22，ID 599）
- [x] 扇出放大的量化模型与"最大可容忍下游数"（[扇出放大与对冲请求/](./扇出放大与对冲请求/)，2026-09-22，ID 600）
- [ ] 合成监测（Synthetic）与 RUM 的互补：用合成做门禁、用 RUM 做判定的闭环
- [ ] CDN / 边缘侧的缓存命中率与回源放大对端到端延迟的影响
- [ ] Core Web Vitals 的字段采集口径：LCP 候选元素淘汰、INP 的交互分组与离群剔除、CLS 的会话窗口
- [ ] head sampling 与 tail sampling 的成本对照：在既有 trace 上重放两种策略的命中率

## 参考资料（实际阅读过的来源）

- [How the Core Web Vitals metrics thresholds were defined — web.dev（官方阈值定义方法论，中文版）](https://web.dev/articles/defining-core-web-vitals-thresholds) — LCP 2.5 s / INP 200 ms / CLS 0.1 三个阈值的推导过程：人类感知研究（Newell、Card、Miller 的"1 秒"实为 0.3–3 s 区间）+ 可实现性验证（"至少 10% 的源达标"+"优化良好站点能持续达标"）；以及 p75 而非 p95 的两条冲突目标与平衡点论证
- [Core Web Vitals report — Google Search Console 官方帮助](https://support.google.com/webmasters/answer/9205520) — LCP ≤2.5 s / INP ≤200 ms / CLS ≤0.1 的 good–needs improvement–poor 三档表与各指标官方定义；"组内 LCP/INP 取 75% 访问的值、CLS 取 75% 访问的最差公共值"的口径说明
- [What Are the Core Web Vitals? LCP, INP & CLS Explained — corewebvitals.io](https://www.corewebvitals.io/core-web-vitals) — INP 于 2024 年 3 月取代 FID（FID 只测首次交互的输入延迟，INP 测整次访问中所有点击/轻触/按键，且取最长交互并剔除离群）；INP 不测量滚动、悬停这类连续交互
- [Traces — OpenTelemetry 官方文档](https://opentelemetry.io/docs/concepts/signals/traces/) — span 的完整字段（name / parent_id / 起止时间戳 / span context / attributes / events / links / status）、SpanKind 五值与官方种属约定、上下文传播作为分布式追踪核心机制的说明；三个示例 span 的 trace_id 相同、parent_id 构成层级，即"trace = 带上下文与层级的结构化日志集合"
- [Queueing Theory for SREs: Little's Law and the Utilisation Knee — cloudandsre.com](https://cloudandsre.com/blog/queueing-theory-for-sres) — 用于本页扇出放大与排队拐点的口径校验（W = S/(1−ρ) 在 ρ=0.7 处 3.3×、0.9 处 10×），与延迟预算"为什么不能打满"的量化依据

### 2026-09-22 首批 5 demo 新增来源

- [W3C Baggage — TR/baggage](https://www.w3.org/TR/baggage/) — 64 成员 / 8192 字节传播下限、"不得传播半个成员"、value 的 ASCII 限制与百分号编码
- [opentelemetry-collector-contrib — `processor/tailsamplingprocessor/processor.go`](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/tailsamplingprocessor/processor.go) — `numDecisionBatches = math.Max(1, DecisionWait.Seconds())`、根 span 的 `MoveToEarlierBatch`、`waitForSpace` 的阻塞/淘汰分支、tick 上 `FinalDecision != Unspecified` 即跳过、`numDropPolicies` 的前缀 break
- [opentelemetry-collector-contrib — `internal/idbatcher/id_batcher.go`](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/tailsamplingprocessor/internal/idbatcher/id_batcher.go) — 批号为绝对编号（`takeID + len(batches)`）、环形槽位替换、`MoveToEarlierBatch` 的 `proposed >= traceCurrentBatch` 空操作条件
- [OpenTelemetry Specification — Trace API（SpanKind）](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/trace/api.md) — 五值 SpanKind 二维表、`CLIENT` 传播后成为远端 `SERVER` 的父（跨进程配对与差值归因的依据）
- [gRPC — Deadlines 官方指南](https://grpc.io/docs/guides/deadlines/) — deadline 是时刻 / timeout 是时长、默认不设即无限等、`DEADLINE_EXCEEDED` 与 `CANCELLED` 的分工、传播时扣除已耗时以屏蔽时钟偏移、13:00:00 / 2s / 0.5s / 1.5s 时序
- [gRPC — Request Hedging 官方指南](https://grpc.io/docs/guides/request-hedging/) — `maxAttempts` 必填且 >5 按 5 处理、`hedgingDelay` 不填则同时发出、deadline 覆盖整条对冲链、`maxTokens`/`tokenRatio` 令牌桶与 `> maxTokens/2` 才发对冲、`grpc-retry-pushback-ms` 负值即不重试
- [Jeff Dean & Luiz André Barroso — The Tail at Scale（CACM）](https://cacm.acm.org/research/the-tail-at-scale/) — 10ms/p99=1s 与扇出 100 → 63%；1/10000 与 2000 台 → 约 18%；Table 1 的 10/70/140 ms；对冲 delay 取 p95 → 额外负载约 5%，1000 keys/100 servers 实测 p99.9 1800ms→74ms 且只多 2% 请求；tied requests 的 2× 网络延迟与 Table 2 的 median −16% / p99.9 近 −40%

> 姊妹阅读：[05-容量规划与性能建模/](../05-容量规划与性能建模/) 把这里的"预算"升级为"可预测的容量曲线"。
