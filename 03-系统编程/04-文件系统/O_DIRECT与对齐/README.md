# O_DIRECT：绕过 page cache 的三道门槛

## 简介

数据库都有自己的缓冲池（InnoDB buffer pool、PostgreSQL shared_buffers）。
如果内核再缓存一份，同一份数据就在内存里待两遍 —— 这就是所谓的 **double buffering**。
`O_DIRECT` 就是给这类应用开的口子：**让 IO 直接走到用户态缓冲区，绕开 page cache**。

但手册对它的第一句描述就泼了冷水（open(2) 原文）：

> In general this will degrade performance, but it is useful in special situations,
> such as when applications do their own caching.

本 demo 用 **Python（32 项断言，可实跑）+ Go** 把 O_DIRECT 的对齐规则、历史口径变化、
以及几条容易踩出数据损坏的硬约束做成可执行模型。

## 原理详解

### 1. 三样东西都要对齐

open(2)：O_DIRECT 可能对**用户缓冲区的地址**、**IO 的长度**、**文件的偏移量**都施加对齐限制。
三样里任何一样不满足，都不算真的 direct IO。

而"对齐到多少"这件事，历史上变过三次：

| 代际 | 对齐口径 | 典型值 | 怎么知道 |
| --- | --- | --- | --- |
| Linux 2.4 | 文件系统块大小 | 4096 | 只能猜 |
| Linux 2.6.0 起 | 块设备**逻辑**块大小 | 512 | `ioctl(BLKSSZGET)` / `blockdev --getss` |
| Linux 6.1 起 | 内核告诉你 | 因 fs 而异 | `statx(STATX_DIOALIGN)` → `stx_dio_mem_align` / `stx_dio_offset_align` |

手册还点名了 XFS 的 `XFS_IOC_DIOINFO`，并说 **有 `STATX_DIOALIGN` 时应该优先用它**。
`statx(2)` 补充：两个对齐值**成对出现** —— `stx_dio_offset_align` 非 0 当且仅当
`stx_dio_mem_align` 非 0，两个都是 0 表示这个文件不支持 O_DIRECT。

### 2. 未对齐的两种命运

open(2)：*The handling of misaligned O_DIRECT I/Os also varies; they can either
fail with EINVAL or fall back to buffered I/O.*

后者是**最难查的性能坑**：你以为绕过了 page cache，实际上内核静默帮你走回缓冲路径，
double buffering 一点没少，而且**没有任何报错**。

### 3. O_DIRECT 不等于 O_SYNC

这是最常见的误解。open(2) 原文：O_DIRECT 会让 IO *make an effort to transfer data
synchronously*，但**不提供 O_SYNC 那种"数据与必要元数据都已传输"的保证**。
要真同步必须 `O_DIRECT | O_SYNC`。

| 组合 | 数据落地 | 元数据落地 |
| --- | --- | --- |
| `O_DIRECT` | 不保证 | 不保证 |
| `O_DIRECT \| O_DSYNC` | 保证（等价于每次 write 后 `fdatasync`） | 不保证 |
| `O_DIRECT \| O_SYNC` | 保证 | 保证 |

**隐藏坑**：glibc 头文件里 `O_SYNC = 04010000`、`O_DSYNC = 010000`，
**`O_SYNC` 天然包含 `O_DSYNC` 那一位**。所以写 `if (flags & O_SYNC)` 去判断"有没有开元数据同步"，
对只给了 `O_DSYNC` 的 fd 会**误判为真**。本 demo 用 `O_SYNC & ~O_DSYNC` 剥出真正的那一位。

### 4. 不要与 fork() 并发

open(2) 用了一整段警告：只要 O_DIRECT 的缓冲区是**私有映射**（堆内存、静态缓冲区、
`MAP_PRIVATE` 的 mmap 都算），就**绝不能**与 `fork()` 并发 —— 无论是异步 IO 还是别的线程提交的，
都必须在 `fork()` 前完成，否则**数据损坏与未定义行为**。

不受此限的三种情况：缓冲区来自 `shmat`、来自 `mmap(MAP_SHARED)`、或者被
`madvise(MADV_DONTFORK)` 标记过。

### 5. 不要混用

open(2)：应用应避免对同一文件（尤其是重叠字节区间）混用 O_DIRECT 与正常 IO；
**即使文件系统把一致性处理对了，总体吞吐也多半比只用一种更慢**。同理，
避免对同一文件同时用 `mmap` 和 direct IO。

demo 把它量化成"每次模式切换要写回脏页 + 作废页缓存再重读"，代价
`= 切换次数 × 2 × 重叠量`。注意**不重叠时没有这笔代价** —— 判据是"重叠"不是"混用"。

### 6. NFS 上的 O_DIRECT 只绕一半

open(2)：NFS 协议**不支持把这个标志传给服务端**，所以 O_DIRECT 只在**客户端**绕过 page cache，
服务端照样可能缓存。

