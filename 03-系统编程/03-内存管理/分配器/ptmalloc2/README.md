# ptmalloc2 — glibc malloc 的 chunk / bins / tcache / arena

## 简介

`ptmalloc2` 是 glibc 自 2.3 起使用的分配器（源自 Doug Lea 的 `dlmalloc`，由 Wolfram Gloger 改造为多线程版）。它不只是一个"用空闲链表找块"的分配器，而是**四层结构的组合**：

1. **chunk**：`malloc` 返回的内存块，带 16 字节头部与低 3 位标志。
2. **bins**：`fastbin` / `unsorted` / `smallbin` / `largebin` 四类空闲链表。
3. **tcache**（glibc 2.26+）：**每线程无锁缓存**，`malloc`/`free` 的第一站。
4. **arena**：主分配区（`sbrk` 堆）+ 由 `mmap` 建立的附加分配区，每区一把锁。

本 demo 把 glibc 源码里的**常量公式**（`request2size`、`bin_index`、`csize2tidx`）逐条复刻并断言，再把 `malloc`/`free` 的**查找顺序**用可观测的步骤日志还原出来。

## 原理详解

### 1. chunk 布局：16 字节头 + 低 3 位标志

```text
使用中:   +0  prev_size (前一块空闲时有效)   +8  size | P | M | A
          +16 ...应用数据...
空闲中:   +0  prev_size                      +8  size | P | M | A
          +16 fd            +24 bk          +32 fd_nextsize  +40 bk_nextsize
          ...应用数据(尾部一个字存放 size 的副本,低 3 位清零)...
```

| 标志 | 值 | 含义 |
| --- | --- | --- |
| `PREV_INUSE` | `0x01` | 前一块**不可被合并**（在应用手里，或在 fastbin/tcache 里） |
| `IS_MMAPPED` | `0x02` | 该块由**单独一次 `mmap`** 服务，不属于任何 heap |
| `NON_MAIN_ARENA` | `0x04` | 来自非主 arena（heap 地址可由块地址反推） |

低 3 位可用于标志，是因为所有 chunk 都是 8 字节的倍数。`PREV_INUSE` 的实际含义是"前一块不是合并候选"，**不等于**"前一块正在被应用使用"。

`mchunkptr` 的语义有个反直觉之处：它指向**前一个 chunk 的最后一个字**（即 `prev_size` 字段），所以 `chunk->prev_size` 只有在 `PREV_INUSE == 0` 时才有效。

### 2. 尺寸换算（x86-64 原式）

```c
#define MIN_CHUNK_SIZE  (offsetof(struct malloc_chunk, fd_nextsize))   /* 32 */
#define MINSIZE         ((MIN_CHUNK_SIZE + MALLOC_ALIGN_MASK) & ~MALLOC_ALIGN_MASK)
#define request2size(req)                                                       \
  (((req) + SIZE_SZ + MALLOC_ALIGN_MASK < MINSIZE) ? MINSIZE                    \
   : ((req) + SIZE_SZ + MALLOC_ALIGN_MASK) & ~MALLOC_ALIGN_MASK)
```

关键数值：`SIZE_SZ=8`、`MALLOC_ALIGNMENT=16`、`CHUNK_HDR_SZ=16`、`MINSIZE=32`。注意 `request2size(64) == 80`、`request2size(1000) == 1008` —— **不是**简单的"向上取整到 16 的倍数"，还要先加上 `SIZE_SZ`（`prev_size` 复用）。

### 3. bins：128 个桶的分层设计

| 类别 | 尺寸范围 | 结构 | 特点 |
| --- | --- | --- | --- |
| **fastbin** | < `MAX_FAST_SIZE`（64 位为 160） | 单链表数组（按 chunk size 精确索引） | **不合并**，访问无需加锁（原子操作） |
| **unsorted** | 任意 | 双向链表（就是第 1 号普通 bin） | `free` 的默认落点；"给一次被快速复用的机会" |
| **smallbin** | < `MIN_LARGE_SIZE` = 1024 | 双向链表，每 bin 单一尺寸 | 入桶前先合并；地址相邻的两个 chunk 不会同时在 smallbin |
| **largebin** | ≥ 1024 | 双向链表 + `fd_nextsize` 尺寸序链 | 一个 bin 含多个尺寸，取"最合适"的并可能切分 |

`bin_index` 的两条路径（64 位）：

```c
#define smallbin_index(sz)   (((sz) >> 4) + SMALLBIN_CORRECTION)      /* CORR = 0 */
#define largebin_index_64(sz)                                                  \
  (((((sz) >> 6) <= 48) ?  48 + ((sz) >> 6) :                                  \
    (((sz) >> 9) <= 20) ?  91 + ((sz) >> 9) :                                  \
    (((sz) >> 12) <= 10) ? 110 + ((sz) >> 12) :                                \
    (((sz) >> 15) <= 4) ?  119 + ((sz) >> 15) :                                \
    (((sz) >> 18) <= 2) ?  124 + ((sz) >> 18) : 126))
```

