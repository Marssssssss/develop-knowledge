# mmap / brk — 虚拟内存接口层（程序 break 与匿名映射）

## 简介

glibc 的 `malloc` 底层只有两个"从内核要内存"的原语：**`brk`/`sbrk`**（抬高"程序 break"）与 **`mmap`**（新建一段独立映射）。二者的语义差别决定了整个分配器的形态——为什么小对象从堆顶切、大对象单独 `mmap`、`free` 之后内存为什么常常**不还给操作系统**。

- **程序 break**（program break）= 未初始化数据段（`.bss`）之后的第一处地址；抬高它 = 向内核申请内存，降低它 = 归还。
- **`mmap`** = 在进程地址空间里新建一段映射（匿名或文件后端），可以独立 `munmap`，与堆互不影响。
- 关键取舍：`brk` 管理的堆是**一段连续区间，只能从顶端收缩**；`mmap` 区域**可独立释放**，但每次映射都有内核开销与页表成本。

本 demo 用 5 组实验把两套原语的**文档级语义**逐条钉死，并量化 `malloc` 在两者之间的**阈值策略**。

## 原理详解

### 1. `brk` 的系统调用语义与 glibc 包装语义**不同**

| 接口 | 成功 | 失败 |
| --- | --- | --- |
| Linux `brk(2)` **系统调用** | 返回**新的** program break | 返回**当前的** break |
| glibc `brk()` **包装函数** | 返回 `0` | 返回 `-1`，`errno = ENOMEM` |
| glibc `sbrk(inc)` **库函数** | 返回**旧的** break（若 break 抬高，该值即新内存起点） | 返回 `(void *) -1`，`errno = ENOMEM` |

`brk()` 返回 `0`/`-1` 是 glibc 包装"多做了一步"（比较新 break 是否等于 `addr`）的结果；内核本身返回的是 break 值。`sbrk()` 在 Linux 上**不是系统调用**，而是库函数，内部调用 `brk` 并记账以返回旧值。调用 `sbrk(0)` 是查询当前 break 的标准手法。

**break 是字节粒度的**：`sbrk(64)` 就让 break 精确前进 64 字节，内核到实际触及页时才分配物理页（按需分页）。

### 2. `mmap` 的参数约束（手册明文）

- `length` **必须 > 0**，否则 `EINVAL`。
- **文件映射的 `offset` 必须是 `sysconf(_SC_PAGE_SIZE)` 的整数倍**，否则 `EINVAL`。巨页映射更严：`offset` 必须是底层巨页大小的整数倍。
- `flags` 中 **`MAP_PRIVATE` / `MAP_SHARED` 必须恰好有一个**（`MAP_SHARED_VALIDATE` 亦计入），否则 `flags` 无效 → `EINVAL`。
- `addr = NULL`：内核自选（页对齐）地址，最可移植；`addr != NULL` 且不带 `MAP_FIXED` 时它只是**提示**。
- 失败返回 **`MAP_FAILED`，即 `(void *) -1`**，并置 `errno`。

### 3. `MAP_ANONYMOUS` / `MAP_FIXED` / `MAP_FIXED_NOREPLACE`

- `MAP_ANONYMOUS`：无文件后端，**内容初始化为 0**；`fd` 被忽略（可移植程序应传 `-1`），`offset` 应为 0。
- `MAP_PRIVATE`：写时复制，改动对其他进程不可见、不写回文件；`MAP_SHARED`：改动对其他映射者可见并写回文件。
- `MAP_FIXED`：**精确**放在 `addr`，若与既有映射重叠则**重叠部分的旧映射被丢弃**（其余部分保留）；地址不可用则失败。手册明确警告：唯一安全用法是该地址范围此前已由别的映射预留。
- `MAP_FIXED_NOREPLACE`（Linux 4.17+）：同样强制地址，但**绝不覆盖**，冲突时 `EEXIST`。

### 4. `munmap` 的 addr / length 是**不对称**的

- `addr` **必须是页大小的整数倍**（否则 `EINVAL`）；`length` 不必。
- 覆盖到的**整页**都会被卸载（部分页不会只卸一半）。
- 范围内没有任何已映射页**不算错误**。
- 关闭文件描述符**不会**解除映射。

### 5. `malloc` 在两者之间的阈值：128 KiB 且**动态调整**

