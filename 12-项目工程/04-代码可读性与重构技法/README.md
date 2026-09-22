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

## 参考资料（已读）

- [Google Engineering Practices — How to do a code review（八项评审维度：设计/功能/复杂度/测试/命名/注释/风格/文档；标准 = 明确提升整体代码健康度即可 LGTM 带小注释；偏好 Small CLs，过大应拆分）](https://google.github.io/eng-practices/review/reviewer/)
