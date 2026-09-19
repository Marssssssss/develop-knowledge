"""稀疏文件模型：SEEK_HOLE/SEEK_DATA 的精确语义 + fallocate 五种模式。

事实来源（全部实读）：

  * lseek(2) —— SEEK_DATA / SEEK_HOLE 的边界规则（洞中间返回 offset、
                文件末尾有隐式洞、offset 越过 EOF 报 ENXIO）、
                "文件系统没有义务报告洞"、最简实现的退化形态、支持该操作的 fs 清单
  * fallocate(2) —— 五种 mode 的语义与各自的 EINVAL 条件、按块向上取整、
                PUNCH_HOLE 必须 OR KEEP_SIZE、COLLAPSE/INSERT 的粒度要求
  * include/uapi/linux/fiemap.h —— FIEMAP_EXTENT_UNWRITTEN（已分配但无数据）
"""

BLOCK = 4096

SEEK_DATA = 3
SEEK_HOLE = 4

# ---- fallocate(2) 的 mode 位 ----
FALLOC_FL_KEEP_SIZE = 0x01
FALLOC_FL_PUNCH_HOLE = 0x02
FALLOC_FL_UNSHARE_RANGE = 0x40
FALLOC_FL_COLLAPSE_RANGE = 0x08
FALLOC_FL_ZERO_RANGE = 0x10
FALLOC_FL_INSERT_RANGE = 0x20

FIEMAP_EXTENT_LAST = 0x0001
FIEMAP_EXTENT_UNWRITTEN = 0x0800


