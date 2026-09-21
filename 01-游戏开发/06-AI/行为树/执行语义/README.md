# 行为树的执行语义：Sequence / Fallback / Reactive / Parallel / 装饰器

> 待研究项落地：`BehaviorTree 完整 demo`。本 demo 不写「行为树怎么用」，而是把 **BehaviorTree.CPP（BT.CPP）的执行语义逐行转写成 Python 与 Go**，用来回答一个具体的问题：**同一个子树，用 `Sequence` 和用 `ReactiveSequence` 包起来，每个 tick 到底哪些孩子被 tick 了几次**。

## 简介

行为树的全部语义只有三件事：**状态**（`IDLE/RUNNING/SUCCESS/FAILURE/SKIPPED`）、**tick 的路由规则**、**复位（halt/reset）时机**。BT.CPP 把这三件事写在 `src/controls/*.cpp` 与 `src/decorators/*.cpp` 里，每个节点的核心都在 30 行以内，但有几处判定与直觉相反：

| 直觉 | 官方实现 |
| --- | --- |
| `Sequence` 每 tick 从头检查所有前置条件 | 只 tick `current_child_idx_` 指向的那个孩子，前面成功的孩子**不再被 tick** |
| `Parallel` 等所有孩子跑完再结算 | 每 tick 一个孩子就**结算一次**，失败的孩子之后的兄弟**根本不会被 tick** |
| `Parallel` 的 `success_threshold=1` 表示「一个成功即可」 | 负阈值才是「从末尾数」；`1` 就是字面的 1，但**默认值是 -1（=全部）** |
| 全 `SKIPPED` 的子树返回 `SKIPPED` | 只有当**阈值非负**时才成立；默认 `Parallel`（阈值 -1）会把 `SKIPPED` 计入成功票，于是全跳过反而返回 `SUCCESS` |

## 原理详解

### 1. 状态与 `isStatusActive`

`include/behaviortree_cpp/basic_types.h`：

```cpp
enum class NodeStatus { IDLE = 0, RUNNING = 1, SUCCESS = 2, FAILURE = 3, SKIPPED = 4 };
inline bool isStatusActive(const NodeStatus& status) {
  return status != NodeStatus::IDLE && status != NodeStatus::SKIPPED;
}
```

`Sequence::tick` 开头用 `isStatusActive(status())` 决定是否清 `skipped_count_` —— 也就是说**只有从非活跃态（IDLE/SKIPPED）重新进入时**计数才归零，`RUNNING` 中途续跑不会清。

### 2. Sequence：记忆下标，失败才回头

```cpp
while(current_child_idx_ < children_count) {
  TreeNode* current_child_node = children_nodes_[current_child_idx_];
  const NodeStatus child_status = current_child_node->executeTick();
  switch(child_status) {
    case NodeStatus::RUNNING:  return NodeStatus::RUNNING;          // 不回头
    case NodeStatus::FAILURE:  resetChildren(); current_child_idx_ = 0; return child_status;
    case NodeStatus::SUCCESS:  current_child_idx_++; break;
    case NodeStatus::SKIPPED:  current_child_idx_++; skipped_count_++; break;
    case NodeStatus::IDLE:     throw LogicError(...);
  }
}
```

- `RUNNING` **直接返回**，前面的孩子保持 `SUCCESS` 不再 tick —— 这就是「Sequence 里的前置条件只在第一次被检查」的来源。
- `FAILURE` 时 `resetChildren()` 把所有孩子清回 `IDLE` 并把下标归零，所以**下一次 tick 是真的从头开始**。
- 走完全部孩子后同样 `resetChildren()`，于是「成功一次」之后下标也归零（连续两次 tick 会得到两份完整的 tick 计数）。
- 全部孩子都 `SKIPPED` 时返回 `SKIPPED`（`skipped_count_ == children_count`），否则返回 `SUCCESS`。

### 3. Fallback：与 Sequence 对偶

