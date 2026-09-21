# GOAP：目标导向行动规划的反向 A\*

> 新子领域 `01-游戏开发/06-AI/GOAP规划`。GOAP（Goal-Oriented Action Planning）与行为树的差别：行为树是**人写死的决策流**，GOAP 是**用 A\* 在世界状态空间里搜出一条动作序列**。本 demo 把 `luxkun/ReGoap` 的规划器逐行转写成 Python 与 Go，重点回答三个问题：**搜索为什么是反向的**、**启发值 h 到底是什么**、**`PlanningEarlyExit` 打开后计划为什么可能变贵**。

## 简介

ReGoap 的搜索是**从目标往回走**的：每个节点持有一份「还剩哪些目标条件没满足」的 `Goal`，选一个**效果能消掉其中至少一个条件**的动作，把该条件从 `Goal` 里删掉、再把动作的**前提**加进 `Goal`，直到 `Goal` 里的条件全部被当前世界状态满足。

| 量 | 官方定义 | 位置 |
| --- | --- | --- |
| `g(node)` | 父节点路径成本 + 动作代价 | `ReGoapNode.Init` |
| `h(node)` | `Goal.Count`，**剩余目标条件的个数** | `ReGoapNode.Init` |
| `f(node)` | `g + h * heuristicMultiplier`（默认 multiplier = 1） | `ReGoapNode.Init` |
| 终点判定 | `goalMergedWithWorld.Count <= 0` | `ReGoapNode.IsGoal` |

`goalMergedWithWorld = Goal.MissingDifference(worldState)` —— 注意它比的是**世界状态**而不是节点自己的 `state`，所以「计划走完了但世界状态仍然不满足」的节点不会被当成终点。

## 原理详解

### 1. 节点的构造：一次 Init 里做完三件事

```csharp
state.AddFromState(Effects);                    // ① 把效果并进节点状态
Goal.ReplaceWithMissingDifference(Effects);     // ② 效果已满足的条件从 Goal 里删掉
Goal.AddFromState(Preconditions);               // ③ 前提变成新的待满足条件
h = Goal.Count;
cost = g + h * heuristicMultiplier;
```

第 ③ 步是「反向」的关键：父节点的 `Goal` 是「要砍树得有斧头」，子节点把它换成「要有斧头」之后还要额外满足「砍树的前提」。**子节点收的 `newGoal` 是父节点的 `Goal`，不是原始目标**（`Expand` 里 `var newGoal = Goal;`）。

### 2. 展开动作的三道闸

```csharp
if (effects.HasAny(Goal) &&                            // 效果至少命中一个待满足条件
    !Goal.HasAnyConflict(effects, precond) &&          // 宽松版：被效果修好的前提冲突不算冲突
    !Goal.HasAnyConflict(effects) &&                   // 严格版：效果不能与 Goal 直接冲突
    possibleAction.CheckProceduralCondition(stackData))
```

- `HasAny(other)` 遍历的是 **other 的键**，用 `ReGoapCondition.IsMatch` 判定；条件对象（`>=2` 这种）出现在哪一侧都能被识别。
- 宽松版与严格版的区别很实用：`Replan{前提 hasAxe=false, 效果 hasAxe=true}` 面对 `Goal{hasAxe=true}` 时**放行**（前提冲突被效果修好），而 `TradeAxe{效果 hasAxe=false, hasWood=true}` 面对 `Goal{hasAxe=true, hasWood=true}` 会被严格版挡掉。
- 动作表是**倒序**遍历的（`for index = actions.Count - 1; index >= 0; index--`），这决定了 `PlanningEarlyExit` 会先撞见哪条计划。

### 3. A\* 的三处非教科书行为

```csharp
while ((frontier.Count > 0) && (iterations < maxIterations) && (frontier.Count + 1 < frontier.MaxSize))
```

1. **`earlyExit` 返回的是「展开时第一个命中的孩子」，不比较成本。** 默认 `PlanningEarlyExit = false`（`ReGoapPlannerSettings`），此时才是按 f 值出队。实测同一份配置：

   | 设置 | 得到的计划 | 总成本 |
   | --- | --- | --- |
   | 默认（earlyExit=false） | `GetAxe → ChopWood` | 3 |
   | `PlanningEarlyExit = true` | `BuyWood` | **5** |

   打开 earlyExit 少了一半的搜索，但计划更贵。
2. **重复状态的去重用的是 `break` 而不是 `continue`**：
   ```csharp
   if (similiarNode.GetCost() > childCost)
       frontier.Remove(similiarNode);
   else
       break;     // ← 直接跳出整个 foreach，本节点剩余的兄弟候选全部丢弃
   ```
   实测：动作表 `[A3, A2, A1]`（倒序展开 A1→A2→A3，A1/A2 产生同一状态且 A1 更便宜）时只入队 **1** 个孩子，A3 连入队机会都没有；换 `[A3, A1, A2]`（先贵后便宜）时入队 **3** 个。
3. **`iterations` 是按「孩子」计数而不是按「出队」计数**，`MaxIterations` 默认 1000，`MaxNodesToExpand` 默认 10000，且循环条件里留了 `+1` 的余量。

### 4. 目标选择与加权随机

`Plan()` 先把可行目标按优先级**升序**排序，`UseWeightedRandomGoalSelection = false` 时直接取**最后一个**（即最高优先级）。打开加权随机后：

