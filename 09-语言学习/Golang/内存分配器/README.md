# Go 运行时内存分配器：size class / small object / tiny allocator

## 简介

Go 的堆分配器是一个**三级缓存 + 尺寸类（size class）**的 TCMalloc 派生设计：几乎所有小对象分配都在
per-P 的 `mcache` 上完成，**不加锁**；`mcache` 缺货才向 per-size-class 的 `mcentral` 要一个 span，
`mcentral` 再缺货才向全局 `mheap` 要页。

本 demo 把这条链路上**可程序化验证**的部分全部还原：官方 68 级 `class_to_size` 表、两级快速映射
`size_to_class8` / `size_to_class128`、`roundupsize`、以及 `mcache` 的 **tiny allocator**。

关键概念：

| 概念 | 一句话 |
| --- | --- |
| size class | 68 个固定档位（8 B ~ 32 KB），请求向上取整到最近档位 |
| span | 一段连续页（多为 8 KB），只服务**一个** size class，切成等长槽位 |
| mcache | per-P 的 span 缓存，分配路径**无锁**；含 tiny allocator |
| mcentral | per-size-class 的中心空闲表，跨 P 共享，需加锁 |
| mheap | 全局页堆，大对象直接从这里按页取 |
| tiny allocator | < 16 B 且**不含指针**的对象合并进同一个 16 B 块 |

历史背景：Go 1.0 起就是这套结构；`size_to_class128` 的 128 字节粒度、`maxTinySize = 16` 的取值
在 `mksizeclasses.go` 中以「最小化最坏浪费」为目标离线求解得出。

## 原理详解

### 1. 请求 → 槽位：roundupsize

`runtime/msize.go` 的主路径（本 demo 只覆盖 `noscan`，即 `typ == nil` 或无指针类型）：

1. `reqSize = size`；若对象**含指针**且小于 64 B，`mallocgc` 会先预留
   `gc.MallocHeaderSize` 字节的对象头（此偏移定义在 `internal/runtime/gc`，本 demo 不建模）。
2. 若 `reqSize <= maxSmallSize - MallocHeaderSize`（32760）：查表取最小不小于它的档位。
3. 否则：`reqSize += pageSize-1; reqSize &^= pageSize-1`，即**向上取整到 8 KB 页**。

注意第 3 步**不是**「取最近的 class」——大对象不落在任何 class 上，这正对应 `mheap` 路径。

### 2. 两级快速映射：为什么是 8 和 128

| 区域 | 粒度 | 表长 | 理由 |
| --- | --- | --- | --- |
| `size <= 1024-8` | 8 B | `size_to_class8` = 129 项 | 小对象尺寸密集，8 B 粒度浪费可控 |
| `1024 < size <= 32768` | 128 B | `size_to_class128` = 249 项 | 大对象再细粒度查表收益低、浪费大 |

两张表都由 `class_to_size` 反推（本 demo 用同一算法重建并与源码数组逐项比对），
查询是 O(1) 数组索引，这正是「分配得快」的一半原因。

### 3. span 布局：objects 与 tail waste

一个 span 只服务一个 class，切成 `span_bytes / class_size` 个槽位，余数为 `tail waste`：

```text
class 5 (48 B), span = 8192 B
+------+------+------+ ... +------+------+--------+
| 48 B | 48 B | 48 B |     | 48 B | 48 B | 32 B   |  <- tail waste
+------+------+------+ ... +------+------+--------+
 170 个槽位（8192/48 = 170.67）        += 8192
```

官方注释表同时给出 `max waste`（列 6）：**请求恰好是「上一档 + 1」时**该 class 的最坏浪费。
本 demo 用 `1 - (前一类尺寸+1) × objects / span` 复算了全部 67 行，最大是 class 1 的 **87.50%**
（1 B 请求塞进 8 B 槽），最小 3.37%（class 50）。

### 4. tiny allocator

`malloc.go: mallocgcTiny` 的规则，逐条照抄：

- 条件：`size < 16` **且**对象不含指针（`noscan`）。含指针的对象**必须**独占槽位——否则 GC
  无法单独扫描/回收其中一个子对象。
- 只有一个 16 B 块（`mcache.tiny` + `tinyoffset`），多个请求共用；块**整体**归 class 2 span 的一个槽。
- 对齐：`size%8==0` 时按 8 对齐；否则 `size%4==0` 按 4；否则 `size%2==0` 按 2；**奇数不额外对齐**。
- 新开块时 `tinyoffset = size`（而不是 0），所以块内子对象**可能不是 8 字节对齐**。
- 源码注释给出的收益：**最坏 2 倍浪费、最好 8 倍收益**；JSON benchmark 上分配次数 −12%、堆大小 −20%。

```text
tiny block（16 B，class 2 的一个槽）
+--------+-----+-----+--------+---------------+
| 5 B    | 3 B | 6 B | (2 B)  |  未使用 8 B    |   <- 3 次请求共用
+--------+-----+-----+--------+---------------+
0        5     8    14       16
```

### 5. 三级缓存与补货计数

```text
goroutine → mcache（per-P，无锁）
              │ span 槽位耗尽
              ▼
            mcentral（per-size-class，加锁；partial/full 两套随 GC 轮换）
              │ 无可用 span
              ▼
            mheap（全局，按页）
```

`spanClass = size class × 2`：每个 class 有 scan / noscan 两份，因为 GC 只需扫描含指针的槽位。
所以一共 `68 × 2 = 136` 个 `spanClass`，对应 136 个 `mcentral`。

## 对比 / 选型

