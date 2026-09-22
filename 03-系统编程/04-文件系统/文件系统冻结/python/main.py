"""演示入口：文件系统冻结与一致性快照。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from freeze import (  # noqa: E402
    FREEZE_EXCL, FREEZE_HOLDER_KERNEL, FREEZE_HOLDER_USERSPACE,
    FREEZE_MAY_NEST, FROZEN_NAME, SB_FREEZE_COMPLETE, SB_FREEZE_FS,
    SB_FREEZE_PAGEFAULT, SB_FREEZE_WRITE, SB_UNFROZEN, SB_WRITERS_NAME,
    Snapshot, SuperBlock, blocks_internal, blocks_pagefault, blocks_write,
    freeze_super, thaw_super,
)


def hdr(t):
    print()
    print("== %s ==" % t)


hdr("sb->s_writers.frozen 的分级（fs/super.c）")
print("  三级 percpu rwsem：%s" % ", ".join(SB_WRITERS_NAME))
for f in (SB_UNFROZEN, SB_FREEZE_WRITE, SB_FREEZE_PAGEFAULT, SB_FREEZE_FS,
          SB_FREEZE_COMPLETE):
    print("  %-22s 挡新写=%-5s 挡页错误=%-5s 挡内部写入=%s"
          % (FROZEN_NAME[f], blocks_write(f), blocks_pagefault(f),
             blocks_internal(f)))
print("  >>> 分级的目的：先只挡新写（页错误仍可服务），等写完再挡页错误，")
print("  >>> 最后才挡内部 fs 线程，中间在 PAGEFAULT 之后 sync 一次。")

hdr("冻结全过程")
sb = SuperBlock()
print("  初始          : %s" % FROZEN_NAME[sb.frozen])
freeze_super(sb, FREEZE_HOLDER_USERSPACE)
print("  freeze        : %s（sync=%d, freeze_fs=%d）"
      % (FROZEN_NAME[sb.frozen], sb.synced, sb.freeze_fs_called))

hdr("半冻结：有写者在跑")
sb2 = SuperBlock()
sb2.active[0] = 1
r = freeze_super(sb2, FREEZE_HOLDER_USERSPACE, wait=False)
print("  有活跃写者    : 返回 %d，停在 %s" % (r, FROZEN_NAME[sb2.frozen]))
print("  再冻一次      : 返回 %d（需 wait_for_partially_frozen）"
      % freeze_super(sb2, FREEZE_HOLDER_USERSPACE))

hdr("只读文件系统：没什么可冻")
sb3 = SuperBlock(rdonly=True)
freeze_super(sb3, FREEZE_HOLDER_USERSPACE)
print("  冻结后        : %s（sync=%d, freeze_fs=%d）"
      % (FROZEN_NAME[sb3.frozen], sb3.synced, sb3.freeze_fs_called))

hdr("嵌套冻结：冻几次就要 thaw 几次")
sb4 = SuperBlock()
freeze_super(sb4, FREEZE_HOLDER_KERNEL | FREEZE_MAY_NEST)
freeze_super(sb4, FREEZE_HOLDER_KERNEL | FREEZE_MAY_NEST)
print("  冻两次        : kcount=%d frozen=%s"
      % (sb4.freeze_kcount, FROZEN_NAME[sb4.frozen]))
thaw_super(sb4, FREEZE_HOLDER_KERNEL)
print("  thaw 一次     : kcount=%d frozen=%s（仍冻结）"
      % (sb4.freeze_kcount, FROZEN_NAME[sb4.frozen]))
thaw_super(sb4, FREEZE_HOLDER_KERNEL)
print("  再 thaw       : frozen=%s，unfreeze_fs 只调了 %d 次"
      % (FROZEN_NAME[sb4.frozen], sb4.unfreeze_fs_called))

hdr("内核与用户是两套计数")
sb5 = SuperBlock()
freeze_super(sb5, FREEZE_HOLDER_KERNEL)
freeze_super(sb5, FREEZE_HOLDER_USERSPACE)
print("  k=%d u=%d" % (sb5.freeze_kcount, sb5.freeze_ucount))
thaw_super(sb5, FREEZE_HOLDER_KERNEL)
print("  只 thaw 内核侧: frozen=%s（用户那份还在）" % FROZEN_NAME[sb5.frozen])

hdr("快照一致性")
sb6 = SuperBlock()
early = Snapshot(sb6, "/dev/vg/lv")
print("  未冻结就拍    : consistent=%s，挂载需 replay=%s"
      % (early.take(), early.needs_replay()))
freeze_super(sb6, FREEZE_HOLDER_USERSPACE)
ok = Snapshot(sb6, "/dev/vg/lv")
print("  冻结后拍      : consistent=%s，挂载需 replay=%s"
      % (ok.take(), ok.needs_replay()))
print("  >>> 未冻结的快照等价于『掉电后的磁盘』，挂载要走日志重放；")
print("  >>> 顺序必须是：fsfreeze --freeze → 拍快照 → fsfreeze --unfreeze")
