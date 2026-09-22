"""XFS 延迟分配（delayed allocation）与 allocsize 挂载选项的可计算模型。

一切常量取自实际读过的源码（见 README 参考资料）：

* ``fs/xfs/libxfs/xfs_format.h``   —— BMBT_* 位宽、XFS_MAX_BMBT_EXTLEN、XFS_MAX_FILEOFF
* ``fs/xfs/xfs_mount.h``           —— XFS_MIN_IO_LOG / XFS_MAX_IO_LOG
* ``fs/xfs/xfs_super.c``           —— m_allocsize_log 默认 16、`ffs(size) - 1` 解析
* ``fs/xfs/xfs_mount.c``           —— m_allocsize_blocks 换算、低空间阈值表
* ``fs/xfs/xfs_iomap.c``           —— xfs_iomap_prealloc_size / xfs_iomap_freesp

模型刻意与 C 代码同构：变量名、判断顺序、位运算都照抄，
这样"模型与内核不一致"才是 bug，而不是"模型抽象掉了细节"。
"""

# ---- fs/xfs/libxfs/xfs_format.h -------------------------------------------
BMBT_EXNTFLAG_BITLEN = 1
BMBT_STARTOFF_BITLEN = 54
BMBT_STARTBLOCK_BITLEN = 52
BMBT_BLOCKCOUNT_BITLEN = 21

BMBT_STARTOFF_MASK = (1 << BMBT_STARTOFF_BITLEN) - 1
BMBT_BLOCKCOUNT_MASK = (1 << BMBT_BLOCKCOUNT_BITLEN) - 1

# (xfs_extlen_t) 是 u32，但掩码本身只有 21 位
XFS_MAX_BMBT_EXTLEN = BMBT_BLOCKCOUNT_MASK          # 2097151
XFS_MAX_FILEOFF = BMBT_STARTOFF_MASK + BMBT_BLOCKCOUNT_MASK

# ---- fs/xfs/xfs_mount.h ----------------------------------------------------
PAGE_SHIFT = 12
XFS_MAX_IO_LOG = 30                                  # 1G
XFS_MIN_IO_LOG = PAGE_SHIFT                          # 页大小，x86_64 上为 12

# ---- fs/xfs/xfs_super.c（xfs_init_mount_work 里的默认值）-------------------
XFS_DEFAULT_ALLOCSIZE_LOG = 16                       # 64k

# 低空间档位（fs/xfs/xfs_mount.h 的 enum xfs_low_space）
XFS_LOWSP_1_PCNT = 0
XFS_LOWSP_2_PCNT = 1
XFS_LOWSP_3_PCNT = 2
XFS_LOWSP_4_PCNT = 3
XFS_LOWSP_5_PCNT = 4
XFS_LOWSP_MAX = 5

NULLFSBLOCK = -1


# --------------------------------------------------------------------------
# 内核位运算助手
# --------------------------------------------------------------------------
def ffs(x: int) -> int:
    """ffs(3)：返回最低置位的 1-based 下标，0 表示没有置位。"""
    if x == 0:
        return 0
    return (x & -x).bit_length()


def roundup_pow_of_two(x: int) -> int:
    if x <= 1:
        return 1
    return 1 << (x - 1).bit_length()


def rounddown_pow_of_two(x: int) -> int:
    """注意：C 的 rounddown_pow_of_two(0) 是未定义结果，故调用方要先判 0。"""
    if x <= 0:
        return 0
    return 1 << (x.bit_length() - 1)


# --------------------------------------------------------------------------
# allocsize 挂载选项
# --------------------------------------------------------------------------
def parse_allocsize(value_bytes: int) -> tuple:
    """xfs_fs_parse_param 的 Opt_allocsize 分支。

    C 代码是 ``m_allocsize_log = ffs(size) - 1``，校验放在后面的
    xfs_validate_params（要求落在 [XFS_MIN_IO_LOG, XFS_MAX_IO_LOG]）。
    所以"解析"与"校验"是两步：**非 2 的幂不会报错，只会被取到最低置位**——
    allocsize=96k 实际得到 32k。
    """
    log = ffs(value_bytes) - 1
    ok = XFS_MIN_IO_LOG <= log <= XFS_MAX_IO_LOG
    return log, ok


