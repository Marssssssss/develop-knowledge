"""文件系统冻结（freeze_super / FSFREEZE）与一致性快照的可计算模型。

来源（全部实际读过，见 README）：
* ``fs/super.c``  —— freeze_super 的分级状态机、may_freeze、freeze_inc
* ``fsfreeze(8)`` —— 用户态工具语义

``sb->s_writers.frozen`` 的取值（fs/super.c 的注释块原文）：

    SB_UNFROZEN:        文件系统正常
    SB_FREEZE_WRITE:    新写被挡、页错误仍允许；等所有写完成后进入下一级
    SB_FREEZE_PAGEFAULT:页错误也被挡；内部 fs 线程仍可改（但不应再弄脏）；
                        等所有页错误完成后 sync 一次
    SB_FREEZE_FS:       内部写入源也被挡；调 ->freeze_fs() 完成后进
    SB_FREEZE_COMPLETE: 主要给文件系统自检"我没在冻结后改东西"用
"""

# ---- sb->s_writers.frozen 的取值 -------------------------------------------
SB_UNFROZEN = 0
SB_FREEZE_WRITE = 1
SB_FREEZE_PAGEFAULT = 2
SB_FREEZE_FS = 3
SB_FREEZE_COMPLETE = 4
SB_FREEZE_LEVELS = 3                 # 只有 WRITE/PAGEFAULT/FS 有 percpu rwsem

FROZEN_NAME = {
    SB_UNFROZEN: "SB_UNFROZEN",
    SB_FREEZE_WRITE: "SB_FREEZE_WRITE",
    SB_FREEZE_PAGEFAULT: "SB_FREEZE_PAGEFAULT",
    SB_FREEZE_FS: "SB_FREEZE_FS",
    SB_FREEZE_COMPLETE: "SB_FREEZE_COMPLETE",
}

# 三个 percpu rwsem 的名字（fs/super.c 的 sb_writers_name[]）
SB_WRITERS_NAME = ("sb_writers", "sb_pagefaults", "sb_internal")

# ---- freeze_holder ---------------------------------------------------------
FREEZE_HOLDER_KERNEL = 1
FREEZE_HOLDER_USERSPACE = 2
FREEZE_MAY_NEST = 4
FREEZE_EXCL = 8
FREEZE_HOLDERS = FREEZE_HOLDER_KERNEL | FREEZE_HOLDER_USERSPACE
FREEZE_FLAGS = FREEZE_HOLDERS | FREEZE_MAY_NEST | FREEZE_EXCL

EBUSY = -16
EINVAL = -22


class SuperBlock:
    """struct super_block 里与冻结相关的那部分状态。"""

    def __init__(self, rdonly=False):
        self.frozen = SB_UNFROZEN
        self.freeze_kcount = 0
        self.freeze_ucount = 0
        self.freeze_owner = None
        self.rdonly = rdonly
        # 各级 percpu rwsem 上"正在进行的写者"计数
        self.active = [0] * SB_FREEZE_LEVELS
        self.synced = 0
        self.freeze_fs_called = 0
        self.unfreeze_fs_called = 0

    @property
    def total(self):
        return self.freeze_kcount + self.freeze_ucount


def may_freeze(sb, who, freeze_owner=None):
    """fs/super.c 的 may_freeze：谁能再冻一次。

    规则：
    * EXCL 只能配 KERNEL，且必须给 owner；已经有 owner 就 False；
      已经冻过多次则把 owner 记成调用者。
    * KERNEL 默认只允许一次，除非带 MAY_NEST
    * USERSPACE 同理，且它**不检查 kcount**——两套计数是分开的
    """
    if who & ~FREEZE_FLAGS:
        return False
    if bin(who & FREEZE_HOLDERS).count("1") > 1:
        return False

    if who & FREEZE_EXCL:
        if not (who & FREEZE_HOLDER_KERNEL):
            return False
        if who & ~(FREEZE_EXCL | FREEZE_HOLDER_KERNEL):
            return False
        if not freeze_owner:
            return False
        if sb.freeze_owner:
            return False
        if sb.freeze_kcount + sb.freeze_ucount:
            sb.freeze_owner = freeze_owner
        return True

    if who & FREEZE_HOLDER_KERNEL:
        return bool(who & FREEZE_MAY_NEST) or sb.freeze_kcount == 0
    if who & FREEZE_HOLDER_USERSPACE:
        return bool(who & FREEZE_MAY_NEST) or sb.freeze_ucount == 0
    return False


def may_unfreeze(sb, who, freeze_owner=None):
    """fs/super.c 的 may_unfreeze：谁能解冻（与 may_freeze 不是同一套规则）。

    几条容易漏的：

    * KERNEL 分支里 ``kcount == 1 && freeze_owner`` → **拒绝**，
      防止别人偷走属于 EXCL 持有者的那一份引用
    * USERSPACE 只看 ``ucount > 0``
    * EXCL 分支：owner 必须匹配；**但总计数 > 1 时会先把 owner 清成
      NULL 再返回 True**（放弃独占声明，只减自己的计数）
    """
    if who & ~FREEZE_FLAGS:
        return False
    if bin(who & FREEZE_HOLDERS).count("1") > 1:
        return False

    if who & FREEZE_EXCL:
        if not (who & FREEZE_HOLDER_KERNEL):
            return False
        if who & ~(FREEZE_EXCL | FREEZE_HOLDER_KERNEL):
            return False
        if who & FREEZE_HOLDER_KERNEL and sb.freeze_kcount == 0:
            return False
        if not sb.freeze_owner:
            return False
        if sb.freeze_owner != freeze_owner:
            return False
        if sb.freeze_kcount + sb.freeze_ucount > 1:
            sb.freeze_owner = None
        return True

    if who & FREEZE_HOLDER_KERNEL:
        if sb.freeze_kcount == 1 and sb.freeze_owner:
            return False
        return sb.freeze_kcount > 0
    if who & FREEZE_HOLDER_USERSPACE:
        return sb.freeze_ucount > 0
    return False


