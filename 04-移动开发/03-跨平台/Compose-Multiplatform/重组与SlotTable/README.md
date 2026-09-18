# Compose 运行时:SlotTable(gap buffer)、group 树与重组

## 简介

Compose 的性能叙事里最关键的一句是"只有读了变化状态的 composable 才会重跑"。支撑这句话的
底层数据结构叫 **SlotTable** —— 它不是树,而是一个**用一维数组表达树的 gap buffer**。
理解它,才能理解为什么 `remember` 的语义是"位置记忆"、为什么循环里不加 `key` 会导致整段
重组、为什么重组被中断时副作用不会生效。

关键概念:

- **SlotTable** —— 两个数组:`groups`(每 group 占固定字段数个整数)与 `slots`(存放值)。
- **group / restart group** —— 重组单位兼树节点;每个非 inline `@Composable` 大致对应一个。
- **gap buffer** —— 数组里留一段"间隙",插入/删除在间隙处进行,避免整体搬动。
- **anchor** —— 指向 group 的稳定引用;间隙移动或 group 增删时必须被维护。

## 原理详解

### 1. 为什么不是树,而是"内联结构体数组 + 间隙"

androidx `SlotTable.kt` 的字段注释原文(逐字):

> `groups` —— "An array to store group information that is stored as groups of
> [Group_Fields_Size] elements of the array. The [groups] array can be thought of as
> an array of an inline struct."
>
> `slots` —— "The slot elements for a group start at the offset returned by [dataAnchor]
> of [groups] and continue to the next group's slots or to [slotsSize] for the last group.
> **When in a writer the [dataAnchor] is an anchor instead of an index as [slots] might
> contain a gap.**"

1. `groups` 是**整数数组**,每 `Group_Fields_Size` 个元素构成一条 group 记录 ——
   相当于把 C 的 `struct Group { ... }` 内联展开,省掉对象头与指针追逐。
2. `slots` 与 `groups` 分开存放:group 记录只留一个 `dataAnchor`,槽区**从该锚点一直延伸
   到下一个 group 的锚点**(最后一个延伸到 `slotsSize`)。
3. 加粗那句是全部麻烦的来源:`slots` 里**可能有间隙**,所以写模式下 `dataAnchor` 不能当
   数组下标用,必须是"锚点"这种会被主动维护的东西。

`Group_Fields_Size` 的取值与 5 个字段的名字(Key / GroupInfo / ParentAnchor / DataAnchor /
ObjectKey),官方注释只到"每组占 N 个元素、可视为内联结构体"这一层,具体拆分来自第三方解读。
本 demo 因此只用**公开 API 可直接观察的量**做断言(groupSize / parent / skipToGroupEnd /
锚点可解析性),不对位级布局下结论 —— 这点在 `python/slot_table.py` 模块注释里也标注了。

### 2. gap buffer 把"插入"的代价从 O(n) 降到 O(1)

一次移动的搬动量,本 demo 用**可数的方式**量了出来:

| 操作 | 搬动的有效元素数 |
| --- | --- |
| 在间隙处连续插入 10 次 / 间隙已在表头再插入 / 删除 1 个元素 | **0**(删除只扩间隙) |
| 把间隙从表尾移到表头(表内 10 个元素) | 10 |
| 100 元素表把间隙移到中部再移回表尾 | 50 + 50 = 100(∝ 移动距离) |

结论就是 gap buffer 的经典性质:**代价 ∝ 间隙移动的距离,而不是表的大小**;
而"连续追加"这一最常见的访问模式,代价恒为 0。这解释了为什么 SlotTable 能支撑
"每帧都在同一位置重跑 composable"这种负载。

### 3. group 树的导航靠 groupSize

`SlotReader` 暴露了 `groupSize(index)` / `parent(index)` / `skipToGroupEnd()`,
后者是"跳过整棵子树"的原语。本 demo 断言了它的结构不变量:

- `groupSize(g) == 1 + Σ groupSize(children)`;`skipToGroupEnd(g) == g + groupSize(g)`
  (一次跳过落在下一个**兄弟**上);