```
weight = max(0, priority) ^ max(0.01, power)
if weight < 0.001: weight = 0.001          // WeightedRandomMinimumWeight
roll = random() * total                     // 命中第一个累计和 ≥ roll 的目标
```

`minWeight = 0.001` 的兜底让优先级 0 的目标仍有约 `0.001 / 5.001 ≈ 0.02%` 的概率被选中（目标优先级 0 与 5 的实测值）。

### 5. 目标已被满足 ⇒ 空计划 ⇒ 视为失败

`leaf.CalculatePath()` 从叶子回溯到根，**因为规划是反向的，回溯顺序恰好就是执行顺序**。若目标一开始就被世界状态满足，叶子就是根本身，路径长度为 0，而 `Plan()` 里 `if (result.Count == 0) continue;` 会把它当成「这个目标没有计划」而换下一个目标。

## 对比：GOAP vs 行为树

| 维度 | 行为树 | GOAP |
| --- | --- | --- |
| 决策来源 | 设计者写死的树 | 运行时搜出来的动作序列 |
| 新动作的接入成本 | 要找位置挂节点 | 只写前提/效果/代价 |
| 单次决策开销 | O(树高) | 一次 A\*（迭代上限 1000） |
| 可预测性 | 强 | 弱（同一状态下换动作表顺序就可能换计划） |
| 典型坑 | 抢占语义 | earlyExit / break / 启发函数 h 不可采纳 |

## 环境

- Python 3.12+（仅标准库）
- Go 1.21+（无第三方依赖）；本机无 Go 工具链时走人工审查 + `bracket_check.py` / `go_sanity.py`

## 运行方式

```bash
cd python && python selfcheck_goap.py     # 29 条断言，输出 PASS = 29
cd go     && go run .                      # 打印默认解 / early-exit 解 / 条件目标解
```

## 关键代码

Python：节点构造（与 `ReGoapNode.Init` 对应）

```python
if action is not None:
    self.goal = goal.clone()                     # 收到的是父节点的 Goal
    self.g += action.cost
    self.state.add_from_state(self.effects)
    self.goal.replace_with_missing_difference(self.effects)
    self.goal.add_from_state(self.preconditions)
self.h = len(self.goal)
self.cost = self.g + self.h
merged = State()
self.goal.missing_difference(self.planner.world, into=merged)
self.goal_merged_with_world = merged
```

Go：状态键（Go 的 map 遍历无序，必须显式序列化才能当键）

```go
func (s *state) key() string {
	keys := make([]string, 0, len(s.values))
	for k := range s.values { keys = append(keys, k) }
	sort.Strings(keys)
	var b strings.Builder
	for _, k := range keys { fmt.Fprintf(&b, "%s=%v;", k, s.values[k]) }
	return b.String()
}
```

## 性能边界

- `h = Goal.Count` 是**剩余条件数**，而一步动作的实际代价可以是任意浮点（本例 `BuyWood` 代价 5 却只消掉 1 个条件）⇒ **h 不是可采纳启发式**，默认配置下 A\* 不保证给出代价最优的计划，只保证「能走通且步数搜索规模可控」。
- 每次 `Expand` 都要为每个候选动作克隆一份 `State` 与 `Goal`，动作表越大、目标条件越多，单次展开越贵；`MaxIterations`/`MaxNodesToExpand` 是唯一的刹车。
- `CalculatePath` 是 O(计划长度)，但规划器是**整条计划一次算完**的，世界状态变化后需要重新规划（ReGoap 的做法是动作失效/目标变化时重跑 `Plan`）。

## 注意事项与常见坑

1. **`PlanningEarlyExit` 默认关**，打开后「先撞见的解」会赢，代价可能更贵——想省 CPU 可以开，想省钱别开。
2. **动作在表里的顺序会影响结果**（倒序展开 + earlyExit + `break` 三处都依赖它），靠「调整动作顺序」来改行为是脆弱的。
3. **子节点收的是父节点的 `Goal` 而不是原始目标**，自己复现时传错会导致 `h` 永远不为 0。
4. **`goalMergedWithWorld` 比的是世界状态**：一条在节点状态里自洽、但没落到世界状态上的计划不会终止搜索。
5. **空计划 == 没有计划**：目标已被满足时规划器返回「无计划」，业务上通常要单独处理「什么都不用做」。
6. `minWeight = 0.001` 与 `power` 下限 `0.01` 都是**先 clamp 再算**，优先级为 0 或负数不会被彻底排除。

## 参考资料

实际读过并逐行对照的源码（luxkun/ReGoap，master 分支）：

- `ReGoap/Planner/ReGoapNode.cs` — `Init` / `Expand` / `CalculatePath` / `IsGoal`
- `ReGoap/Planner/AStar.cs` — `Run`（frontier、explored、stateToNode、earlyExit、迭代上限）
- `ReGoap/Planner/ReGoapPlanner.cs` — `Plan` / `SelectNextGoal` / `UsingDynamicActions` 预检
- `ReGoap/Planner/ReGoapPlannerSettings.cs` — `PlanningEarlyExit=false`、`MaxIterations=1000`、`MaxNodesToExpand=10000`、`WeightedRandomMinimumWeight=0.001`
- `ReGoap/Core/ReGoapState.cs` — `HasAny` / `HasAnyConflict`（两个重载）/ `MissingDifference` / `ReplaceWithMissingDifference`
- `ReGoap/Core/ReGoapCondition.cs` — `IsMatch` / `AreCompatible` / `IsCompatibleWith`
