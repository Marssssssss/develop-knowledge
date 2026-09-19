# mmap 内存映射

## 简介

**`mmap(2)` 把文件直接映射进进程的虚拟地址空间**——文件数据看起来就像进程的内存数组，读写指针就是读写文件；写入通过内核的**page cache** 同步到磁盘,无需走 `read(2)`/`write(2)` 的用户-内核往返。

- **关键概念清单**
  - **虚拟内存映射(VMA)**：内核把 `[addr, addr+len)` 区间登记进进程的页表;首缺页(page fault)由内核从文件读入物理页
  - **Page cache**:文件数据在内核里只存一份,所有映射它的进程共享;多个进程 mmap 同一文件看到的是同一组页
  - **`MAP_SHARED` vs `MAP_PRIVATE`**:前者修改写回文件、其他进程可见;后者 CoW(写时复制),修改不污染源文件
  - **`msync(2)`**:强制把 MAP_SHARED 的脏页 flush 到磁盘,等价于把 `write(2)+fdatasync(2)` 折成一次 page-cache 操作
  - **`MAP_ANONYMOUS`**(Linux 扩展名 `MAP_ANON`):不绑文件,相当于 `malloc` + `memset 0`,MAP_SHARED 下可作父子进程共享内存
- **历史背景**:`mmap` 在 4.2BSD(1983)首次出现;POSIX.1-2001 将其纳入标准;Linux 2.0 引入 `MAP_ANONYMOUS`,2.6.32 后 `MAP_HUGETLB` 增强,4.15 加入 `MAP_SHARED_VALIDATE`(严格校验未知 flag,防与未来 flag 冲突)

## 原理详解

### 1. 工作机制(5 步)

1. **应用调用 `mmap()`** → 内核创建 VMA(`vm_area_struct`),把目标文件 inode 关联到 VMA,分配进程页表项;**不立即分配物理页**(demand paging)
2. **进程首次访问 `[addr, addr+len)`** → CPU 触发 page fault(因为页表项尚无物理页或权限不全)→ 内核在 page cache 里找到该 offset 对应的页;若不在 cache 则从磁盘读入(注意 offset 必须页对齐)
3. **应用读写该地址** → 等价于读写 file-backed 的 page cache 页(对 MAP_SHARED)或该页的私有副本(对 MAP_PRIVATE)
4. **`msync(MS_SYNC)`** 时 → 内核把所有 dirty 页强制提交到块设备并等待 I/O 完成;`MS_ASYNC` 调度但不等待;`MS_INVALIDATE` 丢弃其他进程可能持有的 cache 副本
5. **`munmap()`** → 解除 VMA;MAP_SHARED 的脏页是否落盘依 fs 实现(ext4 默认会,除非有进程显式 msync 后又触发了 page 回收)

### 2. ASCII 时序图:写文件经 mmap

```
进程 A                     内核 page cache                     块设备
  │ mmap(4K, RW, SHARED)    │                                    │
  │────────────────────────>│ 创建 VMA,不分配物理页              │
  │ *p = 0x41 (首次写)      │                                    │
  │──── page fault ────────>│ 从磁盘读页装入 cache ──────────────>│
  │                         │ 标记 dirty                          │
  │ *(p+1) = ... (后续写)   │ 改 cache 页(已存在,零系统调用)    │
  │ msync(MS_SYNC)          │                                    │
  │────────────────────────>│ flush dirty 页 ────────────────────>│ 落盘
  │ munmap()                │ 销毁 VMA,cache 页保留供他用         │
```

### 3. 核心 API & 参数

```c
void *mmap(void *addr, size_t length, int prot, int flags,
           int fd, off_t offset);
int munmap(void *addr, size_t length);
int msync(void *addr, size_t length, int flags);
```

