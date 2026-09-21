# 游戏 AI（非数据驱动）

传统游戏 AI 是**规则驱动**的决策系统，与 ML 路线不同。本目录下每个 demo 都**先读权威源码/原文再落笔**，并把「官方实现与数学直觉不一致」的地方原样记录。

## 经典模型

| 模型 | 特点 | 本目录状态 |
| --- | --- | --- |
| 状态机（FSM） | 简单、确定性强 | 层级版已做（SCXML 语义） |
| 行为树（BT） | 模块化、易调试 | 已做（BehaviorTree.CPP 执行语义） |
| 决策树 | 数据驱动版 BT | 待研究 |
| 效用系统（Utility AI） | 基于评分的灵活选择 | 已做（big-brain 评分与选择） |
| GOAP | 目标导向行动规划 | 已做（ReGoap 反向 A\*） |

## 子领域

- [行为树/](./行为树/) — 已含 demo：[执行语义](./行为树/执行语义/)
- [状态机/](./状态机/) — 已含 demo：[层级状态机 HFSM](./状态机/层级状态机HFSM/)
- [效用系统/](./效用系统/)
- [GOAP规划/](./GOAP规划/)
- [寻路算法/](./寻路算法/) — 已含 demo：[A-star](./寻路算法/A-star/)、[跳点搜索 JPS](./寻路算法/跳点搜索JPS/)

## 已完成 demo

| # | 目录 | 一句话机制 | 语言 |
| --- | --- | --- | --- |
| 1 | [行为树/执行语义/](./行为树/执行语义/) | Sequence/Fallback 的 `RUNNING` 记忆与 Reactive 版「每 tick 重新评估」的差异；Parallel 默认 `failure_threshold_=1` 使三子节点组在第一个孩子失败后立刻判负、后两个孩子**一次都不会被 tick** | Python(54) / Go |
| 2 | [GOAP规划/](./GOAP规划/) | 从目标**反向**展开动作做 A\*（不是正向搜状态空间）；`Expand` 用的是**父节点的** `Goal`，`ReGoapPlannerSettings` 默认 `PlanningEarlyExit=false` 故拿到的是代价 3 的 `GetAxe→ChopWood` 而非早期退出下代价 5 的 `BuyWood` | Python(29) / Go |
| 3 | [效用系统/](./效用系统/) | big-brain 的 `Scorer→Measure→Picker` 三段；`SigmoidEvaluator(k=-0.5)` 默认参数下**不是 S 形**（f(0)=1、f(0.25)=0、f(0.5)=0.5、f(0.75)=0.875），`WeightedProduct` 因折叠初值为 0 恒等于 0 | Python(58) / Go |
| 4 | [寻路算法/跳点搜索JPS/](./寻路算法/跳点搜索JPS/) | 邻域剪枝规则（式 1/2）+ `jump` 递归跳跃，只把 jump point 入堆；**路径代价与 A\* 完全相同**（保最优），但展开节点数在开阔地图上从 14 降到 1、30×30 从 29 降到 1 | Python(30) / Go |
| 5 | [状态机/层级状态机HFSM/](./状态机/层级状态机HFSM/) | SCXML Appendix D 的 microstep 算法：配置集（configuration）不含 `<scxml>` 容器、`isDescendant` **排除相等**、历史值必须在**退出之前**统一记录（与退出合并写会得到空历史）、内部转移与外部转移的 transition domain 不同 | Python(39) / Go |

## 关键实现要点（跨 demo 共性）

- **响应式 vs 记忆式**：BT 的 Reactive 序列每个 tick 从头重跑，普通序列靠 `RUNNING` 索引停在原地；这两者是「每帧重决策」与「承诺直到完成」的分水岭。
- **状态去重口径**：GOAP 的重复状态检测用 `break` 而非 `continue`，入队数随动作声明顺序变化（1 或 3）。
- **语言差异必须显式落地**：Rust 的 `f32` 除法不抛异常（Python 侧用 `_fdiv` 显式模拟 IEEE 语义）、C# 的 `ConcurrentDictionary` 无序遍历（Python 侧用排序键字符串）、`OrderedSet` 用保序 list 代替。
- **无工具链时的验证路径**：本机无 `go`/`gcc`/`clang`/`rustc`，Go 侧一律走人工审查 + `bracket_check.py` + `go_sanity.py --spec check=2` + `go_crossref.py`。

## 待研究

- [x] BehaviorTree 完整 demo — 见 [行为树/执行语义/](./行为树/执行语义/)
- [x] Utility AI 评分系统 — 见 [效用系统/](./效用系统/)
- [ ] FSM 与 BT 混合架构
- [ ] GOAP 与 HTN 规划器的对比
- [ ] NavMesh 与 Detour 寻路（`dtNavMeshQuery` 的 A\* 与漏斗算法 straight path）
- [ ] 群体行为（Boids / 流场 / 编队）
- [ ] 感知系统（视锥、听觉、黑板与事件传播）
- [ ] 行为树的子树与端口重映射（SubTreeNode / Blackboard remapping）
- [ ] 效用系统的「惯性」与切换滞回（避免抖动）

## 参考资料

本轮实际读过并逐行对照的原文/源码：

- [BehaviorTree.CPP](https://github.com/BehaviorTree/BehaviorTree.CPP)（master）— `basic_types.h`、`controls/sequence_node.h`、`fallback_node.h`、`reactive_sequence.h`、`parallel_node.h`、`decorators/inverter_node.h`、`repeat_node.h` 及对应 `.cpp`；`src/control_node.cpp`、`src/tree_node.cpp`
- [luxkun/ReGoap](https://github.com/luxkun/ReGoap)（master）— `ReGoap/Planner/ReGoapNode.cs`、`AStar.cs`、`ReGoapPlanner.cs`、`ReGoapPlannerSettings`、`Core/ReGoapState.cs`、`Core/ReGoapCondition.cs`
- [zkat/big-brain](https://github.com/zkat/big-brain)（main）— `src/scorers.rs`、`src/measures.rs`、`src/evaluators.rs`、`src/pickers.rs`、`src/choices.rs`、`src/thinker.rs`
- Harabor & Grastien, *Online Graph Pruning for Pathfinding on Grid Maps*, AAAI 2011 — <https://users.cecs.anu.edu.au/~dharabor/data/papers/harabor-grastien-aaai11.pdf>（§Neighbour Pruning Rules 式 (1)(2)、Definition 1–3、Algorithm 1/2、Theorem 1）
- W3C, *State Chart XML (SCXML)*, W3C Recommendation — <https://www.w3.org/TR/scxml/>（Appendix D「Algorithm for SCXML Interpretation」全文）
