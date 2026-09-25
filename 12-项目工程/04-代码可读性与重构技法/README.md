# 代码可读性与重构技法

> 可读性不是审美问题，是**维护成本问题**：代码被读的次数远多于被写的次数。
> 重构的前提是「行为不变」——所以重构技法必须与安全网（测试 + 小步 + 自动化工具）绑在一起讲。

## 核心研究主题

- **命名与概念一致性**：一个概念一个词；名字承载的是意图与契约，不是实现细节
- **函数与抽象层次**：单一抽象层次原则（SLAP）、函数长度只是表象、
  「做了几件事」的正确判据是能否抽出有意义的子函数名
- **注释**：写 why 不写 what；解释不直观的取舍、历史约束、为什么不能那样做
- **坏味道识别**：Long Method / Large Class / Feature Envy / Data Clumps / Primitive Obsession /
  Shotgun Surgery / Divergent Change —— 味道指向的不是「丑」，而是「下次改它会很痛」
- **重构手法**：Extract Method/Variable、Inline、Move、Rename、Replace Conditional with Polymorphism、
  Introduce Parameter Object 等；小步且每步可提交
- **重构的安全性**：先有测试再重构、IDE 自动重构 vs 手工改的边界、重构与功能改动**不要混在一个提交**
- **评审标准**：评审目的是让代码库健康度**持续改善**而非追求完美；小 CL 优先（Google eng-practices 实读）

## 待研究

- [ ] 「明确提升整体代码健康度」的 LGTM 判据如何落到团队可执行的 checklist
- [ ] 大 CL 的拆分策略：按重构/功能/格式分层提交的可操作方法
- [ ] 坏味道 → 重构手法的映射表（每个味道对应哪条手法，而非笼统「重构一下」）
- [ ] 参数对象 vs 长参数列表的取舍点；何时该引入类型而非字典/元组
- [ ] 条件复杂度：卫语句、表驱动、多态三种改法各自的适用条件
- [ ] 认知负荷度量：一段代码需要同时记住几件事
- [ ] DRY vs AHA 的抽象时机判据：Sandi Metz「重复远比错误的抽象便宜」+ Kent C. Dodds 的 AHA——同一逻辑第 3 次重复且模式稳定时才提取；两次模拟需求变更对比「提前抽象 vs 先重复后抽象」的总改动成本
- [ ] Sandi Metz 四规则（类≤100 行/方法≤5 行/参数≤4 个，出自 Ruby Rogues 播客 ep87）与「方法可以不止 5 行」的反方观点：过度拆分增加跳转次数——同一段代码两版实测跳转次数与理解时间
- [ ] 评审规模的缺陷检出实证：SmartBear/Cisco 研究的 200–400 行/次、单次 60–90 分钟甜点，超过 400 行每行检出率骤降——用本仓库 PR 历史按行数分桶统计评审时长与逃逸缺陷率
- [ ] Extract Method 的失败模式：被提取块给多个后续使用的临时变量赋值、靠副作用产出多值、参数爆炸（IDE 官方明言多输出值不支持自动提取）——收集 10 个真实失败样例给出 Replace Temp with Query/返回 record/调整切分边界的手工路径
- [ ] 布尔标志参数与控制标志的机械化修法：Remove Flag Argument（拆成两个具名函数）与 Replace Control Flag with Break，判据是调用点出现读不出行为的 true/false
- [ ] 标识符命名实证：全词命名比字母缩写平均快约 19% 理解（Herka et al.），但 URL 等高频缩写与全词常无显著差异（Feild et al.）——「全词/缩写 × 有无上下文」2×2 受控实验测理解时间
- [ ] God class 拆分顺序：先对字段做内聚聚类用 Extract Class 切出数据组，再用 Extract Subclass/Interface 把方法移到数据所在处——对同一 god class 分别按「先数据/先方法」拆分并对比内聚耦合度量变化
- [ ] 死代码的安全删除流程：语言级静态检测 + 覆盖率/生产遥测交叉验证「真的没人调」+ 单独可回滚提交（社区警告：被误闲置的代码可能是潜伏 bug 信号）
- [ ] 嵌套深度与理解时间实证：减少嵌套显著缩短阅读理解时间并提高开发者信心——同算法嵌套 2 层 vs 4 层两版本计时小实验，验证差值是否可复现
- [ ] 「坏味道只是表层信号」的团队训练法：Fowler 强调味道未必是真问题（「有些长方法没问题」），「smell of the week」每周只聚焦搜一种坏味道——4 周实验统计误报率与确改率

## 参考资料（已读）

- [Google Engineering Practices — How to do a code review（八项评审维度：设计/功能/复杂度/测试/命名/注释/风格/文档；标准 = 明确提升整体代码健康度即可 LGTM 带小注释；偏好 Small CLs，过大应拆分）](https://google.github.io/eng-practices/review/reviewer/)
- [Refactoring.com Catalog（手法准确名称与别名：Remove Flag Argument、Replace Control Flag with Break、Replace Temp with Query、Split Variable、Remove Dead Code 等）](https://refactoring.com/catalog/)
- [Code Smells — Refactoring.Guru（坏味道五分类：Bloaters/Object-Orientation Abusers/Change Preventers/Dispensables/Couplers；Dispensables 含 Comments、Dead Code、Speculative Generality）](https://refactoring.guru/refactoring/smells)
- [AHA Programming — Kent C. Dodds（AHA 词源借自 Cher Scarlett；对 Metz「宁要重复不要错误抽象」的引用；rule of three/WET 与「为变化而优化」的抽象时机判据）](https://kentcdodds.com/blog/aha-programming)
- [CodeSmell — Martin Fowler（坏味道=深层问题的表层信号；Kent Beck 造词；「有些长方法没问题」的保留意见；smell of the week 团队教学练习）](https://martinfowler.com/bliki/CodeSmell.html)
