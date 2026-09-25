# 架构模式与模块边界

> 架构的实质是**切边界**：哪部分可以独立变化、独立替换、独立理解。
> 边界切对了，改需求是局部修改；切错了，任何小改动都要动全身。

## 核心研究主题

- **分层架构**：依赖方向必须单向；「跳过一层」的取舍与防止退化成面条式调用
- **六边形 / 端口与适配器**：把框架与基础设施推到外侧，领域逻辑不依赖任何外部技术；
  依赖倒置是它的机制核心
- **DDD 战略设计**：限界上下文、上下文映射、防腐层（ACL）、共享内核；语言边界即模块边界
- **DDD 战术设计**：聚合根与一致性边界、值对象、领域事件；聚合不要按数据库表来划
- **模块化单体 vs 微服务**：先有模块边界，才有服务边界；提前分布式只会把编译期错误变成运行时故障
- **C4 模型**：Context / Container / Component / Code 四层视图，解决「架构图谁都看不懂」的问题
- **模块质量度量**：内聚（模块内部关联强度）与耦合（模块间依赖强度）的可计算定义

## 待研究

- [ ] 限界上下文的识别方法：从业务事件/用例/团队边界反推，而不是先画服务图
- [ ] 聚合根边界的判据：一致性要求 vs 并发吞吐的权衡（大聚合事务安全但串行）
- [ ] 防腐层的实现形态与成本：翻译层放哪一侧、谁来维护
- [ ] 模块化单体的 enforcement：编译期可见性（Java JPMS / Go internal / Python 约定）各能强到什么程度
- [ ] 分层架构的依赖检查如何自动化（配合 [05-依赖治理](../05-依赖治理与耦合控制/) 与 [07-代码质量度量](../07-代码质量度量与架构守护/)）
- [ ] 「先分布式后模块化」的失败模式：分布式单体（distributed monolith）的成因
- [ ] 垂直切片架构的耦合判据：「切片间最小耦合、切片内最大耦合」（耦合轴对齐变更轴），按请求/用例纵切让 CQRS 自然出现；代价是放弃 Repository/Service 等全局抽象、且以团队具备坏味道识别力为前提
- [ ] CQRS 的适用判据（Fowler）：只有「少数复杂域更易建模」与「读写规模不对称需独立伸缩」两类有收益；只能按限界上下文局部使用，对多数系统是 risky complexity
- [ ] Saga 的两种编排形态：协同式（服务间互发领域事件）vs 编排式（中央协调器/Process Manager 下发命令）；无自动回滚时补偿事务如何设计、缺隔离性靠 semantic lock 等对策补救
- [ ] Transactional Outbox：业务更新与事件写入同一本地事务的 outbox 表，由 relay（polling publisher 或 CDC 日志尾随）投递——跨库跨 MQ 的 2PC 为何不可行、消费端为何必须幂等且保序
- [ ] 数据所有权与 Database per Service：私有表/schema/独立库三档隔离，用独立 DB 账号+授权挡「绕过 API 直查」——编译期强制之外的数据层强制；Shared Database 为何被标为反模式
- [ ] Monolith First 的四条拆分路径：精心模块化的单体 / 从边缘剥离服务留静默核心 / 牺牲式架构 / 粗粒度 duolith；配合 MicroservicePremium 判断何时才值得付微服务的固定成本
- [ ] 服务粒度的判据：按业务能力/子域而非技术层或数据表拆分，用 SRP + Common Closure Principle（一起变的放一起）定归属，以「一次业务变更只应改一个服务」作验证
- [ ] 事件溯源的收益与代价：事件日志作 source of truth 换来完整重建/时间点查询/纠错重放；外部系统分不清重放与真实处理（需网关抑制）、读副本因事件传播时差失步
- [ ] C4 的文档即代码：Structurizr DSL 一个 model 派生 systemContext/container/component/dynamic/deployment 多视图，DSL 文本进版本库并可导出 PlantUML/Mermaid，替代易失同步的手画图
- [ ] Hexagonal/Onion/Clean 三家依赖规则同异：共同点是依赖只准向内指向领域；差异在内部结构——Hexagonal 只定内外不定内层、Onion 同心圈纳入 DDD 概念、Clean 把用例（interactor）提为一等公民

## 参考资料（已读）

- [Monolith First — Martin Fowler（成功微服务故事几乎都始于被拆大的单体、老手也难一开始画对边界；四种拆分策略与 MicroservicePremium）](https://martinfowler.com/bliki/MonolithFirst.html)
- [CQRS — Martin Fowler（读写用不同模型；仅两类场景有收益；只应在特定限界上下文用；多数系统引入 risky complexity）](https://martinfowler.com/bliki/CQRS.html)
- [Event Sourcing — Martin Fowler（事件日志可完整重建/时间查询/纠错重放；外部系统在重放时需网关抑制消息；读端因事件传播时差与主库失步）](https://martinfowler.com/eaaDev/EventSourcing.html)
- [Saga — microservices.io（saga=本地事务序列；choreography 与 orchestration 两式；无自动回滚需补偿事务；无隔离性需 semantic lock 等对策）](https://microservices.io/patterns/data/saga.html)
- [Transactional Outbox — microservices.io（outbox 表随业务事务写入；2PC 不可行的理由；polling publisher / 事务日志尾随两种 relay；消费端幂等与发布顺序保证）](https://microservices.io/patterns/data/transactional-outbox.html)
- [Database per Service — microservices.io（private-tables/schema/db-server 三档实现；建议独立 DB 账号+grants 强制封装；Shared Database 被明确描述为 anti-pattern）](https://microservices.io/patterns/data/database-per-service.html)
- [Decompose by Business Capability — microservices.io（SRP 与 Common Closure Principle 作为归属判据；变更理想上只影响一个服务以避免跨团队协调）](https://microservices.io/patterns/decomposition/decompose-by-business-capability.html)
- [Vertical Slice Architecture — Jimmy Bogard（按请求纵切；slice 间最小耦合、slice 内最大耦合；天然得到 CQRS；以团队会识别坏味道为前提）](https://www.jimmybogard.com/vertical-slice-architecture/)
- [Modular Monolith and "Package by Component" — Simon Brown（全 public 时四种组织风格语法等价：组织≠封装；「用编译器强制架构」优于事后静态检查）](https://simonbrown.je/modular-monolith)
- [Structurizr DSL（workspace = model + views 的模型/视图分离；视图类型清单；Lite 与 CLI 工具链、可导出 PlantUML/Mermaid）](https://structurizr.com/dsl)
