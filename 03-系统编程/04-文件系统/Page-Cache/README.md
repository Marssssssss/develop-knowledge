# Page Cache 与 writeback

## 简介

**Page cache** 是 Linux 内核的核心数据缓存:所有文件 I/O(无论 `read(2)`/`write(2)` 还是 `mmap(2)`)都先经过这一层。**writeback** 是把脏页(dirty pages)异步写回磁盘的机制,延迟 I/O 以提高吞吐。

- **关键概念清单**
  - **Page cache**(页缓存):内核以 4KB(`PAGE_SIZE`)为单位缓存文件数据;映射到 `address_space` 结构(`struct address_space` → `struct inode`)
  - **Dirty 页**:cache 页与磁盘不一致,需 writeback;按 dirty 原因分 `PG_dirty`(普通脏)、`PG_dirty`(因 mmap 写)
  - **写回策略**:内核线程 `pdflush`(老)→ `per-BDI flusher`(Linux 2.6.32+,`bdi_writeback` 内核线程)
  - **`sync_file_range(2)`**:对单文件做精细化 writeback 控制(不刷 metadata);`SYNC_FILE_RANGE_WRITE` 启动异步写回
  - **`posix_fadvise(2)`**:给内核访问模式提示(`POSIX_FADV_RANDOM` 关闭预读;`POSIX_FADV_SEQUENTIAL` 加倍预读;`POSIX_FADV_WILLNEED` 主动预读;`POSIX_FADV_DONTNEED` 丢弃 cache 页)
  - **`madvise(2)`**:对 **mmap 区域**的内存建议(`MADV_DONTNEED` 释放 + 重读从文件;`MADV_RANDOM`/`MADV_SEQUENTIAL`/`MADV_WILLNEED` 类同 fadvise)
- **历史背景**:Linux 2.6 之前内核单 `pdflush` 线程处理所有 dirty 页;2.6.32 后改为 **per-block-device** flusher;Linux 4.0 加入 writeback throttling 机制(`/sys/block/<dev>/queue/nr_requests`、`vm.dirty_*`)

## 原理详解

### 1. 数据流:`write()` → page cache → disk

```
进程        write(fd, buf, len)        内核 page cache                 磁盘
 │                                       │                              │
 │ write 系统调用                          │                              │
 │─────────────────────────────────────>│                              │
 │                                       │ find_or_create_page()       │
 │                                       │ 把 [pos, pos+len] 对应页标 PG_dirty
 │                                       │  → 放入 inode->i_dirty_list │
 │ write 立即返回(只是脏页)              │                              │
 │<─────────────────────────────────────│                              │
 │                                       │                              │
 │                                       │ 后续由以下任一触发 writeback:│
 │                                       │  (a) 内存不足 (vm.dirty_*)
 │                                       │  (b) dirty 页超过阈值          │
 │                                       │  (c) 用户调 sync_file_range  │
 │                                       │  (d) 用户调 fsync             │
 │                                       │  (e) `dirty_expire_centisecs` │
 │                                       │      默认 30 秒               │
 │                                       │                              │
 │                                       │ bdi_writeback 线程启动 ──→ submit_bio → 块设备驱动
 │                                       │                              │ ──写入磁盘
```

### 2. writeback 触发条件(linux 4.0+)

| 触发源 | 阈值/频率 | 控制参数 |
|--------|----------|----------|
| 内存压力(分配新页时) | `vm.dirty_background_ratio` / `_bytes` 默认 10% / 0 | `/proc/sys/vm/dirty_*` |
| 显式 dirty 比例上限 | `vm.dirty_ratio` / `_bytes` 默认 20% / 0 | 同上;write 在 dirty 比例到 `dirty_ratio` 时同步阻塞,直到 writeback |
| 时间过期 | `vm.dirty_expire_centisecs` 默认 3000(30s) | 旧 dirty 页的"过期"时间 |
| 时间间隔 | `vm.dirty_writeback_centisecs` 默认 500(5s) | 内核线程周期性唤醒 |
| 显式 | `fsync(2)`/`fdatasync(2)`/`sync_file_range(2)` | 用户态 |
| 卸载 | `umount` 时强制 writeback | 内核自动 |

### 3. 核心 API

#### `posix_fadvise(2)`

```c
int posix_fadvise(int fd, off_t offset, off_t len, int advice);
```

