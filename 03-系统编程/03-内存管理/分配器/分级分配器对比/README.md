# 分级分配器对比：jemalloc / tcmalloc / mimalloc

> 目录：`03-系统编程/03-内存管理/分配器/分级分配器对比/`
> 语言：Python（`python/sizeclass.py` + `python/main.py` + `python/selfcheck_alloc.py`，**65 断言实跑全绿**）/ Go（`go/sizeclass.go` + `go/main.go` 人工审查）

## 一、简介

三个现代分配器都靠「**尺寸分级(size class) + 分层缓存**」来避免全局锁，但分级的**档位表**、
**缓存层级**和**回收策略**各不相同。本 demo 把三者的**可直接核对的常量与公式**落成模型：

| | 分级单位 | 缓存层级 | 回收 / 归还 |
| --- | --- | --- | --- |
| jemalloc | quantum 16 B，每翻倍 4 档 | thread → arena（4×CPU 个）→ extent | dirty/muzzy 两段 **sigmoid 衰减** |
| tcmalloc | 60~80 档，8 B 或 16 B 对齐 | **per-CPU slab**（rseq）→ transfer cache → central free list → page heap | 溢出批量退回 middle-end |
| mimalloc | 73 档（12.5% 指数递增） | 线程本地 segment/arena → 分片空闲链表 | 页级归还 + 首类 heap |

## 二、原理

### 2.1 jemalloc 的档位递推

`jemalloc(3)` 的 Table 1（4 KiB 页 + 16 字节 quantum）可以用一条规则完整生成：

```
初始: [8]  ∪  quantum × {1..8}        -> 8,16,32,...,128
其后: spacing S 每轮产生 [5S,6S,7S,8S]，S 每次翻倍
```

- 于是**每个 binade（翻倍区间）恰好 4 个间隔**：`[1024,1280,1536,1792,2048]` 的相邻差恒为 `1024/4 = 256`；
- 内部碎片最坏值是 `(S-1)/5S`，随档位单调趋近 **20%** —— 实测 ≥160 B 的档位最坏碎片 `0.1999 < 20%`；
- 例外正是原文说的 *all but the smallest*：超过 20% 的只有 **16 / 32 / 48 / 64** 这四档
  （64 B 档最坏 `(64-32-1)/64 = 23.4%`）。

**small / large 的分界 = 4 × 页大小 = 16 KiB**：8 KiB 是 small 的最后一档，16 KiB 是 large 的第一档。

> ⚠️ **官方表格与递推规则冲突（记录不改正）**：按递推规则，spacing = 2 KiB 的那一组应包含
> 10 KiB / 12 KiB / 14 KiB / 16 KiB，但 Table 1 的 Small 段止于 8 KiB、Large 段直接从 16 KiB 起，
> 10~14 KiB 三档在表里查不到。本 demo 断言「递推规则能生成它们」且「表里没有它们」，不擅自补表。

### 2.2 jemalloc 的 arena 与衰减

- `opt.narenas` 默认 `4 × CPU`（单 CPU 为 1）；`opt.percpu_arena=percpu` 时 arena 数 = CPU 数；
  `=phycpu` 时一个**物理核**一个 arena（两个超线程共享，8 核 16 线程 → 4 个 arena）。
- `opt.dirty_decay_ms` 默认 **10 秒**，`0` = 立刻 purge，`-1` = 关闭；
  `opt.muzzy_decay_ms` 默认 **0（关闭）**。
- 原文称衰减是「**两端 purge 速率为 0 的 sigmoid 曲线**」。取 smoothstep 累积曲线
  `S(u) = 3u² − 2u³` 的导数建模，则速率 `6u(1-u)` 在 `u=0` 与 `u=1` 处均为 0、中点为峰值，
  且 `S(0.5) = 0.5`（**中点刚好 purge 掉一半**）。

### 2.3 tcmalloc 的 per-CPU 缓存

TCMalloc Design Doc 里最硬的一条公式：

```
静态最大容量(class c) = (start[c+1] - start[c]) / sizeof(ptr)
```

即**容量不是配置出来的，而是由 slab 布局里相邻两档数组起点的间距决定的**，
运行期容量只能在这个静态上限之下浮动。配套语义：

- 数组耗尽 → 从 middle-end **批量**补充；溢出 → **批量**退回；
- 某档耗尽且未到硬编码上限时，可以**从同 CPU 的其它档偷容量**（本 demo 断言：dst 有空间时总槽位**守恒**；
  dst 已到上限时 `give = 0`，src 的容量不会被白白扣掉）；
- 上限由 `MallocExtension::SetMaxPerCpuCacheSize` 控制 ⇒ **CPU 越多，整机可缓存的总内存越多**
  （本 demo：4 CPU 的预算 = 1 CPU 的 4 倍）；
- `MallocExtension::ReleaseCpuMemory` 清空指定 CPU 的缓存；
- 原文还提到实际缓存量平均约为上限的一半。

**8 B 对齐 vs 16 B 对齐**：`__STDCPP_DEFAULT_NEW_ALIGNMENT__ <= 8` 时用 8 字节对齐的档位，
于是 24 B / 40 B 不再被抬到 32 B / 48 B；否则 24 → 32、40 → 48。12 B 在两种口径下都落到 16 B。

### 2.4 mimalloc 的页与对象上限（`include/mimalloc/types.h` 实读）

