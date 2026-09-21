# SRE 与可靠性工程

> 2026-09-12 巡检类目自动拓展新增（06-DevOps 维度补全：监控与可观测性关注"数据采集与
> 展示"，本目录关注"用指标驱动可靠性决策"的工程实践）。
> 2026-09-21 首批 5 个 demo 落地（ID 517-521）。

## 简介

SRE（Site Reliability Engineering）由 Google 系统化提出，核心思想是**用软件工程方法
运维系统**，把"要多可靠"变成可量化、可决策的数字。三大支柱：

- **SLI（Service Level Indicator）**：服务水平的量化测量，标准形态是"好事件数 / 总事件
  数"的时间窗口比值（如可用性、延迟达标率）
- **SLO（Service Level Objective）**：SLI 的目标值（如 99.9% 的请求 30 天内成功）
- **错误预算（Error Budget）**：`1 − SLO`，即允许"不可靠"的量。99.9% 的 30 天 SLO 意味
  着只有 43.2 分钟的预算；预算内可以激进发布，耗尽则冻结发布、优先可靠性工作

Google SRE 书明确指出：**100% 可靠性是错误目标**——冗余再多也有非零的同时故障概率；
用户与系统之间的链路（家庭 WiFi、运营商、移动设备）远比服务本身不可靠，每个额外的
"九"成本约 100 倍而边际收益趋近于零。

## 已完成 demo 索引

| ID | 目录 | 核心机制（一句话） | 语言 |
| --- | --- | --- | --- |
| 517 | [SLI窗口与聚合口径/](./SLI窗口与聚合口径/) | 区间向量是**左开右闭** `(t−w, t]`；`rate/increase` 走 `extrapolatedRate` 的外推（阈值 = 平均间隔×1.1，超了退化为半间隔，counter 还有零点钳制）；OpenSLO 的 rolling 长度恒定、calendar 长度随日历变——同一故障 rolling 拖到 4/20 才恢复，calendar 在 4/1 就清零 | Python(64 断言实跑) / Go(人工审查) |
| 518 | [错误预算与燃烧率/](./错误预算与燃烧率/) | 燃烧率 = 错误率 ÷ 预算率；Sloth `getBurnRateFactor` 把"预算%+长窗"翻译成燃烧率（30d 得 14.4/6/3/1，28d 整体按 28/30 缩放）；OpenSLO 三种 `budgetingMethod` 的差别是**加权方式**——同一份数据能给出 0.989 / 0.083 / 0.542 | Python(79 断言实跑) / Go(人工审查) |
| 519 | [多窗口多燃烧率告警/](./多窗口多燃烧率告警/) | 告警表达式是 `(短窗 and 长窗) or (短窗 and 长窗)`，阈值 = 燃烧率 × 预算率；短窗的作用是**叫停**——同一故障比"只看长窗"提前 55 分钟停；慢燃（0.12%）静态阈值 1% 完全漏报而 ticket 档能抓住 | Python(67 断言实跑) / Go(人工审查) |
| 520 | [退避与抖动/](./退避与抖动/) | 转写 AWS 官方模拟器：纯指数退避**一点工都不省**（工作量与不退避同为 5050，耗时却长 94 倍）；Decorrelated Jitter 是**有状态**的，`n` 被忽略且非单调；三种 jitter 里 Equal 最差、Full 与 Decorr 接近 | Python(61 断言实跑) / Go(人工审查) |
| 521 | [过载保护与自适应并发/](./过载保护与自适应并发/) | Envoy 梯度控制器 `gradient=(minRTT+B)/sampleRTT`、`limit_new=g·L+sqrt(L)`；稳态有闭式 `L=1/(1−g)²`；headroom 恒为正故**过冲后阻尼收敛**；minRTT 要连续 5 个窗口踩到下限才重算，jitter=0 会让整个集群同步掉容量 | Python(68 断言实跑) / Go(人工审查) |

## 待研究知识点

