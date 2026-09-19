"""Btrfs 风格 CoW 的 extent 记账模型。

事实来源（全部实读）：

  * btrfs-man5(5) —— datacow/nodatacow、datasum/nodatasum、compress 三者互斥关系、
                     挂载选项"按顺序处理、最后出现者生效"、autodefrag 的 64KiB 判定、
                     碎片整理会打断 reflink、swapfile 必须 NODATACOW 且预分配无洞、
                     subvol 与 subvolid 必须指向同一 subvolume
  * include/uapi/linux/fiemap.h —— FIEMAP_EXTENT_* / FIEMAP_FLAG_* 的数值与语义

模型只记"物理块有没有被多个文件引用"，这正是 CoW 省空间与放大空间的根源。
"""

BLOCK_SIZE = 4096

# ---- fiemap.h: fe_flags ----
FIEMAP_EXTENT_LAST = 0x00000001
FIEMAP_EXTENT_UNKNOWN = 0x00000002
FIEMAP_EXTENT_DELALLOC = 0x00000004   # 位置未定，同时置 UNKNOWN
FIEMAP_EXTENT_ENCODED = 0x00000008
FIEMAP_EXTENT_DATA_ENCRYPTED = 0x00000080
FIEMAP_EXTENT_NOT_ALIGNED = 0x00000100
FIEMAP_EXTENT_DATA_INLINE = 0x00000200  # 同时置 NOT_ALIGNED
FIEMAP_EXTENT_DATA_TAIL = 0x00000400    # 同时置 NOT_ALIGNED
FIEMAP_EXTENT_UNWRITTEN = 0x00000800    # 已分配但无数据（读出来是 0）
FIEMAP_EXTENT_MERGED = 0x00001000
FIEMAP_EXTENT_SHARED = 0x00002000       # 与其他文件共享空间

# ---- fiemap.h: fm_flags ----
FIEMAP_FLAG_SYNC = 0x00000001
FIEMAP_FLAG_XATTR = 0x00000002
FIEMAP_FLAG_CACHE = 0x00000004
FIEMAP_FLAGS_COMPAT = FIEMAP_FLAG_SYNC | FIEMAP_FLAG_XATTR

FLAG_NAMES = [
    (FIEMAP_EXTENT_LAST, "LAST"), (FIEMAP_EXTENT_UNKNOWN, "UNKNOWN"),
    (FIEMAP_EXTENT_DELALLOC, "DELALLOC"), (FIEMAP_EXTENT_ENCODED, "ENCODED"),
    (FIEMAP_EXTENT_DATA_ENCRYPTED, "DATA_ENCRYPTED"),
    (FIEMAP_EXTENT_NOT_ALIGNED, "NOT_ALIGNED"),
    (FIEMAP_EXTENT_DATA_INLINE, "DATA_INLINE"),
    (FIEMAP_EXTENT_DATA_TAIL, "DATA_TAIL"),
    (FIEMAP_EXTENT_UNWRITTEN, "UNWRITTEN"), (FIEMAP_EXTENT_MERGED, "MERGED"),
    (FIEMAP_EXTENT_SHARED, "SHARED"),
]


def flag_names(flags):
    return [n for b, n in FLAG_NAMES if flags & b]


class Allocator(object):
    """物理块分配器：只关心每块的引用计数。"""

    def __init__(self, block_size=BLOCK_SIZE):
        self.block_size = block_size
        self.next_phys = 0
        self.refs = {}  # phys_block_index -> 引用计数

    def alloc(self, blocks=1):
        p = self.next_phys
        self.next_phys += blocks
        for i in range(p, p + blocks):
            self.refs[i] = 1
        return p

    def share(self, phys, blocks=1):
        for i in range(phys, phys + blocks):
            self.refs[i] = self.refs.get(i, 0) + 1

    def drop(self, phys, blocks=1):
        for i in range(phys, phys + blocks):
            self.refs[i] -= 1
            if self.refs[i] == 0:
                del self.refs[i]

    def refcount(self, phys):
        return self.refs.get(phys, 0)

    def live_blocks(self):
        return len(self.refs)

    def live_bytes(self):
        return self.live_blocks() * self.block_size


class Extent(object):
    def __init__(self, logical, phys, blocks, unwritten=False):
        self.logical = logical      # 文件内逻辑块号
        self.phys = phys            # 物理块号
        self.blocks = blocks
        self.unwritten = unwritten  # 预分配但没写过数据

    def end(self):
        return self.logical + self.blocks


class File(object):
    def __init__(self, name, extents, nocow=False):
        self.name = name
        self.extents = extents
        self.nocow = nocow          # 带 NODATACOW 属性（chattr +C）

    def blocks(self):
        return sum(e.blocks for e in self.extents)


def make_file(alloc, name, blocks, nocow=False):
    phys = alloc.alloc(blocks)
    return File(name, [Extent(0, phys, blocks)], nocow=nocow)


