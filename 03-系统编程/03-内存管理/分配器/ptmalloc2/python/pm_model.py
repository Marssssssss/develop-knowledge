"""ptmalloc2 的常量公式与尺寸/索引换算(64 位,x86-64 原式)。

来源:raw.githubusercontent.com/bminor/glibc/master/malloc/malloc.c
      raw.githubusercontent.com/bminor/glibc/release/2.35/master/malloc/malloc.c
"""

# ---- 64 位平台常量(malloc.c 实际宏) ----
SIZE_SZ = 8
MALLOC_ALIGNMENT = 2 * SIZE_SZ  # 16
MALLOC_ALIGN_MASK = MALLOC_ALIGNMENT - 1
CHUNK_HDR_SZ = 2 * SIZE_SZ  # 16
MIN_CHUNK_SIZE = 4 * SIZE_SZ  # offsetof(struct malloc_chunk, fd_nextsize) = 32
MINSIZE = (MIN_CHUNK_SIZE + MALLOC_ALIGN_MASK) & ~MALLOC_ALIGN_MASK  # 32

NBINS = 128
NSMALLBINS = 64
SMALLBIN_WIDTH = MALLOC_ALIGNMENT
SMALLBIN_CORRECTION = 1 if MALLOC_ALIGNMENT > CHUNK_HDR_SZ else 0  # = 0
MIN_LARGE_SIZE = (NSMALLBINS - SMALLBIN_CORRECTION) * SMALLBIN_WIDTH  # 1024

MAX_FAST_SIZE = 160  # 64 位
DEFAULT_MMAP_THRESHOLD = 128 * 1024
TCACHE_FILL_COUNT_MASTER = 16  # glibc master
TCACHE_FILL_COUNT_235 = 7  # glibc 2.35
TCACHE_SMALL_BINS_MASTER = 64
TCACHE_LARGE_BINS_MASTER = 12
TCACHE_MAX_BINS_MASTER = TCACHE_SMALL_BINS_MASTER + TCACHE_LARGE_BINS_MASTER  # 76
TCACHE_MAX_BINS_235 = 64

# 标志位(chunk size 低 3 位,因 chunk 均为 8 字节倍数)
PREV_INUSE = 0x01
IS_MMAPPED = 0x02
NON_MAIN_ARENA = 0x04

MAX_ARENAS_FACTOR = 8  # arena 数上限 = 8 * CPU 核数
TOP_CHUNK_SIZE = 1 << 20  # top chunk 初始大小(教学取 1 MiB)
DEFAULT_TRIM_THRESHOLD = 128 * 1024


def request2size(req):
    """malloc.c 原式:(((req)+SIZE_SZ+MALLOC_ALIGN_MASK < MINSIZE) ? MINSIZE
                        : ((req)+SIZE_SZ+MALLOC_ALIGN_MASK) & ~MALLOC_ALIGN_MASK)"""
    padded = req + SIZE_SZ + MALLOC_ALIGN_MASK
    return MINSIZE if padded < MINSIZE else padded & ~MALLOC_ALIGN_MASK


def csize2tidx(x):
    """chunksize -> tcache index:(((x) - MINSIZE) / MALLOC_ALIGNMENT)"""
    return (x - MINSIZE) // MALLOC_ALIGNMENT


def tidx2csize(idx):
    """tcache index -> chunksize:(((size_t) idx) * MALLOC_ALIGNMENT + MINSIZE)"""
    return idx * MALLOC_ALIGNMENT + MINSIZE


def usize2tidx(x):
    """用户请求字节数 -> tcache index"""
    return csize2tidx(request2size(x))


def in_smallbin_range(sz):
    return sz < MIN_LARGE_SIZE


def smallbin_index(sz):
    return (sz >> 4) + SMALLBIN_CORRECTION


def largebin_index_64(sz):
    if (sz >> 6) <= 48:
        return 48 + (sz >> 6)
    if (sz >> 9) <= 20:
        return 91 + (sz >> 9)
    if (sz >> 12) <= 10:
        return 110 + (sz >> 12)
    if (sz >> 15) <= 4:
        return 119 + (sz >> 15)
    if (sz >> 18) <= 2:
        return 124 + (sz >> 18)
    return 126


def bin_index(sz):
    return smallbin_index(sz) if in_smallbin_range(sz) else largebin_index_64(sz)


class Chunk:
    __slots__ = ("off", "size", "inuse", "mmapped")

    def __init__(self, off, size, inuse=True, mmapped=False):
        self.off, self.size, self.inuse, self.mmapped = off, size, inuse, mmapped

    def prev_size_valid(self):
        """手册:prev_size 只有在前一个 chunk 空闲(PREV_INUSE=0)时才有意义。"""
        return not self.inuse