| 参数 | 含义 |
|------|------|
| `addr` | 期望映射地址,通常传 `NULL`(让内核选);`MAP_FIXED` 时强制地址 |
| `length` | 字节数,**按页大小向上取整**(`sysconf(_SC_PAGESIZE)`,通常 4096);文件末尾的部分页在写后会被 zero-fill |
| `prot` | `PROT_READ`/`PROT_WRITE`/`PROT_EXEC`/`PROT_NONE`(位或) |
| `flags` | 必含 `MAP_SHARED` 或 `MAP_PRIVATE`(二选一);可叠加 `MAP_ANONYMOUS`(fd 忽略,长度按页对齐,内容初始化为 0)、`MAP_FIXED`/`MAP_FIXED_NOREPLACE`(Linux 4.17+) |
| `fd` | 文件描述符;`MAP_ANONYMOUS` 时传 -1;mmap 后 fd 可立即 close,不影响映射 |
| `offset` | **页对齐**(必须);从文件该 offset 起 length 字节被映射 |

返回值:`MAP_FAILED = (void*)-1`,errno 指示原因(`EINVAL`/`EACCES`/`ENOMEM`/`ENFILE`...)。

### 4. MAP_SHARED vs MAP_PRIVATE(CoW 行为对比)

```
写入方  ──mmap──MAP_SHARED──>   [file-backed page cache 页]   <──mmap──MAP_PRIVATE──  读取方
                                  │
写入方 *p = 'A'                   │
────────────────────────────────> │  cache 页更新为 'A'(dirty)
                                  │
                                  │  读取方首次 *q 触发 CoW ─→ 复制一份该页给读取方
                                  │  (private 副本与 cache 页脱钩)
                                  │
                                  │  读取方 *q = 'B'         ※ 仅改自己的副本
                                  │
查询方  read(fd, buf, 1)         │
────────────────────────────────> │  读到 'A' (写入方结果)
```

- **MAP_SHARED**:变更写回 file-backed cache,所有共享映射的进程看见,**最终落盘**(MS_SYNC/msync()/pdflush)
- **MAP_PRIVATE**:**写时复制(Copy-on-Write, CoW)**——只读访问时与 SHARED 共用物理页;首次写触发 CoW,内核为该进程复制一份私有页;后续修改不污染源文件、不影响其他进程

### 5. 内核视角:file-backed vs anonymous

| 类型 | flags 组合 | 后备存储 | 典型用途 |
|------|-----------|---------|---------|
| 文件映射 | `MAP_SHARED` + fd | 文件 inode | mmap 编辑大文件、内存共享同一文件 |
| 文件映射 | `MAP_PRIVATE` + fd | 文件 inode + 私有副本 | 加载可执行文件、动态库 |
| 匿名 | `MAP_SHARED` + `MAP_ANONYMOUS` | swap/物理内存(共享) | 父子进程 IPC(显式共享) |
| 匿名 | `MAP_PRIVATE` + `MAP_ANONYMOUS` | 物理内存 + 私有副本 | 等价 `malloc`(glibc 大分配走 mmap) |

## 对比/选型

| 维度 | `read(2)/write(2)` | `mmap(2)+memcpy` |
|------|--------------------|-----------------|
| 数据拷贝次数 | 2 次(磁盘→内核→用户) | 1 次(磁盘→page cache,用户直访) |
| 随机访问 | 每次 `lseek+read`(系统调用) | 直接指针运算(零系统调用) |
| 大文件读 | 受限于用户 buffer | 整文件常驻内存(惰性按页加载) |
| 写持久化 | 显式 `write+fsync` | `msync(MS_SYNC)` 或依赖脏页回写 |
| 进程间共享 | 必须 `read(pipe)` 或 POSIX SHM | `MAP_SHARED` 同一文件或 `MAP_ANONYMOUS` |
| 缺点 | — | 文件长度固定;截断/`ftruncate` 后映射需要重新 mmap |

## 环境准备

