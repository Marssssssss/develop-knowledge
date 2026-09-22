# XFS 延迟分配与 `allocsize`

XFS 的 delayed allocation（延迟分配）与 ext4 的 data=ordered 日志是两种完全不同的"延后"：ext4 延后的是**日志提交**，XFS 延后的是**块分配本身**。写 `write()` 返回时，XFS 只在内存的 `XFS_COW_FORK`/delalloc 预留里记账，盘上还没有任何数据块；真正的挑块发生在回写（`xfs_buffered_write_iomap_begin` → `xfs_iomap_write_delay`）时，因此一次顺序写能被"攒"成一条长 extent，而不是几十条短 extent。

代价是：`write()` 之后立刻 `stat` 看到的 `st_blocks` 可能是 0，且 ENOSPC 只能在回写时才报出来（`man xfs(5)` 的 `allocsize` 条目与 `Documentation/filesystems/xfs/` 都有说明）。

## 核心机制

### 1. `allocsize` 挂载选项：`ffs(size) - 1` 是个陷阱

```c
/* fs/xfs/xfs_super.c, Opt_allocsize 分支 */
if (suffix_kstrtoint(param->string, 10, &size))
        return -EINVAL;
parsing_mp->m_allocsize_log = ffs(size) - 1;
parsing_mp->m_features |= XFS_FEAT_ALLOCSIZE;
```

`ffs` 取的是**最低置位**的 1-based 下标。所以：

- `allocsize=64k` → `ffs(65536)-1 = 16` → 64 KiB，符合直觉
- `allocsize=96k` → `96*1024 = 98304 = 0x18000`，最低置位是 bit 15 → **32 KiB**

**非 2 的幂不会报错**，只是被静默取到最低的那个 2 的幂。合法性检查是后一步 `xfs_validate_params` 的事（`XFS_MIN_IO_LOG = PAGE_SHIFT` … `XFS_MAX_IO_LOG = 30`，即 4 KiB … 1 GiB），所以 `allocsize=96k` 能通过校验但语义不是你写的那个数。默认值 `mp->m_allocsize_log = 16`（64 KiB，见 `xfs_super.c`），换算成块是：

```c
/* fs/xfs/xfs_mount.c */
mp->m_allocsize_blocks = 1U << (mp->m_allocsize_log - sbp->sb_blocklog);
```

4 KiB 块大小下 = 16 块。

### 2. 动态投机预分配 `xfs_iomap_prealloc_size`

不指定 `allocsize` 时走"动态"模式，随文件增大而增大，上界是单个 extent 能表达的最大长度：

```c
/* fs/xfs/libxfs/xfs_format.h */
#define BMBT_BLOCKCOUNT_BITLEN  21
#define XFS_MAX_BMBT_EXTLEN     ((xfs_extlen_t)((1ULL << 21) - 1))   /* 2097151 */
```

注意 **2097151 不是 2 的幂**，这直接导致源码里那句"先 roundup 再取 min"的注释——否则后面的 `rounddown_pow_of_two` 会把最大值也砍掉一档。

算法（`fs/xfs/xfs_iomap.c`，本 demo 逐行转写）：

1. `isize < allocsize 字节数` → **返回 0**（很可能只写这一次，不预分配）
2. `isize < dalign`、或没有前驱 extent、或写在洞后面（前驱不紧邻 offset）→ 返回 `m_allocsize_blocks`
3. 否则 `plen = 前驱长度`，再往前回溯累加，**要求逻辑与物理同时连续**（`got.startoff+got.len == prev.startoff` 且 `got.startblock+got.len == prev.startblock`），前驱是 delalloc（NULLSTARTBLOCK）立即停
4. `alloc = plen * 2`；若超过 `XFS_MAX_BMBT_EXTLEN` 则改用 `XFS_B_TO_FSB(offset)`
5. `alloc = min(roundup_pow_of_two(XFS_MAX_BMBT_EXTLEN), alloc)` ← 第 4 步的补偿
6. 空闲空间节流：`xfs_iomap_freesp` 跨过 5% 给 `shift=2`，每再低一档 +1，最低 6
7. 配额节流取三者最小，`shift` 取最大；`alloc >>= shift`
8. `rounddown_pow_of_two`（**0 未定义，故先判 0**），再封顶 `XFS_MAX_BMBT_EXTLEN`
9. `while (alloc && alloc >= freesp) alloc >>= 4` —— 预分配不得超过可用空间
10. 最后**兜底抬到 `m_allocsize_blocks`**

