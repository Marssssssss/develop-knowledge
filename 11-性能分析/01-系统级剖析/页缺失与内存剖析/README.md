# 页缺失与内存剖析

## 简介

内存剖析绕不开三个"账本口径"——**RSS / PSS / USS**——和一类最便宜的事件——**缺页（page fault）**。搞错口径就会出现"十个进程各占 40 MB，加起来超过物理内存"的荒谬结论；搞错缺页类型就会把"缺页多"误判成"内存不够"。

关键概念：

- **缺页（page fault）**：进程访问**尚未建立映射**或**页不在内存**的地址时的陷阱。**minor（次缺页）**不需要磁盘 I/O（页已在 page cache、COW 复制、零页、或只需建页表项）；**major（主缺页）**需要磁盘 I/O
- **RSS**：常驻集大小。**共享页在每个进程里都被完整计入 → 会重复计数**
- **PSS**：比例集大小。**每一页按其被多少进程共享来均摊** → 跨进程求和才有意义
- **USS**：私有内存 = `Private_Clean + Private_Dirty`，只属于这一个进程
- **COW**：`fork()` 后父子共享只读页，任一方写入触发**次缺页**并复制
- **THP（透明大页）**：用 2 MB 页代替 4 KB 页，减少 TLB 与缺页次数，但**会让 PSS 计算不精确**

## 原理详解

### 1. 三种口径的定义与关系

`/proc/[pid]/smaps` 是**逐 VMA（虚拟内存区域）**的账本；`smaps_rollup` 是**全进程汇总**（去掉逐 VMA 部分，并额外多出 `Pss_Anon` / `Pss_File` / `Pss_Shmem` 三个字段）。

**PSS 的权威定义（kernel.org）**：

> 一个进程的 PSS 是它占有的内存页数，其中每一页都**按其被多少个进程共享来均分**。若某进程有 1000 页完全独享、另有 1000 页与**另一个**进程共享，则它的 PSS 为 **1500**。

由此得到三个口径的关系：

| 口径 | 公式 | 跨进程求和是否有意义 |
| --- | --- | --- |
| RSS | `Σ Rss` | ❌ 共享页重复计数，求和会超过物理内存 |
| PSS | `Σ Pss` | ✅ **正确**（共享页被均摊） |
| USS | `Σ (Private_Clean + Private_Dirty)` | ✅ 但漏掉共享的部分 |

**私有/共享的判定与 `MAP_SHARED` 无关**：内核的规则是——一页若**恰好被映射一次**则记"私有"，**被映射多次**则记"共享"（**同一进程内多次映射也算**）。THP 等大分配下语义略有不同：若大分配的**所有**页确定只映射在同一进程，则记私有。

### 2. smaps 字段

| 字段 | 含义 |
| --- | --- |
| `Size` | 该映射的大小（虚拟，不等于占用物理内存） |
| `KernelPageSize` / `MMUPageSize` | 支撑该 VMA 的最小内核页 / MMU 可能使用的最大页（如 hugetlb 场景二者可能不同） |
| `Rss` | 该映射中**当前驻留 RAM** 的量 |
| `Pss` | 该映射在本进程中的**比例份额** |
| `Pss_Dirty` | PSS 中的脏页部分（`Pss_Clean` 未单列，用 `Pss - Pss_Dirty` 算） |
| `Shared_Clean` / `Shared_Dirty` / `Private_Clean` / `Private_Dirty` | 按"共享/私有 × 干净/脏"切分的页量 |
| `Referenced` | 当前被标记为**已访问**的量 |
| `Anonymous` | 不属于任何文件的量。**即使映射关联了文件也可能含匿名页**：`MAP_PRIVATE` 下改过的页会被换成私有匿名副本 |
| `KSM` | KSM 合并页数（KSM 放置的零页不计入） |
| `LazyFree` | 被 `madvise(MADV_FREE)` 标记的量（在内存压力下、页干净时才释放） |
| `AnonHugePages` | 由 PMD 级大页映射且**非文件**的内存量（THP） |
| `ShmemPmdMapped` / `FilePmdMapped` | 大页映射的共享内存 / 文件后备内存 |
| `Shared_Hugetlb` / `Private_Hugetlb` | hugetlbfs 页。**出于历史原因不计入 Rss/Pss**，也不含在 `{Shared,Private}_{Clean,Dirty}` 里 |
| `Swap` | 本应为匿名却被换出的量；对 shmem 映射还包含底层 shmem 对象被换出的部分 |
| `SwapPss` | 比例 swap 份额；与 `Swap` 不同，**不**计入底层 shmem 对象被换出的页 |
| `Locked` | 是否被锁在内存中 |
| `THPeligible` | 该映射是否有资格分配自然对齐的 THP 页（1/0） |
| `ProtectionKey` | pkeys 关联的保护键（需内核与 CPU 都支持） |
| `VmFlags` | 两字母编码的内核标志（`rd/wr/ex/sh/gd/io/ht/sf/…`）。**不保证跨内核版本稳定**，用前要按具体版本确认 |