| advice | 含义 |
|--------|------|
| `POSIX_FADV_NORMAL` | 默认;**readahead window = backing device 默认** |
| `POSIX_FADV_SEQUENTIAL` | 顺序访问;**readaad window ×2** |
| `POSIX_FADV_RANDOM` | 随机访问;**readahead = 0**(关闭预读) |
| `POSIX_FADV_WILLNEED` | 即将访问;**非阻塞预读**(典型 few MB) |
| `POSIX_FADV_DONTNEED` | 不再访问;**释放 cache 页**(offset + len 必须页对齐,否则部分页忽略) |
| `POSIX_FADV_NOREUSE` | Linux 6.3+ 信号;page replacement 可忽略访问 |

注意:`POSIX_FADV_DONTNEED` "Implementation may attempt to write back dirty pages in the specified region, but this is not guaranteed. Any unwritten dirty pages will not be freed. If the application wishes to ensure that dirty pages will be released, it should call `fsync(2)` or `fdatasync(2)` first."

#### `sync_file_range(2)`

```c
int sync_file_range(int fd, off_t offset, off_t nbytes, unsigned int flags);
```

| flag | 含义 |
|------|------|
| `SYNC_FILE_RANGE_WAIT_BEFORE` | 等待之前已 submit 的页写完 |
| `SYNC_FILE_RANGE_WRITE` | 启动 dirty 页 write-out(异步) |
| `SYNC_FILE_RANGE_WAIT_AFTER` | 等待刚 submit 的页写完 |

**危险 sysctl**:官方 man page 原文 "This system call is **extremely dangerous** and should not be used in portable programs"——**不写 metadata**、不保证 overwrite、不支持 CoW(btrfs/ZFS)。

#### `madvise(2)`

针对 **mmap 区域** 的版本,advice 值类似 `posix_fadvise`:
- `MADV_NORMAL` / `MADV_RANDOM` / `MADV_SEQUENTIAL` / `MADV_WILLNEED` / `MADV_DONTNEED`
- 区别:`MADV_DONTNEED` 释放 cache 后,**再次访问会从底层文件重读**;`MADV_REMOVE`(Linux 2.6.16+)**直接戳洞**(`fallocate(FALLOC_FL_PUNCH_HOLE)`),后续访问得到 0

### 4. page cache 在内核中的表示

```
struct address_space {
    struct inode           *host;        // 关联的 inode
    struct radix_tree_root page_tree;   // 所有 cache 页(以 index 为键)
    struct list_head       i_dirty_list; // 脏页链表
    ...
};

struct page {
    unsigned long       flags;          // PG_dirty, PG_locked, PG_uptodate, ...
    struct address_space *mapping;      // 反向指针
    pgoff_t             index;          // 文件内 offset / PAGE_SIZE
    void                *virtual;       // 虚拟地址(for highmem pages 才有意义)
    ...
};
```

**脏页链表**:每个 inode 通过 `i_dirty_list` 链接所有脏页;`balance_dirty_pages()` 周期扫描。

### 5. ASCII 时序:writeback 流程

```
[User]                    [fs/file.c]              [mm/page-writeback.c]        [Block Layer]
   │                          │                            │                          │
   │ write(fd, buf, 4K)       │                            │                          │
   │────────────────────────>│                            │                          │
   │                          │ __generic_file_write_iter  │                          │
   │                          │  find_or_create_page()      │                          │
   │                          │  set_page_dirty()           │                          │
   │                          │  add to i_dirty_list        │                          │
   │                          │───────────────────────────>│                          │
   │                          │                            │ wb_check_start()         │
   │                          │                            │  if dirty_pages > dirty_background_ratio
   │                          │                            │  wakeup bdi_writeback thread
   │                          │                            │                            │
   │                          │                            │ (异步)                     │
   │                          │                            │ bdi_writeback_thread       │
   │                          │                            │  writeback_sb_inodes()     │
   │                          │                            │  -> writepage              │
   │                          │                            │  -> submit_bio ───────────>│
   │                          │                            │                            │ 写磁盘
   │                          │                            │                            │
   │ sync_file_range(WRITE)   │                            │                            │
   │────────────────────────>│ generic_write_sync          │                            │
   │                          │  __filemap_fdatawrite_range│                            │
   │                          │  do_writepages ───────────>│                            │
   │                          │                            │  writeback_inodes_wb       │
   │                          │                            │  schedule WRITE out        │
   │                          │                            │  (立即开始,不等待)         │
```

## 对比/选型