`fallback_node.cpp` 的结构完全对称，只把 `SUCCESS`/`FAILURE` 的分支调换：命中 `SUCCESS` 就复位并返回 `SUCCESS`，`FAILURE` 才 `current_child_idx_++`；走完返回 `FAILURE`。**这是「选择器」语义**：试到第一个成功的策略为止。

### 4. ReactiveSequence：每 tick 从头扫 + 抢占

```cpp
for(size_t index = 0; index < childrenCount(); index++) {
  const NodeStatus child_status = current_child_node->executeTick();
  switch(child_status) {
    case NodeStatus::RUNNING:
      for(size_t i = 0; i < childrenCount(); i++)
        if(i != index) haltChild(i);        // ← 其余兄弟全部复位
      ...
      return NodeStatus::RUNNING;
```

- 没有 `current_child_idx_`：**每次 tick 都从第 0 个孩子开始**，所以「条件节点 + 动作节点」的组合里条件会被反复求值，条件一旦不成立，后面 RUNNING 的动作立刻被 halt —— 这就是 BT 的抢占（preemption）。
- 与之对照，`Sequence` 里条件只在跨过它的那一次被求值。
- 官方 `ReactiveSequence::throw_if_multiple_running` 是 **static 且默认 false**：多个孩子同时 RUNNING 时默认**不抛异常**，只是除第一个之外的兄弟都被 halt 掉。

### 5. Parallel：阈值语义与「循环内提前结算」

```cpp
size_t ParallelNode::successThreshold() const {
  if(success_threshold_ < 0)
    return size_t(std::max(int(children_nodes_.size()) + success_threshold_ + 1, 0));
  return size_t(success_threshold_);
}
```

- 默认 `success_threshold_ = -1` → `n + (-1) + 1 = n`（全部成功）；`failure_threshold_ = 1`（**一个失败即失败**）。负值表示「从末尾数」：`-2` 配 3 个孩子 = 2。
- 构造检查：`children_count < successThreshold()` 直接抛 `LogicError("Number of children is less than threshold. Can never succeed.")`。
- **成功/失败的判定写在 for 循环内部**，每 tick 完一个孩子就立即结算一次。失败的两条判据：
  ```cpp
  if(((children_count - failure_count_) < required_success_count) ||
     (failure_count_ == failureThreshold()))
  ```
  第一条是「剩下的孩子全成功也凑不够票数」，第二条才是字面阈值。实测：3 个孩子默认阈值下，第 0 个失败会让 `(3-1) < 3` 成立 → 立刻 `FAILURE`，**孩子 1、2 一次都不会被 tick**。
- `completed_list_` 记录已结算的孩子下标，已完成的孩子不再重复 tick。
- 阈值**非负**时全 `SKIPPED` 返回 `SKIPPED`；默认的 `-1` 走 `(success_count_ + skipped_count) >= required` 这条分支 → 全跳过反而返回 `SUCCESS`。

### 6. 装饰器：Inverter 与 Repeat

- `InverterNode`：`SUCCESS ↔ FAILURE`，`RUNNING`/`SKIPPED` **原样透传**（不翻转），翻转后调 `resetChild()`。
- `RepeatNode`：`SUCCESS` 一次 `repeat_count_++`，`do_loop = repeat_count_ < num_cycles_ || num_cycles_ == -1`；`FAILURE` 立刻把计数清零并返回 `FAILURE`；`RUNNING` 直接返回；`SKIPPED` 时官方注释明确写了「**Don't reset the counter**」，只 `resetChild()` 后返回 `SKIPPED`。

## 对比：Sequence vs ReactiveSequence vs Parallel

| 维度 | Sequence | ReactiveSequence | Parallel（默认阈值） |
| --- | --- | --- | --- |
| 每 tick 起点 | `current_child_idx_` | 恒为 0 | 0，但跳过 `completed_list_` |
| 条件是否重算 | 否 | **是** | 是（未结算的孩子） |
| RUNNING 时别人 | 不动 | **全部 halt** | 继续 tick 下一个 |
| 结束条件 | 全 SUCCESS / 任一 FAILURE | 同左 | 票数达到阈值 |
| 内存 | 一个下标 + skipped 计数 | 一个 running 下标 | completed 集合 + 两个计数 |

