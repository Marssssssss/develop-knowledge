"""XFS 延迟分配生命周期状态机（从 xfsalloc.py 拆出）。

对应内核路径：write() 只建 delalloc 预留 → writeback 时真正挑块并置
XFS_EXT_UNWRITTEN 标志 → I/O 完成后转 written → release/inode reclaim 时
由 xfs_free_eofblocks 砍掉 EOF 之外的投机预分配。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xfsalloc import Extent, iomap_prealloc_size, set_low_space_thresholds  # noqa: E402

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
