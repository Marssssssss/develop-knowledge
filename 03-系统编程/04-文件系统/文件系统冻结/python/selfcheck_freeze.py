"""文件系统冻结自检：把 fs/super.c 的条文变成断言。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from freeze import (  # noqa: E402
    may_unfreeze,
    EBUSY, EINVAL, FREEZE_EXCL, FREEZE_FLAGS, FREEZE_HOLDER_KERNEL,
    FREEZE_HOLDER_USERSPACE, FREEZE_HOLDERS, FREEZE_MAY_NEST,
    FROZEN_NAME, SB_FREEZE_COMPLETE, SB_FREEZE_FS, SB_FREEZE_LEVELS,
    SB_FREEZE_PAGEFAULT, SB_FREEZE_WRITE, SB_UNFROZEN, SB_WRITERS_NAME,
    Snapshot, SuperBlock, blocks_internal, blocks_pagefault, blocks_write,
    freeze_inc, freeze_super, may_freeze, thaw_super,
    wait_for_partially_frozen,
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


K = FREEZE_HOLDER_KERNEL
U = FREEZE_HOLDER_USERSPACE
N = FREEZE_MAY_NEST
X = FREEZE_EXCL

print("== 1. frozen 的取值与等级数（fs/super.c） ==")
check("SB_UNFROZEN == 0", SB_UNFROZEN == 0)
check("SB_FREEZE_WRITE == 1", SB_FREEZE_WRITE == 1)
check("SB_FREEZE_PAGEFAULT == 2", SB_FREEZE_PAGEFAULT == 2)
check("SB_FREEZE_FS == 3", SB_FREEZE_FS == 3)
check("SB_FREEZE_COMPLETE == 4", SB_FREEZE_COMPLETE == 4)
check("SB_FREEZE_LEVELS == 3（只有前三级有 percpu rwsem）",
      SB_FREEZE_LEVELS == 3)
check("COMPLETE 不参与 rwsem（4 > LEVELS）", SB_FREEZE_COMPLETE > SB_FREEZE_LEVELS)
check("三个 rwsem 名字是 sb_writers/sb_pagefaults/sb_internal",
      SB_WRITERS_NAME == ("sb_writers", "sb_pagefaults", "sb_internal"))
check("五个状态名齐全", len(FROZEN_NAME) == 5)

print("== 2. 分级到底挡什么（三条方向性断言） ==")
check("UNFROZEN 什么都不挡",
      not blocks_write(SB_UNFROZEN) and not blocks_pagefault(SB_UNFROZEN)
      and not blocks_internal(SB_UNFROZEN))
check("WRITE 级：挡新写，但**仍允许页错误**",
      blocks_write(SB_FREEZE_WRITE) and not blocks_pagefault(SB_FREEZE_WRITE)
      and not blocks_internal(SB_FREEZE_WRITE))
check("PAGEFAULT 级：页错误也挡，内部 fs 线程仍可动",
      blocks_write(SB_FREEZE_PAGEFAULT) and blocks_pagefault(SB_FREEZE_PAGEFAULT)
      and not blocks_internal(SB_FREEZE_PAGEFAULT))
check("FS 级：连内部写入源也挡",
      blocks_write(SB_FREEZE_FS) and blocks_pagefault(SB_FREEZE_FS)
      and blocks_internal(SB_FREEZE_FS))
check("COMPLETE 级：全挡（它只是给 fs 自检用的辅助态）",
      blocks_write(SB_FREEZE_COMPLETE) and blocks_pagefault(SB_FREEZE_COMPLETE)
      and blocks_internal(SB_FREEZE_COMPLETE))
check("单调性：等级越高挡得越多",
      all(blocks_write(f) for f in range(SB_FREEZE_WRITE, SB_FREEZE_COMPLETE + 1))
      and sum(blocks_pagefault(f) for f in range(5)) == 3
      and sum(blocks_internal(f) for f in range(5)) == 2)

print("== 3. may_freeze：谁能再冻一次 ==")
sb = SuperBlock()
check("全新 fs 上 KERNEL 可冻", may_freeze(sb, K))
check("全新 fs 上 USERSPACE 可冻", may_freeze(sb, U))
check("KERNEL 与 USERSPACE 不能同时给（hweight>1）",
      not may_freeze(sb, K | U))
check("未知标志位会被拒绝", not may_freeze(sb, K | 0x40))
sb.freeze_kcount = 1
check("已冻过一次（kernel）→ 不带 MAY_NEST 再冻被拒", not may_freeze(sb, K))
check("带 MAY_NEST 就可以", may_freeze(sb, K | N))
check("user 计数仍是 0，USERSPACE 照样能冻", may_freeze(sb, U))
sb.freeze_ucount = 1
check("user 也冻过一次 → USERSPACE 不带 MAY_NEST 被拒", not may_freeze(sb, U))

print("== 4. FREEZE_EXCL 的额外约束 ==")
sb2 = SuperBlock()
check("EXCL 必须配 KERNEL：EXCL|USERSPACE 被拒", not may_freeze(sb2, X | U))
check("EXCL 必须给 owner：EXCL|KERNEL 无 owner 被拒",
      not may_freeze(sb2, X | K))
check("EXCL|KERNEL|MAY_NEST 也被拒（EXCL 只允许与 KERNEL 组合）",
      not may_freeze(sb2, X | K | N))
check("EXCL|KERNEL 带 owner 可以", may_freeze(sb2, X | K, "owner-a"))
sb2.freeze_owner = "owner-a"
check("已经有 owner 了 → 再申请 EXCL 被拒",
      not may_freeze(sb2, X | K, "owner-b"))
check("EXCL 分支里『已冻过多次才记 owner』：干净时 owner 不写",
      SuperBlock().freeze_owner is None)

print("== 5. freeze_super 的状态推进 ==")
sb3 = SuperBlock()
check("初始 UNFROZEN", sb3.frozen == SB_UNFROZEN)
r = freeze_super(sb3, U)
check("冻结成功返回 0", r == 0)
check("最终落到 SB_FREEZE_COMPLETE", sb3.frozen == SB_FREEZE_COMPLETE)
check("过程中 sync 了 1 次", sb3.synced == 1)
check("调用了 ->freeze_fs() 一次", sb3.freeze_fs_called == 1)
check("ucount 变成 1", sb3.freeze_ucount == 1 and sb3.freeze_kcount == 0)
r = thaw_super(sb3, U)
check("thaw 成功", r == 0)
check("thaw 后回到 UNFROZEN", sb3.frozen == SB_UNFROZEN)
check("调用了 ->unfreeze_fs()", sb3.unfreeze_fs_called == 1)

sb4 = SuperBlock()
sb4.active[0] = 1                       # 有一个正在进行的写者
freeze_super(sb4, U, wait=False)
check("有活跃写者且不等待 → 停在 WRITE 级", sb4.frozen == SB_FREEZE_WRITE,
      FROZEN_NAME[sb4.frozen])
r = freeze_super(sb4, U)
check("半冻结态上再冻 → EBUSY（要走 wait_for_partially_frozen）", r == EBUSY)
sb4.active[0] = 1
sb4.frozen = SB_UNFROZEN
r = freeze_super(sb4, U, wait=False)
check("重新冻结仍停在 WRITE 级", r == EBUSY and sb4.frozen == SB_FREEZE_WRITE)
wait_for_partially_frozen(sb4)
check("wait_for_partially_frozen 后回到 UNFROZEN", sb4.frozen == SB_UNFROZEN)
r = freeze_super(sb4, U)
check("重试后能一路走完", r == 0 and sb4.frozen == SB_FREEZE_COMPLETE)

print("== 6. 只读文件系统：直接标 COMPLETE ==")
sb5 = SuperBlock(rdonly=True)
r = freeze_super(sb5, U)
check("只读 fs 冻结返回 0", r == 0)
check("不经过分级，直接 COMPLETE", sb5.frozen == SB_FREEZE_COMPLETE)
check("没有 sync（本来就没脏数据）", sb5.synced == 0)
check("也没调 ->freeze_fs()", sb5.freeze_fs_called == 0)

print("== 7. 嵌套冻结：多次冻要多次 thaw ==")
sb6 = SuperBlock()
check("第一次冻", freeze_super(sb6, K | N) == 0)
check("再冻一次（MAY_NEST）", freeze_super(sb6, K | N) == 0)
check("kcount == 2", sb6.freeze_kcount == 2)
r = thaw_super(sb6, K)
check("第一次 thaw 后仍冻结（计数没归零）", r == 0 and sb6.frozen == SB_FREEZE_COMPLETE)
check("kcount == 1", sb6.freeze_kcount == 1)
r = thaw_super(sb6, K)
check("第二次 thaw 才真正解冻", r == 0 and sb6.frozen == SB_UNFROZEN)
check("->unfreeze_fs() 只在最后一次调用", sb6.unfreeze_fs_called == 1)

print("== 8. 内核持有者与用户持有者是两套计数 ==")
sb7 = SuperBlock()
freeze_super(sb7, K)
freeze_super(sb7, U)
check("kcount=1 ucount=1", sb7.freeze_kcount == 1 and sb7.freeze_ucount == 1)
thaw_super(sb7, K)
check("只 thaw 内核侧，fs 仍是冻结的",
      sb7.frozen == SB_FREEZE_COMPLETE and sb7.freeze_ucount == 1)
thaw_super(sb7, U)
check("两边都 thaw 才解冻", sb7.frozen == SB_UNFROZEN)

print("== 9. EXCL 冻结必须拿同一个 owner 才能 thaw ==")
sb8 = SuperBlock()
freeze_super(sb8, X | K, "owner-a")
check("冻结成功且记下 owner", sb8.freeze_owner == "owner-a")
r = thaw_super(sb8, X | K, "owner-b")
check("别人来 thaw → EBUSY", r == EBUSY)
check("fs 仍是冻结的", sb8.frozen == SB_FREEZE_COMPLETE)
r = thaw_super(sb8, X | K, "owner-a")
check("正确 owner 才能 thaw", r == 0 and sb8.frozen == SB_UNFROZEN)
check("thaw 后 owner 清空", sb8.freeze_owner is None)

sb8b = SuperBlock()
freeze_super(sb8b, X | K, "owner-a")
check("EXCL 冻结后再用普通 KERNEL 解冻会被拒（不许偷 owner 的引用）",
      thaw_super(sb8b, K) == EBUSY)
check("被拒后仍是冻结的", sb8b.frozen == SB_FREEZE_COMPLETE)

sb8c = SuperBlock()
freeze_super(sb8c, X | K, "owner-a")
freeze_super(sb8c, U)
check("EXCL + USERSPACE 双重冻结", sb8c.freeze_kcount == 1
      and sb8c.freeze_ucount == 1)
r = thaw_super(sb8c, X | K, "owner-a")
check("总计数 > 1 时 EXCL thaw 成功但先放弃 owner",
      r == 0 and sb8c.freeze_owner is None and sb8c.frozen == SB_FREEZE_COMPLETE)
check("还剩 user 那一份", sb8c.freeze_ucount == 1)
r = thaw_super(sb8c, U)
check("最后 user thaw 才真正解冻", r == 0 and sb8c.frozen == SB_UNFROZEN)

print("== 10. 快照一致性 ==")
sb9 = SuperBlock()
snap_early = Snapshot(sb9, "/dev/vg/lv")
check("未冻结就拍快照 → 不一致", not snap_early.take())
check("不一致的快照挂载时要 replay", snap_early.needs_replay())
check("拍的时候 fs 确实还没冻结", snap_early.taken_at_frozen == SB_UNFROZEN)

freeze_super(sb9, U)
snap_ok = Snapshot(sb9, "/dev/vg/lv")
check("冻结后拍快照 → 一致", snap_ok.take())
check("一致的快照不需要 replay", not snap_ok.needs_replay())
thaw_super(sb9, U)

sb10 = SuperBlock(rdonly=True)
snap_ro = Snapshot(sb10, "/dev/vg/lv")
check("只读 fs 本身就是一致的", snap_ro.take())
check("也不需要 replay", not snap_ro.needs_replay())
check("结论：快照必须在 FSFREEZE 之后拍，否则等价于掉电镜像",
      snap_early.needs_replay() and not snap_ok.needs_replay())

print()
print("TOTAL: %d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