```c
MI_ARENA_SLICE_SHIFT   = 13 + MI_SIZE_SHIFT          // 64 位: 16 -> 64 KiB
MI_SMALL_PAGE_SIZE     = MI_ARENA_MIN_OBJ_SIZE       // 64 KiB
MI_MEDIUM_PAGE_SIZE    = 8 * MI_SMALL_PAGE_SIZE      // 512 KiB ("=byte in the bchunk bitmap")
MI_LARGE_PAGE_SIZE     = MI_SIZE_SIZE * MI_MEDIUM_PAGE_SIZE  // 4 MiB ("=word in the bchunk bitmap")

MI_SMALL_MAX_OBJ_SIZE  = (MI_SMALL_PAGE_SIZE  - 4 KiB) / 6   // 10240 B = 10 KiB
MI_MEDIUM_MAX_OBJ_SIZE = (MI_MEDIUM_PAGE_SIZE - 4 KiB) / 6   // 86698 B ≈ 84 KiB
MI_LARGE_MAX_OBJ_SIZE  = MI_LARGE_PAGE_SIZE / 8              // 512 KiB

MI_BIN_HUGE = 73; MI_BIN_FULL = 74; MI_BIN_COUNT = 75
MI_MAX_ALIGN_SIZE = 16
```

> ⚠️ 源码注释只说 size class 按 **12.5% 指数递增**，没给档位表。
> 按纯 `v += v/8` 从 8 B 推到 512 KiB 需要 **98 步 > 73**，说明 `_mi_bin` 还混了小尺寸的按字递推段。
> **只记录，不反推档位表。**

## 三、对比

| 维度 | jemalloc | tcmalloc | mimalloc |
| --- | --- | --- | --- |
| 档位间隔 | 每翻倍 4 档（≈20% 碎片） | 60~80 档，8/16 B 对齐 | 73 档，12.5% 指数 |
| 最小粒度 | quantum 16 B（另有 8 B 档） | 16 B（或 8 B） | 16 B |
| 缓存定位 | arena（可配 per-CPU / per-phyCPU） | **per-CPU slab + rseq** | 线程本地 segment |
| 容量来源 | 无静态容量概念 | **= 数组间距 / 指针大小** | 页内分片空闲链表 |
| 归还节奏 | dirty→muzzy→clean 两段 sigmoid | 溢出批量退回 | 页级整块归还 |
| 默认并发度 | 4 × CPU 个 arena | 每逻辑 CPU 一段 | 每线程堆 |

## 四、环境

- Python ≥ 3.9 / Go 1.20+（Go 无工具链时走人工审查）
- 无第三方依赖

## 五、运行

```bash
cd python && python main.py             # 打印三家的档位与容量
cd python && python selfcheck_alloc.py  # 65 条断言
cd go     && go run .                   # Go 镜像（需 Go 工具链）
```

## 六、关键代码

```python
# 静态容量完全由 slab 布局决定（tcmalloc）
for i, c in enumerate(cls):
    nxt = self.starts[cls[i + 1]] if i + 1 < len(cls) else self.slab_end
    self.static_cap[c] = (nxt - self.starts[c]) // PTR

# 偷容量：受 src 余量与 dst 静态余量双重约束
def steal(self, src, dst, slots):
    give = min(slots, self.cap[src], self.static_cap[dst] - self.cap[dst])
    ...
```

## 七、性能与边界

- **jemalloc**：`oversize_threshold` 默认 8 MiB，超过它的请求走专用 arena，避免大块污染小块；
  `lg_extent_max_active_fit` 默认 6 ⇒ 复用 dirty extent 时最大比例 **2⁶ = 64**。
- **tcmalloc**：超过 `kMaxSize` 的对象**不进 front/middle 两层缓存**，直接由 backend 分配并向上取整到
  TCMalloc 页大小；per-CPU 模式依赖 **restartable sequences**（`rseq(2)`）实现无锁。
- **mimalloc**：`MI_PADDING` 在默认构建（`MI_SECURE < 3` 且非 DEBUG）下为 **0**，
  即没有 8 字节的 canary/delta 尾部；开启后每个块多 `sizeof(mi_padding_t)` = 8 B。
- **跨三家**：档位越密，内部碎片越小，但每档一份元数据 ⇒ 档位数是「碎片 vs 元数据」的折中。

## 八、坑

1. **「每翻倍 4 档」是 4 个*间隔*不是 4 个*档位*** —— `[1024,2048]` 闭区间里有 5 个值、4 个间隔。
2. **内部碎片的「20%」只在 ≥160 B 的档位成立**，最小 4 档最高到 46.9%（16 B 档 `(16-8-1)/16`）。
3. **jemalloc Table 1 在 8~16 KiB 区间与递推规则不一致**，不要拿递推结果去"补全"官方表。
4. **mimalloc 的 12.5% 只是比例**，反推档位数会得到 98 ≠ 73，别当公式用。
5. **per-CPU 静态容量是布局的副产品**：改动 slab 里任一档的起点，后面所有档的容量都会变。
6. **`phycpu` 做的是 `cpu / threads_per_core`**，不检测超线程是否真的开启（原文明确说明）。

## 九、参考资料（实际读过）

- jemalloc(3) man page（Table 1 尺寸档位、narenas / percpu_arena / dirty·muzzy decay /
  oversize_threshold / lg_extent_max_active_fit）
  — <https://jemalloc.net/jemalloc.3.html>
- TCMalloc Design Doc（size class、per-CPU 静态容量、容量窃取、MaxPerCpuCacheSize、
  ReleaseCpuMemory、rseq、transfer cache / central free list / span）
  — <https://google.github.io/tcmalloc/design.html>
- mimalloc `include/mimalloc/types.h`（dev3：arena slice / 三级页尺寸 / 对象上限 / MI_BIN_*）
  — <https://raw.githubusercontent.com/microsoft/mimalloc/dev3/include/mimalloc/types.h>
- mimalloc 官网（v1/v2/v3 设计演进与技术报告入口）
  — <https://microsoft.github.io/mimalloc/>
