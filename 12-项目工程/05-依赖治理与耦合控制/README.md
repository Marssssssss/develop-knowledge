# 依赖治理与耦合控制

> 大型项目失控的第一征兆通常不是「代码难读」，而是**依赖关系失控**：环、隐式依赖、底层反向依赖高层。
> 依赖是有方向的资产，必须能被看见、被度量、被约束。

## 核心研究主题

- **循环依赖**：为什么环是模块化杀手（编译单元、测试隔离、理解成本）；破除手段——
  提取公共模块、引入接口倒置、用事件/消息打断双向依赖、合并本就内聚的两个模块
- **包设计原则**：REP/CCP/CRP（内聚三原则的取舍张力）与 ADP/SDP/SAP（依赖三原则）；
  不稳定度 I = Ce / (Ce + Ca) 与「依赖方向应指向更稳定的模块」
- **稳定抽象原则**：稳定的东西必须是抽象的（接口/协议），否则会被全系统钉死
- **依赖倒置与依赖注入**：DIP 的机制（高层定义接口）与 DI 的成本（构造图复杂度、调试跳转型成本）；
  Service Locator 的问题（隐藏依赖、运行时才发现缺失）
- **构建层依赖治理**：模块/包可见性（Go `internal`、Java JPMS/ArchUnit、Python 约定 + 静态检查）、
  第三方依赖的版本锁定与供应链风险
- **架构守护**：把「不允许的依赖关系」写成**可执行的测试**，而不是写在 wiki 里

## 待研究

- [ ] 在 CI 中计算包级 Ce/Ca/I 并设阈值：阈值怎么定才不吵（增量门禁 vs 全量门禁）
- [ ] 循环依赖检测算法（Tarjan SCC）与「最小破除边集」的选择策略
- [ ] DI 容器 vs 手工构造：什么规模下容器才划算
- [ ] 依赖倒置的滥用：给每个类都配接口造成的接口爆炸与阅读成本
- [ ] 跨语言对比：Go/Java/Python 各自的可见性机制能强制到什么程度
- [ ] 依赖更新的成本模型：直接依赖 vs 传递依赖的升级爆炸半径
- [ ] Python 循环 import 的运行时实证：模块执行前先挂 `sys.modules` 导致「部分初始化」，成败取决于首个被 import 的模块——函数内延迟 import / `import module` 替代 from-import / `TYPE_CHECKING` 三种破法的代价与失效场景
- [ ] Go MVS vs Cargo/npm 解析器对比：最小版本选择为何无需 lockfile 也确定（「未测试的新版本不主动进构建」）；同一依赖图分别在 Go/Cargo 复现选版本差异与 2-CNF/Horn 公式保证的极小性
- [ ] TypeScript/ESM 循环依赖「编译能过、运行才炸」：用 madge/dpdm 检测传递环（A→B→C→A）并观察 V8 的 undefined 绑定/TDZ 报错形态；type-only import 造成的误报排除
- [ ] deptrac 与 import-linter 的契约模型对比：layers/independence/acyclic siblings/forbidden/protected 各自表达力边界，uncovered（未归层）与 skipped violations（豁免清单）两种机制如何防止规则腐烂
- [ ] Renovate/Dependabot 批量升级策略：分组 PR 降噪 vs 大批合并难 bisect 的权衡，配合 merge queue 的批量重置；「未使用依赖的自动升级占 CI 浪费约 74.5%」这类数据的统计口径核查
- [ ] SLSA 分级在 CI 的落点：L1 文档化 provenance → L2 托管构建+签名 provenance → L3 加固构建的递进门槛；CycloneDX/SPDX 双标准 SBOM 的生成与验证流程
- [ ] 发布-订阅的「隐式依赖」实证：事件总线把编译期耦合换成运行时与 schema 耦合——同一业务改动分别用直接调用与事件广播实现，对比影响面发现手段（IDE 引用查找 vs 全库 grep 事件名）与失效模式
- [ ] 隐藏依赖的显式化代价模型：单例/环境变量/系统时钟（全局可变单例）作隐式依赖——同一逻辑用全局取时 vs 注入 Clock 的测试可控行为对比，量化重构前后 mock 点数量
- [ ] 接口隔离原则（ISP）的可度量判据：以「客户端实际调用方法数占接口方法数比例」而非方法数阈值判定 fat interface——静态分析脚本检测各客户端使用不相交子集、空实现/抛异常桩两种信号
- [ ] semver 漂移实证：`^` 范围在不同时间 `npm install` 解析出不同依赖树，lockfile 如何恢复确定性、`npm ci` 的 manifest/lockfile 一致性校验为何该作 CI 默认——两次时间点安装做树 diff

## 参考资料（已读）

- [The Go Module Reference: Minimal Version Selection（MVS 遍历 require 图取各模块最高被需求版本得 build list；无 lockfile 也确定；replace/exclude 仅主模块生效）](https://go.dev/ref/mod)
- [Minimal Version Selection — Russ Cox（构造/升级/降级四算法；约束落在 2-CNF+Horn 类保证多项式极小解；与 Cargo「选最新+lockfile 补救」的保真度对比）](https://research.swtch.com/vgo-mvs)
- [The Import System — Python 官方文档（加载伪代码先 `sys.modules[name] = module` 再 `exec_module`；失败时仅删除失败模块的缓存项——循环 import 部分初始化的机制原文）](https://docs.python.org/3/reference/import.html)
- [Import Linter 官方文档（五种契约类型 forbidden/protected/layers/independence/acyclic siblings 的定义与 `.importlinter` 配置；`lint-imports` 以退出码接入 CI）](https://import-linter.readthedocs.io/en/stable/)
- [Deptrac 官方文档（layers+ruleset 配置模型（collector 匹配 FQCN）；violations/skipped/uncovered/allowed 四类报告输出与 `deptrac analyse` 的 CI 非零退出）](https://deptrac.github.io/deptrac/)