- **操作系统**:Linux 2.6+(`MAP_SHARED_VALIDATE` 需 4.15+,`MAP_FIXED_NOREPLACE` 需 4.17+);macOS 行为类似但 MAP_ANONYMOUS 行为细节不同;Windows 有 `CreateFileMapping` 但 API 不同
- **语言版本**:GCC 9+ / Python 3.8+ / Go 1.18+
- **依赖**:无第三方依赖;Python 用内置 `mmap` 标准库;Go 用 `golang.org/x/sys/unix` 直接走 syscall(注:为避免引入外部依赖,本 demo 走 `syscall.Mmap`(Go 1.20+) / 旧版用 `syscall.Syscall6(SYS_MMAP,...)`)

## 运行方式

```bash
cd c      && gcc -O2 -Wall -Wextra -pedantic mmap_demo.c -o mmap_demo && ./mmap_demo
cd python && python3 mmap_demo.py
cd go     && go run mmap_demo.go
```

## 关键代码片段

### C(POSIX mmap 写文件 + MAP_PRIVATE CoW)

```c
int fd = open("data.bin", O_RDWR | O_CREAT, 0644);
ftruncate(fd, 4096);                   // 文件必须 ≥ length
volatile char *p = mmap(NULL, 4096,
    PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
if (p == MAP_FAILED) { perror("mmap"); exit(1); }

p[0] = 'A';                             // 首次写触发 page fault,内核从文件读页
p[1] = 'B';
msync((void*)p, 4096, MS_SYNC);         // 强制 dirty 页落盘

// 再开一个独立 mmap 验证落盘
int fd2 = open("data.bin", O_RDONLY);
char *q = mmap(NULL, 4096, PROT_READ, MAP_PRIVATE, fd2, 0);
// q[0] == 'A', q[1] == 'B'(磁盘已更新)
q[0] = 'X';                              // CoW:不影响 p,不影响磁盘
munmap(q, 4096);
```

### Python(`mmap` 模块,MAP_PRIVATE vs MAP_SHARED 对比)

```python
import mmap, os
with open('data.bin', 'r+b') as f:
    m = mmap.mmap(f.fileno(), 4096, access=mmap.ACCESS_WRITE)  # SHARED
    m[0:2] = b'AB'
    m.flush()                                                  # msync

# 另一进程/新打开 → 从磁盘读 'AB'
with open('data.bin', 'rb') as f:
    assert f.read(2) == b'AB'

# 同一文件以 PRIVATE 打开 → 修改不影响源
with open('data.bin', 'r+b') as f:
    m_priv = mmap.mmap(f.fileno(), 4096, access=mmap.ACCESS_COPY)  # PRIVATE
    m_priv[0] = 'X'
    m_priv.close()
# data.bin 第 1 字节仍是 'A'(磁盘未变)
```

### Go(`syscall.Mmap` + 手动映射 MAP_PRIVATE)

```go
f, _ := os.OpenFile("data.bin", os.O_RDWR|os.O_CREATE, 0644)
f.Truncate(4096)
b, _ := syscall.Mmap(int(f.Fd()), 0, 4096,
    syscall.PROT_READ|syscall.PROT_WRITE,
    syscall.MAP_SHARED)
defer syscall.Munmap(b)
b[0] = 'A'; b[1] = 'B'
syscall.Msync(b, syscall.MS_SYNC)
```

## 性能与边界

- **首字节时延**(cold page fault):mmap 首字节读触发 disk I/O,等同于 `lseek+read` 单页的开销(典型 4KB 页 ~5-50 ms HDD / ~50-200 µs SSD)
- **顺序读吞吐**:在 cache 命中后,mmap 读等同内存读(GB/s 级),优于 `read()` 用户拷贝(~ 减半带宽)
- **空间开销**:每页 VMA 内核开销约 256B(`vm_area_struct` + 页表项);mmap 1GB 文件约 16K 个 VMA,内存占用 MB 级(不会预先加载数据,只是页表)
- **文件大小**:理论 ≤ `SSIZE_MAX`(2^63-1 on 64-bit Linux);实际受块设备、文件系统和 `ulimit -l`(锁内存限制)
- **`MAP_HUGETLB`**:使用 2MB 大页时,页表项减少 ~512 倍,但需 `CAP_IPC_LOCK` 或预留 hugepage
- **mmap 数量上限**:每进程 VMA 数默认 `vm.max_map_count = 65536`;可调

