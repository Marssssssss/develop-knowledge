# 可测试性设计

> 可测试性不是「测试写得多不多」，而是**这段代码能被多便宜地测**。
> 它是设计属性：如果测一个函数要先起数据库、等时钟、打三个桩，问题在设计而不在测试。

## 核心研究主题

- **接缝（seam）**：可替换点在哪里——参数、接口、虚拟方法、函数指针、模块边界；
  设计阶段留缝，测试才不必动手术
- **可控性与确定性**：注入时钟、随机数、ID 生成；把「现在/随机/外部 IO」变成显式输入；
  避免依赖全局状态与静态单例
- **测试替身光谱**：dummy / stub / spy / mock / fake 的语义差别；
  mock 滥用（测实现细节而非行为）导致「重构即红」的脆弱测试
- **测试分层与代价**：单元/集成/端到端的金字塔——越往上越真但越慢越脆，
  关键是把断言放在能稳定复现的层
- **突变测试**：用「故意注入的 bug 是否被测试杀死」来衡量测试的有效性，而非看覆盖率数字
- **契约测试**：跨服务/跨模块边界用契约（消费者驱动）替代全链路集成
- **Flaky 测试归因**：时间、并发顺序、共享状态、外部依赖、随机数据；flaky 会摧毁团队对测试信号的信任

## 待研究

- [ ] mock vs fake 的选择判据：什么情况下该写一个真的内存实现而不是打桩
- [ ] 「测行为不测实现」的可操作表述：断言应该是 observable effect 而非调用序列
- [ ] 突变测试的成本控制：只对变更文件跑、突变算子选择
- [ ] 契约测试的边界：消费者驱动契约（CDC）在多版本共存时的矩阵爆炸
- [ ] 测试隔离与并行：共享 fixture 与全局状态导致的顺序依赖
- [ ] 测试金字塔的反例：为何有些系统「集成测试反而更划算」（逻辑薄、IO 厚）
- [ ] 测试奖杯 vs 金字塔：金字塔只权衡速度/成本，奖杯加入置信度轴与静态检查层（「测试越像软件被真实使用的方式越可信」）——同一组件分别写单测与只 mock 网络层的集成测试，实测 bug 拦截差异
- [ ] 快照测试的失效模式：快照超过几十行就没人真的 review、失败被「删了重录」——实测快照大小、序列化归一化与 snapshot-diff 对文本 diff 噪音的抑制
- [ ] testcontainers 式一次性真依赖 vs 内存数据库 fake：SQL 方言、约束、事务回滚语义只有真引擎暴露——容器按套件复用/按测试重建两种粒度的启动成本与状态保证
- [ ] 行覆盖率与突变覆盖率的背离：行覆盖只证明代码被执行、突变覆盖才证明断言有效——同一套件跑 PIT 双色报告实测两指标差距，识别「高覆盖低防御」测试
- [ ] Go `-race` 竞态检测的能力边界：只抓运行时真正执行到的并发交错、无假阳性但对未执行路径零保证——5-10x 内存/2-20x CPU 成本下与压测、race 构建灰度的配合取舍
- [ ] flaky 测试的重试掩盖论与隔离流程：auto-retry 在什么条件下把真实回归洗成绿色、quarantine 不绑限期工单就沦为垃圾场——从统计检测、隔离到修复删除的量化流水线
- [ ] property-based testing 的收缩（shrink）机制：失败样例自动缩成最小反例（如 `ls=[0, 0]`）如何改变调试入口——随机生成不可复现问题 vs 样本数据库回放的回归稳定性
- [ ] 测试数据构造 builder vs object mother 判据：命名工厂方法随场景增长把测试耦死——「默认值 + 显式覆盖」builder 在实体加字段时的维护成本与两者混合式用法
- [ ] 动态语言 monkey patch 打桩 vs 接口注入的接缝成本差：`unittest.mock.patch` 耦合 import 路径且有全局副作用、补内部私有方法是设计坏味道——同一依赖在 Python 与 Go/Java 下的替身实现对比

## 参考资料（已读）

- [TestDouble — Martin Fowler（Meszaros 五类替身原始定义：dummy「只传不用」、fake「有真实现但走捷径」、spy「记录调用」、mock「预编程期望并在验证时检查」；state/behavior 验证之分）](https://martinfowler.com/bliki/TestDouble.html)
- [Static vs Unit vs Integration vs E2E — Kent C. Dodds（测试奖杯四层含静态层；「测试越像使用方式越可信」的置信度论证；他只 mock 网络请求与动画组件）](https://kentcdodds.com/blog/static-vs-unit-vs-integration-vs-e2e-tests)
- [Effective Snapshot Testing — Kent C. Dodds（640 行快照无人 review、团队只会「删了重录」的失效案例；snapshot-diff 只序列化状态间差异；自定义序列化器消除平台噪音）](https://kentcdodds.com/blog/effective-snapshot-testing)
- [Stryker Mutator（30+ 突变算子可选用；静态分析+并行测试进程提速；报告定位存活突变体）](https://stryker-mutator.io/)
- [PIT — pitest.org（「传统覆盖只度量哪些代码被执行，不检查测试能否发现故障」；浅绿行覆盖/深绿突变覆盖双色报告；最有效用法是只对变更代码高频运行）](https://pitest.org/)
- [Testcontainers（「Unit tests with real dependencies」；一次性轻量容器按已知状态启动；50+ 模块；数据访问层兼容性测试）](https://testcontainers.com/)
- [Data Race Detector — go.dev（基于 C++/LLVM ThreadSanitizer；内存 5-10x/CPU 2-20x 开销；「只找运行时实际发生的竞争，未执行路径找不到」；`GORACE` 与 race 构建标签）](https://go.dev/doc/articles/race_detector)
- [Hypothesis — PyPI（「不只报告任意失败用例，而是报告最简单的那个」的收缩机制；`ls=[0, 0]` 最小反例示例）](https://pypi.org/project/hypothesis/)