## 环境

- Python 3.12+（仅用标准库）
- Go 1.21+（无第三方依赖）；本机无 Go 工具链时走人工审查 + `bracket_check.py` / `go_sanity.py`

## 运行方式

```bash
cd python && python selfcheck_bt.py       # 54 条断言，输出 PASS = 54
cd go     && go run .                      # 打印几个场景的逐 tick 结果
```

## 关键代码

Python（Sequence 的核心循环，与 `sequence_node.cpp` 一一对应）：

```python
while self.current_child_idx_ < children_count:
    child = self.children[self.current_child_idx_]
    child_status = child.execute_tick()
    if child_status == RUNNING:
        return RUNNING                       # 前面的孩子不再被 tick
    if child_status == FAILURE:
        self.reset_children()
        self.current_child_idx_ = 0
        return child_status
    if child_status == SUCCESS:
        self.current_child_idx_ += 1
    elif child_status == SKIPPED:
        self.current_child_idx_ += 1
        self.skipped_count_ += 1
    elif child_status == IDLE:
        raise LogicError(...)
```

Go（`Parallel` 的负阈值换算，注意 `int` 与 `size_t` 的差异）：

```go
func (p *parallel) succThreshold() int {
	if p.successThreshold < 0 {
		return maxInt(len(p.children)+p.successThreshold+1, 0)  // C++ 里是 size_t(max(int, 0))
	}
	return p.successThreshold
}
```

## 性能边界

- `Sequence`/`Fallback` 是 O(1) 额外内存、单 tick 摊还 O(1)（跨 tick 只看一个孩子）。
- `ReactiveSequence` 每 tick 都要**重扫全部前缀**，前缀越长越贵；且它每 tick 都会 halt 兄弟节点，若动作节点的 `halt()` 有副作用（取消寻路、播停止动画）会被反复触发。
- `Parallel` 每 tick 固定 O(n) 且维护 `completed_list_`（`std::unordered_set`），n 大时是常数不小的开销。
- 三种节点都不保存子树的中间结果，**状态复位完全靠 `resetChildren()`**，因此子树被多个父节点共享是不安全的（BT.CPP 用 `Subtree` 节点做实例化来规避）。

## 注意事项与常见坑

1. **「条件 + 动作」用 `Sequence` 包，条件只在跨过它那一次被求值**；要每次检查请换 `ReactiveSequence`，代价是每 tick 重算。
2. **`Parallel` 的失败判据第一条是「凑不够票」而不是「失败数达标」**，默认阈值下一个失败就足以让 3 孩子组立刻失败，剩余孩子的 `onentry`/副作用不会发生。
3. **`SKIPPED` 不是「跳过」那么简单**：它既参与 `all_children_skipped` 判定，又在 `Parallel` 的负阈值分支里充当成功票。
4. **`Repeat` 的 `SKIPPED` 不清计数**，被跳过的那一轮**仍然算进 `repeat_count_`**——这与 `FAILURE` 清零的语义相反，混用时很容易数错圈数。
5. 让叶子返回 `IDLE` 会直接 `throw LogicError`（BT.CPP 里是异常，Python/Go 侧建模为 `LogicError`/`error`），不要指望父节点兜住它。

## 参考资料

实际读过并逐行对照的源码（BehaviorTree.CPP master 分支）：

- `include/behaviortree_cpp/basic_types.h` — `NodeStatus` 枚举、`isStatusActive`、`isStatusCompleted`
- `include/behaviortree_cpp/controls/sequence_node.h`、`fallback_node.h`、`parallel_node.h`、`reactive_sequence.h`
- `src/controls/sequence_node.cpp`、`src/controls/fallback_node.cpp`、`src/controls/reactive_sequence.cpp`、`src/controls/parallel_node.cpp`
- `src/decorators/inverter_node.cpp`、`src/decorators/repeat_node.cpp`
- `src/control_node.cpp` — `resetChildren` / `haltChild` / `haltChildren`
- `src/tree_node.cpp` — `executeTick` / `haltNode`
