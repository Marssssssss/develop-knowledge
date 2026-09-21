# 层级状态机：SCXML 的转移域、进入/退出顺序与历史状态

> 新子领域条目 `01-游戏开发/06-AI/状态机/层级状态机HFSM`，同时落地原 `06-AI/README.md` 待研究项里的 FSM 部分。层级状态机（Harel statecharts）比普通 FSM 多出来的能力是**嵌套 + 并行 + 历史**，代价是「一次转移到底要退出/进入哪些状态」变成一件需要精确定义的事。W3C 的 SCXML 规范在 Appendix D 里把这件事写成了完整伪码，本 demo 把它逐条转写成 Python 与 Go。

## 简介

一次 macrostep 的三段式：

```
enabledTransitions = selectTransitions(event)      // 谁被这个事件触发
removeConflictingTransitions(enabledTransitions)   // 并行区域里的冲突剔除
microstep:  exitStates → executeTransitionContent → enterStates
```

真正决定「哪些状态被退出」的是**转移域（transition domain）**：

- `internal` 且目标是源的后代 ⇒ 域 = 源状态；
- 否则域 = `findLCCA([source] + targets)`（**最小公共复合祖先**）。

域定了，退出集就是「配置中所有域的后代」，进入集由 `addDescendantStatesToEnter` + `addAncestorStatesToEnter` 补出来。

| 直觉 | 规范 |
| --- | --- |
| 转移会重启父状态 | 兄弟之间转移的域就是父状态，**父状态根本不退出**（它自己是域，不是域的后代） |
| `isDescendant(a, b)` 包含 `a == b` | **不包含**：只认子/孙。这条决定了父状态会不会被一起退出 |
| 退出顺序随便 | `exitOrder`：后代先于祖先（等价于 document order 的逆序） |
| 历史状态在退出时记 | 规范把「记录历史值」和「真正退出」分成**两个循环**，先全记完再逐个退 |

## 原理详解

### 1. selectTransitions：原子状态出发，沿祖先链上溯

```
for state in configuration 里的原子状态（document order）:
    for s in [state] + getProperAncestors(state, null):
        取 s 上第一条 event 匹配且条件成立的转移（document order）→ 加入并停
```

- **只有原子状态会触发搜索**，复合状态靠冒泡被找到。
- 每个原子状态**最多贡献一条**转移；命中即停（`break loop`）。
- 实测：原子状态 `s1a` 与祖先 `s1` 都有 `go` 的转移时，`s1a` 的那条赢；`s1a` 没有时才轮到 `s1`。

### 2. removeConflictingTransitions：并行区域才有冲突

只有并行状态激活时才可能有多条转移。冲突判据是**退出集相交**：

```
if computeExitSet([t1]) ∩ computeExitSet([t2]) ≠ ∅:
    if isDescendant(t1.source, t2.source):  t1 抢占 t2（移除 t2）
    else:                                   t2 赢（t1 被丢弃）
```

两条规则的分工：
- **后代优先**：祖先的转移（由另一个分支的原子状态选中）先入列，后代的后来 ⇒ 后代把它挤掉。实测 `b` 与 `b2` 同时被选中时留下 `b2`。
- **否则 document order 更早者赢**：无祖孙关系时（如两个平行分支 `a1` 与 `b1`），先被选中者保留。
- **targetless 转移的退出集为空，因此不与任何转移冲突**。

### 3. 退出集与退出顺序

```
function computeExitSet(transitions)
    for t in transitions:
        if t.target:                       # targetless 不做任何事
            domain = getTransitionDomain(t)
            for s in configuration:
                if isDescendant(s, domain): statesToExit.add(s)
```

`isDescendant` 不含相等 ⇒ **域自己不退出**。这是 `internal` / `external` 唯一的可观测差异：

| 转移 | 源 | 目标 | 域 | 退出 |
| --- | --- | --- | --- | --- |
| `type="internal"` | `s1` | `s1b`（子） | `s1` | 只退 `s1a` |
| `type="external"` | `s1` | `s1b`（子） | `<scxml>`（因为 `s1` 不是自己的真祖先） | 退 `s1a` **和 `s1`**，再重新进入 |

退出顺序 `exitOrder` = 后代先于祖先（逆 document order），实测跨分支转移的退出日志是 `["s1a", "s1"]`。

### 4. 进入集与进入顺序

`computeEntrySet` 分两步：

```
for t in transitions:
    for s in t.target:  addDescendantStatesToEnter(s)     # 目标及其默认后代
    ancestor = getTransitionDomain(t)
    for s in getEffectiveTargetStates(t):
        addAncestorStatesToEnter(s, ancestor)             # 补上域以内的祖先
```

- `addDescendant` 遇到复合状态就把它的默认初始子状态一路加到底，遇到并行状态就把**每个还没有后代在集合里的分支**都加进来。
- `addAncestor` 沿 `getProperAncestors(s, ancestor)` 向上补，遇到并行祖先同样补兄弟分支（这就是「跨分支转移后另一分支要重新初始化」的来源）。
- 进入顺序 `entryOrder` = 祖先先于后代（等价于 document order），实测 `["s2", "s2a"]`。

