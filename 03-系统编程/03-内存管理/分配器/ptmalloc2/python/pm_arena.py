"""主 arena 的教学简化版:保留 glibc 的 bins/tcache 结构和查找顺序。

来源:sourceware.org/glibc/wiki/MallocInternals(malloc/free 逐步骤)
"""

from pm_model import *  # noqa: F401,F403

class Arena:
    """主 arena 的教学简化版:保留 bins/tcache/查找顺序,合并按地址序相邻性。"""

    def __init__(self, tcache_count=TCACHE_FILL_COUNT_MASTER, cpu=8):
        self.chunks = []           # 按地址序
        self.top_size = TOP_CHUNK_SIZE
        self.tcache = {}           # tidx -> list[Chunk]
        self.tcache_count = tcache_count
        self.fastbins = {}         # size -> list[Chunk]
        self.unsorted = []
        self.smallbins = {}        # bin idx -> list[Chunk]
        self.largebins = {}        # bin idx -> list[Chunk]
        self.mmap_threshold = DEFAULT_MMAP_THRESHOLD
        self.trim_threshold = DEFAULT_TRIM_THRESHOLD
        self.mmap_max = 65536
        self.arena_max = MAX_ARENAS_FACTOR * cpu
        self.path = []
        self.munmaps = 0
        self.trims = 0

    def _step(self, name):
        self.path.append(name)

    def reset_path(self):
        self.path = []

    # ---------- malloc ----------
    def malloc(self, req):
        c = self._allocate(req)
        c.inuse = True
        return c

    def _allocate(self, req):
        self.reset_path()
        nb = request2size(req)

        self._step("check-mmap-threshold")
        if nb >= self.mmap_threshold:
            self._step("mmap-direct")
            self.munmaps += 1
            return Chunk(0, nb, mmapped=True)

        slots = self.tcache.get(csize2tidx(nb))
        if slots:
            self._step("tcache-hit")
            return slots.pop()
        self._step("tcache-miss-exact-only")

        if nb < MAX_FAST_SIZE and self.fastbins.get(nb):
            self._step("fastbin-hit")
            self._step("prefill-tcache-from-fastbin")
            return self.fastbins[nb].pop()

        if in_smallbin_range(nb) and self.smallbins.get(smallbin_index(nb)):
            self._step("smallbin-hit")
            self._step("prefill-tcache-from-smallbin")
            return self.smallbins[smallbin_index(nb)].pop(0)

        if not in_smallbin_range(nb):
            self._flush_fastbins_to_unsorted()
            self._step("large:flush-fastbins-to-unsorted")

        hit = self._scan_unsorted(nb)
        if hit is not None:
            self._step("unsorted-scan-hit")
            return hit

        if not in_smallbin_range(nb):
            hit = self._search_large_bins(nb)
            if hit is not None:
                self._step("largebin-hit")
                return hit

        if self.fastbins:
            self._flush_fastbins_to_unsorted()
            self._step("small:consolidate-fastbins-and-retry")
            hit = self._scan_unsorted(nb)
            if hit is not None:
                return hit

        self._step("split-top")
        return self._carve_top(nb)

    def _flush_fastbins_to_unsorted(self):
        for lst in self.fastbins.values():
            self.unsorted.extend(lst)
        self.fastbins = {}

    def _scan_unsorted(self, nb):
        """手册:这是**唯一**把 chunk 放进 small/large bin 的地方。"""
        hit = None
        keep = []
        for c in self.unsorted:
            if hit is None and c.size >= nb:
                hit = c
                continue
            keep.append(c)
        self.unsorted = keep
        for c in keep:
            if in_smallbin_range(c.size):
                self.smallbins.setdefault(smallbin_index(c.size), []).append(c)
            else:
                self.largebins.setdefault(largebin_index_64(c.size), []).append(c)
        if hit is not None and hit.size > nb + MINSIZE:
            self._split(hit, nb)
        return hit

    def _search_large_bins(self, nb):
        for idx in range(largebin_index_64(nb), NBINS):
            lst = self.largebins.get(idx)
            if not lst:
                continue
            lst.sort(key=lambda c: c.size)
            for c in list(lst):
                if c.size >= nb:
                    lst.remove(c)
                    if c.size > nb + MINSIZE:
                        self._split(c, nb)
                    return c
        return None

    def _carve_top(self, nb):
        if self.top_size < nb:
            raise MemoryError("top chunk exhausted(真实 glibc 会 sbrk/mmap 新 heap)")
        self.top_size -= nb
        c = Chunk(len(self.chunks) * 4096, nb)
        self.chunks.append(c)
        return c

    def _split(self, c, nb):
        rest = Chunk(c.off + nb, c.size - nb, inuse=False)
        c.size = nb
        self.chunks.insert(self.chunks.index(c) + 1, rest)

    # ---------- free ----------
    def free(self, c):
        self.reset_path()
        c.inuse = False
        if c.mmapped:
            self._step("munmap")
            self.munmaps += 1
            return "munmap"

        slots = self.tcache.setdefault(csize2tidx(c.size), [])
        if len(slots) < self.tcache_count:
            self._step("tcache-store")
            slots.append(c)
            return "tcache"

        if c.size < MAX_FAST_SIZE:
            self._step("fastbin")
            self.fastbins.setdefault(c.size, []).append(c)
            return "fastbin"

        self._step("coalesce")
        merged = self._coalesce(c)
        if self.chunks and self.chunks[-1] is merged:
            self._step("absorb-into-top")
            self.top_size += merged.size
            self.chunks.remove(merged)
            return "top"

        self._step("unsorted")
        self.unsorted.append(merged)
        if merged.size >= self.trim_threshold:
            self._step("trim")
            self.trims += 1
            return "unsorted+trim"
        return "unsorted"

    def _coalesce(self, c):
        while True:
            i = self.chunks.index(c)
            merged = False
            for j in (i + 1, i - 1):
                if 0 <= j < len(self.chunks) and not self.chunks[j].inuse:
                    other = self.chunks[j]
                    c.off = min(c.off, other.off)
                    c.size += other.size
                    self.chunks.remove(other)
                    merged = True
                    break
            if not merged:
                return c

    # ---------- 观测 ----------
    def heap_bytes(self):
        """应用当前实际持有(in-use)的字节数。"""
        return sum(c.size for c in self.chunks if c.inuse)

    def in_arena_bytes(self):
        """从内核视角仍属于本进程的堆字节数:in-use + free + top。"""
        return sum(c.size for c in self.chunks) + self.top_size