于是 `bin_index(1008) == 63`、`bin_index(1024) == 64`，在 `MIN_LARGE_SIZE` 处恰好连续；largebin 按 64 / 512 / 4096 / 32768 / 262144 / "剩余全部" 逐级放宽。

### 4. tcache：每线程无锁缓存（glibc 2.26+）

- **每线程**一份，访问**不需要锁 arena**。
- 结构是"数组 + 单链表"，但链表指针指向 **payload（用户区）**而不是 chunk 头。
- 每个 bin 装**单一尺寸**；数组按 chunk size 间接索引：

```c
#define tidx2csize(idx)  (((size_t) idx) * MALLOC_ALIGNMENT + MINSIZE)
#define csize2tidx(x)    (((x) - MINSIZE) / MALLOC_ALIGNMENT)
#define usize2tidx(x)    csize2tidx (checked_request2size (x))
```

- 每 bin 数量受 `tcache_count` 限制；**关键语义：只做精确尺寸匹配** —— 对应 bin 为空时**不会**退而用更大的 chunk（那会造成内部碎片），而是回落到普通 `malloc` 路径去锁 arena。

**版本差异**（读源码才发现，容易被老资料误导）：

| 常量 | glibc 2.35 | glibc master |
| --- | --- | --- |
| `TCACHE_MAX_BINS` | `64` | `64 + 12 = 76`（新增 12 个 large bin，覆盖到 4 MiB chunk） |
| `TCACHE_FILL_COUNT` | `7` | `16` |

### 5. arena：主区用 `brk`，附加区用 `mmap`

主 arena 对应进程初始堆（`sbrk` 扩展）；检测到锁竞争后由 `mmap` 创建附加 arena 与 heap，**数量上限 = 8 × CPU 核数**（`M_ARENA_MAX` 可覆盖）。每个 arena 一把锁；但 fastbin 的访问可用原子操作完成，不必加锁。线程用 TLS 记住自己上次用的 arena。

### 6. `malloc` 与 `free` 的逐步骤顺序

```text
malloc: ① tcache(仅精确匹配) → ② >= 阈值则 mmap 直取 → ③ fastbin
        → ④ smallbin → ⑤ 请求"大"则把所有 fastbin 清进 unsorted(边清边合并)
        → ⑥ 扫 unsorted 并归位到 small/large bin(源码里唯一做归位的地方)
        → ⑦ 搜索 large bin(可能切分) → ⑧ 若 fastbin 仍有残留则合并后重试 ⑥⑦
        → ⑨ 切分 top chunk

free:   ① tcache 有空位就放进去 → ② 足够小则进 fastbin
        → ③ 是 mmap 块则 munmap → ④ 与相邻空闲块合并
        → ⑤ 放入 unsorted(除非已成为 top) → ⑥ 足够大则清 fastbin + 尝试修剪堆
```

`free` 的一个重要事实：**"释放"并不把内存还给操作系统**。`free` 只是把块标成"应用可复用"；从内核视角这块内存仍属于本进程。只有堆顶（紧邻未映射内存的那一段）足够大时才可能被 `munmap`/`sbrk` 归还。

## 环境准备

- Python 3.10+（模型版，跨平台可跑）
- Go 1.21+（模型版，跨平台可跑）

## 运行方式

```bash
python3 python/main.py    # 83 个断言,跨平台
go run ./go               # 同一模型的 Go 版
```

## 关键代码片段

### chunk 尺寸与 tcache 索引（对应源码宏）

```python
def request2size(req):
    padded = req + SIZE_SZ + MALLOC_ALIGN_MASK
    return MINSIZE if padded < MINSIZE else padded & ~MALLOC_ALIGN_MASK

def csize2tidx(x):  return (x - MINSIZE) // MALLOC_ALIGNMENT
def tidx2csize(idx): return idx * MALLOC_ALIGNMENT + MINSIZE
```

### 查找顺序的可观测日志（Go 版同一顺序）

```go
a.step("check-mmap-threshold")
if nb >= a.mmapThreshold { a.step("mmap-direct"); return mmapped }
if slots := a.tcache[csize2tidx(nb)]; len(slots) > 0 { a.step("tcache-hit"); ... }
a.step("tcache-miss-exact-only")
if nb < maxFastSize && len(a.fastbins[nb]) > 0 { a.step("fastbin-hit"); ... }
if inSmallbinRange(nb) { if lst := a.smallbins[smallbinIndex(nb)]; len(lst) > 0 { ... } }
if !inSmallbinRange(nb) { a.flushFastbinsToUnsorted(); a.step("large:flush-fastbins-to-unsorted") }
```

### `free` 的"不还给 OS"

```python
def in_arena_bytes(self):
    """从内核视角仍属于本进程的堆字节数:in-use + free + top。"""
    return sum(c.size for c in self.chunks) + self.top_size
```

