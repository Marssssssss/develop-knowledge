# Archetype（原型）存储

## 简介

- **archetype 存储**是 ECS 三大存储路线之一（另两种是 sparse set、bitset，见同目录 `../ECS/`）。Unity Entities 官方文档对它的定义是：**archetype 是「同一世界中拥有相同组件类型组合的实体」的唯一标识**——所有带 `A+B` 的实体共享一个 archetype，带 `A+B+C` 的是另一个，`A+Z` 又是另一个。
- 相同 archetype 的实体与组件存放在叫做 **chunk** 的均匀内存块里，**每块 16KiB**，能放多少实体取决于该 archetype 的组件数量与大小（Unity 文档原话）。
- 它解决的是**查询**问题：要找所有带 `A+B` 的实体，只需找出组件集合包含 `A+B` 的 archetype 列表，而不必逐个实体做成员测试；代价是**增删组件要把实体整个搬到另一个 archetype 的 chunk**（所谓 structural change）。

## 原理详解

### 内存布局：chunk 内每组件一个数组

```text
archetype {Position, Velocity} 的一个 chunk（16KiB）:

  ids      : [ e7 | e3 | e9 | ... | -- | -- ]     ← entity id 数组
  Position : [ P7 | P3 | P9 | ... | -- | -- ]     ← 每组件一个数组
  Velocity : [ V7 | V3 | V9 | ... | -- | -- ]
                ↑                     ↑
              count=3 之前紧密排布    capacity 之后是空闲槽

  capacity = 16KiB / (entity id 大小 + Σ 组件大小)
```

- 数组**紧密排布**：第 i 个实体就在下标 i；新实体放第一个空位。
- 删除时把 **chunk 内最后一个实体搬来填空**（swap-remove），而不是跨 chunk 搬运——这一点决定了"删一个实体"是 O(1) 而不是 O(chunk)。
- chunk 满了才新建 chunk；chunk 内最后一个实体被移走就释放该 chunk。

### 增删组件 = 换 archetype + 搬走全部组件

add/remove 组件会改变实体的 archetype，Unity 必须把它搬到另一个 chunk（没有合适的就新建）。离场时同样按"最后实体填空 / 空则释放"处理。文档明确：**设置普通组件的值不是 structural change**（不用搬），但**设置 shared component 的值**是。

### 为什么查询快

查询 = 找出「组件集合 ⊇ 查询集合」的 archetype → 遍历它们的 chunk。实体的组件组合种类在游戏运行早期就趋于稳定，所以**查询的 archetype 列表可以缓存**，只有当世界里出现新的 archetype 时才需要重算（本 demo 用 `arch_version` 单调计数实现这一失效逻辑）。

## 对比 / 选型

| 维度 | Archetype（本 demo） | Sparse set（见 `../ECS/`） |
| --- | --- | --- |
| 迭代 | 纯连续数组，SoA/SIMD 友好 | 有间接寻址（sparse → dense） |
| 查询 | 命中 archetype 列表，近乎 O(命中数) | 遍历最小集合 + 成员测试 |
| 增删组件 | **搬走该实体全部组件**（本例 3 组件 = 3 次写入） | O(1) swap-remove |
| 内存 | 组合多时碎成很多小 chunk | sparse 数组按最大 entity id 预留 |
| 结构变更线程 | 只能主线程，产生 sync point | 同样需要同步，但代价小得多 |

## 环境准备

- Python 3.8+（标准库，零依赖）
- Go 1.21+（零依赖）
- 无平台依赖，纯内存模拟

## 运行方式

```bash
python3 python/archetype.py     # 27 条断言
cd go && go run archetype.go
```

## 关键代码片段

Python 版的核心就是「搬移」这一对操作（`_place` / `_unplace`）：