def freeze_inc(sb, who):
    """增加对应持有者的计数，返回总计数。"""
    if who & FREEZE_HOLDER_KERNEL:
        sb.freeze_kcount += 1
    if who & FREEZE_HOLDER_USERSPACE:
        sb.freeze_ucount += 1
    return sb.freeze_kcount + sb.freeze_ucount


def freeze_dec(sb, who):
    """thaw 时减回去。"""
    if who & FREEZE_HOLDER_KERNEL and sb.freeze_kcount:
        sb.freeze_kcount -= 1
    if who & FREEZE_HOLDER_USERSPACE and sb.freeze_ucount:
        sb.freeze_ucount -= 1
    return sb.freeze_kcount + sb.freeze_ucount


def freeze_super(sb, who, freeze_owner=None, wait=True):
    """freeze_super 的转写（省略了锁与重试，只保留状态推进）。

    返回 0 或负 errno。wait=False 时停在半冻结态（用于演示
    wait_for_partially_frozen）。
    """
    if sb.frozen == SB_FREEZE_COMPLETE:
        if may_freeze(sb, who, freeze_owner):
            freeze_inc(sb, who)
            return 0
        return EBUSY

    if sb.frozen != SB_UNFROZEN:
        # wait_for_partially_frozen：等它落到 UNFROZEN 或 COMPLETE
        return EBUSY

    if sb.rdonly:
        # 只读文件系统没什么可冻的，直接标 COMPLETE
        sb.freeze_owner = freeze_owner
        sb.frozen = SB_FREEZE_COMPLETE
        return 0

    # 第一级：挡新写，等已有写完
    sb.frozen = SB_FREEZE_WRITE
    if not _wait_write(sb, SB_FREEZE_WRITE, wait):
        return EBUSY
    # 第二级：挡页错误，之后 sync
    sb.frozen = SB_FREEZE_PAGEFAULT
    if not _wait_write(sb, SB_FREEZE_PAGEFAULT, wait):
        return EBUSY
    sb.synced += 1
    # 第三级：挡内部写入源，调 ->freeze_fs()
    sb.frozen = SB_FREEZE_FS
    if not _wait_write(sb, SB_FREEZE_FS, wait):
        return EBUSY
    sb.freeze_fs_called += 1
    # 源码：WARN_ON_ONCE(freeze_inc(sb, who) > 1); freeze_owner = freeze_owner;
    freeze_inc(sb, who)
    sb.freeze_owner = freeze_owner
    sb.frozen = SB_FREEZE_COMPLETE
    return 0


def wait_for_partially_frozen(sb):
    """wait_for_partially_frozen：等半冻结态落到 UNFROZEN 或 COMPLETE。

    真实内核是 sleep 在 s_writers.frozen 上等另一个 freezer 推进
    （或它失败回滚）。这里简化为：把各级活跃写者清空并回退到 UNFROZEN。
    """
    sb.active = [0] * SB_FREEZE_LEVELS
    sb.frozen = SB_UNFROZEN
    return 0


def _wait_write(sb, level, wait):
    """sb_wait_write：等该级所有写者退出。wait=False 时若有写者就返回 False。"""
    idx = level - 1
    if sb.active[idx]:
        if not wait:
            return False
        sb.active[idx] = 0
    return True


def thaw_super(sb, who, freeze_owner=None):
    """thaw_super_locked 的简化：计数归零才真正解冻。"""
    if sb.frozen != SB_FREEZE_COMPLETE:
        return EINVAL
    if not may_unfreeze(sb, who, freeze_owner):
        return EBUSY
    if freeze_dec(sb, who):
        # 还有其他持有者：只减自己的计数，fs 保持冻结，owner 不动
        return 0
    sb.unfreeze_fs_called += 1
    sb.frozen = SB_UNFROZEN
    sb.freeze_owner = None
    return 0


def blocks_write(frozen):
    """该状态下"新的用户写"是否被挡。"""
    return frozen in (SB_FREEZE_WRITE, SB_FREEZE_PAGEFAULT, SB_FREEZE_FS,
                      SB_FREEZE_COMPLETE)


def blocks_pagefault(frozen):
    """页错误从第二级起被挡（第一级仍允许——这是分级的意义）。"""
    return frozen in (SB_FREEZE_PAGEFAULT, SB_FREEZE_FS, SB_FREEZE_COMPLETE)


def blocks_internal(frozen):
    """内部 fs 线程从第三级起被挡。"""
    return frozen in (SB_FREEZE_FS, SB_FREEZE_COMPLETE)


# --------------------------------------------------------------------------
# 快照一致性：冻结 + LVM/设备级快照
# --------------------------------------------------------------------------
class Snapshot:
    """冻结 + 快照的最小模型。

    关键点：**快照必须在文件系统冻结之后拍**，否则快照里的 fs 镜像
    等价于"掉电后的磁盘"，挂载时要走日志重放（journal replay）。
    """

    def __init__(self, sb, device):
        self.sb = sb
        self.device = device
        self.taken_at_frozen = None
        self.consistent = False

    def take(self) -> bool:
        self.taken_at_frozen = self.sb.frozen
        # 只有完全冻结（或本身就只读）才算"一致性快照"
        self.consistent = (self.sb.frozen == SB_FREEZE_COMPLETE) or self.sb.rdonly
        return self.consistent

    def needs_replay(self) -> bool:
        """挂载这份快照要不要跑日志恢复。"""
        return not self.consistent
