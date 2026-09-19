# 文件系统

## 子领域

- **VFS 抽象层**：super_block / inode / dentry / file，`statx(2)` 字段映射
- **Page Cache**：page cache 的使用姿势（`posix_fadvise` / `sync_file_range`）与回写调优（`vm.dirty_*`）
- **稀疏文件与打洞**：`SEEK_HOLE`/`SEEK_DATA`、`fallocate` 五种模式、`FIEMAP`
- **日志文件系统**：ext4 / XFS（JBD2 预写日志）
- **Copy-on-Write 文件系统**：Btrfs / ZFS（reflink、快照、send/receive）
- **绕过 page cache**：`O_DIRECT` 的对齐规则与代价

## 已完成 demo

| Demo | 路径 | 知识点 |
| --- | --- | --- |
| 021 | [mmap内存映射/](mmap内存映射/) | mmap(2) 系统调用：file-backed 映射、MAP_SHARED vs MAP_PRIVATE(CoW)、MAP_ANONYMOUS 父子进程 IPC、`msync(2)` 持久化、ftruncate 后 SIGBUS 边界 |
| 022 | [ext4-Journaling/](ext4-Journaling/) | JBD2 journal 块格式（预写日志）：descriptor/data/commit 块结构、recovery 重放未崩溃事务、JBD2 大端 vs ext4 小端、`JBD2_FLAG_ESCAPE` 边界处理 |
| 023 | [Page-Cache/](Page-Cache/) | Linux page cache：`posix_fadvise`（NORMAL/RANDOM/SEQUENTIAL/WILLNEED/DONTNEED）、`sync_file_range` 精细 writeback、`/proc/meminfo` Dirty/Cached 状态观察、readahead 调优 |
| 392 | [VFS与statx/](VFS与statx/) | `statx(2)` 请求掩码 ≠ 返回掩码（四种组合）、`STATX__RESERVED` 触发 EINVAL、`stx_attributes & stx_attributes_mask`、`AT_STATX_*` 同步三态、`stx_blocks` 512B 单位、字段可来自不同时刻（Py 36 断言） |
| 393 | [CoW与快照/](CoW与快照/) | Btrfs CoW：reflink 只加引用计数、往中间写把 extent 切三段、defrag 打断 reflink 造成空间放大、`datacow`/`datasum`/`compress` 互斥与顺序生效、send stream 的 CRC32C（初值 0 不取反）（Py 48 断言） |
| 394 | [O_DIRECT与对齐/](O_DIRECT与对齐/) | O_DIRECT 三样对齐（地址/偏移/长度）、2.4→2.6→6.1 三代口径、未对齐可能静默退回 buffered、`O_DIRECT ≠ O_SYNC`（glibc 里 O_SYNC 含 O_DSYNC 位）、禁止与 `fork()` 并发（Py 32 断言） |
| 395 | [稀疏文件与打洞/](稀疏文件与打洞/) | `SEEK_DATA`/`SEEK_HOLE` 边界（洞中间原样返回、末尾隐式洞、ENXIO 收尾）、`fallocate` 五种 mode 与各自 EINVAL、`FIEMAP_EXTENT_UNWRITTEN`、最简实现的退化（Py 40 断言 + C 真实系统调用） |
| 396 | [回写与脏页/](回写与脏页/) | `vm.dirty_*`：分母是 available 而非 total、`*_bytes` 与 `*_ratio` 互斥（另一个读出来是 0）、`dirty_bytes` 两页下限、background 阈值 vs 限流阈值、`dirty_writeback_centisecs=0` 禁用定期回写（Py 34 断言） |

## 待研究

- [x] Linux VFS 四大对象（super/inode/dentry/file）与 `statx(2)` 字段映射 → **392**
- [x] Copy-on-Write 文件系统（Btrfs）与日志 fs 的取舍 → **393**
- [x] `direct I/O` (`O_DIRECT`) 绕过 page cache 的对齐与代价 → **394**
- [x] 稀疏文件与打洞（`SEEK_HOLE` / `fallocate` / `FIEMAP`）→ **395**
- [x] Page cache 回收策略（`vm.dirty_*` 回写调优）→ **396**
- [ ] XFS 的延迟分配（delayed allocation）与 `allocsize` 挂载选项
- [ ] fs-verity 与 dm-integrity：只读完整性校验（Merkle 树）
- [ ] io_uring 对文件 IO 的影响（`IORING_OP_READ` / 注册缓冲区）
- [ ] 目录项缓存（dcache）与 `getdents(2)` 的大目录遍历
- [ ] 文件系统冻结与一致性快照（`FSFREEZE` / LVM snapshot）
