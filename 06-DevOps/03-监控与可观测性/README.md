# 监控与可观测性

## 子领域与已完成 demo

| demo | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 051 | [Prometheus指标模型/](./Prometheus指标模型/) | 数据模型 + exposition 文本格式 0.0.4 解析（HELP/TYPE、转义、histogram 展开约定） | C / Python / Go |
| 052 | [分布式追踪与TraceContext/](./分布式追踪与TraceContext/) | W3C Trace Context：traceparent 校验/逐跳传播/Span 树还原 | C / Python / Go |
| 053 | [直方图分位数估算/](./直方图分位数估算/) | histogram_quantile 线性插值 + 边界规则 + 桶宽误差分析 | C / Python / Go |

三件套构成可观测性"指标 + 追踪"主干：051 定义指标如何暴露 → 053 展示指标侧最有信息量的分位数查询 → 052 补齐跨服务请求关联。

## 待研究

- [ ] Grafana 可视化与告警规则
- [ ] OpenTelemetry Collector 管线（receivers/processors/exporters）
- [ ] Prometheus TSDB 存储原理（head block / WAL / 2h block 压缩）
- [ ] PromQL 一般语法与 range vector 语义
- [ ] ELK / Loki 日志系统
- [ ] Exemplars（metrics ↔ traces 关联）
