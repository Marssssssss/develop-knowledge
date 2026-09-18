# Compose Multiplatform

Jetpack Compose 的跨平台移植:把 Compose 的**运行时 + UI + 自绘渲染**整体带到
Kotlin Multiplatform 上,于是「共享 UI」而不是只共享逻辑。代价是运行时里多了一套
必须自行维护的结构——**SlotTable**,它把组合过程中产生的树与状态存成两个扁平数组。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [重组与SlotTable/](./重组与SlotTable/) | 重组机制与 SlotTable(groups 数组 + slots gap buffer + anchor) |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 340 | `重组与SlotTable/` | `groups` 整数数组(每 group 占 `Group_Fields_Size` 个元素)与 `slots` 数组的 **gap buffer**;`dataAnchor` 在写模式下是**锚点而非下标**;`groupSize` / `skipToGroupEnd` 跳过原语;四类 group(Restart / Replaceable / Movable / Node) | Kotlin / Python |

## 待研究

- [ ] 重组作用域的最小化重算(推导式状态与 `derivedStateOf` 的失效传播)
- [ ] Compose 的 `Applier` 与平台 UI 树的桥接(Android View / iOS UIKit / 桌面)
- [ ] Compose Multiplatform 在 iOS 上的渲染后端(Skia 与 Metal 桥)
- [ ] 快照系统 `SnapshotStateObserver` 与全局快照的读写隔离
