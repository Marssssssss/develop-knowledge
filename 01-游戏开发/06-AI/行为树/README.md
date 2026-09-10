# 行为树（Behavior Tree）

## 节点类型

- **Composite**：Selector（任一子节点成功即成功）/ Sequence（按序）/ Parallel
- **Decorator**：Inverter / Repeater / Cooldown
- **Leaf**：Action / Condition

## 与状态机的区别

| 维度 | 状态机 | 行为树 |
| --- | --- | --- |
| 复杂度 | O(n) 节点维护转移表 | 树结构，可视化好 |
| 复用 | 差 | 高（子树可移植） |
| 调试 | 一般 | 优秀（每帧可视化） |

## 待研究

- [ ] BT 节点基类（C# 实现）
- [ ] Selector / Sequence 的 tick 行为
- [ ] 黑板（Blackboard）共享数据