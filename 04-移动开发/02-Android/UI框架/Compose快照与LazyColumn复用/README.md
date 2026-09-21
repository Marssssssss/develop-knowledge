# Compose 快照系统与 LazyColumn 复用

Compose 的「状态一变、UI 自动重画」背后是两层机制：**快照（Snapshot）**负责让状态变更可以被
原子地提交/回滚，**LazyColumn**负责只测量可见的少数几项并靠 `key` 复用 composition。
本 demo 把两层都做成纯逻辑模型。

对应源码（androidx 主干）：

- `compose/runtime/runtime/src/commonMain/kotlin/androidx/compose/runtime/snapshots/Snapshot.kt`
- `compose/runtime/runtime/src/commonMain/kotlin/androidx/compose/runtime/SnapshotState.kt`
- `compose/foundation/foundation/src/commonMain/kotlin/androidx/compose/foundation/lazy/LazyListMeasure.kt`
- `compose/foundation/foundation/src/commonMain/kotlin/androidx/compose/foundation/lazy/LazyList.kt`

## 一、原理详解

### 1.1 状态的物理形态：一条按 snapshotId 排序的 StateRecord 链

每个状态对象不是「一个值」，而是一串带 `snapshotId` 的记录。读某个快照里的值时，
找 **snapshotId ≤ 当前快照 id** 的最新一条——这就是 `readable()`。
所以「旧快照里读旧值、新快照里读新值」是天然成立的，不需要额外备份。

`PreexistingSnapshotId = 1` 是「世界初创」那条记录的 id，它在冲突判定里有特殊地位（见 1.4）。

### 1.2 什么叫「碰撞写」

`Snapshot.apply()` 的源码注释给了**唯一**定义：

> *"A write is considered colliding if any write occurred in a state object in a snapshot
> applied since the snapshot was taken."*

即：自本快照被 take 之后，**有别的状态写在别的快照里被 apply 了**，才算碰撞。
本快照自己写的、以及 take 之前就存在的，都不算。

### 1.3 apply() 的两条路径

```kotlin
if (modified == null || modified.size == 0) {
    closeLocked()
    resetGlobalSnapshotLocked(globalSnapshot, emptyLambda)   // 空修改：不可能冲突，直接成功
} else {
    val result = innerApplyLocked(nextSnapshotId, modified, optimisticMerges,
                                  openSnapshots.clear(globalSnapshot.snapshotId))
    if (result != SnapshotApplyResult.Success) return result  // 冲突且合并不了 → 失败
    ...
}
```

**空修改的 apply 永远不会失败**——这条很有用：`withMutableSnapshot {}` 里什么都没改时不必担心。

### 1.4 innerApplyLocked 的三分支合并

对每一个被修改的状态对象，官方代码是：

```kotlin
val current  = readable(first, nextId, invalidSnapshots)   // 全局最新
val previous = readable(first, this.snapshotId, start)     // 本快照 take 时可见
if (previous.snapshotId == PreexistingSnapshotId.toSnapshotId()) return@forEach  // ①
if (current != previous) {                                                       // ② 碰撞
    val applied = readable(first, this.snapshotId, this.invalid)  // 本快照自己写的
    val merged = optimisticMerges?.get(current) ?: state.mergeRecords(previous, current, applied)
    when (merged) {
        null    -> return SnapshotApplyResult.Failure(this)   // ③ 合并不了 → 整个 apply 失败
        applied -> { /* 本地变更掩盖冲突，什么都不做 */ }
        current -> { mergedRecords.add(state to current.create(snapshotId)); statesToRemove.add(state) }
        else    -> { mergedRecords.add(...) }                 // 采用协商值
    }
}
```

三条要点：

1. **① 初始记录不参与冲突判定**——如果一个状态对象还只有 `PreexistingSnapshotId=1` 那条记录，
   直接跳过。这是「首次写永远成功」的由来。
2. **`merged == applied`（本快照写入）** → 冲突被忽略，本地值胜出；
3. **`merged == current`（全局最新）** → 本地修改作废，并把该状态**从 modified 集合里移除**
   （源码注释：*"If we revert to current then the state is no longer modified."*）。

> **符号口径**：源码里 `applied` 指本快照的写入、`current` 指全局最新。本 demo 为避免误读，
> 在 `snapshot_core.py` 中显式命名为 `local` / `global_current`，并在注释里给出对齐关系。

### 1.5 快照不保证可串行化

