# 演进式设计与遗留系统重构

> 「推倒重写」是业内失败率最高的方案之一。这一支研究**如何在系统不停机的前提下换掉它**——
> 增量、可回滚、每一步都能交付价值。

## 核心研究主题

- **Strangler Fig（绞杀者模式）**：新功能建在遗留系统之外但与其共存，逐步把行为迁移到新代码，
  遗留部分自然枯死。Fowler 强调四项活动（顺序不代表先后）：明确目标产出 / 拆分问题 /
  交付各部分 / 改变组织以支撑持续演进
- **seam（接缝）识别**：找到可插入的分割点，让系统能被拆开；「设计良好的系统里缝已经存在，
  但这样的系统是独角兽」
- **transitional architecture**：为新旧共存而写的临时结构，完成后会消失——看似浪费，
  但换来的是更低风险与更早收益
- **Branch by Abstraction**：先引入抽象层，再切换实现，最后删除旧实现；让大改动可以小步提交
- **特性开关**：开关的生命周期管理与开关债（长期不清理的开关 = 不可达代码 + 组合爆炸）
- **并行运行与对比验证**：影子流量 / diff testing / 双写回填；数据迁移的一致性校验
- **刻画测试（characterization test）**：为「不知道该有什么行为」的遗留代码固化现状，再动刀

## 待研究

- [ ] 重写 vs 绞杀的决策判据：什么条件下推倒重来反而合理（规模小、边界清、可停机）
- [ ] 从调用图/数据依赖自动找 seam 的启发式方法
- [ ] 开关债的量化与清理策略：开关老化、依赖开关的测试矩阵
- [ ] 双写回填的一致性校验：如何证明新旧数据源等价（抽样对比 vs 全量 checksum）
- [ ] 大批量重构的提交策略与回滚单位（配合 [04-代码可读性与重构技法](../04-代码可读性与重构技法/)）
- [ ] 组织因素：Conway 定律下，架构演进为何必须配套团队结构变化
- [ ] Feathers 三类 seam 的激活手法对比：预处理期 `#ifdef` 测试宏、链接期替换测试桩、对象期 Extract & Override/参数化构造注入——各自适用语言与无测试遗留代码的第一步操作
- [ ] Sprout Method 与 Wrap Method 两个最小侵入手法：新增逻辑外移为可单测的新方法/新类 vs 重命名旧方法后以原名包裹前后行为——侵入面、测试收益与「发芽堆积」代价对比
- [ ] expand-contract（parallel change）数据库迁移三阶段：扩展期新旧列并存、迁移期回填与读切换、收缩期删旧——与 pgroll 这类零停机迁移工具的自动化机制对比
- [ ] 事务性 outbox + CDC（尾随 binlog/WAL）替代应用层双写：同事务写 outbox 表、按聚合 ID 作消息键保序、at-least-once 下的幂等消费——与双写回填校验的适用边界
- [ ] GitHub Scientists 式并行实验库的机制设计：control/candidate 随机顺序执行限只读、compare/ignore 抑制已知差异、publish 上报 mismatch 与耗时、按百分比放量、测试中 raise_on_mismatches
- [ ] 特性开关按类型（release/experiment/ops/permission）的寿命与实现形态契约：静态 if/else vs 动态 Toggle Router、过期时间/定时炸弹/一进一出 WIP 上限等强制清理机制
- [ ] Mikado Method：朴素尝试→把编译/测试破坏点记为前置节点→revert→逐叶完成勾销，用涌现式依赖图（Mikado Graph）替代大爆炸重构计划的工作流
- [ ] 绞杀模式的切流实施层：API 网关/反向代理按路由逐步切流、facade 兼容旧契约、路由规则如何表达迁移进度与回滚单位（回切路由即回滚）
- [ ] Google 大规模变更（LSC）的工具与流程：Rosie 把全库变更拆成小 CL 分发给 owner 审查（全局审批人模式）、Kythe 语义搜索找目标、类型别名/转发函数防回潮、约 500 次编辑的拆分经验值与清理阶段

## 参考资料（已读）

- [Martin Fowler — StranglerFigApplication（渐进式现代化的四项活动；识别 seam；接受 transitional architecture；指出遗留系统僵化的根源在于产生它的设计思维与组织流程，需同步做组织变革）](https://martinfowler.com/bliki/StranglerFigApplication.html)
- [Feature Toggles (aka Feature Flags) — Pete Hodgson（四类型×寿命×静态/动态矩阵；Toggle Point/Router 术语；过期日期/定时炸弹/新增须删旧的 WIP 上限；Knight Capital 案例）](https://martinfowler.com/articles/feature-toggles.html)
- [Scientist — GitHub（control/candidate 恒返 control 值；两分支随机顺序执行故只限只读方法；compare/ignore 抑制已知差异；publish 上报 mismatch 与耗时；百分比放量与 raise_on_mismatches）](https://github.com/github/scientist)
- [Branch by Abstraction — Martin Fowler（三步法：建抽象→迁全部调用方→换实现再删抽象；Paul Hammant 为 trunk-based 提出；双实现并行校验变体）](https://martinfowler.com/bliki/BranchByAbstraction.html)
- [Parallel Change — Martin Fowler（expand/migrate/contract 三阶段在接口、数据库、部署（canary/蓝绿）、远程 API 四种场景的用法；跳过 contract 阶段会使代码库更糟）](https://martinfowler.com/bliki/ParallelChange.html)
- [Software Engineering at Google ch22 — Large-Scale Changes（Rosie 的分片/全局审批人工作流；CL 元数据聚合全局上下文；Kythe 语义搜索与 Tricorder 防回潮；约 500 次编辑拆分经验值）](https://abseil.io/resources/swe-book/html/ch22.html)
