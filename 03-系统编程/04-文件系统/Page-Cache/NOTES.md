# Page Cache 与 writeback —— 补充笔记

> 主文档见 [README.md](./README.md)。本页保留被篇幅挤出来的细节,内容同样出自
> 实读过的 man page(见 README 参考资料)。

## 性能与边界(完整版)

- **Page size**:典型 4KB;x86-64/ARM64 也支持 2MB/1GB hugepage(`MAP_HUGETLB`)
- **Page cache 大小上限**:理论无上限;受物理 RAM + 内核 `vm.pagecache_limit_mb`(3.5+);
  默认所有空闲 RAM 都可用于 cache
- **Readahead window**:`/sys/block/<dev>/queue/read_ahead_kb`(默认 128 KB);
  `POSIX_FADV_SEQUENTIAL` ×2,`RANDOM` 关闭
- **Dirty ratio**:`/proc/sys/vm/dirty_ratio`(默认 20%);写吞吐峰值 vs 崩溃时丢数据量
- **writeback 吞吐**:由 `/sys/block/<dev>/queue/nr_requests` 控制队列深度(默认 128);
  瓶颈在块设备而非 page cache
- **`posix_fadvise(POSIX_FADV_DONTNEED)` 释放范围**:offset + size **必须页对齐**;
  否则部分页被忽略(Linux 文档原文)

## 注意事项与常见坑(完整 10 条)

1. **`POSIX_FADV_DONTNEED` 不保证刷 dirty 页**:若区间含未写的 dirty 数据,这些页不释放;
   **必须先 `fsync()`/`fdatasync()`**(man page 原话:
   "Any unwritten dirty pages will not be freed.")
2. **`sync_file_range()` 不刷 metadata**(inode、目录项等):**只用于已存在的块覆盖**,
   不保证数据安全;CoW fs(btrfs/ZFS)下"覆盖已分配块"做不到,可能 `EOPNOTSUPP` 效果
3. **`sync_file_range()` 不 flush 块设备 write cache**:RAID 卡电池 + 磁盘 write-back cache
   仍可能丢数据;关键数据需 `open(O_DSYNC)` 或 `blockdev --setro`
4. **`posix_fadvise(POSIX_FADV_WILLNEED)` 受内存压力影响**:"The amount of data read may be
   decreased by the kernel depending on virtual memory load";**非阻塞 + 非保证**
5. **`O_DIRECT` 与 page cache 互斥**:`O_DIRECT` I/O 不进 page cache → 后续 `read()` 看不到刚
   `O_DIRECT` 写的数据;**`O_DIRECT` 与 mmap 同一文件需要 `msync`/`fsync` 协调**
6. **写 mmap 后只调 `mprotect(PROT_READ)` 不刷 dirty**:`mprotect` 只改权限,内核仍 dirty;
   需 `msync(MS_SYNC)` 显式落盘
7. **`vm.dirty_ratio` 触发的同步阻塞**:`write()` 在内核可能进入 `balance_dirty_pages()` 等
   writeback,长时间高负载下 `write()` 时延抖动大;调低 `dirty_ratio` 可降低单次写时延,
   但降低吞吐
8. **`/proc/<pid>/io` 中的 `write_bytes` 计入 page cache 写入而非磁盘写**:`iotop -ap` 的统计是
   page cache 视角;真正落盘字节数用 `iostat -dx` 的 `w/s` 或 `bdi_writeback` 统计
9. **`posix_fadvise` advice 是 hint,内核可忽略**:`POSIX_FADV_RANDOM` 关闭预读后,某些文件
   系统仍可能预读(如 FAT);不要把 fadvise 当硬性保证
10. **`madvise(MADV_DONTNEED)` 对 MAP_SHARED 的语义**:"subsequent accesses of pages in the
    range will succeed, but will result in either repopulating the memory contents from the
    up-to-date contents of the underlying mapped file"—— 适合"读完不再用"的场景

## 与同目录其它 demo 的关系

| 主题 | 去哪看 |
| --- | --- |
| `vm.dirty_*` 各旋钮的精确语义与阈值计算 | [回写与脏页/](../回写与脏页/) |
| `O_DIRECT` 的对齐规则与"静默退回 buffered" | [O_DIRECT与对齐/](../O_DIRECT与对齐/) |
| 打洞 / `FIEMAP` / 稀疏文件 | [稀疏文件与打洞/](../稀疏文件与打洞/) |
| CoW fs 上"覆盖已分配块"为何不成立 | [CoW与快照/](../CoW与快照/) |
| mmap 的四种映射与 SIGBUS 边界 | [mmap内存映射/](../mmap内存映射/) |