`apply()` 开头就写着 *"does not guarantee serializable snapshots ... doesn't prevent
crossing writes (https://arxiv.org/pdf/1412.2324.pdf)"*：即**两个并发快照各自改不同的状态对象
会都成功**，即使这在串行化语义下不成立——Compose 选择的是「能用就行 + 冲突时兜底」，不是数据库事务。

### 1.6 enter / leave 的断言

```kotlin
public fun unsafeLeave(oldSnapshot: Snapshot?) {
    checkPrecondition(threadSnapshot.get() === this) {
        "Cannot leave snapshot; $this is not the current snapshot"
    }
    restoreCurrent(oldSnapshot)
}
```

`JNIEnv` 的教训在这里重演：**快照是线程局部的**，`enter`/`leave` 必须配对且不能跨线程。

### 1.7 LazyColumn：只测可见的，且越界要钳

`measureLazyList` 的开头就有两条硬规则：

```kotlin
if (itemsCount <= 0) {
    // empty data set. reset the current scroll and report zero size
    ...
}
var currentFirstItemIndex = firstVisibleItemIndex
if (currentFirstItemIndex >= itemsCount) {
    // the data set has been updated and now we have less items that we were scrolled to before
    currentFirstItemIndex = itemsCount - 1
    currentFirstItemScrollOffset = 0
}
```

即：**数据集变空 → 重置滚动；数据集变小 → 钳到最后一项并把 offset 归零**。

滚动消费：

```kotlin
currentFirstItemScrollOffset -= scrollDelta
if (currentFirstItemIndex == 0 && currentFirstItemScrollOffset < 0) {
    scrollDelta += currentFirstItemScrollOffset   // 被吃掉的部分不计入已消费
    currentFirstItemScrollOffset = 0
}
```

间隔（spaceBetweenItems）**只加在非末项**——源码注释：*"we add spaceBetweenItems as an extra
spacing for all items apart from the last one"*。

`beyondBoundsItemCount` 决定可见区域之外多测几项（预取手感），`pinnedItems` 则不管是否可见都要测。

### 1.8 key 决定复用，且位置修正不能产生读观测

`LazyList.kt` 里每次测量前都会：

```kotlin
Snapshot.withoutReadObservation {
    firstVisibleItemIndex = state.updateScrollPositionIfTheFirstItemWasMoved(
        itemProvider, state.firstVisibleItemIndex)
    firstVisibleItemScrollOffset = state.firstVisibleItemScrollOffset
}
```

两件事：

- 用 **key**（`keyIndexMap`）而不是位置来找回滚动锚点——头部插入一项时，用户的可视位置不会跳；
- 包在 `withoutReadObservation` 里，否则「读 `firstVisibleItemIndex`」本身会登记为依赖，
  导致测量反过来触发重组（自激循环）。

复合项复用同理：**key 相同且 contentType 相同**才能复用槽位；只给位置不传 key 时，
key 退化为位置，插入/删除后状态就串位。

## 二、对比：快照 vs 其它状态管理

| 维度 | Compose Snapshot | 普通可变对象 | 响应式（Rx/LiveData） |
| --- | --- | --- | --- |
| 原子性 | apply 整体成功/失败 | 无 | 取决于实现 |
| 冲突 + 隔离 | mergeRecords 可协商、嵌套快照可回滚 | 后写胜、无隔离 | 无 |

## 三、环境要求

- Python 3.8+（模型无依赖）
- 真机需 `androidx.compose.runtime` / `androidx.compose.foundation`

## 四、运行方式

```bash
cd 04-移动开发/02-Android/UI框架/Compose快照与LazyColumn复用
python selfcheck_compose.py      # 期望输出 PASS 46
```

## 五、关键代码

- `snapshot_core.py`：`StateRecord` / `StateObject` / `MutableSnapshot.apply()`
- `main.py`：`measure_lazy_list` / `LazyListState` / `withoutReadObservation` / `reused_slots`
- `selfcheck_compose.py`：46 条断言
- `ComposeSnapshot.kt`：Kotlin 侧等价实现

## 六、性能边界与注意事项

- **快照是写时复制，不是锁**：并发写同一状态对象时只有一个能 apply 成功，另一个拿 `Failure`。
- `mergeRecords` 是官方留给计数器这类场景的口子：返回 `applied`（本地）可实现「后写胜」，
  返回协商值可实现「累加」；返回 `null` 则让整个 apply 失败。
- **不要指望可串行化**（源码注释已明确）；需要强一致就自己加事务边界。
- LazyColumn 的性能几乎全押在 **key** 上：不给 key 时插入/删除会导致后续项全部重建，锚点也找不回。
- `beyondBoundsItemCount` 每多一项就多一次测量与组合，**线性增加主线程负担**。
- **常见坑**：在 `derivedStateOf` 之外读 `LazyListState.firstVisibleItemIndex` 做布局决策会抖动。

## 七、参考资料

实际读取的源码：

- `https://raw.githubusercontent.com/androidx/androidx/androidx-main/compose/runtime/runtime/src/commonMain/kotlin/androidx/compose/runtime/snapshots/Snapshot.kt`
- `.../compose/foundation/foundation/src/commonMain/kotlin/androidx/compose/foundation/lazy/LazyListMeasure.kt`
- `.../compose/foundation/foundation/src/commonMain/kotlin/androidx/compose/foundation/lazy/LazyList.kt`
> 口径说明：快照冲突的符号定义在源码中较绕（`applied` / `current` 两个词与直觉相反），
> 本 demo 采用官方注释原文「碰撞 = 自 take 之后有别的已 apply 快照写过」作为唯一判据，
> 并在代码注释中给出与源码符号的对齐表。