## 性能与边界

| 维度 | 数值/结论 |
| --- | --- |
| 单次分配元数据 | 16 字节头（`prev_size` 与前一块复用），最小块 32 字节 |
| 小对象分配 | tcache 命中即无锁返回（常数时间，通常 1~2 次内存访问） |
| `bin_index` 代价 | 纯位运算，无查表、无循环 |
| 碎片来源 | fastbin 不合并、tcache 精确匹配、arena 每区独立（跨区不能复用） |
| 大对象 | ≥ 128 KiB 走 `mmap`，`free` 即 `munmap`，天然无碎片 |
| 可扩展性 | arena 上限 8×CPU；超过后仍会在锁上排队 |

## 注意事项与常见坑

1. **`request2size` 不是"向上取整到 16"**：要先加 `SIZE_SZ`。把 `malloc(64)` 的 chunk size 当成 64（实际 80）会让所有以尺寸为键的断言（fastbin / tcache idx）全部错位 —— 本 demo 开发期正是这样挂了 3 处断言。
2. **`prev_size` 是"借用"的**：块在使用中时这 8 字节属于**前一块**的数据区，读它是未定义行为。
3. **`PREV_INUSE` 语义容易读反**：它表示"前一块不可合并"，fastbin / tcache 里的块也是置位的。判断"能否合并"要同时看这一位与目标块自身的状态。
4. **tcache 不做尺寸放宽**：这是它和 `unsorted` / `largebin` 的本质区别；实现里若写成"找不到精确的就用一个更大的"，会凭空造出内部碎片，且断言往往还能过（因为它只是"更浪费"而不是"更错"）。
5. **`bin_index` 的文档注释与实际公式有 "slop"**：源码注释自己写着 `There is actually a little bit of slop in the numbers in bin_index for the sake of speed`。断言应直接对公式求值，不要照抄注释里的区间描述。
6. **版本差异必须标注**：`TCACHE_FILL_COUNT` 在 2.35 是 7、在 master 是 16，`TCACHE_MAX_BINS` 从 64 变 76。任何"每 bin 最多 7 个"的说法都要注明 glibc 版本。
7. **`free` 之后 RSS 不下降是正常的**：想让内存真的回去，要么靠堆顶修剪（`M_TRIM_THRESHOLD`，默认 128 KiB），要么 `malloc_trim(0)`，要么把大块配成 `mmap` 路径。
8. **教学简化已标明**：本模型把"物理相邻合并"简化为"地址序链表的相邻合并"，用块地址序模拟 heap 布局；真实 ptmalloc2 通过 `prev_size` 反向定位前块、通过 chunk 尾部 size 副本做完整性校验，本模型不含这些。

## 参考资料（实际联网阅读过）

- [MallocInternals — glibc 官方 wiki](https://sourceware.org/glibc/wiki/MallocInternals) — chunk 布局与 `prev_size`/`size` 复用、三个标志位、最小 chunk `4*sizeof(void*)`、bins 四分类与 `fd_nextsize`、tcache（payload 链表 / `tcache_count` / 仅精确匹配）、arena 与 heap（main 用 `sbrk`、附加用 `mmap`、上限 8×CPU）、以及 `malloc`/`free`/`realloc` 的逐步骤清单。
- [malloc.c — glibc master](https://raw.githubusercontent.com/bminor/glibc/master/malloc/malloc.c) — `request2size` / `MINSIZE` / `MIN_CHUNK_SIZE` / `CHUNK_HDR_SZ` / `smallbin_index` / `largebin_index_64` / `tidx2csize` / `csize2tidx` 原式；`TCACHE_SMALL_BINS=64`、`TCACHE_LARGE_BINS=12`、`TCACHE_FILL_COUNT=16`、`MAX_TCACHE_COUNT=UINT16_MAX`。
- [malloc.c — glibc release/2.35](https://raw.githubusercontent.com/bminor/glibc/release/2.35/master/malloc/malloc.c) — `TCACHE_MAX_BINS 64` 与 `TCACHE_FILL_COUNT 7`，用于给出**版本差异对照**。
- [mallopt(3) — Linux man-pages 6.19](https://man7.org/linux/man-pages/man3/mallopt.3.html) — `M_MMAP_THRESHOLD` 128 KiB 与动态调整、`M_TRIM_THRESHOLD`（`-1` 禁用修剪）、`M_TOP_PAD`、`M_MMAP_MAX` 65536、`M_ARENA_MAX` 与 `M_ARENA_TEST` 的关系。
- [malloc(3) — Linux man-pages 6.19](https://man7.org/linux/man-pages/man3/malloc.3.html) — NOTES 中"检测到锁竞争后创建额外 arena，每个 arena 由 `brk(2)` 或 `mmap(2)` 建立并配自己的互斥量"；以及 `MMAP_THRESHOLD` 默认 128 kB。
