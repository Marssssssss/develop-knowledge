"""O_DIRECT 自检：把 open(2) NOTES / statx(2) 的条文变成断言。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from direct_io import (  # noqa: E402
    FALLBACK, FS_BLOCK, LOGICAL_BLOCK, O_DIRECT, O_DSYNC, O_SYNC, STRICT, Fs,
    O_SYNC_METADATA,
    alignment_for, check_io, fork_race, io_cost, open_direct, statx_dioalign,
    sync_guarantee,
)

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %-52s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-52s %s" % (label, detail))


def raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


# ext4：6.1 起支持 STATX_DIOALIGN，实测常见对齐是 512/512
EXT4 = Fs("ext4", mem_align=512, off_align=512, misaligned=STRICT,
          kernel=(6, 6))
# XFS 风格的宽松实现：未对齐静默退回 buffered
XFS = Fs("xfs", mem_align=512, off_align=512, misaligned=FALLBACK,
         kernel=(6, 1))
# 老内核（2.4 系）：拿不到 statx，只能按 4096 猜
OLD24 = Fs("ext3-2.4", mem_align=0, off_align=0, kernel=(2, 4, 30),
           statx_dioalign=False)
# 2.6 但还没到 6.1：按逻辑块 512 猜
MID26 = Fs("ext4-2.6.32", mem_align=0, off_align=0, kernel=(2, 6, 32),
           statx_dioalign=False)
# 不支持 O_DIRECT 的文件系统：open() 直接 EINVAL
TMPFS = Fs("tmpfs", supports_direct=False)
NFS = Fs("nfs", mem_align=512, off_align=512, nfs=True)

print("== 1. 对齐要求的三个代际 ==")
check("6.1+ 用 statx 查到真实对齐",
      statx_dioalign(EXT4) == (512, 512), "%s" % (statx_dioalign(EXT4),))
check("2.6 起按块设备逻辑块 512 猜", alignment_for(MID26) == (512, 512),
      "%s" % (alignment_for(MID26),))
check("2.4 系按文件系统块 4096 猜", alignment_for(OLD24) == (4096, 4096),
      "%s" % (alignment_for(OLD24),))
check("拿不到 statx 时 statx_dioalign 返回 (0,0)",
      statx_dioalign(MID26) == (0, 0))
check("常量口径", FS_BLOCK == 4096 and LOGICAL_BLOCK == 512)

print("== 2. 三样东西都要对齐：地址 / 偏移 / 长度 ==")
check("全对齐 → 真 direct",
      check_io(EXT4, 4096, 4096, 4096) == "direct")
check("缓冲区地址未对齐 → EINVAL",
      raises(lambda: check_io(EXT4, 300, 4096, 4096)))
check("偏移量未对齐 → EINVAL",
      raises(lambda: check_io(EXT4, 4096, 1000, 4096)))
check("偏移量 512 在 512 对齐下合法（换个用例确认上面不是巧合）",
      check_io(EXT4, 4096, 512, 4096) == "direct")
check("长度未对齐 → EINVAL",
      raises(lambda: check_io(EXT4, 4096, 4096, 1000)))
check("512 偏移在 512 对齐下合法", check_io(EXT4, 512, 512, 512) == "direct")
check("512 偏移在 4096 对齐下非法（2.4 口径）",
      raises(lambda: check_io(OLD24, 4096, 512, 4096)))

print("== 3. 未对齐也可能静默退回 buffered ==")
check("宽松实现退回 buffered",
      check_io(XFS, 4096, 300, 4096) == FALLBACK)
check("退回后不报错（静默，最难查的性能坑）",
      not raises(lambda: check_io(XFS, 4096, 300, 4096)))

print("== 4. 文件系统不支持 O_DIRECT ==")
check("open(O_DIRECT) 直接 EINVAL",
      raises(lambda: open_direct(TMPFS, O_DIRECT)))
check("不带 O_DIRECT 就正常", open_direct(TMPFS, 0) == "buffered")
check("读不到对齐值时 check_io 也 EINVAL",
      raises(lambda: check_io(TMPFS, 4096, 4096, 4096)))

print("== 5. O_DIRECT 不等于 O_SYNC ==")
g = sync_guarantee(O_DIRECT)
check("只给 O_DIRECT：数据都不保证落地",
      g["data"] is False and g["metadata"] is False, "%s" % g)
g = sync_guarantee(O_DIRECT | O_SYNC)
check("O_DIRECT|O_SYNC：数据与元数据都保证",
      g["data"] and g["metadata"], "%s" % g)
g = sync_guarantee(O_DIRECT | O_DSYNC)
check("O_DIRECT|O_DSYNC：只保证数据（等价 fdatasync）",
      g["data"] and not g["metadata"], "%s" % g)
check("三个标志位互不相等", len({O_DIRECT, O_SYNC, O_DSYNC}) == 3)
check("glibc 里 O_SYNC 含 O_DSYNC 位（所以不能用 &O_SYNC 判元数据）",
      O_SYNC & O_DSYNC == O_DSYNC, "O_SYNC=%#o O_DSYNC=%#o" % (O_SYNC, O_DSYNC))
check("剥掉 O_DSYNC 后剩下的位才代表元数据同步",
      O_SYNC_METADATA == 0o4000000 and (O_DSYNC & O_SYNC_METADATA) == 0,
      "%#o" % O_SYNC_METADATA)

print("== 6. 禁止与 fork() 并发 ==")
check("私有映射（堆/静态区）→ 风险",
      raises(lambda: fork_race("private")))
check("MAP_SHARED → 允许", fork_race("map_shared") == "ok")
check("shmat → 允许", fork_race("shm") == "ok")
check("MADV_DONTFORK → 允许", fork_race("dontfork") == "ok")

print("== 7. 混用 buffered 与 direct 的代价 ==")
size, overlap, switches = 64 * 4096, 4096, 10
pure = io_cost("direct", size)
mixed = io_cost("mixed", size, switches, overlap)
check("重叠区混用比纯 direct 贵", mixed > pure, "%d > %d" % (mixed, pure))
check("贵出来的量 = 切换次数 × 2 × 重叠量",
      mixed - pure == switches * 2 * overlap, "%d" % (mixed - pure))
check("不重叠时没有额外代价",
      io_cost("mixed", size, switches, 0) == pure)

print("== 8. NFS 上的 O_DIRECT 只在客户端生效 ==")
check("客户端仍走 direct", check_io(NFS, 4096, 4096, 4096) == "direct")
check("但服务端可能仍在缓存（协议传不过这个标志）", NFS.nfs is True)

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
