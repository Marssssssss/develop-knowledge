# SRE 与可靠性工程

> 2026-09-12 巡检类目自动拓展新增（06-DevOps 维度补全：监控与可观测性关注"数据采集与
> 展示"，本目录关注"用指标驱动可靠性决策"的工程实践）。

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

## 待研究知识点

- [ ] SLI 的选择与标准化（窗口/聚合方式/客户端 vs 服务端测量）
- [ ] SLO 目标设定（不要基于当前性能拍脑袋；从用户在乎什么倒推）
- [ ] 错误预算策略（Error Budget Policy）：预算消耗分级动作（50% 通知 → 75% 冻结 →
  100% 只允许 P0/安全修复）
- [ ] burn rate 告警：多窗口燃烧率（1h/6h）替代静态阈值告警
- [ ] 事后复盘（Postmortem）文化与 blameless 原则
- [ ] Toil（琐事）识别与自动化：手动/重复/无持久价值的工作要系统性消除
- [ ] 混沌工程与故障注入（对照 08-安全 与 11-性能分析 的故障模拟）
- [ ] 容量规划与过载应对（仓促应对 vs 有序降级）

## 参考资料（实际阅读过的权威来源）

- [Service Level Objectives — Google SRE Book Ch.4](https://sre.google/sre-book/service-level-objectives/) — SLI/SLO/SLA 官方定义、目标选择原则、错误预算动机
- [Implementing SLOs — Google SRE Workbook Ch.2](https://sre.google/workbook/implementing-slos/) — 为什么 100% 是错误目标、每个九成本递增、落地步骤与错误预算政策