## 对比

| | buffered IO | O_DIRECT |
| --- | --- | --- |
| 数据经过 | 用户缓冲 → page cache → 设备 | 用户缓冲 → 设备 |
| 内存占用 | 与 page cache 共享，自适应 | 应用自己的池，固定 |
| 重复缓存 | 有（double buffering） | 无 |
| 小随机读 | 快（readahead + cache 命中） | 慢（每次真读盘） |
| 大顺序扫描 | 可能污染 cache | 不污染 |
| 崩溃一致性 | 靠 `fsync` | 仍要靠 `fsync`（除非带 O_SYNC） |
| 对齐要求 | 无 | 地址 / 长度 / 偏移三样 |

## 环境

- Python 3.8+（自检零依赖）
- Go 1.20+（本机无工具链时走代码审查）

## 运行方式

```bash
cd python && python check.py     # 32 项断言
cd go && go run .
```

## 关键代码

对齐判定（Python 节选）：

```python
def check_io(fs, buf_addr, offset, length):
    open_direct(fs, O_DIRECT)
    mem, off = alignment_for(fs)
    if mem == 0:
        raise OSError("EINVAL: 该文件系统不支持 O_DIRECT")
    if buf_addr % mem or offset % off or length % off:
        if fs.misaligned == STRICT:
            raise OSError("EINVAL: 未对齐（要求 %d/%d）" % (mem, off))
        return FALLBACK          # 也可能静默退回 buffered I/O
    return "direct"
```

同步语义（注意 `O_SYNC_METADATA` 那一行）：

```python
O_SYNC_METADATA = O_SYNC & ~O_DSYNC   # O_SYNC 含 O_DSYNC 位，必须剥掉

def sync_guarantee(flags):
    return {
        "data": bool(flags & (O_SYNC | O_DSYNC)),
        "metadata": bool(flags & O_SYNC_METADATA),
    }
```

## 性能边界

- **O_DIRECT 不是性能开关，是"我自己管缓存"的声明**。读多命中高的负载开了会更慢。
- 单次 IO 越大越划算：对齐到 512 意味着**小于 512 的写根本不能走 direct**，
  小 IO 密集的负载会被迫退回 buffered 或被拆成读-改-写。
- 混用的代价随**切换次数**线性增长，与数据量无关 —— 交替模式比"先全 buffered 后全 direct"糟得多。
- `O_DIRECT` 省下的是内存与一次拷贝，**没有省下磁盘寻道**；顺序大块 IO 的收益主要来自
  "不污染 page cache"而非"更快"。

## 注意事项与常见坑

1. **别把 `O_DIRECT` 当同步用**，要持久化就 `|O_SYNC` 或显式 `fsync`。
2. **`flags & O_SYNC` 判不出元数据同步**（glibc 里 O_SYNC 含 O_DSYNC 位）。
3. **未对齐可能静默退回 buffered**，性能问题会伪装成"O_DIRECT 没效果"。
4. **别在私有映射缓冲区上边跑 O_DIRECT 边 fork()** —— 数据损坏，且不报错。
5. **别对同一文件混用 buffered 与 direct**，尤其重叠区间。
6. **别和 `mmap` 一起用**。
7. **文件系统可能根本不支持**：`open()` 直接 `EINVAL`（tmpfs 就是典型）。
8. **老内核会直接忽略这个标志**（手册：*Older Linux kernels simply ignore this flag*），
   所以"没报错"不等于"生效了"。
9. **NFS 上只绕客户端缓存**，别指望服务端也绕过。
10. **对齐值要现场查**：6.1+ 用 `statx(STATX_DIOALIGN)`，别硬编码 512 或 4096
    （手册明说"varies by filesystem and kernel version and might be absent entirely"）。

## 参考资料

以下均为本 demo 撰写时**实际读取**的资料：

- `open(2)` Linux man-pages 6.19：<https://man7.org/linux/man-pages/man2/open.2.html>
  （O_DIRECT 标志条目、NOTES 里的 O_DIRECT 整节：对齐限制、三种查询途径、
  2.4/2.6 对齐口径、fork 并发禁令、混用建议、NFS 行为、O_DSYNC/O_SYNC 定义）
- `statx(2)` Linux man-pages 6.19：<https://man7.org/linux/man-pages/man2/statx.2.html>
  （`stx_dio_mem_align` / `stx_dio_offset_align` 成对语义、`STATX_DIOALIGN` 自 6.1、
  支持面因文件系统而异）
- glibc `sysdeps/unix/sysv/linux/bits/fcntl-linux.h`：
  <https://raw.githubusercontent.com/bminor/glibc/master/sysdeps/unix/sysv/linux/bits/fcntl-linux.h>
  （`O_DIRECT = __O_DIRECT = 040000`、`O_DSYNC = __O_DSYNC = 010000`、`O_SYNC = 04010000`）