def allocsize_blocks(allocsize_log: int, blocklog: int) -> int:
    """xfs_mount.c: mp->m_allocsize_blocks = 1U << (m_allocsize_log - sb_blocklog)"""
    return 1 << (allocsize_log - blocklog)


# --------------------------------------------------------------------------
# 低空间阈值
# --------------------------------------------------------------------------
def set_low_space_thresholds(dblocks: int) -> list:
    """xfs_set_low_space_thresholds：先整除 100（截断），再乘 1..5。"""
    d = dblocks // 100
    return [d * (i + 1) for i in range(XFS_LOWSP_MAX)]


def iomap_freesp(free_blocks: int, low_space: list) -> int:
    """xfs_iomap_freesp：跨过 5% 档位给 shift=2，每再低一档 +1。"""
    shift = 0
    if free_blocks < low_space[XFS_LOWSP_5_PCNT]:
        shift = 2
        if free_blocks < low_space[XFS_LOWSP_4_PCNT]:
            shift += 1
        if free_blocks < low_space[XFS_LOWSP_3_PCNT]:
            shift += 1
        if free_blocks < low_space[XFS_LOWSP_2_PCNT]:
            shift += 1
        if free_blocks < low_space[XFS_LOWSP_1_PCNT]:
            shift += 1
    return shift


# --------------------------------------------------------------------------
# extent 与 inode
# --------------------------------------------------------------------------
class Extent:
    """xfs_bmbt_irec 的三要素。startblock 为 NULLFSBLOCK 表示延迟分配。"""

    __slots__ = ("startoff", "blockcount", "startblock")

    def __init__(self, startoff, blockcount, startblock):
        self.startoff = startoff
        self.blockcount = blockcount
        self.startblock = startblock

    def is_nullstartblock(self) -> bool:
        return self.startblock == NULLFSBLOCK

    def __repr__(self):
        return "Extent(off=%d,len=%d,blk=%s)" % (
            self.startoff, self.blockcount,
            "NULL" if self.is_nullstartblock() else self.startblock)


class Inode:
    """xfs_iomap_prealloc_size 需要的那份状态。"""

    def __init__(self, isize_bytes, extents, blocklog=12):
        self.isize_bytes = isize_bytes
        self.extents = list(extents)          # 按 startoff 升序
        self.blocklog = blocklog

    def fsb(self, nbytes):
        return nbytes >> self.blocklog

    def fsb_to_b(self, nblocks):
        return nblocks << self.blocklog

    def prev_extent(self, idx):
        """xfs_iext_prev_extent：返回下标 idx 之前的一条及其下标。"""
        j = idx - 1
        if j < 0:
            return None, j
        return self.extents[j], j


# --------------------------------------------------------------------------
# 动态投机预分配主算法
# --------------------------------------------------------------------------
def iomap_prealloc_size(ip, offset_bytes, icur, allocsize_blocks_, dalign_blocks,
                        free_blocks, low_space, qblocks=None, quota_shift=0):
    """xfs_iomap_prealloc_size 的逐行转写。

    icur 是"覆盖或紧邻 offset 之前的那条 extent"在 ip.extents 中的下标。
    返回预分配块数（fsb 单位）。
    """
    offset_fsb = ip.fsb(offset_bytes)

    # 文件比最小预分配还小 → 完全不预分配（很可能只写这一次）
    if ip.isize_bytes < ip.fsb_to_b(allocsize_blocks_):
        return 0

    prev, cur = ip.prev_extent(icur)
    # 小文件、或写在洞后面 → 直接用最小预分配
    if ip.isize_bytes < ip.fsb_to_b(dalign_blocks) or prev is None or \
            prev.startoff + prev.blockcount < offset_fsb:
        return allocsize_blocks_

    plen = prev.blockcount
    while True:
        got, cur = ip.prev_extent(cur)
        if got is None:
            break
        if plen > XFS_MAX_BMBT_EXTLEN // 2 or got.is_nullstartblock() or \
                got.startoff + got.blockcount != prev.startoff or \
                got.startblock + got.blockcount != prev.startblock:
            break
        plen += got.blockcount
        prev = got

    alloc_blocks = plen * 2
    if alloc_blocks > XFS_MAX_BMBT_EXTLEN:
        alloc_blocks = ip.fsb(offset_bytes)

    # XFS_MAX_BMBT_EXTLEN 不是 2 的幂，先向上取整再取 min，
    # 免得后面 rounddown 把最大预分配也砍一刀。
    alloc_blocks = min(roundup_pow_of_two(XFS_MAX_BMBT_EXTLEN), alloc_blocks)

    freesp = free_blocks
    shift = iomap_freesp(free_blocks, low_space)

    if qblocks is not None:
        alloc_blocks = min(alloc_blocks, qblocks)
    shift = max(shift, quota_shift)

    if shift:
        alloc_blocks >>= shift
    # rounddown_pow_of_two(0) 未定义
    if alloc_blocks:
        alloc_blocks = rounddown_pow_of_two(alloc_blocks)
    if alloc_blocks > XFS_MAX_BMBT_EXTLEN:
        alloc_blocks = XFS_MAX_BMBT_EXTLEN

    while alloc_blocks and alloc_blocks >= freesp:
        alloc_blocks >>= 4
    if alloc_blocks < allocsize_blocks_:
        alloc_blocks = allocsize_blocks_
    return alloc_blocks