| 场景 | 推荐做法 |
|------|---------|
| 流式读大文件(GB 级) | `posix_fadvise(POSIX_FADV_DONTNEED)` 已读完的区段;防 cache 撑爆 |
| 数据库随机读 | `posix_fadvise(POSIX_FADV_RANDOM)` 关预读;`O_DIRECT` 绕过 page cache 直接 DMA |
| 大文件写后立刻读 | 显式 `fdatasync(2)`(等 data 落盘)或 `msync(MS_SYNC)`(mmap) |
| 数据库事务 commit | `fsync(2)`(含 metadata);`fdatasync(2)`(仅 data,更快) |
| 预热 cache | `posix_fadvise(POSIX_FADV_WILLNEED)` 非阻塞预读 |
| 精细 writeback | `sync_file_range(WAIT_BEFORE\|WRITE\|WAIT_AFTER)` 组合 |

**`O_DIRECT` vs page cache**:
- `O_DIRECT`:绕过 page cache,直接 DMA 到用户 buffer;**应用必须自己管理对齐**(512B 或 4KB)+ buffer size
- **使用场景**:数据库(MySQL InnoDB 自带 O_DIRECT)、视频处理
- **缺点**:失去 readahead + 失去 write coalescing;小 I/O 性能下降

## 环境准备

- **操作系统**:Linux 2.6+(POSIX_FADV_* 早;`POSIX_FADV_NOREUSE` 需 6.3+);macOS 行为类似但 advisory 不强制
- **语言版本**:GCC 9+ / Python 3.8+ / Go 1.18+
- **依赖**:`<fcntl.h>` + `<sys/mman.h>`;无第三方依赖
- **查看 page cache 状态**:`/proc/meminfo` 的 `Cached:` / `Buffers:` / `Dirty:`;`/proc/<pid>/io` 看进程 R/W;`vmtouch <file>`(可选工具)看具体文件占用

## 运行方式

### C

```bash
cd c && gcc -O2 -Wall -Wextra -pedantic page_cache.c -o page_cache && ./page_cache
```

### Python

```bash
cd python && python3 page_cache_demo.py
```

### Go

```bash
cd go && go run page_cache.go
```

## 关键代码片段

### C(`posix_fadvise` + `sync_file_range` 组合)

```c
int fd = open("big.bin", O_RDWR | O_CREAT | O_TRUNC, 0644);
ftruncate(fd, 16 * 1024 * 1024);            // 16 MB

/* 顺序写 16 MB,只 dirty 不刷 */
char *buf = malloc(16 * 1024 * 1024);
memset(buf, 0x42, 16 * 1024 * 1024);
write(fd, buf, 16 * 1024 * 1024);

/* 标记前 4 MB 不再需要 → 内核可释放 cache 页 */
posix_fadvise(fd, 0, 4 * 1024 * 1024, POSIX_FADV_DONTNEED);

/* 显式触发 writeback:不写 metadata,只刷数据 */
sync_file_range(fd, 0, 16 * 1024 * 1024,
                SYNC_FILE_RANGE_WAIT_BEFORE | SYNC_FILE_RANGE_WRITE | SYNC_FILE_RANGE_WAIT_AFTER);

/* 全部 sync */
fsync(fd);
```

### Python(`os.posix_fadvise` + `os.sync_file_range` 需 Linux 3.x+,需 fcntl 模拟)

```python
import os, ctypes

libc = ctypes.CDLL("libc.so.6", use_errno=True)
P_FADVISE = 25  # SYS_fadvise64
DONTNEED = 4

def fadvise_dontneed(fd, off, length):
    return libc.syscall(P_FADVISE, ctypes.c_int(fd),
                        ctypes.c_long(off), ctypes.c_long(length), ctypes.c_int(DONTNEED))

with open("big.bin", "wb") as f:
    f.write(b"\x42" * (16 * 1024 * 1024))
    fd = f.fileno()

fadvise_dontneed(fd, 0, 4 * 1024 * 1024)  # 释放前 4 MB
os.fsync(fd)                              # 刷全部 dirty
```

### Go(走 `syscall.Syscall6(SYS_FADVISE64,...)`)

```go
import "syscall"

const (
    posixFadvDontneed = 4
    sysFadvise64      = 272 // Linux x86_64
)

func fadvise(fd, off, len uint64, advice int) error {
    _, _, e := syscall.Syscall6(sysFadvise64,
        uintptr(fd), uintptr(off), uintptr(len), uintptr(advice), 0, 0)
    if e != 0 { return e }
    return nil
}
```

## 性能与边界

