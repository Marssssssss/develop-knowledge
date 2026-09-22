"""XFS 延迟分配与 allocsize 自检：把源码条文变成断言。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from delalloc import (
    DELALLOC, UNWRITTEN, WRITTEN, DelallocFile,
)
from xfsalloc import (  # noqa: E402
    BMBT_BLOCKCOUNT_BITLEN, BMBT_STARTOFF_BITLEN, Extent, Inode,
    NULLFSBLOCK, XFS_DEFAULT_ALLOCSIZE_LOG,
    XFS_MAX_BMBT_EXTLEN, XFS_MAX_FILEOFF, XFS_MAX_IO_LOG, XFS_MIN_IO_LOG,
    allocsize_blocks, ffs, iomap_freesp, iomap_prealloc_size,
    parse_allocsize, rounddown_pow_of_two, roundup_pow_of_two,
    set_low_space_thresholds,
)

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %-56s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-56s %s" % (label, detail))


BS = 4096
BLOCKLOG = 12
KiB = 1024

print("== 1. 位宽与上限（xfs_format.h） ==")
check("BMBT_BLOCKCOUNT_BITLEN == 21", BMBT_BLOCKCOUNT_BITLEN == 21)
check("BMBT_STARTOFF_BITLEN == 54", BMBT_STARTOFF_BITLEN == 54)
check("XFS_MAX_BMBT_EXTLEN == 2097151", XFS_MAX_BMBT_EXTLEN == 2097151,
      "%d" % XFS_MAX_BMBT_EXTLEN)
check("XFS_MAX_BMBT_EXTLEN 不是 2 的幂（所以才要 roundup 再 rounddown）",
      rounddown_pow_of_two(XFS_MAX_BMBT_EXTLEN) != XFS_MAX_BMBT_EXTLEN,
      "rounddown=%d" % rounddown_pow_of_two(XFS_MAX_BMBT_EXTLEN))
check("roundup_pow_of_two(2097151) == 2097152",
      roundup_pow_of_two(XFS_MAX_BMBT_EXTLEN) == 1 << 21)
check("XFS_MAX_FILEOFF = (2^54-1)+(2^21-1)",
      XFS_MAX_FILEOFF == (1 << 54) - 1 + (1 << 21) - 1, "%d" % XFS_MAX_FILEOFF)

print("== 2. allocsize 挂载选项（xfs_super.c） ==")
check("XFS_MIN_IO_LOG == PAGE_SHIFT(12)", XFS_MIN_IO_LOG == 12)
check("XFS_MAX_IO_LOG == 30（1G）", XFS_MAX_IO_LOG == 30)
check("默认 m_allocsize_log == 16", XFS_DEFAULT_ALLOCSIZE_LOG == 16)
check("默认 allocsize 是 64 KiB", 1 << XFS_DEFAULT_ALLOCSIZE_LOG == 64 * KiB)
check("默认 allocsize_blocks(4K 块) == 16",
      allocsize_blocks(XFS_DEFAULT_ALLOCSIZE_LOG, BLOCKLOG) == 16)
check("allocsize=64k → log 16", parse_allocsize(64 * KiB) == (16, True))
check("allocsize=4k → log 12（下限）", parse_allocsize(4 * KiB) == (12, True))
check("allocsize=1g → log 30（上限）", parse_allocsize(1 << 30) == (30, True))
check("allocsize=2k → log 11 越界 EINVAL", parse_allocsize(2 * KiB) == (11, False))
check("allocsize=2g → log 31 越界 EINVAL", parse_allocsize(1 << 31) == (31, False))
check("allocsize=96k 不报错但被 ffs 取到最低置位 → 32k",
      parse_allocsize(96 * KiB) == (15, True),
      "96k 实际得到 %d 字节" % (1 << parse_allocsize(96 * KiB)[0]))
check("allocsize=0 → ffs(0)-1 = -1 越界", parse_allocsize(0) == (-1, False))
check("ffs(1)==1 / ffs(4096)==13", ffs(1) == 1 and ffs(4096) == 13)

print("== 3. 低空间阈值（xfs_mount.c） ==")
th = set_low_space_thresholds(1_000_000)
check("dblocks=1e6 → 阈值 [10000,20000,30000,40000,50000]",
      th == [10000, 20000, 30000, 40000, 50000], str(th))
th99 = set_low_space_thresholds(99)
check("dblocks=99 → 整除 100 得 0，五档全 0", th99 == [0, 0, 0, 0, 0], str(th99))
check("free 恰好等于 5% 不给 shift（是严格小于）",
      iomap_freesp(50_000, [10_000, 20_000, 30_000, 40_000, 50_000]) == 0)
check("free=49999 → shift 2", iomap_freesp(49_999, th) == 2)
check("free=39999 → shift 3", iomap_freesp(39_999, th) == 3)
check("free=29999 → shift 4", iomap_freesp(29_999, th) == 4)
check("free=19999 → shift 5", iomap_freesp(19_999, th) == 5)
check("free=9999  → shift 6", iomap_freesp(9_999, th) == 6)
check("free=0 也只到 shift 6（没有第七档）", iomap_freesp(0, th) == 6)

print("== 4. 动态投机预分配：早退分支 ==")
AB = 16                      # 最小预分配块数（64 KiB / 4 KiB）
DA = 0                       # 无条带对齐
FREE = 1_000_000
LOW = set_low_space_thresholds(2_000_000)

# 4.1 文件比最小预分配还小 → 完全不预分配
ip = Inode(AB * BS - 1, [Extent(0, 4, 0x1000)])
check("isize < allocsize 字节数 → 返回 0",
      iomap_prealloc_size(ip, 16 * BS, 1, AB, DA, FREE, LOW) == 0)
ip2 = Inode(AB * BS, [Extent(0, 4, 0x1000)])
check("isize 恰好等于 allocsize 字节数 → 不再返回 0",
      iomap_prealloc_size(ip2, 16 * BS, 1, AB, DA, FREE, LOW) != 0)

# 4.2 写在洞后面 → 只用最小预分配
ip3 = Inode(1 << 20, [Extent(0, 8, 0x1000), Extent(64, 8, 0x1100)])
check("前一条不紧邻 offset（洞）→ 返回最小预分配 16",
      iomap_prealloc_size(ip3, 100 * BS, 2, AB, DA, FREE, LOW) == 16)

# 4.3 有 dalign 且文件小于 dalign → 最小预分配
ip4 = Inode(1 << 20, [Extent(0, 64, 0x1000)])
check("isize < dalign 字节数 → 返回最小预分配 16",
      iomap_prealloc_size(ip4, 64 * BS, 1, AB, 512, FREE, LOW) == 16)
check("dalign 边界：isize 恰好等于 dalign 时不走该分支（是严格小于）",
      iomap_prealloc_size(ip4, 64 * BS, 1, AB, 256, FREE, LOW) != 16,
      "dalign=256 块即 1MiB，与 isize 相等 → 落到主算法")

print("== 5. 主算法：plen 加倍与连续性检查 ==")


def mk(extents, isize):
    return Inode(isize, extents)


# 只有一条前驱，长度 4 → plen=4 → alloc=8 → rounddown 8 → 下限抬到 16
ip = mk([Extent(0, 4, 0x1000)], 1 << 22)
check("单条前驱 len=4 → 8 → rounddown 8 → 下限 16",
      iomap_prealloc_size(ip, 4 * BS, 1, AB, DA, FREE, LOW) == 16)

# 前驱 64 块 → plen=64 → alloc=128 → rounddown 128
ip = mk([Extent(0, 64, 0x1000)], 1 << 22)
check("单条前驱 len=64 → 128 → rounddown 128",
      iomap_prealloc_size(ip, 64 * BS, 1, AB, DA, FREE, LOW) == 128)

# 两条逻辑 + 物理都连续：plen 累加 8+8=16 → alloc=32
ip = mk([Extent(0, 8, 0x1000), Extent(8, 8, 0x1008)], 1 << 22)
check("两条连续 → plen=16 → alloc=32",
      iomap_prealloc_size(ip, 16 * BS, 2, AB, DA, FREE, LOW) == 32)

# 逻辑连续但物理不连续 → 第二条不参与累加
ip = mk([Extent(0, 8, 0x1000), Extent(8, 8, 0x9000)], 1 << 22)
check("物理不连续 → plen 只算前一条 8 → alloc=16 → 下限 16",
      iomap_prealloc_size(ip, 16 * BS, 2, AB, DA, FREE, LOW) == 16)

# 前驱是延迟分配（NULLFSBLOCK）→ 立即 break
ip = mk([Extent(0, 8, NULLFSBLOCK), Extent(8, 8, 0x1000)], 1 << 22)
check("前驱是 delalloc(NULLFSBLOCK) → break，plen=8 → 16",
      iomap_prealloc_size(ip, 16 * BS, 2, AB, DA, FREE, LOW) == 16)

# plen 超过 MAX/2 → 停止累加
half = XFS_MAX_BMBT_EXTLEN // 2            # 1048575
ip = mk([Extent(0, half, 0x1000), Extent(half, 8, 0x1000 + half)], 1 << 40)
off_fsb = half + 8
# 空闲足够时不受 freesp 挤压
r_break = iomap_prealloc_size(ip, off_fsb * BS, 2, AB, DA, 10_000_000, LOW)
check("plen*2 超过 MAX → 改用 offset 的 fsb，再 rounddown 到 2^20",
      r_break == 1 << 20, "offset_fsb=%d → %d 块" % (off_fsb, r_break))
# 配对断言：break 一旦触发，plen 究竟加到多少都不影响结果——因为
# 两者都超过 MAX，都被 XFS_B_TO_FSB(offset) 接管。
ip_nobreak = mk([Extent(0, 8, 0x1000), Extent(8, half, 0x1008)], 1 << 40)
r_nobreak = iomap_prealloc_size(ip_nobreak, off_fsb * BS, 2, AB, DA, 10_000_000, LOW)
check("配对：break 触发与否结果相同（都走 offset 回退）", r_break == r_nobreak,
      "%d vs %d" % (r_break, r_nobreak))
# 同一个值在 free=1e6 时会被"预分配不得 >= 可用空间"的 >>=4 循环压到 2^16
r_squash = iomap_prealloc_size(ip, off_fsb * BS, 2, AB, DA, FREE, LOW)
check("配对：free 只有 1e6 时 2^20 被 >>=4 压成 2^16", r_squash == 1 << 16,
      "%d" % r_squash)

print("== 6. 上限与节流 ==")
# alloc 超过 MAX → 改用 XFS_B_TO_FSB(offset)
big = XFS_MAX_BMBT_EXTLEN // 2 + 1
ip = mk([Extent(0, big, 0x1000)], 1 << 40)
off = 3 << 30                               # 3 GiB 处
r = iomap_prealloc_size(ip, off, 1, AB, DA, FREE, LOW)
check("plen*2 超过 MAX → 改用 offset 的 fsb", r == rounddown_pow_of_two(off >> BLOCKLOG),
      "offset=3GiB → %d 块" % r)

# 低空间节流：shift=2 把 128 砍到 32
low_free = [10_000, 20_000, 30_000, 40_000, 50_000]
ip = mk([Extent(0, 64, 0x1000)], 1 << 22)
check("shift=2 时 128 → 32",
      iomap_prealloc_size(ip, 64 * BS, 1, AB, DA, 49_999, low_free) == 32)
check("shift=3 时 128 → 16（rounddown 16）",
      iomap_prealloc_size(ip, 64 * BS, 1, AB, DA, 39_999, low_free) == 16)
check("shift=4 时 128 → 8 → 抬到下限 16",
      iomap_prealloc_size(ip, 64 * BS, 1, AB, DA, 29_999, low_free) == 16)
check("配额更严时取 min(alloc, qblocks)",
      iomap_prealloc_size(ip, 64 * BS, 1, AB, DA, FREE, LOW, qblocks=17) == 16,
      "qblocks=17 → min(128,17)=17 → rounddown 16")

# 预分配大于可用空间：>>=4 循环
ip = mk([Extent(0, 64, 0x1000)], 1 << 22)
check("alloc 128 而 free=100 → 128>>4=8 → 抬到 16",
      iomap_prealloc_size(ip, 64 * BS, 1, AB, DA, 100, set_low_space_thresholds(10_000))
      == 16)

print("== 7. 延迟分配生命周期 ==")
ip = Inode(0, [])
f = DelallocFile(ip, AB, DA, FREE, 2_000_000)
f.write(0, 4 * BS)
check("write 后仍是 delalloc 状态", f.state == DELALLOC)
check("write 不产生盘上 extent", len(ip.extents) == 0)
check("但 isize 已经更新", ip.isize_bytes == 4 * BS)
f.writeback()
check("writeback 后进入 unwritten", f.state == UNWRITTEN)
check("isize 16KiB < allocsize 64KiB → 预分配 0，只落写到的 4 块",
      ip.extents[0].blockcount == 4, "blockcount=%d" % ip.extents[0].blockcount)
f.io_complete()
check("io 完成后转 written", f.state == WRITTEN)

# 顺序追加：第二次写 64K 之后触发投机预分配
f.write(4 * BS, 64 * KiB)
f.writeback()
check("第二次 writeback 落 16 块（64K/4K）", ip.extents[-1].blockcount == 16,
      "blockcount=%d" % ip.extents[-1].blockcount)
check("speculative 为 0（这次没有多要）", f.speculative == 0)
f.io_complete()

# 大文件连续追加 → 膨胀的预分配会被 release 砍掉
ip2 = Inode(0, [])
f2 = DelallocFile(ip2, AB, DA, FREE, 2_000_000)
f2.write(0, 1 << 20)                       # 1 MiB
f2.writeback()
n_before = ip2.extents[-1].blockcount
f2.io_complete()
cut = f2.release()
check("1MiB 单写：落 256 块", n_before == 256, "=%d" % n_before)
check("release 砍掉 EOF 之外部分", cut == 0, "cut=%d" % cut)

ip3 = Inode(0, [])
f3 = DelallocFile(ip3, AB, DA, FREE, 2_000_000)
f3.write(0, 1 << 20)
f3.writeback()
f3.write(1 << 20, 4 * BS)                  # 再写 1 块
f3.writeback()
total = sum(e.blockcount for e in ip3.extents)
cut = f3.release()
check("追加写后 release 砍掉多余的投机预分配", cut > 0, "cut=%d" % cut)
check("release 后总块数 == EOF 所需（1MiB+16KiB = 260 块）",
      total - cut == (1 << 20) // BS + 4,
      "%d vs %d" % (total - cut, (1 << 20) // BS + 4))

print()
print("TOTAL: %d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