## 注意事项与常见坑

1. **`offset` 必须页对齐**:`mmap(fd, len, ..., fd, offset=1)` 在 Linux 上返回 `EINVAL`;POSIX 规定 "must be multiple of page size"
2. **`length` 不必页对齐**:内核向上取整;但超出文件大小的部分在 MAP_SHARED 下写会产生扩展(直到写满最后一个页);**若文件长度 < length,必须先 `ftruncate(fd, length)`**,否则 `SIGBUS`
3. **`MAP_PRIVATE` 写时复制不复制整个区间**:首字节写才触发该页的 CoW,不必担心大文件 mmap 的复制开销
4. **关闭 fd 不解除映射**:`mmap()` 后可立即 `close(fd)`;反之,关闭映射后,只要 page cache 还引用该 inode,文件数据仍驻留在内存
5. **文件被截断(`ftruncate`)后访问尾部** → `SIGBUS`;`ftruncate` 之前 mmap 的区间,若新长度 < offset+length,访问超长部分会总线错误。**避免**:截断前先 `munmap`
6. **`mmap` 写文件 ≠ 原子持久化**:`msync(MS_SYNC)` 落盘但仍受块设备 write cache 影响;完整持久化需 `open(O_DSYNC)` 或 `fsync` 块设备 + `hdparm -W0` 关闭 write cache
7. **大量 mmap 撑爆 `/proc/sys/vm/max_map_count`**:默认 65536 个 VMA/进程;长期跑的服务(数据库、Redis)常见坑——`vm.max_map_count` 不足会 `mmap: Cannot allocate memory`
8. **MAP_SHARED 写顺序跨多个页不保证**:`p[0]='A'; p[4096]='B'; msync()` 写两页时,内核可并发 flush;若依赖"先写低页后写高页"的逻辑(如校验和),需用 `mlock()` 或单页 msync 强制串行
9. **POSIX mmap `flags` 在 `MAP_SHARED` 与 `MAP_PRIVATE` 之外不强制要求**;Linux 用 `MAP_SHARED_VALIDATE` 替代 `MAP_SHARED` 可获 strict flag 检查(防止与未来新 flag 冲突)
10. **glibc `mmap(NULL, len, RW, ANON|PRIVATE, -1, 0)` = `malloc(len)`,但大分配才走 mmap**:小分配走 `brk/sbrk` 池;用 `mallopt(M_MMAP_THRESHOLD, ...)` 可强制走 mmap

## 参考资料(实际阅读过的权威来源)

- [mmap(2) — Linux manual page](https://man7.org/linux/man-pages/man2/mmap.2.html) — Linux mmap(2) 系统调用完整定义,包含 MAP_SHARED_VALIDATE(4.15+)/MAP_FIXED_NOREPLACE(4.17+)/MAP_SYNC 等 Linux 扩展
- [mmap(3p) — Linux manual page](https://man7.org/linux/man-pages/man3/mmap.3p.html) — POSIX.1-2017 标准 mmap 定义;prot/flags 行为规范;MAP_FIXED "implementation-defined whether MAP_FIXED shall be supported"
- [sys/mman.h(0p) — POSIX header](https://man7.org/linux/man-pages/man0/sys_mman.h.0p.html) — POSIX `<sys/mman.h>` 头文件符号:PROT_*/MAP_*/MS_*/POSIX_MADV_* 常量
- [Michael Kerrisk, *The Linux Programming Interface*](https://man7.org/tlpi/) — Ch.49 "Memory Mappings" 详细章节:MAP_SHARED vs PRIVATE 行为、msync 语义、demand paging(本 demo 部分细节参考)