### 3. status / statm / meminfo

`/proc/[pid]/status` 的 `Vm*`：

| 字段 | 含义 |
| --- | --- |
| `VmPeak` / `VmSize` | 峰值 / 当前**虚拟**内存大小 |
| `VmHWM` | **峰值常驻集（high water mark）**——内存泄漏排查的关键字段 |
| `VmRSS` | `RssAnon + RssFile + RssShmem` |
| `VmSwap` | **匿名私有**数据的 swap 用量（**不含** shmem） |
| `VmData` / `VmStk` / `VmExe` / `VmLib` / `VmPTE` | 数据段 / 栈 / 文本段 / 共享库代码 / **页表**占用 |

`/proc/[pid]/statm` 是七个页数：`size resident shared trs lrs drs dt`，其中 **`trs`/`drs` 已被标记为"损坏"**（含义不符），`lrs`/`dt` 在现代内核上**恒为 0**——不要用。

> ⚠️ **SMP 注意**：为可伸缩性，RSS 相关信息是**异步**统计的，值可能不精确。要精确快照就自己扫 `smaps` 的页表（慢但准）。

`/proc/meminfo` 关键项：`MemTotal`、`MemFree`、**`MemAvailable`（不 swap 就能给新应用用的内存估算，由 MemFree + SReclaimable + 文件 LRU 和 zone 低水位算出，不是简单空闲量）**、`Buffers`、`Cached`（含 tmpfs/shmem，**不含** SwapCached）、`Active/Inactive`、`AnonPages`、`Mapped`、`Shmem`、`Slab = SReclaimable + SUnreclaim`、`KernelStack`、`PageTables`、`Dirty`、`Writeback`、`CommitLimit`（`vm.overcommit_memory=2` 时才有约束意义）、`Committed_AS`（**已分配**而非已使用：`malloc` 1 GB 只碰 300 MB 仍算 1 GB 提交）。

### 4. 缺页事件怎么量化

三种取数方式：

| 方式 | 命令/接口 | 说明 |
| --- | --- | --- |
| 累计计数 | `getrusage()` 的 `ru_minflt` / `ru_majflt` | 最省事，进程级累计 |
| 解析 `/proc/self/stat` | 第 10、12 字段（`min_flt`、`maj_flt`） | 脚本友好 |
| PMU 软件事件 | `PERF_TYPE_SOFTWARE` 的 `PAGE_FAULTS` / `PAGE_FAULTS_MIN` / `PAGE_FAULTS_MAJ` | man7 明确定义：MIN = **不需要磁盘 I/O**，MAJ = **需要磁盘 I/O** |

**次缺页不一定是问题**：零页映射、COW、`madvise(MADV_DONTNEED)` 后的再次触发都是次缺页，反而说明"页还在 page cache 里"。**要警惕的是主缺页**——那才是真的打到了磁盘。

### 5. 堆剖析（Massif 模型）

Valgrind 的 **Massif** 是"测**内存**而不是测时间"的剖析器，机制值得单独拎出来：

- Massif **默认只测堆**：`malloc` / `calloc` / `realloc` / `memalign` / `new` / `new[]`，**不直接测** `mmap` / `mremap` / `brk`，也不测代码段/数据段/BSS → 报告值可能**显著小于** `top`
- `--pages-as-heap=yes` 切换到**页级**分析：`mmap` 等分配的每一页都当成一个块，于是**代码段/数据段/BSS/栈都被计入**（此时不允许再开 `--stacks=yes`）
- 快照机制：起初**每次分配/释放都拍快照**，随运行时间**降频**；达到 `--max-snapshots`（默认 100）时**删掉一半**旧快照
- 快照分三类：**普通**（`:`）、**详细**（`@`，默认每第 10 个，`--detailed-freq` 控制）、**峰值**（`#`，最多一个）
- **峰值只在"释放之后"才记录**：从不释放就没有峰值记录；释放后又涨到新高但不再释放，报告的峰值会**偏低**（`--peak-inaccuracy` 默认只保证真实峰值 1% 以内）
- `--time-unit=i|ms|B`：默认按**已执行指令数**做横轴，对短程序几乎全是空白；改用 `--time-unit=B`（按堆操作字节数）跨机器最可复现
- 详细快照给出**分配树**：每行是一个**代码位置**（不是函数）贡献的字节数与占比，且满足不变量——某条目的大小 = 其子条目大小之和

## 环境准备