def reflink_copy(alloc, src):
    """cp --reflink：只复制元数据，物理块引用计数 +1。"""
    out = File(src.name + ".reflink", [])
    for e in src.extents:
        alloc.share(e.phys, e.blocks)
        out.extents.append(Extent(e.logical, e.phys, e.blocks, e.unwritten))
    out.nocow = src.nocow
    return out


def cow_write(alloc, f, block_off, nblocks=1):
    """写一段区间。返回本次真正新分配的物理块数。

    extent 是**块粒度**的区间：往中间写会把一个 extent 切成三段（头/改/尾），
    只有中间那几块要新分配 —— CoW 的代价正比于"被改的块数"而非文件大小，
    这正是快照便宜、随机写昂贵的原因。
    """
    if f.nocow:
        return 0  # NODATACOW：原地覆盖，不新分配（代价是可能写坏半个块）
    allocated = 0
    out = []
    lo, hi = block_off, block_off + nblocks
    for e in f.extents:
        if e.end() <= lo or e.logical >= hi:
            out.append(e)
            continue
        os_, oe = max(lo, e.logical), min(hi, e.end())
        # 头：仍指向原物理块，引用计数不动
        if os_ > e.logical:
            out.append(Extent(e.logical, e.phys, os_ - e.logical, e.unwritten))
        # 中：复制出来再改写
        delta = os_ - e.logical
        alloc.drop(e.phys + delta, oe - os_)
        new_phys = alloc.alloc(oe - os_)
        allocated += oe - os_
        out.append(Extent(os_, new_phys, oe - os_))
        # 尾：仍指向原物理块（注意物理块号要跟着偏移）
        if oe < e.end():
            out.append(Extent(oe, e.phys + (oe - e.logical),
                              e.end() - oe, e.unwritten))
    out.sort(key=lambda x: x.logical)
    f.extents = out
    return allocated


def defrag(alloc, f):
    """碎片整理：把文件重写成连续的一块 —— 代价是**打断所有 reflink**。

    btrfs-man5 原文：defrag 会 break up the reflinks of COW data（cp --reflink、
    snapshot、去重的数据），"may cause considerable increase of space usage"。
    """
    total = f.blocks()
    for e in f.extents:
        alloc.drop(e.phys, e.blocks)
    phys = alloc.alloc(total)
    f.extents = [Extent(0, phys, total)]
    return total


def fiemap(alloc, f):
    """按 fiemap.h 的规则生成 extent 列表与 fe_flags。"""
    out = []
    for i, e in enumerate(sorted(f.extents, key=lambda x: x.logical)):
        flags = 0
        if alloc.refcount(e.phys) > 1:
            flags |= FIEMAP_EXTENT_SHARED
        if e.unwritten:
            flags |= FIEMAP_EXTENT_UNWRITTEN
        if i == len(f.extents) - 1:
            flags |= FIEMAP_EXTENT_LAST
        out.append((e.logical * alloc.block_size, e.phys * alloc.block_size,
                    e.blocks * alloc.block_size, flags))
    return out


def logical_bytes(alloc, f):
    """文件看起来的大小（不去重）。"""
    return f.blocks() * alloc.block_size


def exclusive_bytes(alloc, f):
    """这个文件**独占**的物理空间：只数引用计数为 1 的块。

    共享块不计入 —— 快照刚建好时，源与快照的 exclusive 都是 0，
    一旦哪边开始写，被改的块就变成它独占。
    """
    n = 0
    for e in f.extents:
        for i in range(e.phys, e.phys + e.blocks):
            if alloc.refcount(i) == 1:
                n += 1
    return n * alloc.block_size


def resolve_mount_options(opts):
    """挂载选项**按顺序**处理，最后出现者生效（btrfs-man5 原文）。

    约束（均来自手册）：
      * nodatacow 隐含 nodatasum，并禁用压缩
      * nodatacow 或 nodatasum 任一启用 → 压缩被禁用
      * 压缩启用 → nodatacow 与 nodatasum 被禁用
      * datasum 隐含 datacow
    """
    st = {"datacow": True, "datasum": True, "compress": False}
    for o in opts:
        if o == "compress":
            st["compress"] = True
            st["datacow"] = True
            st["datasum"] = True
        elif o == "nocompress":
            st["compress"] = False
        elif o == "nodatacow":
            st["datacow"] = False
            st["datasum"] = False
            st["compress"] = False
        elif o == "datacow":
            st["datacow"] = True
        elif o == "nodatasum":
            st["datasum"] = False
            st["compress"] = False
        elif o == "datasum":
            st["datasum"] = True
            st["datacow"] = True
    return st


def swapfile_ok(nocow_set, has_holes):
    """swapfile 的两条硬约束：必须 NODATACOW，且必须预分配（不能有洞）。"""
    return nocow_set and not has_holes
