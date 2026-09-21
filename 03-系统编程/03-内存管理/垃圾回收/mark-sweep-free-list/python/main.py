"""
标记-清除(mark-sweep) + 空闲链表合并(coalescing) —— 原理级模型。

依据（本 README 参考资料中实际读过的原文）：
  * Doug Lea, "A Memory Allocator"(dlmalloc 设计说明)：
      - 两大核心不变式 = **Boundary Tags**(边界标记) + **Binning**(分箱)
      - 128 个固定宽度 bin，近似对数间隔；< 512 字节的 bin **每个只装一种尺寸**，间隔 8 字节
      - 搜索是 smallest-first / **best-fit**；bin 内按尺寸排序，同尺寸 **最旧优先**(oldest-first)
      - 算法总类目 = **best-first with coalescing**
      - 新版本 **在使用中的 chunk 上省略尾部 trailer**；最小可分配 chunk：
        32 位指针 16 字节 / 64 位指针 24 字节
      - wilderness chunk 当成"比所有 chunk 都大"来参与 best-first 扫描
      - mmap 阈值默认 1 MB，且仅当 arena 里满足不了时才用
      - 缓存策略之一 = **Deferred Coalescing**(延迟合并)
  * Linux man-pages madvise(2)：MADV_DONTNEED 对匿名私有映射 → 之后访问得到零页

本模块把上面每一条都落成可断言的行为。
"""

WORD = 8                 # 64 位：1 word = 8 字节
HDR_WORDS = 1            # chunk 头：size + flag，占 1 word

# dlmalloc 原文：空闲 chunk 里要同时放 size 与两条 bin 链指针
#   32 位: 4(size) + 4 + 4 = 12 -> 8 字节对齐 -> 16 字节
#   64 位: 8(size) + 8 + 8 = 24 字节
MIN_CHUNK_BYTES_32 = 16
MIN_CHUNK_BYTES_64 = 24
MIN_CHUNK_WORDS_64 = MIN_CHUNK_BYTES_64 // WORD        # 3

NBINS = 128              # dlmalloc: "a surprisingly large number (128) of fixed-width bins"
SMALL_BIN_LIMIT = 512    # < 512 字节的 bin 每个只装一种尺寸
SMALL_BIN_SPACING = 8    # 间隔 8 字节

DEFAULT_MMAP_THRESHOLD = 1 << 20   # dlmalloc 原文 "currently by default 1MB"


class Block:
    """一个 chunk。addr 是 word 下标，size 是**含头**总字数。"""

    __slots__ = ("addr", "size", "free", "seq", "mmapped", "has_footer")

    def __init__(self, addr, size, free=True, seq=0, mmapped=False, has_footer=True):
        self.addr = addr
        self.size = size
        self.free = free
        self.seq = seq                 # 分配/释放次序，用于 oldest-first
        self.mmapped = mmapped
        self.has_footer = has_footer   # 经典边界标记：所有 chunk 都有尾标记

    @property
    def payload(self):
        return self.addr + HDR_WORDS

    @property
    def usable(self):
        return self.size - HDR_WORDS