`malloc` 正常从堆（`sbrk`）分配；**大于 `MMAP_THRESHOLD` 且无法从空闲链表满足**的请求改用**私有匿名 `mmap`**。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `M_MMAP_THRESHOLD` | `128*1024` | 上限 `DEFAULT_MMAP_THRESHOLD_MAX`：32 位 `512*1024`；64 位 `4*1024*1024*sizeof(long)` = 32 MiB |
| `M_TRIM_THRESHOLD` | `128*1024` | 堆顶连续空闲达到该值才 `sbrk` 归还；**设为 `-1` 完全禁用修剪** |
| `M_TOP_PAD` | `128*1024` | `sbrk` 时的填充量，**总是向上取整到页边界** |
| `M_MMAP_MAX` | `65536` | `0` 表示禁用 mmap 服务大请求 |

**动态阈值**（易被忽略的要点）：初始 128 KiB；当**被释放**的块 > 当前阈值且 ≤ `DEFAULT_MMAP_THRESHOLD_MAX` 时，阈值**上调到该块大小**；同时 trim 阈值被动态调整为**动态阈值的 2 倍**。而**只要**用 `mallopt()` 设过 `M_TRIM_THRESHOLD` / `M_TOP_PAD` / `M_MMAP_THRESHOLD` / `M_MMAP_MAX` 中任意一个，**动态调整即被禁用**。

```text
阈值: 128 KiB --free(200 KiB)--> 200 KiB --free(1 MiB)--> 1 MiB --free(64 MiB)--> 1 MiB(超上限不动)
trim: 256 KiB                     400 KiB                 2 MiB
```

### 6. `free` 并不等于"还给操作系统"

`free()` 只是把块标记为"可被应用复用"；从内核视角内存**仍属于本进程**。只有堆顶（紧邻未映射内存的那一段）足够大时，才会有一部分被 `munmap`/`sbrk` 归还。这解释了 `/proc/self/statm` 里 **VSZ 远大于 RSS** 的常态：地址空间是"保留"，物理页按需落下。

## 环境准备

- Python 3.10+（模型版，跨平台可跑）
- Go 1.21+（模型版，跨平台可跑）
- C：任意 Linux + `gcc`/`clang` + glibc ≥ 2.33（`mallinfo2`）；模型版不需要

## 运行方式

```bash
python3 python/main.py      # 63 个断言,跨平台
go run go/main.go           # 同一模型的 Go 版

# C 版直接调用真实系统调用(仅 Linux)
cc -O2 -Wall -Wextra -D_GNU_SOURCE c/mmap_brk.c -o mmap_brk && ./mmap_brk
```

## 关键代码片段

### 只丢弃**重叠部分**，其余映射必须保留

```python
def _discard(self, start, length):
    lo, hi = start, start + length
    for m in list(self.maps):
        m_lo, m_hi = m["start"], m["start"] + m["length"]
        if m_lo >= hi or lo >= m_hi:
            continue
        self.unmapped_spans.append((max(lo, m_lo), min(hi, m_hi)))
        self.maps.remove(m)
        if m_lo < lo:                       # 保留左段
            keep = lo - m_lo
            self.maps.append({**m, "length": keep, "data": m["data"][:keep]})
        if hi < m_hi:                       # 保留右段
            off = hi - m_lo
            self.maps.append({**m, "start": hi, "length": m_hi - hi,
                              "data": m["data"][off:]})
```

### 动态 mmap 阈值（`mallopt(3)` 原文的直译）

```python
def on_free(self, block_size):
    if not self.dynamic:
        return "frozen"
    if block_size > self.mmap_threshold and block_size <= DEFAULT_MMAP_THRESHOLD_MAX:
        self.mmap_threshold = block_size
        self.trim_threshold = 2 * self.mmap_threshold
        return "raise"
    return "keep"
```

### C：区分"系统调用返回新 break"与"glibc 包装返回 0/-1"

```c
void *b0 = sbrk(0);          /* 查询当前 break —— increment 0 */
void *p  = sbrk(64);         /* 成功返回**旧的** break,即新内存起点 */
check("新 break 前进了 64 字节", sbrk(0) == (char *)b0 + 64);
check("brk() 成功返回 0", brk(b0) == 0);
errno = 0;
check("brk() 到堆起点以下失败", brk((char *)b0 - 4096 * 1024) == -1);
check("失败时 errno=ENOMEM", errno == ENOMEM);
```

## 性能与边界

| 维度 | `brk` 堆 | `mmap` 区域 |
| --- | --- | --- |
| 分配开销 | 极低（只移动一个指针） | 一次系统调用 + 页表/VMA 建立 |
| 释放粒度 | **只能从顶端整体收缩** | 任意页范围可独立 `munmap` |
| 碎片 | 中间空洞不可还给 OS | 无堆内碎片问题 |
| 映射数上限 | 无 | `vm.max_map_count` 限制（`ENOMEM` 的一种来源） |
| 与 `RLIMIT_DATA` | 受限制 | Linux 4.7 起**同样**受限制（此前不受） |