- 一个 group 的槽数等于"到下一个 group 的槽区起点"的距离(源码注释口径),
  且所有 group 的槽数之和等于 `slotsSize`(记账自洽)。

编译器的跳过逻辑正是建在这个原语上:参数没变时执行 `$composer.skipToGroupEnd()`,
一次跨过整个子树,而不是逐个判断子节点。

### 4. 锚点为什么"必须是锚点"

把间隙从表尾移到 c1 与 c2 的槽区之间,两个锚点之间的**物理距离**会立刻多出
一个间隙长度。本 demo 把这一点量化了:

- 间隙在表尾:物理距离 == 逻辑槽数;间隙落在两者之间:物理距离 == 逻辑槽数 **+ 间隙长度**;
  把间隙移回表尾:物理距离又恢复。

所以写模式下拿两个 `dataAnchor` 相减当"元素个数"用,结果会随间隙位置漂移。
正确的做法是维护锚点(间隙移动时同步平移),并在删除 group 时让"指向被删子树的锚点"
失效 —— 运行时对应的报错就是 *"Anchor refers to a group that was removed"*。

### 5. 一次"表刚好占满"的删除,曾经踩过坑

androidx commit `a038886`「Fix boundary condition in slot table」的说明是:*"When a group is
removed from a slot table that is full (no gap) the parent anchor update code was skipped."*
也就是说"表**恰好没有间隙**"是个容易漏掉的分支。本 demo 在 `python/main.py` 的 D 段
复刻了这个场景:先构造一张物理容量刚好占满、`gap_len == 0` 的表(13 个 group),
再删掉一个含 11 个 group 的子树,断言删除后 `verifyWellFormed` 仍然自洽;
并用"如果像历史 bug 那样跳过父指针重映射,就会指向越界下标"做反证。

### 6. 四类 group 与 Compose 运行时组件

group 的四种类型(第三方逆向整理口径):
- **Restart**(`startRestartGroup`/`endRestartGroup`)—— 重组的最小单位,可被失效重启;
  返回 `ScopeUpdateScope` 保存重跑 lambda。
- **Replaceable**(控制流 `if`/`when`)—— 结构可变但**不能移动**,只能原地插入/删除/替换。
- **Movable**(`key(x) { ... }`)—— 位置 + `dataKey` 双标识,重排时可整体搬家(列表场景)。
- **Node**(`createNode`/`useNode`/`apply`)—— 真正的 UI 节点:`createNode` 只在首次组合调用,
  重组用 `useNode` 复用,属性更新记录成 `apply` 在组合结束后统一执行。

运行时侧的分工(第三方对 compose-multiplatform-core 的解读口径):**Composer/ComposerImpl**
是编译器生成代码调用的 API,维护 SlotTable 与变更列表;**Composition** 持有 SlotTable 并维护
"状态对象 → scope"映射与失效集合;**Recomposer** 等待失效、按帧重组、应用变更;
**Snapshot** 提供事务式隔离(子快照可见父,父不可见子的改动直到 `apply()`)。

## 对比 / 选型

| 维度 | SlotTable(gap buffer) | 常规树结构(对象 + 指针) |
| --- | --- | --- |
| 遍历局部性 | 数组连续,遍历友好 | 指针追逐,缓存不友好 |
| 顺序重跑的插入代价 | **0**(间隙就地) | 视实现 |
| 非顺序插入 | ∝ 间隙移动距离 | 链表可 O(1),但失去局部性 |
| 身份 | **位置**(位置记忆),位置一变就得靠 `key()` 找回 | 引用本身,天然稳定 |

## 环境准备

- 操作系统:任意(自检脚本是纯 Python);Python 3.8+,无第三方依赖
- Kotlin / Compose Multiplatform:阅读代码用,本机无工具链,不做编译

## 运行方式

### Python(结构模型自检)

```bash
cd python
python3 main.py     # 54 项断言
```

### Kotlin

`kotlin/RecompositionDemo.kt` 是代码侧观察(编译器改写形态、四类 group 对应写法),需放进 Compose 工程编译。

## 关键代码片段