```python
def _place(self, eid: int, arch: Archetype) -> None:
    ch = arch.free_chunk()                 # 有空位就用，没有就新建 chunk
    idx = ch.count
    ch.ids[idx] = eid
    for c, arr in ch.arrays.items():
        arr[idx] = self.comps[eid][c]      # 每个组件都写一遍 = 搬移代价
        self.copies += 1
    ch.count += 1
    self.loc[eid] = (arch.key, ch, idx)

def _unplace(self, eid: int) -> None:
    _key, ch, idx = self.loc.pop(eid)
    last, moved = ch.count - 1, ch.ids[ch.count - 1]
    if moved != eid:                       # swap-remove：块内最后实体填空
        ch.ids[idx] = moved
        for c, arr in ch.arrays.items():
            arr[idx] = arr[last]
        self.loc[moved] = (ch.key, ch, idx)  # 回写被搬移者的定位
    ch.count -= 1
```

Go 版同构（`place` / `unplace`），用 `map[string]float64` 表示组件集合、`*chunk` 直接存在 `loc` 里避免 chunk 下标失效。

## 性能与边界

- `capacity = 16384 / (4 + Σ|组件|)`。本 demo 的 `{Position, Velocity}` 实体 20 字节 → **819 个/块**；组件越多容量越小（`{P,V,H}` 24 字节 → 682）。
- 增删组件的代价随**组件数量**线性增长，与实体总数无关；sparse set 路线则是常数。这就是为什么高频切换 tag/buff 的场景（每帧大量增删组件）不适合 archetype 存储。
- 查询复杂度 ≈ O(命中实体数)，与实体总数无关——这是 archetype 的核心优势。
- 结构变更只能在主线程做，且会产生 **sync point**（等待所有已调度 job 完成），Unity 官方建议用 `EntityCommandBuffer` 把一帧内的多次变更合并成一个 sync point。

## 注意事项与常见坑

- **swap-remove 发生在 chunk 内部**：本 demo 断言了「删掉 chunk0 的首个实体后，填坑的是 chunk0 的最后一个实体」，不会因为 chunk1 有实体就跨块搬运。跨块搬运是常见的错误实现。
- **chunk 下标会失效**：销毁空 chunk 后，若用 `archetype.chunks[i]` 数字下标定位，其它实体的记录会整体错位。本 demo 直接在定位里持有 chunk 对象引用规避。
- **被搬移者必须回写定位**：只搬数组不回写 `loc[moved].index`，下一次删它就会删错位置（demo 断言 `loc[filler].index == 0`）。
- **archetype 是"组合"而非"顺序"**：`{Position, Velocity}` 与 `{Velocity, Position}` 是同一个 archetype（demo 断言 `len(archetypes) == 1`）。
- **查询缓存的失效条件只有"出现新 archetype"**：往已有 archetype 里加实体不需要重算缓存（demo 断言 recompute 次数不变）。
- **不要把 archetype 数量当成实体数量**：组合爆炸时会出现大量只装几个实体的 chunk，内存碎片化严重（Unity 的 Archetypes window 就是用来看 allocated vs unused 的）。

## 参考资料（实际阅读过的权威来源）

- [Unity — Archetypes concepts（Entities 1.0.16）](https://docs.unity3d.com/Packages/com.unity.entities@1.0/manual/concepts-archetypes.html) — archetype 定义、chunk 16KiB、每组件一个数组 + entity id 数组、紧密排布、最后实体填空、满则新建/空则销毁、查询可缓存。
- [Unity — Structural changes concepts（Entities 1.0.16）](https://docs.unity3d.com/Packages/com.unity.entities@1.0/manual/concepts-structural-changes.html) — 哪些操作算 structural change、为何只能主线程、sync point 与 EntityCommandBuffer、设置普通组件值不算结构变更而设置 shared component 算。
- [Sander Mertens — ECS FAQ](https://github.com/SanderMertens/ecs-faq) — ECS 三种存储路线（archetype / sparse set / bitset）的分类与取舍（同目录 `../ECS/` 已引）。