- 操作系统：Linux（`smaps` 需 `CONFIG_MMU`）；Valgrind Massif 需额外安装 valgrind
- 语言：Python 3.8+ / Go 1.21+ / C（GCC）
- **Python 与 Go 部分完全离线可跑**

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -pedantic page_fault_types.c -o page_fault_types
./page_fault_types        # 区分次缺页/主缺页;演示 COW、MADV_DONTNEED、MAP_POPULATE
```

### Python

```bash
python3 smaps_pss.py               # 解析内置 smaps 样例,算 RSS/PSS/USS 与 PSS 均摊
python3 smaps_pss.py /proc/self/smaps   # 解析真实 smaps
```

### Go

```bash
go run .        # Massif 式堆采样模型:降频快照、峰值规则、分配树聚合
```

## 关键代码片段

**PSS 均摊**是这一节的核心算术——共享页要按共享者数量分摊：

```python
def pss_of(rss_kb: int, shared_with: int) -> float:
    """shared_with = 该页一共被几个进程映射(含自己);1 => 独享"""
    return rss_kb / shared_with
```

**用两个时间点求缺页速率**（比累计值有用得多）：

```c
long minor = ru2.ru_minflt - ru1.ru_minflt;
long major = ru2.ru_majflt - ru1.ru_majflt;   /* major 才是真的打了磁盘 */
```

## 性能与边界

- `smaps` 遍历**逐页扫页表**，进程大的时候**很慢**（数百 ms 到数秒）；要汇总优先用 `smaps_rollup`
- `smaps`/`maps` 的读取**本质上有竞态**：只有**单次 `read`** 才保证一致；内核保证"地址不倒退（两块不重叠）"与"遍历期间始终存在的 vaddr 一定出现"
- Massif 会**大幅放慢**程序（尤其开 `--stacks=yes`）；`--pages-as-heap` 下调用栈更难读
- Massif 的子进程数据会与父进程**混在同一个输出文件**里（除非 `--massif-out-file` 用 `%p`）
- THP 场景下 PSS 可能**不精确**（内核可能用大分配内每页的平均映射次数做近似）

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 各进程 RSS 之和 > 物理内存 | RSS **重复计入共享页** | 改用 **PSS** 求和 |
| `VmRSS` 与 `top` 的 RES 对不上 | RSS 异步统计、可能不精确 | 需要精确值就扫 `smaps` |
| `statm` 的 `lrs`/`dt` 恒为 0、`trs`/`drs` 含义不对 | 这三个字段**已损坏/废弃** | 用 `status` 的 `Vm*` 或 `smaps` |
| 缺页很多但性能正常 | 大多是**次缺页**（COW/零页/page cache 命中） | 单独统计 `maj_flt` / `PAGE_FAULTS_MAJ` |
| 主缺页数偏高 | 真的在从磁盘取页 | 查 `io.pressure`、page cache 命中率、是否过度 `MADV_DONTNEED` |
| Massif 报告比 `top` 小很多 | 默认**不测** `mmap`/`brk` 与代码/数据/BSS 段 | 用 `--pages-as-heap=yes` |
| Massif 报的峰值偏低 | 峰值**只在释放后**记录，且默认 1% 精度 | 减小 `--peak-inaccuracy`（会更慢），或确保有释放动作 |
| 用完 `VmFlags` 后升级内核结果变了 | 该字段**不保证跨版本稳定** | 按目标内核版本确认语义 |
| `VmSwap` 比 swap 总量小 | `VmSwap` **不含** shmem 的 swap | 结合 `/proc/meminfo` 的 `Shmem` 看 |

## 参考资料（实际阅读过的权威来源）

- [The /proc Filesystem — Linux Kernel Documentation](https://docs.kernel.org/filesystems/proc.html) — `smaps` 全部字段与含义、**PSS 的准确定义与均摊示例**、私有/共享的判定规则与 THP 例外、`smaps_rollup` 的额外字段、`statm` 七个字段（含已损坏字段说明）、`status` 的 `Vm*` 全表（`VmHWM`/`VmRSS`/`VmSwap`）、SMP 异步统计警告、`meminfo` 关键项（`MemAvailable`/`CommitLimit` 公式/`Committed_AS`）、`VmFlags` 编码表
- [perf_event_open(2) — man7.org](https://man7.org/linux/man-pages/man2/perf_event_open.2.html) — `PERF_COUNT_SW_PAGE_FAULTS` / `_MIN`（无需磁盘 I/O）/ `_MAJ`（需要磁盘 I/O）的权威定义
- [Valgrind — Massif: a heap profiler](https://valgrind.org/docs/manual/ms-manual.html) — 采样机制与降频、三类快照、`--pages-as-heap`、`--time-unit`、峰值记录规则与 `--peak-inaccuracy`、分配树不变量与畸形栈回溯、全部局限性
- [PSI - Pressure Stall Information — Linux Kernel Documentation](https://docs.kernel.org/accounting/psi.html) — 内存压力的官方指标（与缺页/回收对照阅读）
