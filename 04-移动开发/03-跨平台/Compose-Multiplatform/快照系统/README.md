# Compose 快照系统：id 位集合、可见性判据与 apply 冲突合并

> 目录：`04-移动开发/03-跨平台/Compose-Multiplatform/快照系统/`
> 语言：Python（`python/main.py` + `python/snapshot_ids.py` + `python/selfcheck_snapshot.py`，**56 断言实跑全绿**）/ Go（`go/snapshot.go` + `go/main.go` 人工审查 + bracket_check + go_sanity + go_crossref）

## 一、简介

Compose 的状态（快照系统）是一套**多版本并发控制（MVCC）**：每个 `StateObject` 持有一条 `StateRecord` 链表，每条记录带一个 `snapshotId`；读的时候按「当前快照能看到的最大 id」挑一条，写的时候**不动原记录而是链头插入一条新的**。

本 demo 复刻三件事：

1. `SnapshotIdSet` —— 一个为「高位密集、低位稀疏」优化的位集合；
2. `valid()` / `readable()` —— 可见性判据；
3. `MutableSnapshot.apply()` 里的 `innerApplyLocked` —— 碰撞检测与 `mergeRecords` 冲突合并。

## 二、原理

### 2.1 快照 id 从哪来

```kotlin
val nextSnapshotId = Snapshot.PreexistingSnapshotId.toSnapshotId() + 1   // = 2
private val globalSnapshot = GlobalSnapshot(snapshotId = nextSnapshotId.also { nextSnapshotId += 1 }, ...)
```

| 常量 | 值 | 含义 |
| --- | --- | --- |
| `INVALID_SNAPSHOT` | `0` | 保留 id，`valid()` 一律拒绝 |
| `PreexistingSnapshotId` | `1` | 新建状态对象的初始记录 id |
| 全局快照首个 id | `2` | 也来自 `nextSnapshotId` |

`StateRecord()` 的默认构造器用的是 **`currentSnapshot().snapshotId`**，所以「在全局下新建的对象」初始记录 id 是当前全局 id，而不是 1。

### 2.2 `SnapshotIdSet`：两个 Long + 一个有序数组

```kotlin
private val upperSet: Long,   // [lowerBound+64, lowerBound+127]
private val lowerSet: Long,   // [lowerBound,    lowerBound+63]
private val lowerBound: SnapshotId,
private val belowBound: SnapshotIdArray?   // 低于下界的，有序数组
```

| 操作 | 复杂度 |
| --- | --- |
| 窗口内 `get/set/clear` | **O(1)** |
| 下界以下 `get` | O(log N)（二分） |
| 下界以下 `set/clear` | O(N)（数组搬移） |
| 窗口之上 | 恒为 0 |

`set(id)` 越出窗口上沿时**整体下移窗口**：把原 `lowerSet` 的每一位移进 `belowBound`，`upperSet` 变成新的 `lowerSet`，下界抬到 `((id+1)/64)*64`。

整个类**不可变**：`set`/`clear` 返回新实例，**无变化时返回同一个实例**（本 demo A4/A7 断言到）。源码还刻意**不实现 `equals`**（注释：规范化代价太高，且快照系统用不上）。

### 2.3 可见性：三条件

```kotlin
private fun valid(currentSnapshot: SnapshotId, candidateSnapshot: SnapshotId, invalid: SnapshotIdSet) =
    candidateSnapshot != INVALID_SNAPSHOT && candidateSnapshot <= currentSnapshot && !invalid.get(candidateSnapshot)
```

`readable` 就是「在这条链上取**合法的、snapshotId 最大**的那条」；全部非法时返回 `null`（源码注释：全局被别的线程推进过时会发生，此时会重新读一次）。

`invalid` 的来源是 **取快照那一刻仍开着的其它快照**——它们还没提交，因此对本快照不可见：

```kotlin
// resetGlobalSnapshotLocked
val result = block(openSnapshots.clear(snapshotId))   // 去掉全局自己
```

### 2.4 apply：什么叫「碰撞」

`innerApplyLocked` 的注释把三条记录讲得很清楚：

| 记录 | 怎么求 |
| --- | --- |
| `applied` | `readable(first, snapshotId, invalid)` —— 本快照看到的那条 |
| `current` | `readable(first, nextId, invalidSnapshots)` —— 下一个快照会看到的那条 |
| `previous` | `readable(first, snapshotId, invalid ∪ {snapshotId} ∪ previousIds)` —— **假如本快照没改过**会看到的那条 |

判定：

```
previous.snapshotId == PreexistingSnapshotId  -> 直接采用（嵌套快照里新建的对象）
current === previous                          -> 无碰撞，最常见
否则                                          -> 问 mergeRecords(previous, current, applied)
                                                 返回 null  => SnapshotApplyResult.Failure
```