class SparseFile(object):
    """块粒度的稀疏文件。

    data     : 有真实数据的块
    unwritten: 已预分配但没写过的块（读出来是 0，fiemap 会打 UNWRITTEN）
    其余块即"洞"。
    """

    def __init__(self, size=0, block=BLOCK):
        self.size = size
        self.block = block
        self.data = set()       # 块号
        self.unwritten = set()  # 块号

    # ---- 基本查询 ----
    def nblocks(self):
        return (self.size + self.block - 1) // self.block

    def is_hole(self, blk):
        return blk not in self.data and blk not in self.unwritten

    def allocated_blocks(self):
        return len(self.data | self.unwritten)

    def apparent_bytes(self):
        return self.size

    def allocated_bytes(self):
        return self.allocated_blocks() * self.block

    # ---- lseek(2) ----
    def lseek(self, offset, whence):
        """只实现 SEEK_DATA / SEEK_HOLE。返回新偏移，或抛 OSError(ENXIO)。"""
        if offset > self.size:
            raise OSError("ENXIO: offset 越过文件末尾")
        if whence not in (SEEK_DATA, SEEK_HOLE):
            raise OSError("EINVAL: 只支持 SEEK_DATA / SEEK_HOLE")
        blk = offset // self.block
        last = self.nblocks()
        want_hole = (whence == SEEK_HOLE)
        for b in range(blk, last):
            cur_hole = self.is_hole(b)
            if cur_hole == want_hole:
                # 已经落在目标区间里：offset 在洞中间时原样返回（手册原文）
                return offset if b == blk else b * self.block
        if want_hole:
            return self.size      # 末尾之后是隐式洞
        raise OSError("ENXIO: offset 落在文件末尾的洞里")

    # ---- 写入 ----
    def write(self, offset, length):
        lo, hi = offset // self.block, (offset + length - 1) // self.block + 1
        for b in range(lo, hi):
            self.data.add(b)
            self.unwritten.discard(b)
        self.size = max(self.size, offset + length)

    def read(self, offset, length=1):
        blk = offset // self.block
        return 0 if self.is_hole(blk) else 1

    # ---- fallocate(2) ----
    def fallocate(self, mode, offset, length):
        if mode & FALLOC_FL_COLLAPSE_RANGE:
            return self._collapse(offset, length, mode)
        if mode & FALLOC_FL_INSERT_RANGE:
            return self._insert(offset, length, mode)
        if mode & FALLOC_FL_PUNCH_HOLE:
            if not (mode & FALLOC_FL_KEEP_SIZE):
                raise OSError("EINVAL: PUNCH_HOLE 必须与 KEEP_SIZE 一起给")
            return self._punch(offset, length)
        if mode & FALLOC_FL_ZERO_RANGE:
            return self._zero(offset, length, bool(mode & FALLOC_FL_KEEP_SIZE))
        return self._alloc(offset, length, bool(mode & FALLOC_FL_KEEP_SIZE))

    def _blk_range(self, offset, length):
        """按块向上取整 —— 手册：fallocate 可能分配得比请求的多。"""
        first = offset // self.block
        last = (offset + length - 1) // self.block
        return first, last

    def _alloc(self, offset, length, keep_size):
        first, last = self._blk_range(offset, length)
        for b in range(first, last + 1):
            if b not in self.data:
                self.unwritten.add(b)
        if not keep_size:
            self.size = max(self.size, offset + length)
        return last - first + 1

    def _punch(self, offset, length):
        """打洞：整块从文件里移除，跨界的**部分块被清零**，文件大小不变。"""
        first, last = self._blk_range(offset, length)
        freed = 0
        for b in range(first, last + 1):
            if b in self.data or b in self.unwritten:
                freed += 1
            self.data.discard(b)
            self.unwritten.discard(b)
        return freed

    def _zero(self, offset, length, keep_size):
        """ZERO_RANGE：转成 unwritten extent，只有元数据 IO。"""
        first, last = self._blk_range(offset, length)
        for b in range(first, last + 1):
            self.data.discard(b)
            self.unwritten.add(b)
        if not keep_size:
            self.size = max(self.size, offset + length)
        return last - first + 1

    def _collapse(self, offset, length, mode):
        if mode & ~FALLOC_FL_COLLAPSE_RANGE:
            raise OSError("EINVAL: COLLAPSE_RANGE 不能与其他标志并用")
        if offset % self.block or length % self.block:
            raise OSError("EINVAL: 粒度必须是文件系统逻辑块大小的倍数")
        if offset + length >= self.size:
            raise OSError("EINVAL: 区间触及或越过 EOF，请改用 ftruncate")
        self._shift(offset, -length)
        self.size -= length
        return length

    def _insert(self, offset, length, mode):
        if mode & ~FALLOC_FL_INSERT_RANGE:
            raise OSError("EINVAL: INSERT_RANGE 不能与其他标志并用")
        if offset % self.block or length % self.block:
            raise OSError("EINVAL: 粒度必须是文件系统逻辑块大小的倍数")
        if offset >= self.size:
            raise OSError("EINVAL: offset 达到或越过 EOF，请改用 ftruncate")
        self._shift(offset, length)
        self.size += length
        return length

    def _shift(self, offset, delta):
        """把 [offset, size) 的内容整体平移 delta 字节（按块粒度）。"""
        dblk = delta // self.block
        start = offset // self.block
        old_data = set(self.data)
        old_unw = set(self.unwritten)
        self.data, self.unwritten = set(), set()
        for b in sorted(old_data | old_unw):
            if b < start:
                tgt = b
            else:
                tgt = b + dblk
            if b in old_data:
                self.data.add(tgt)
            else:
                self.unwritten.add(tgt)

    # ---- fiemap ----
    def fiemap(self):
        """合并相邻同类块，输出 (logical, length, flags)。"""
        out = []
        for b in sorted(self.data | self.unwritten):
            flags = FIEMAP_EXTENT_UNWRITTEN if b in self.unwritten else 0
            if out and out[-1][0] + out[-1][1] == b * self.block \
                    and out[-1][2] == flags:
                out[-1][1] += self.block
            else:
                out.append([b * self.block, self.block, flags])
        if out:
            out[-1][2] |= FIEMAP_EXTENT_LAST
        return [tuple(x) for x in out]