- [x] SLI 的选择与标准化（窗口/聚合方式/客户端 vs 服务端测量）→ demo 517
- [ ] SLO 目标设定（不要基于当前性能拍脑袋；从用户在乎什么倒推）
- [x] 错误预算策略（Error Budget Policy）：预算消耗分级动作 → demo 518（阈值与预算%的换算已落地，分级动作本身待研究）
- [x] burn rate 告警：多窗口燃烧率（1h/6h）替代静态阈值告警 → demo 519
- [ ] 事后复盘（Postmortem）文化与 blameless 原则
- [ ] Toil（琐事）识别与自动化：手动/重复/无持久价值的工作要系统性消除
- [ ] 混沌工程与故障注入（对照 08-安全 与 11-性能分析 的故障模拟）
- [x] 容量规划与过载应对（仓促应对 vs 有序降级）→ demo 521（过载侧：自适应并发与降载；容量规划侧见 `11-性能分析/容量规划与性能建模/`）
- [ ] 重试预算与重试放大（retry budget / per-try timeout 与 deadline 传播）
- [ ] SLO 与发布门禁的联动：预算耗尽如何真正冻结发布
- [ ] 告警疲劳的度量（precision / recall / 告警量人均比）

## 参考资料（实际阅读过的权威来源）

- [Service Level Objectives — Google SRE Book Ch.4](https://sre.google/sre-book/service-level-objectives/) — SLI/SLO/SLA 官方定义、目标选择原则、错误预算动机
- [Implementing SLOs — Google SRE Workbook Ch.2](https://sre.google/workbook/implementing-slos/) — 为什么 100% 是错误目标、每个九成本递增、落地步骤与错误预算政策

第二批（ID 517-521）新读的来源：

- [OpenSLO v1 规范（仓库 README 即规范正文）](https://github.com/OpenSLO/OpenSLO) — `duration-shorthand`、`timeWindow` 的 rolling / calendar-aligned 双语分支、`ratioMetric` 三形态与 `rawType`、`budgetingMethod` 三口径、`AlertCondition` 的 `burnrate` 条件
- [Prometheus — Querying basics](https://prometheus.io/docs/prometheus/latest/querying/basics/) — 区间向量"left-open and right-closed"原文
- [Prometheus — Query functions](https://prometheus.io/docs/prometheus/latest/querying/functions/) — `rate()` / `increase()` / `irate()` 官方定义与用途建议
- [Prometheus 源码 `promql/functions.go`](https://github.com/prometheus/prometheus/blob/main/promql/functions.go) — `extrapolatedRate` 全文（517 的转写对象）
- [slok/sloth — `internal/alert/window.go`](https://github.com/slok/sloth/blob/main/internal/alert/window.go) — `getBurnRateFactor`、Google 默认预算百分比注释
- [slok/sloth — `internal/alert/windows/google-30d.yaml` / `google-28d.yaml`](https://github.com/slok/sloth/tree/main/internal/alert/windows) — 四档长短窗数值（注明取自 SRE workbook）
- [slok/sloth — `internal/plugin/slo/core/alert_rules_v1/plugin.go`](https://github.com/slok/sloth/blob/main/internal/plugin/slo/core/alert_rules_v1/plugin.go) — `mwmbAlertTpl` 模板全文（519 的转写对象）
- [AWS Architecture Blog — Exponential Backoff And Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) — OCC 竞争与 N² 工作量、三种 jitter 的命名与定性对比
- [aws-samples/aws-arch-backoff-simulator — `src/backoff_simulator.py`](https://github.com/aws-samples/aws-arch-backoff-simulator) — 四种策略源码（520 的转写对象）
- [Envoy — Adaptive Concurrency filter](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/adaptive_concurrency_filter.html) — 梯度控制器公式、minRTT 测量与 5 窗口触发、jitter、headroom
- [Envoy — Overload manager](https://www.envoyproxy.io/docs/envoy/latest/configuration/operations/overload_manager/overload_manager.html) — `threshold` / `scaled` 两类 trigger、cgroup 内存压力计算

> 注：本轮 `sre.google` 在本机不可达（curl schannel 与 urllib 均为连接重置 / 握手超时，
> WebFetch 亦失败）。SRE workbook 的数值经 sloth 源码与其 windows YAML 转引，两处
> YAML 文件头均注明取自 `sre.google/workbook/alerting-on-slos`。