一个容易看漏的性质：**第 3 步的 `plen > MAX/2` 提前 break 对最终结果没有影响**。因为 break 一旦触发就意味着 `alloc = plen*2 > XFS_MAX_BMBT_EXTLEN`，第 4 步立即用 offset 覆盖掉 plen。本 demo 用成对断言（两条只差回溯是否中断的 extent 布局）钉住了这一点。

### 3. 生命周期与回收

```
write()  ──► delalloc（仅内存预留，st_blocks 不变）
   │
writeback ─► unwritten（真块已分配，extent 带 XFS_EXT_UNWRITTEN 标志）
   │
io complete ► written（标志清掉）
   │
release/inode reclaim ─► xfs_free_eofblocks 砍掉 EOF 之外的投机预分配
```

最后一步是关键：投机预分配**不是永久占用**，close 或 inode reclaim 时会被收回。所以"文件系统莫名其妙满了"往往是大量正在写的文件的投机预分配叠加导致的，等它们 close 就会回落。

## 已验证的行为

| 场景 | 结果 |
| --- | --- |
| `isize` 小于 64 KiB | 预分配 **0** |
| 单条前驱 4 块 | 8 → rounddown 8 → 兜底抬到 **16** |
| 单条前驱 64 块 | **128** |
| 两条物理连续 8+8 | plen=16 → **32** |
| 物理不连续（0x1000/0x9000） | plen 只算前一条 → **16** |
| 前驱是 delalloc | 立即 break → **16** |
| `free` 只有 1% 以下 | `shift=6`，128 → 2 → 兜底 **16** |
| `allocsize=96k` | 实际 **32 KiB**（不报错） |

## 文件说明

- `python/xfsalloc.py` —— 常量、`parse_allocsize`、`set_low_space_thresholds`、`iomap_freesp`、`iomap_prealloc_size` 逐行转写，以及 `DelallocFile` 生命周期状态机
- `python/selfcheck_xfsalloc.py` —— 59 条断言（实跑全绿）
- `python/main.py` —— 演示入口
- `go/xfsalloc.go` —— Go 侧镜像（常量、解析、节流、主算法）

## 参考资料（实际读过）

- `fs/xfs/libxfs/xfs_format.h` — BMBT 位宽、`XFS_MAX_BMBT_EXTLEN`、`XFS_MAX_FILEOFF`
  <https://github.com/torvalds/linux/blob/master/fs/xfs/libxfs/xfs_format.h>
- `fs/xfs/xfs_mount.h` — `XFS_MAX_IO_LOG 30` / `XFS_MIN_IO_LOG PAGE_SHIFT`、`m_allocsize_blocks`、`m_low_space`
  <https://github.com/torvalds/linux/blob/master/fs/xfs/xfs_mount.h>
- `fs/xfs/xfs_super.c` — `Opt_allocsize` 的 `ffs(size)-1`、默认 `m_allocsize_log = 16`、校验区间
  <https://github.com/torvalds/linux/blob/master/fs/xfs/xfs_super.c>
- `fs/xfs/xfs_mount.c` — `xfs_set_low_space_thresholds`、`m_allocsize_blocks` 换算
  <https://github.com/torvalds/linux/blob/master/fs/xfs/xfs_mount.c>
- `fs/xfs/xfs_iomap.c` — `xfs_iomap_prealloc_size` / `xfs_iomap_freesp` 全文
  <https://github.com/torvalds/linux/blob/master/fs/xfs/xfs_iomap.c>
- `fs/xfs/libxfs/xfs_bmap.c` — `xfs_bmap_btalloc_at_eof` 的 EOF 精确分配尝试
  <https://github.com/torvalds/linux/blob/master/fs/xfs/libxfs/xfs_bmap.c>
- `xfs(5)` — `allocsize` 取值范围（页大小 ~ 1 GiB，2 的幂）与动态行为
  <https://man7.org/linux/man-pages/man5/xfs.5.html>
- `Documentation/filesystems/xfs/xfs-delayed-logging-design.html`
  <https://www.kernel.org/doc/html/latest/filesystems/xfs/xfs-delayed-logging-design.html>

> 未取到/未验证的部分：实时子卷走 `xfs_rtbxlen_to_blen`，demo 未覆盖；`xfs_eof_alignment`（条带对齐）只实现为 `dalign_blocks` 入参，未建模 RAID 几何。
