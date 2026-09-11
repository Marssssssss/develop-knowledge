# 文件系统

## 子领域

- **VFS 抽象层**
- **Page Cache**
- **日志文件系统**：ext4 / XFS
- **Copy-on-Write 文件系统**：Btrfs / ZFS

## 已完成 demo

| Demo | 路径 | 知识点 |
| --- | --- | --- |
| 021 | [mmap内存映射/](mmap内存映射/) | mmap(2) 系统调用:file-backed 映射、MAP_SHARED vs MAP_PRIVATE(CoW)、MAP_ANONYMOUS 父子进程 IPC、`msync(2)` 持久化、ftruncate 后 SIGBUS 边界 |
| 022 | [ext4-Journaling/](ext4-Journaling/) | JBD2 journal 块格式(预写日志):descriptor/data/commit 块结构、recovery 重放未崩溃事务、JBD2 大端 vs ext4 小端、`JBD2_FLAG_ESCAPE` 边界处理 |
| 023 | [Page-Cache/](Page-Cache/) | Linux page cache:`posix_fadvise` (NORMAL/RANDOM/SEQUENTIAL/WILLNEED/DONTNEED)、`sync_file_range` 精细 writeback、`/proc/meminfo` Dirty/Cached 状态观察、readahead 调优 |

## 待研究

- [ ] Linux VFS 四大对象（super/inode/dentry/file）— 仅需 stat(2)/statx(2) 视角的字段与 inode(7) 内字段映射
- [ ] Copy-on-Write 文件系统（Btrfs / ZFS）与日志 fs 的取舍(Btrfs CoW 空洞管理 + send/receive)
- [ ] `direct I/O` (`O_DIRECT`) 绕过 page cache:数据库场景(MySQL InnoDB buffer pool、PostgreSQL)
- [ ] Page cache 回收策略 (`vm.pagecache_limit_mb`、`/proc/sys/vm/dirty_*` 调优实战)