class Heap:
    def __init__(self, nwords, policy="best", defer_coalesce=False,
                 classic_tags=False, mmap_threshold=DEFAULT_MMAP_THRESHOLD):
        self.n = nwords
        self.policy = policy                 # first | best | dlmalloc(best+wilderness)
        self.defer = defer_coalesce          # 延迟合并开关
        self.classic_tags = classic_tags     # True = 使用中 chunk 也保留 footer
        self.mmap_threshold = mmap_threshold
        self.seq = 0
        self.mmap_base = nwords
        self.blocks = [Block(0, nwords, True, 0, has_footer=classic_tags)]

    # ---------- 查询 ----------
    def _idx_of(self, addr):
        for i, b in enumerate(self.blocks):
            if b.addr <= addr < b.addr + b.size:
                return i
        raise KeyError("addr not in heap: %r" % addr)

    def block_at(self, payload_addr):
        return self.blocks[self._idx_of(payload_addr - HDR_WORDS)]

    def wilderness(self):
        return self.blocks[-1]

    def free_blocks(self):
        return [b for b in self.blocks if b.free]

    def fragmentation_holes(self):
        return sum(1 for b in self.blocks if b.free)

    # ---------- binning（dlmalloc 口径） ----------
    @staticmethod
    def bin_index(nbytes):
        """< 512 字节：每个 bin 一种尺寸，间隔 8 字节；>= 512：近似对数间隔。"""
        if nbytes < SMALL_BIN_LIMIT:
            return max(1, (nbytes + SMALL_BIN_SPACING - 1) // SMALL_BIN_SPACING)
        # 近似对数：每翻倍 4 个 bin（与 jemalloc 表 1 同构，dlmalloc 原文只说
        # "approximately logarithmically spaced"，这里取一种读法并显式标注）
        import math
        step = max(0.0, math.log2(nbytes / float(SMALL_BIN_LIMIT)))
        return min(NBINS - 1, SMALL_BIN_LIMIT // SMALL_BIN_SPACING + int(step * 4))

    def bins(self):
        """返回 {bin_index: [Block]}（bin 内按 size 升序、同 size 按 seq 升序）。"""
        d = {}
        for b in self.blocks:
            if not b.free:
                continue
            d.setdefault(self.bin_index(b.usable * WORD), []).append(b)
        for v in d.values():
            v.sort(key=lambda x: (x.size, x.seq))
        return d

    # ---------- 分配 ----------
    def malloc(self, nwords):
        need = nwords + HDR_WORDS
        if need < MIN_CHUNK_WORDS_64:
            need = MIN_CHUNK_WORDS_64
        # mmap 阈值：超过阈值且 arena 满足不了 -> 走独立映射
        if need * WORD > self.mmap_threshold and not self._arena_can_fit(need):
            return self._mmap_alloc(need)
        i = self._find_fit(need)
        if i is None:
            return None
        return self._split_and_take(i, need)

    def _arena_can_fit(self, need):
        return any(b.free and b.size >= need for b in self.blocks)

    def _mmap_alloc(self, need):
        b = Block(self.mmap_base, need, False, self._next_seq(), mmapped=True)
        # 独立映射不在 arena 地址空间里，且永不与 arena chunk 合并
        self.blocks.append(b)
        self.mmap_base += need + 1
        return b.payload

    def _mmap_blocks(self):
        return [b for b in self.blocks if b.mmapped]

    def _find_fit(self, need):
        cands = [i for i, b in enumerate(self.blocks) if b.free and b.size >= need]
        if not cands:
            return None
        if self.policy == "first":
            return cands[0]
        w = self.wilderness()
        if self.policy == "dlmalloc":
            # wilderness 视为"可以变得比谁都大"，因此只在没有其它候选时才用
            nonwild = [i for i in cands if self.blocks[i] is not w]
            if nonwild:
                return min(nonwild, key=lambda i: self.blocks[i].size)
            return cands[0]
        return min(cands, key=lambda i: self.blocks[i].size)   # best

    def _split_and_take(self, i, need):
        b = self.blocks[i]
        rest = b.size - need
        b.free = False
        b.seq = self._next_seq()
        b.has_footer = self.classic_tags     # 新版本：使用中 chunk 省略 trailer
        if rest >= MIN_CHUNK_WORDS_64:
            nb = Block(b.addr + need, rest, True, self._next_seq(),
                       has_footer=self.classic_tags)
            self.blocks.insert(i + 1, nb)
            b.size = need
        return b.payload

    def _next_seq(self):
        self.seq += 1
        return self.seq

    # ---------- 释放与合并 ----------
    def free(self, payload_addr):
        b = self.block_at(payload_addr)
        assert not b.free, "double free"
        b.free = True
        b.seq = self._next_seq()
        b.has_footer = True                  # 空闲 chunk 必须有尾部标记
        if not self.defer:
            self.coalesce()

    def coalesce(self):
        out = []
        for b in self.blocks:
            if out and out[-1].free and b.free and \
               out[-1].addr + out[-1].size == b.addr and not (out[-1].mmapped or b.mmapped):
                out[-1].size += b.size
            else:
                out.append(b)
        self.blocks = out
        return len(out)

    # ---------- 边界标记自检 ----------
    def footer_words(self):
        """整堆为 footer 付出的 word 数（经典 vs 新版）。"""
        n = 0
        for b in self.blocks:
            if b.free or b.has_footer:
                n += 1
        return n


class MSHeap(Heap):
    """在空闲链表堆上叠加 mark-sweep。"""

    def __init__(self, nwords, **kw):
        Heap.__init__(self, nwords, **kw)
        self.refs = {}        # payload -> [payload]
        self.roots = []
        self.sweep_scanned = 0
        self.mark_visited = 0

    def new(self, nwords, refs=None):
        p = self.malloc(nwords)
        if p is None:
            return None
        self.refs[p] = list(refs or [])
        return p

    def mark(self):
        seen = set()
        stack = list(self.roots)
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            self.mark_visited += 1
            stack.extend(self.refs.get(x, []))
        return seen

    def sweep(self):
        """清扫：未被标记的已分配 chunk 归还空闲链表，然后合并。"""
        live = self.mark()
        collected = 0
        for b in self.blocks:
            self.sweep_scanned += 1          # 清扫必须扫过整个堆
            if not b.free and b.payload not in live:
                b.free = True
                b.seq = self._next_seq()
                self.refs.pop(b.payload, None)
                collected += 1
        self.coalesce()
        return collected

    def gc_alloc(self, nwords, refs=None):
        """分配失败 -> 触发 GC -> 重试（经典 mark-sweep 分配器行为）。"""
        p = self.new(nwords, refs)
        if p is None:
            self.sweep()
            p = self.new(nwords, refs)
        return p


def demo_basic():
    h = Heap(64)
    a = h.malloc(4)
    b = h.malloc(4)
    c = h.malloc(4)
    h.free(b)
    print("blocks:", [(x.addr, x.size, x.free) for x in h.blocks])
    h.free(a)
    h.free(c)
    print("after coalesce -> 1 block:", len(h.blocks), h.blocks[0].size)
    return h


if __name__ == "__main__":
    demo_basic()