# --------------------------------------------------------------------------
# 延迟分配生命周期
# --------------------------------------------------------------------------
DELALLOC = "delalloc"        # 只在内存预留，盘上还没有块
UNWRITTEN = "unwritten"      # 已分配真实块，但内容尚未落盘
WRITTEN = "written"          # 转换完成


class DelallocFile:
    """简化版延迟分配状态机：write → writeback → io completion → release。

    * ``write()`` 只记账，不产生盘上块（st_blocks 不变）
    * ``writeback()`` 才真正挑块，并按上面的启发式多要一段投机预分配
    * ``io_complete()`` 把 unwritten 转成 written
    * ``release()`` 对应 xfs_release/xfs_free_eofblocks：砍掉 EOF 之外的部分
    """

    def __init__(self, ip, allocsize_blocks_, dalign_blocks, free_blocks, dblocks):
        self.ip = ip
        self.allocsize_blocks = allocsize_blocks_
        self.dalign_blocks = dalign_blocks
        self.free_blocks = free_blocks
        self.low_space = set_low_space_thresholds(dblocks)
        self.pending = []            # [(offset_bytes, length_bytes)]
        self.state = DELALLOC
        self.speculative = 0

    def write(self, offset, length):
        self.pending.append((offset, length))
        self.ip.isize_bytes = max(self.ip.isize_bytes, offset + length)
        self.state = DELALLOC

    def writeback(self) -> int:
        """一次 writeback：为最后一条 pending 算预分配并落到 extent 列表。

        返回本次真正落盘的块数。
        """
        if not self.pending:
            return 0
        offset, length = self.pending[-1]
        self.pending = []
        startoff = self.ip.fsb(offset)
        # 覆盖 offset..offset+length 所需的块数（向上取整到块）
        bs = 1 << self.ip.blocklog
        need = ((offset + length + bs - 1) >> self.ip.blocklog) - startoff

        icur = len(self.ip.extents)
        for i, e in enumerate(self.ip.extents):
            if e.startoff >= startoff:
                icur = i
                break

        want = iomap_prealloc_size(
            self.ip, offset, icur, self.allocsize_blocks,
            self.dalign_blocks, self.free_blocks, self.low_space)

        if want >= need:
            total = want
        else:
            total = need
        self.speculative = max(0, total - need)

        self.ip.extents.append(Extent(startoff, total, 0x10000 + startoff))
        self.ip.extents.sort(key=lambda e: e.startoff)
        self.state = UNWRITTEN
        return total

    def io_complete(self):
        if self.state == UNWRITTEN:
            self.state = WRITTEN

    def release(self) -> int:
        """砍掉 EOF 之后的投机预分配，返回回收的块数。"""
        eof_fsb = self.ip.fsb(self.ip.isize_bytes)
        if self.ip.isize_bytes % (1 << self.ip.blocklog):
            eof_fsb += 1
        kept, cut = [], 0
        for e in self.ip.extents:
            if e.startoff >= eof_fsb:
                cut += e.blockcount
                continue
            over = (e.startoff + e.blockcount) - eof_fsb
            if over > 0:
                e.blockcount -= over
                cut += over
            kept.append(e)
        self.ip.extents = kept
        self.speculative = 0
        return cut