`StateRecord.mergeRecords` 的**默认实现返回 `null`**，也就是「任何碰撞都不可合并」。想让两个快照的写都能留下，必须自己实现 mutation policy（本 demo D10 用「求和」把 1 和 2 合并成 3）。

源码还点明：这套算法**不保证可串行化**（注释直接给了 arXiv 1412.2324 的链接，指出没有防 cross write）。

### 2.5 对象初始化记账

`notifyObjectsInitialized()` 之前，快照里**新建**的状态对象即使被改也不算「被改过」（不进 `modified`）；调用之后才记账。本 demo E1/E2 断言到这一区分。

## 三、对比

| 维度 | 传统 MVCC（数据库） | Compose 快照 |
| --- | --- | --- |
| 版本链 | undo log / 回滚段 | 每个对象自己的 `StateRecord` 链 |
| 可见性 | 事务 id + 活跃事务集 | `snapshotId ≤ 当前 && ∉ invalid` |
| 写冲突 | 锁或 abort | `mergeRecords`（默认 = 直接失败） |
| 隔离级别 | 可配置 | 不保证可串行化（源码注释） |
| 读副作用 | 无 | **会回调 `readObserver`**（composition 靠它订阅） |

## 四、环境

- Python 3.13（标准库）
- Go 1.21+（无本机工具链，Go 版只做人工审查与静态检查）

## 五、运行

```bash
cd python && python main.py                 # 快照隔离 / apply / SnapshotIdSet 窗口
cd python && python selfcheck_snapshot.py   # 56 项断言
```

## 六、关键代码

| 文件 | 对应源码 |
| --- | --- |
| `python/snapshot_ids.py:SnapshotIdSet` | `snapshots/SnapshotIdSet.kt:36-250` |
| `python/main.py:valid` | `snapshots/Snapshot.kt:2107` |
| `python/main.py:readable` | `snapshots/Snapshot.kt:2122` |
| `python/main.py:SnapshotSystem.apply` | `Snapshot.kt:1020 innerApplyLocked` |
| `python/main.py:SnapshotSystem.take_mutable_snapshot` | `Snapshot.kt:2048 / :1985` |

## 七、性能边界

- `SnapshotIdSet` 对**高 128 位**是 O(1)，正是快照 id 单调递增的利用点；id 一旦落到下界以下就退化成 O(log N)/O(N)。
- 全局快照的 `invalid` 是**去掉自己之后**的 `openSnapshots` 快照（不是引用），否则全局会看不到自己 id 的记录。
- 每次 `takeMutableSnapshot` / `apply` 都会让全局快照 id 前进一格 —— **长时间不 dispose 的快照会把 id 越推越远**，并让 `belowBound` 越来越长。
- `readable` 最坏要遍历整条记录链；链长等于「这个对象被写过多少次」。

## 八、坑

1. **`previous.snapshotId == PreexistingSnapshotId` 会跳过碰撞检测**——只有「初始记录 id 就是 1」的对象会走这条路；本 demo 复现时发现，若误把所有新建对象的初始 id 都设成 1，两个快照的写就都检测不出冲突了。
2. **`invalid` 必须去掉全局自己**，否则「全局看不到全局」。
3. **apply 成功后全局 id 要再前进一格**（大于合并记录的 id），否则合并出来的记录会被自己的 invalid 集合挡住。
4. `addRange(from, until)` 的 **`until` 是排他的**（源码就是一个个 `set`）。
5. **mergeRecords 返回 `previous` 或 `current` 时语义不同**：返回 `current` 表示「放弃本次修改」，该对象会从 `modified` 里被摘掉。
6. `SnapshotIdSet` **没有 `equals`**，两个内容相同的集合可能字段不同，不要靠相等判断。
7. `lowest()` 先看窗口里的两个 Long，**窗口非空时不会去看 `belowBound`**。
8. 不 dispose 的快照会 pin 住记录，源码注释明说是「难查的内存泄漏」。

## 九、参考资料（实际读过）

- `JetBrains/compose-multiplatform-core@master` —
  `compose/runtime/runtime/src/commonMain/kotlin/androidx/compose/runtime/snapshots/Snapshot.kt`（107 KB）、
  `.../snapshots/SnapshotIdSet.kt`、`.../snapshots/SnapshotStateList.kt`
  （经 `cdn.jsdelivr.net/gh/JetBrains/compose-multiplatform-core@master/...` 抓取）
- 源码注释引用的可串行化讨论：arXiv 1412.2324（`MutableSnapshot.apply` 上方注释）