```python
# 间隙移动:代价 ∝ 移动距离,间隙本身不算搬动
def move_gap_to(self, position):          # position 是"间隙前有多少个有效元素"
    if position < self.gap_start:
        moved = self.gap_start - position
        self.data[position + self.gap_len: self.gap_start + self.gap_len] = \
            self.data[position:self.gap_start]
    elif position > self.gap_start:
        moved = position - self.gap_start
        self.data[self.gap_start:self.gap_start + moved] = \
            self.data[self.gap_start + self.gap_len: position + self.gap_len]
    else:
        moved = 0
    self.gap_start = position
    self.moves += moved
    return moved
```

```python
# 删除子树:父指针重映射 + 锚点失效/平移 —— 两者都不能少
for aid, gi in list(self.anchors.items()):
    if index <= gi < index + span:
        del self.anchors[aid]                  # 指向被删子树 -> 失效
    elif gi >= index + span:
        self.anchors[aid] = gi - span          # 在其后 -> 整体前移
```

## 性能与边界

- **顺序重跑**是主路径:每帧在同序位置重跑 composable,间隙几乎不动,插入代价 0;
  而**非顺序变更**(列表头插入、条件分支翻转)迫使间隙移动,代价 ∝ 距离 ——
  这正是"列表不加 `key` 导致卡顿"的结构性原因。
- 跳过(`skipToGroupEnd`)不免费:它要求 group 头部的子树规模记账**始终正确**
  ("表刚好占满"那次 bug 正出在这里);group 数量失控则两个数组同时增长。

## 注意事项与常见坑

1. **把 `remember` 当"按名字缓存"**:它是**位置记忆**,只有同一调用点才返回同一份值。
2. **循环里不给 `key`**:列表头插入会让后续所有项的 group 身份错位,导致整段重组。
3. **在 composable 里写副作用**:`applyChanges` 只在**成功**的组合之后执行,组合中断时
   副作用不会被记录 —— 副作用必须走 `SideEffect` / `DisposableEffect`。
4. **指望"锚点会自己修好"**:锚点失效是硬错误(*"Anchor refers to a group that was
   removed"*),持有跨越 group 增删的锚点必须自己保证生命周期。
5. **忽略"表刚好占满"这一分支**:历史上正是这里漏掉了父指针更新(a038886);任何按数组
   容量做边界判断的代码都要单独覆盖"无间隙"状态。
6. **把 `Group_Fields_Size` 的字段含义当官方定论**:官方注释只到"内联结构体"一层,
   更细的字段拆分是社区逆向结果,版本间可能变化。

## 参考资料(实际阅读过的来源)

- [androidx `SlotTable.kt` 字段注释原文](https://github.com/CarGuo/gsy_flutter_book/blob/master/Flutter-frc.md)
  —— `groups`/`slots` 逐字注释、gap buffer 与 key 的作用、`applyChanges` 时机
- [androidx commit `a038886` · Fix boundary condition in slot table](https://github.com/b95505017/androidx/commit/a0388864a7b8669ba7a8dfd7780f5a757102f6c9)
  —— "table is full (no gap)" 时父锚点更新被跳过的原始缺陷
- [androidx commit `01940843` · API dump](https://github.com/androidx/androidx/commit/01940843f865e47a211e6a842585de045042d3e0)
  —— `SlotTable` / `SlotReader` / `SlotWriter` 的公开方法面(groupSize、skipToGroupEnd、verifyWellFormed…)
- [compose-jb issue #345](https://github.com/JetBrains/compose-jb/issues/345)
  —— "Anchor refers to a group that was removed" 的实际报错栈
- [deepwiki · compose-multiplatform-core · Composition and Compiler Integration](https://deepwiki.com/JetBrains/compose-multiplatform-core/3.1.1-composition-and-compiler-integration)
  —— 第三方逆向整理:group 头字段拆分、四类 group 语义、运行时组件分工
- [androiddevkit · Jetpack Compose](https://androiddevkit.com/topics/jetpack-compose)
  —— 第三方整理:编译器生成的 `startRestartGroup` / 跳过逻辑 / `updateScope` 形态

> 口径说明:`developer.android.com` 本机网络不可达,SlotTable 细节主要取自 androidx 源码
> 注释与提交记录,第三方整理的内容已逐处标注。