| 维度 | Go（TCMalloc 派生） | jemalloc / tcmalloc | 朴素 malloc |
| --- | --- | --- | --- |
| 尺寸档位 | 68 档，8 B ~ 32 KB | 数十档 | 无（或按 2 的幂） |
| 缓存层级 | mcache(P) → mcentral(class) → mheap | tcache(thread) → arena | 无 |
| 分配路径加锁 | 无（小对象） | 无 | 每次 |
| 大对象 | 按页向 mheap 取 | 按 chunk / huge | 按 span |
| 碎片控制 | 离线求解 max waste | 离线统计 | 依赖 free list |

## 环境准备

- 操作系统：任意（模型是纯 Python，与平台无关）
- Python：3.8+（本 demo 实测 3.13.12）
- Go：仅 `go/` 目录需要，1.24+ 才能复现表数据（`class_to_size` 会随版本微调）

## 运行方式

### Python（含全部断言，推荐先跑这个）

```bash
cd python
python3 main.py            # 61 项断言，退出码 0 表示全绿
python3 inject_tables.py <path-to-sizeclasses.go>   # 可选：从官方源码重新注入/复核表数据
```

### Go

```bash
cd go
go run .                   # main.go + tables.go，同一批断言
```

## 关键代码片段

`python/go_sizeclasses.py` —— 两级映射的构造（与 `mksizeclasses.go` 同逻辑）：

```python
def build_size_to_class(table_size, div, base):
    """由 class_to_size 反推「字节数 → class」表。"""
    out = []
    for i in range(table_size // div + 1):
        want = base + i * div
        cls = 1
        while CLASS_TO_SIZE[cls] < want:   # 最小不小于 want 的档位
            cls += 1
        out.append(cls)
    return out
```

`python/main.py` —— tiny allocator 的对齐与换块（照抄 `mallocgcTiny`）：

```python
off = self.tinyoffset
if size % 8 == 0:      off = (off + 7) & ~7
elif size % 4 == 0:    off = (off + 3) & ~3
elif size % 2 == 0:    off = (off + 1) & ~1     # 奇数不额外对齐
if off + size <= TINY_SIZE and self.tiny != 0:
    self.tinyoffset = off + size                 # 块内继续切
    return self.tiny + off, TINY_SIZE
# 否则新开 16 B 块，并把 tinyoffset 置为 size（不是 0）
```

## 性能与边界

- **分配复杂度**：小对象 O(1)（数组索引 + 指针加法），无锁；大对象 O(1) + 可能的 `mheap` 加锁。
- **最坏浪费**：class 1 = 87.50%（`OFFICIAL_MAX_WASTE_BP[0] == 8750`），即 1 B 请求占 8 B。
- **典型放大**：`roundupsize(100) == 112`，10 万个 100 B 对象实占 **11.2 MB**（本 demo H2 断言）。
- **tiny 上限**：仅 `< 16 B` 且不含指针；`size == 16` 已不进 tiny（必须能被显式释放）。
- **平台差异**：`class_to_size` 与 `size_to_class128` 由 `mksizeclasses.go` 生成，**跨版本可能变化**；
  32 位平台上 12 B 对象的 tiny 对齐有额外 8 字节对齐分支（issue 37262）。
- **本 demo 未覆盖**：含指针小对象的 `mallocHeaderSize` 偏移（常量在 `internal/runtime/gc` 内，
  本轮未能取到该文件原文，故未断言具体数值）；span 的 GC 复用（`sweepgen` 与 swept/unswept 两套角色）。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 「我只要 100 字节」却占 112 字节 | 尺寸类向上取整 | 按 class 尺寸设计结构体，或让热对象 ≤ 96 B |
| 大量 1~8 字节的小分配内存暴涨 | class 1 的最坏浪费 87.5% | 尽量让短字符串/小结构体进 tiny，或复用一个对象 |
| 断言 `a1 == a2` 却发现地址不同 | **同块 ≠ 同地址**，tiny 块内按 offset 切 | 断言应比较「是否落在 `[tiny, tiny+16)` 区间」 |
| 用 `zip()` 比较两张等长表 | `zip` **静默截断**，长度不一致查不出来 | 先断言 `len(a) == len(b)`，再逐项比 |
| 手工抄 `class_to_size` 出错 | 68 项 × 6 列，肉眼无法核 | 用 `inject_tables.py` 从官方源码程序化注入（本 demo 的做法） |

## 参考资料（实际阅读过的权威来源）

- [Go 1.24.0 `src/runtime/sizeclasses.go`](https://raw.githubusercontent.com/golang/go/go1.24.0/src/runtime/sizeclasses.go) — 68 级 `class_to_size`、`class_to_allocnpages`、`size_to_class8/128` 与含 67 行的官方注释表（本 demo 全部表数据来源）。
- [Go 1.24.0 `src/runtime/msize.go`](https://raw.githubusercontent.com/golang/go/go1.24.0/src/runtime/msize.go) — `roundupsize` 主路径与 `maxSmallSize - MallocHeaderSize` 判据。
- [Go 1.24.0 `src/runtime/malloc.go`](https://raw.githubusercontent.com/golang/go/go1.24.0/src/runtime/malloc.go) — `mallocgc` 分派、`mallocgcTiny` 的对齐与换块规则、tiny allocator 的收益注释。
- [Go 1.24.0 `src/runtime/mcache.go`](https://raw.githubusercontent.com/golang/go/go1.24.0/src/runtime/mcache.go) — "Per-thread (in Go, per-P) cache for small objects. No locking needed" 与 `tiny/tinyoffset/tinyAllocs` 字段语义。
- [Go 1.24.0 `src/runtime/mcentral.go`](https://raw.githubusercontent.com/golang/go/go1.24.0/src/runtime/mcentral.go) — `mcentral` 的 `partial[2]/full[2]` 与 swept/unswept 随 GC 轮换角色。
