# 监控与可观测性

## 子领域与已完成 demo

| demo | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 051 | [Prometheus指标模型/](./Prometheus指标模型/) | 数据模型 + exposition 文本格式 0.0.4 解析（HELP/TYPE、转义、histogram 展开约定） | C / Python / Go |
| 052 | [分布式追踪与TraceContext/](./分布式追踪与TraceContext/) | W3C Trace Context：traceparent 校验/逐跳传播/Span 树还原 | C / Python / Go |
| 053 | [直方图分位数估算/](./直方图分位数估算/) | histogram_quantile 线性插值 + 边界规则 + 桶宽误差分析 | C / Python / Go |
| 347 | [PromQL范围向量与rate外推/](./PromQL范围向量与rate外推/) | 即时/范围向量、lookback 5m、左开右闭区间、`rate`/`increase` 外推（阈值 1.1×平均间隔）、计数器重置补偿与零点截断 | C / Python / Go |
| 348 | [TSDB存储与Head块/](./TSDB存储与Head块/) | 2h 不可变块 + Head 块 + WAL 重放、`wal/` 段 128 MB 至少留 3 段、保留期与「崩溃会丢多少」的口径 | C / Python / Go |
| 349 | [OpenTelemetryCollector管线/](./OpenTelemetryCollector管线/) | 组件 `type[/name]` 复合键与六段组件表、pipeline 引用校验、两端 fanout、connector 依赖环检测、`memory_limiter` 三态、`batch` 阈值切分、退避重试与背压 | C / Python / Go |
| 350 | [Alertmanager分组抑制与静默/](./Alertmanager分组抑制与静默/) | matcher 语言、路由树与 `continue` 阻断、分组键、`inhibit_rules`、静默窗口、`group_wait`/`group_interval`/`repeat_interval`，以及 `for` 的 pending→firing 与 `keep_firing_for` | C / Python / Go |
| 351 | [Loki日志存储与LogQL/](./Loki日志存储与LogQL/) | 只索引标签 + chunk 滚动三条件（idle/size/age）、时序去重三规则、LogQL 两层相反的锚定语义、`__error__` 门禁、`unwrap` 消费标签、limits 均摊 | C / Python / Go |

051–053 构成「指标 + 追踪」主干：051 定义指标如何暴露 → 053 展示指标侧最有信息量的分位数查询 →
052 补齐跨服务请求关联。347–351 是第二批，把主干扩到**查询语义**（347）、**存储内核**（348）、
**采集与路由**（349/350）与**日志信号**（351），五者合起来才是「指标 + 追踪 + 日志」三支柱的完整地图。

## 待研究

- [ ] Grafana 可视化与告警规则（347–351 只覆盖了查询/存储/路由语义，不含可视化与 dashboard 建模）
- [x] OpenTelemetry Collector 管线（receivers/processors/exporters）→ demo 349
- [x] Prometheus TSDB 存储原理（head block / WAL / 2h block 压缩）→ demo 348
- [x] PromQL 一般语法与 range vector 语义 → demo 347
- [x] ELK / Loki 日志系统 → demo 351（Loki 部分；ELK 仍未覆盖）
- [ ] Exemplars（metrics ↔ traces 关联）
- [ ] 远程写入与联邦（remote_write / federation / OTLP 出口）
- [ ] 告警规则评估与分片（rule evaluation、`evaluation_interval`、rule sharding）