### 5. 历史状态：先记录，再退出

```
for s in statesToExit:                 # 第一个循环：只记录
    if s.history.type == "deep":  value = 配置里所有 s 的原子后代
    else:                         value = 配置里所有 parent == s 的直接子状态
    historyValue[h.id] = value
for s in statesToExit:                 # 第二个循环：执行 onexit 并移出配置
    configuration.delete(s)
```

**两个循环不能合并**：合并后先退出的子状态已经不在配置里，shallow 历史会记成空。实测合并前记到 `[]`、分开后正确记到 `["s2b"]`。

## 对比：FSM / HFSM / 行为树

| 维度 | 扁平 FSM | HFSM（statecharts） | 行为树 |
| --- | --- | --- | --- |
| 状态数爆炸 | 严重 | 靠嵌套 + 并行收敛 | 靠节点复用 |
| 转移副作用范围 | 手写 | **由 LCCA 自动推导** | 由父节点 halt 规则决定 |
| 事件未处理时的行为 | 忽略 | **沿祖先链冒泡** | 向父节点返回 FAILURE |
| 正交（并行）行为 | 无 | `<parallel>` 原生支持 | `Parallel` 节点 |
| 「回到上次的哪里」 | 手写变量 | 历史伪状态 | 无（依赖黑板） |

## 环境

- Python 3.12+（仅标准库）
- Go 1.21+（无第三方依赖）；本机无 Go 工具链时走人工审查 + `bracket_check.py` / `go_sanity.py`

## 运行方式

```bash
cd python && python selfcheck_hfsm.py    # 39 条断言，输出 PASS = 39
cd go     && go run .                     # 打印 start / go / leave / next / back 的配置变化
```

## 关键代码

Python：转移域（internal 与 external 的分水岭）

```python
def get_transition_domain(self, t):
    targets = self.get_effective_target_states(t)
    if not targets:
        return None
    src = self.states[t.source]
    if (t.kind == "internal" and src.is_compound()
            and all(self.is_descendant(x, t.source) for x in targets)):
        return t.source
    return self.find_lcca([t.source] + targets)
```

Python：退出必须分两个循环

```python
ordered = self.exit_order(states_to_exit)
for s in ordered:            # ① 先统一记录历史值
    ... self.history_value[hid] = value
for s in ordered:            # ② 再逐个退出
    self.configuration.discard(s)
```

## 性能边界

- 单次 macrostep 的代价是 O(配置大小 × 状态树深度)：`selectTransitions` 对每个原子状态都要走一遍祖先链，`computeExitSet` 对每条转移都要扫一遍配置。
- `findLCCA` 的朴素实现是 O(状态数 × 深度)；工程实现会预先算好深度与父指针，把 LCCA 降到 O(深度)。
- 并行分支越多，配置越大，`removeConflictingTransitions` 的 `computeExitSet` 被重复调用的次数越多（本实现对每一对冲突都要重算一次退出集）。
- 深嵌套 + 深历史会让「一次转移影响的节点数」远大于直觉值，UI/游戏逻辑里把 `onentry`/`onexit` 当「轻量回调」用是危险的。

## 注意事项与常见坑

1. **`isDescendant` 不含相等**。写成 `anc in ancestors or sid == anc` 会让「域自己」也被退出，兄弟转移变成重启父状态——这是最常见的移植错误。
2. **`external` 转移会退出源状态自己**：目标是自己的子状态时，`LCCA(source, child)` 必须跳到 source 之上，于是 source 被退掉再进入（`onentry` 会重跑）。想要「只换子状态」请用 `type="internal"`。
3. **历史记录必须先于退出**，两个循环合并会让 shallow 历史永远为空。
4. **targetless 转移不参与冲突判定**（退出集为空），可以用它做「纯动作」转移，但要小心它会阻止同层其它转移被选中（因为命中即停）。
5. **`<scxml>` 元素本身不计入 configuration**：规范里 `getProperAncestors(state, null)` 会包含它，但 `enterStates` 对初始转移的 `addAncestorStatesToEnter(s, <scxml>)` 返回空集，所以根只是容器。本实现按「不计入」处理，配置里只有真正的状态。
6. 本 demo 未实现 `invoke`、数据模型、`done.state.*` 事件与 `<final>` 的完成传播，只保留了状态拓扑与转移语义。

## 参考资料

实际读过并逐条对照的规范原文：

- W3C, *State Chart XML (SCXML): State Machine Notation for Control Abstraction*, W3C Recommendation — Appendix D「Algorithm for SCXML Interpretation」全文（`selectTransitions`、`removeConflictingTransitions`、`microstep`、`exitStates`、`computeExitSet`、`enterStates`、`computeEntrySet`、`addDescendantStatesToEnter`、`addAncestorStatesToEnter`、`getTransitionDomain`、`findLCCA`、`getEffectiveTargetStates`、`getProperAncestors`、`isDescendant`、entryOrder/exitOrder/documentOrder 的定义）
- 同规范 §「Semantics」对 macrostep / microstep 与内部事件队列的说明
