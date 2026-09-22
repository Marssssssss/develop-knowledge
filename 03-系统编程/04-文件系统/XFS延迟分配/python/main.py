"""演示入口：XFS 延迟分配与 allocsize。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xfsalloc import (  # noqa: E402
    Extent, Inode, XFS_MAX_BMBT_EXTLEN, DelallocFile, allocsize_blocks,
    iomap_freesp, iomap_prealloc_size, parse_allocsize,
    set_low_space_thresholds,
)

BS = 4096
BLOCKLOG = 12
AB = 16
FREE = 10_000_000
LOW = set_low_space_thresholds(20_000_000)


def hdr(t):
    print()
    print("== %s ==" % t)


hdr("allocsize 挂载选项（xfs_super.c：m_allocsize_log = ffs(size) - 1）")
for v in (4, 8, 16, 32, 64, 96, 128, 256):
    log, ok = parse_allocsize(v * 1024)
    got = (1 << log) if log >= 0 else 0
    print("  allocsize=%-6s → log=%-3s 合法=%-5s 实际=%s" % (
        "%dk" % v, log, ok, "%d KiB" % (got // 1024) if ok else "EINVAL"))
print("  >>> 非 2 的幂不报错，只被 ffs 取到最低置位：96k 实际是 32k")

hdr("低空间节流（xfs_iomap_freesp：跨 5% 给 shift=2，每低一档 +1）")
th = set_low_space_thresholds(1_000_000)
print("  dblocks=1,000,000 → 阈值 %s" % th)
for f in (60_000, 49_999, 39_999, 29_999, 19_999, 9_999):
    print("  free=%-7d → shift=%d" % (f, iomap_freesp(f, th)))

hdr("动态投机预分配（xfs_iomap_prealloc_size）")
print("  XFS_MAX_BMBT_EXTLEN = %d（不是 2 的幂！）" % XFS_MAX_BMBT_EXTLEN)
cases = [
    ("小文件 isize=16KiB", Inode(16 * 1024, [Extent(0, 4, 0x1000)]), 4 * BS),
    ("单条前驱 4 块", Inode(64 * BS, [Extent(0, 4, 0x1000)]), 4 * BS),
    ("单条前驱 64 块", Inode(1 << 22, [Extent(0, 64, 0x1000)]), 64 * BS),
    ("两条物理连续 8+8", Inode(1 << 22, [Extent(0, 8, 0x1000),
                                       Extent(8, 8, 0x1008)]), 16 * BS),
    ("物理不连续 8+8", Inode(1 << 22, [Extent(0, 8, 0x1000),
                                     Extent(8, 8, 0x9000)]), 16 * BS),
]
for name, ip, off in cases:
    icur = len(ip.extents)
    for i, e in enumerate(ip.extents):
        if e.startoff >= ip.fsb(off):
            icur = i
            break
    r = iomap_prealloc_size(ip, off, icur, AB, 0, FREE, LOW)
    print("  %-20s → 预分配 %d 块（%d KiB）" % (name, r, r * 4))

hdr("延迟分配生命周期")
ip = Inode(0, [])
f = DelallocFile(ip, AB, 0, FREE, 20_000_000)
f.write(0, 16 * BS)
print("  write(64KiB)     : state=%-9s extents=%d isize=%d"
      % (f.state, len(ip.extents), ip.isize_bytes))
f.writeback()
print("  writeback        : state=%-9s extents=%d blocks=%d"
      % (f.state, len(ip.extents), ip.extents[0].blockcount))
f.write(16 * BS, 64 * 1024)
f.writeback()
print("  write(64KiB)+回写: extents=%d blocks=%s"
      % (len(ip.extents), [e.blockcount for e in ip.extents]))
f.io_complete()
print("  io_complete      : state=%s" % f.state)

ip2 = Inode(0, [])
f2 = DelallocFile(ip2, AB, 0, FREE, 20_000_000)
f2.write(0, 1 << 20)
f2.writeback()
f2.write(1 << 20, 16 * BS)
f2.writeback()
before = sum(e.blockcount for e in ip2.extents)
cut = f2.release()
print("  追加 1MiB+16KiB   : 回写后 %d 块，release 砍掉 %d 块，剩 %d 块"
      % (before, cut, before - cut))
print("  >>> 投机预分配在 close/inode reclaim 时被 xfs_free_eofblocks 收回")
