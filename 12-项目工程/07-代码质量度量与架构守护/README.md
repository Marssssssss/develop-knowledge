# 代码质量度量与架构守护

> 度量本身不改善代码，**被执行到的约束才改善代码**。
> 这一支研究两件事：指标到底在测什么（口径），以及怎么让它变成 CI 里过不去的门。

## 核心研究主题

- **复杂度度量**：圈复杂度（线性独立路径数）vs 认知复杂度（Sonar 定义：对嵌套加权、对线性顺序不罚）；
  二者的分歧点正是「一堆平铺的 if」算不算复杂
- **规模与重复**：LOC/函数长度/参数个数只是代理指标；重复率（token/行级）与复制粘贴的治理
- **耦合与内聚度量**：包级 Ce/Ca、不稳定度、LCOM（类内聚缺失）——配合 [05-依赖治理](../05-依赖治理与耦合控制/)
- **覆盖率口径**：行覆盖 / 分支覆盖 / 条件覆盖 / 突变得分；为什么行覆盖 90% 可能仍漏掉大量行为
- **静态分析层次**：风格 lint → 语义缺陷（空指针、资源泄漏）→ 数据流/污点分析 → 架构规则
- **架构适应度函数（fitness function）**：把架构约束写成随每次提交自动求值的函数；
  全量门禁 vs **增量门禁**（只对新代码/新依赖生效，避免历史债堵塞流水线）
- **度量的滥用**：指标一旦成为 KPI 就会被优化（拆分函数降圈复杂度、写无断言测试提覆盖率）

## 待研究

- [ ] 圈复杂度与认知复杂度在真实代码上的具体分歧（构造对照样例对比）
- [ ] 增量门禁的落地：如何用 diff 计算「本次新增的违规」并只拦新增
- [ ] 适应度函数的粒度选择：函数级（快）vs 系统级（慢但准）与执行频率
- [ ] 技术债量化（如 SQALE）的可信度：还款工作量估算从哪来
- [ ] 静态分析的误报与「告警疲劳」：抑制机制的治理（不要全局关闭规则）
- [ ] 架构规则表达力对比：ArchUnit（Java 字节码级）这类库能表达什么、表达不了什么
- [ ] 认知复杂度计算器的规则级复刻：按白皮书附录 B 的增量/结构/嵌套加权三清单实现 AST 计分器（else if/else 只 +1 不受嵌套加权、同类二元逻辑算子序列才 +1、递归环 +1），用附录 C 六个例题（sumOfPrimes=7 等）做 golden test 并与 SonarQube 对拍
- [ ] LCOM 三种口径的分歧实证：CK 1994 原始定义（不共享字段访问的方法对数 − 共享的对数）vs Henderson-Sellers 归一化 (m−a/M)/(m−1) vs LCOM4 连通分量——构造「明显该拆但 CK 版算出低值」的反例比较检出差异
- [ ] Maintainability Index 公式拆解：VS 版 `MAX(0,(171−5.2·ln(Halstead)−0.23·CC−16.2·ln(LOC)))×100/171` 与 SEI 原始负值域的 remap 差异——量化三项贡献占比，检验 20/10 阈值实际把代码切在哪
- [ ] 相对代码 churn 与缺陷密度的相关性复现（Nagappan & Ball ICSE 2005，Microsoft）：churned LOC/总 LOC 的相对 churn 比绝对 churn 更能预测缺陷密度——用 git log 按文件算相对 churn 对照后续 bugfix 提交密度
- [ ] 重复检测算法对比：jscpd 用 Rabin–Karp 滚动哈希在 token 流上找克隆（`--ignore-identifiers` 归 Type-2、`--similarity 0.85` 走语法树级 Type-3）——与 PMD CPD 的 token 阈值法互查漏检/误报
- [ ] 「已执行≠已验证」对照实验：零断言/弱断言测试把行覆盖率刷到 100%，再用 mutmut（整数 +1、<→<=、break↔continue）测突变得分崩塌幅度——识别覆盖率门禁 gaming 的量化判据
- [ ] ruff 规则分级与默认集选择：默认 `["E4","E7","E9","F"]` vs v0.16 起扩到 7 倍默认规则且每条带 correctness/refactor/performance 标签——分档运行按标签统计新增告警的信噪比，产出团队默认集决策依据
- [ ] Danger.js 把人肉评审惯例代码化：Dangerfile 读 PR 元数据输出 message/warn/fail/markdown 四级动作——「lockfile 必须同步」「TODO 残留」规则，划清流程惯例与 linter 管代码本身的边界
- [ ] 突变测试提速机制实证：mutmut 按 max_stack_depth 只跑直接目标测试、按变更函数增量缓存重测、超时判据=原测试时长×倍率杀挂起变异体——全量跑与选择跑的得分一致性和耗时比
- [ ] 度量阈值从哪来：SATT 类研究在数千开源项目上算度量分布、按分位数定阈值而非拍脑袋——对自有仓库统计函数长度与复杂度分布（P50/P90/P99），对照 Sonar/ruff 默认阈值落在哪个分位

## 参考资料（已读）

- [ArchUnit 官网（以普通单元测试形式检查包/类依赖、分层与切片、循环依赖；基于字节码导入类结构；有 .NET 移植版）](https://www.archunit.org/)
- [Cognitive Complexity 白皮书 — SonarSource（3 条基本规则+Nesting/Structural/Fundamental/Hybrid 四类增量；循环与 catch 受嵌套加权而 switch 整体只 +1；递归环每方法 +1；附录含逐行标注分数的例题）](https://www.sonarsource.com/docs/CognitiveComplexity.pdf)
- [Code Metrics — Maintainability Index（Microsoft Learn；精确公式与 171 基线、负值截 0 后重定基；0-9 红/10-19 黄/20-100 绿阈值表）](https://learn.microsoft.com/en-us/visualstudio/code-quality/code-metrics-maintainability-index-range-and-meaning?view=vs-2022)
- [mutmut 官方文档（变异算子实例 0→1、<→<=、break↔continue；killed/survived；max_stack_depth 测试选择；超时=原时长×倍数；按变更函数增量缓存）](https://mutmut.readthedocs.io/en/latest/)
- [jscpd（Rabin–Karp 滚动哈希按各语言 token 规则分词；`--ignore-identifiers`/`--ignore-literals` 归 Type-2；`--similarity` 走 Type-3；`--baseline` + fail-on-new-clones 增量门禁）](https://github.com/kucherenko/jscpd)
- [Danger JS（「把团队规范代码化、把日常评审杂务自动化」；Dangerfile 机制与 message/warn/fail/markdown 四个反馈函数；约 60 个插件）](https://danger.systems/js/)
- [ckjm Metric Descriptions — Spinellis（LCOM 精确定义：不共享字段访问的方法对数减共享的方法对数，沿用 CK 1994；WMC/DIT/NOC/CBO/RFC/LCOM/Ca/NPM 全清单）](https://www.spinellis.gr/sw/ckjm/doc/metric.html)