- **Page size**:典型 4KB;x86-64/ARM64 也支持 2MB/1GB hugepage(`MAP_HUGETLB`)
- **Page cache 大小上限**:理论无上限;受物理 RAM + 内核 `vm.pagecache_limit_mb`(3.5+);**默认**所有空闲 RAM 都可用于 cache
- **Readahead window**:`/sys/block/<dev>/queue/read_ahead_kb`(默认 128 KB);`POSIX_FADV_SEQUENTIAL` ×2,`RANDOM` 关闭
- **Dirty ratio**:`/proc/sys/vm/dirty_ratio`(默认 20%);**写吞吐峰值** vs **崩溃时丢数据量** 的 trade-off
- **writeback 吞吐**:由 `/sys/block/<dev>/queue/nr_requests` 控制队列深度(默认 128);瓶颈在块设备而非 page cache
- **`posix_fadvise(POSIX_FADV_DONTNEED)` 释放范围**:offset + size **必须页对齐**;否则部分页被忽略(Linux 文档原文)

## 注意事项与常见坑

1. **`POSIX_FADV_DONTNEED` 不保证刷 dirty 页**:若区间含未写的 dirty 数据,这些页不释放;**必须先 `fsync()`/`fdatasync()`**(man page 原话)
2. **`sync_file_range()` 不刷 metadata**(inode、目录项等):**只用于已存在的块覆盖**,不保证数据安全;CoW fs(btrfs/ZFS)下"覆盖已分配块"做不到,可能 `EOPNOTSUPP` 效果
3. **`sync_file_range()` 不 flush 块设备 write cache**:RAID 卡电池 + 磁盘 write-back cache 仍可能丢数据;关键数据需 `open(O_DSYNC)` 或 `blockdev --setro`
4. **`posix_fadvise(POSIX_FADV_WILLNEED)` 受内存压力影响**:"The amount of data read may be decreased by the kernel depending on virtual memory load";**非阻塞 + 非保证**
5. **`O_DIRECT` 与 page cache 互斥**:`O_DIRECT` I/O 不进 page cache → 后续 `read()` 看不到刚 `O_DIRECT` 写的数据;**`O_DIRECT` 与 mmap 同一文件需要 `msync`/`fsync` 协调**
6. **写 mmap 后只调 `mprotect(PROT_READ)` 不刷 dirty**:`mprotect` 改权限,内核仍 dirty;需 `msync(MS_SYNC)` 显式落盘
7. **`vm.dirty_ratio` 触发的同步阻塞**:`write()` 系统调用在内核可能进入 `balance_dirty_pages()` 等 writeback,长时间高负载下 `write()` 时延抖动大;**调低 `dirty_ratio`** 可降低单次写时延,但降低吞吐
8. **`/proc/<pid>/io` 中的 `write_bytes` 计入 page cache 写入而非磁盘写**:`iotop -ap` 的统计是 page cache 视角;**真正落盘字节数用 `iostat -dx` 的 `w/s` 或 `bdi_writeback` 统计**
9. **`posix_fadvise` advice 是 hint,内核可忽略**:`POSIX_FADV_RANDOM` 关闭预读后,某些文件系统仍可能预读(如 FAT);**不要把 fadvise 当硬性保证**
10. **`madvise(MADV_DONTNEED)` 对 MAP_SHARED 的语义**:"subsequent accesses of pages in the range will succeed, but will result in either repopulating the memory contents from the up-to-date contents of the underlying mapped file"——**适合"读完不再用"的场景**

## 参考资料(实际阅读过的权威来源)

- [posix_fadvise(2) — Linux manual page](https://man7.org/linux/man-pages/man2/fadvise64.2.html) — Linux posix_fadvise 完整定义;含 NORMAL/SEQUENTIAL/RANDOM/NOREUSE/WILLNEED/DONTNEED 6 种 advice 的语义 + "implementation may attempt to write back dirty pages...but not guaranteed" 原文
- [sync_file_range(2) — Linux manual page](https://man7.org/linux/man-pages/man2/sync_file_range.2.html) — Linux sync_file_range 完整定义;含 "This system call is **extremely dangerous** and should not be used in portable programs" + CoW fs 不适用警告
- [madvise(2) — Linux manual page](https://man7.org/linux/man-pages/man2/madvise.2.html) — Linux madvise 完整定义;含 MADV_NORMAL/RANDOM/SEQUENTIAL/WILLNEED/DONTNEED/REMOVE/DONTFORK/HWPOISON/MERGEABLE 等建议值
- [posix_fadvise(3p) — POSIX manual page](https://man7.org/linux/man-pages/man3/posix_fadvise.3p.html) — POSIX.1-2017 标准定义;POSIX_FADV_* 6 个标准 advice 值
- [sync(2) / syncfs(2) — Linux manual page](https://man7.org/linux//man-pages/man2/syncfs.2.html) — sync() / syncfs() 的语义,Linux sync() 实际等待 I/O 完成(不同于 POSIX spec)