**动态阈值的副作用**：一次 200 KiB 的释放会把阈值抬到 200 KiB，此后的 150 KiB 请求就不再单独 `mmap`（回到堆）。这把"单次大块的历史"变成了后续分配的长期策略——既是性能优化，也是**内存滞留**的来源。

## 注意事项与常见坑

1. **`brk` 的返回语义有两套**：读内核代码/`strace` 时看到的是"返回 break"，读 glibc 文档看到的是 `0`/`-1`。本 demo 的模型显式区分了 `sys_brk` 与 `glibc_brk` 两个方法，避免混用。
2. **`sbrk` 返回的是"旧"值**：把返回指针当"新分配的起始地址"恰好也对，但把返回指针当"新 break"就错了（差一个 increment）。
3. **`MAP_FIXED` 只丢重叠部分**：初版模型按"整条映射"丢弃，导致"范围外的页不受影响"断言失败——**手册的措辞是 `the overlapped part`，不是整条映射**。这类"粒度写粗了"的错误不会报错，只会让后续断言莫名失败。
4. **`MAP_FIXED_NOREPLACE` 不能靠"先查再建"来模拟**：手册强调它的价值在于**原子地**（相对其他线程）尝试，两步走存在 TOCTOU 窗口。
5. **`MAP_ANONYMOUS` 不保证"立刻占用物理内存"**：内容是零语义，但页表项往往是零页只读共享，首次写入才真正落页。用 RSS 观察时不要期待"映射即占用"。
6. **`length=0` 与 `length` 向上取整**：`length` 必须 > 0，但实际占用的地址范围会向上取整到页；`8000` → 占 2 页。`munmap` 的 `length` 反过来**不必**对齐。
7. **不要把 `MAP_FAILED` 当 `NULL` 判断**：`MAP_FAILED` 是 `(void *) -1`，`if (p)` 判空完全拦不住失败。
8. **`mallopt` 一设就冻结动态阈值**：线上服务里给 `M_TRIM_THRESHOLD` 设一个值，会顺带永久关闭 mmap 阈值的动态调整——两条策略是耦合的。

## 参考资料（实际联网阅读过）

- [brk(2) — Linux man-pages 6.19](https://man7.org/linux/man-pages/man2/brk.2.html) — 程序 break 定义；`brk`/`sbrk` 返回值；NOTES 中"Linux 上 `sbrk()` 用 `brk()` 实现并记账"以及"C library/kernel differences"两段（本 demo demo1 的直接依据）。
- [mmap(2) — Linux man-pages 6.19](https://man7.org/linux/man-pages/man2/mmap.2.html) — `offset must be a multiple of the page size as returned by sysconf(_SC_PAGE_SIZE)`；`MAP_ANONYMOUS` 内容清零；`MAP_PRIVATE`/`MAP_SHARED` 必须恰好一个；`MAP_FIXED` "the overlapped part of the existing mapping(s) will be discarded"；`MAP_FIXED_NOREPLACE` 与 `EEXIST`；`MAP_FAILED` 即 `(void *)-1`；`munmap` 的 addr 页对齐/length 不要求/无映射不算错；关闭 fd 不解除映射。
- [malloc(3) — Linux man-pages 6.19](https://man7.org/linux/man-pages/man3/malloc.3.html) — NOTES 中"大于 `MMAP_THRESHOLD` 的块用私有匿名 `mmap(2)`"、`MMAP_THRESHOLD` 默认 128 kB、Linux 4.7 起 `RLIMIT_DATA` 同样约束 mmap 分配、以及"检测到锁竞争时创建额外 arena"。
- [mallopt(3) — Linux man-pages 6.19](https://man7.org/linux/man-pages/man3/mallopt.3.html) — `M_MMAP_THRESHOLD`/`M_TRIM_THRESHOLD`/`M_TOP_PAD`/`M_MMAP_MAX`/`M_ARENA_MAX` 的默认值与**动态 mmap 阈值**规则（含"trim 阈值调整为动态阈值 2 倍"与"设置任一参数即禁用动态调整"）。
- [proc_pid_statm(5) — Linux man-pages 6.19](https://man7.org/linux/man-pages/man5/proc_pid_statm.5.html) — `size`/`resident`/`shared` 等字段的页数口径，用于区分 VSZ 与 RSS。
- [madvise(2) — Linux man-pages 6.19](https://man7.org/linux/man-pages/man2/madvise.2.html) — `MADV_DONTNEED` 的语义（主动放弃页面，后续访问重新读到零页/文件内容）。
