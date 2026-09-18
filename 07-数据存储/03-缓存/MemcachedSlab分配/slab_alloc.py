"""Memcached slab 分配器。

权威依据（本轮抓取自 memcached master 分支源码）：
  - slabs.c 头部注释：Slab 最大 1MB、切成 chunk；chunk 尺寸从
    "sizeof(item) + 一小段 key/value 空间" 起，按倍率增长到「最大 slab 的一半」。
  - slabs.c slabs_init()：size 初值、8 字节对齐、break 条件、perslab 计算、
    power_largest 的特殊处理。
  - memcached.c settings 默认值：maxbytes 64MB、factor 1.25、item_size_max 1MB、
    slab_page_size 1MB、slab_chunk_size_max = page/2、chunk_size 48。
  - memcached.h：CHUNK_ALIGN_BYTES 8、MAX_NUMBER_OF_SLAB_CLASSES (63+1)、
    POWER_SMALLEST 1、POWER_LARGEST 256、item 结构体。
"""

# ---------------------------------------------------------------- 默认值

DEFAULT_MAXBYTES = 64 * 1024 * 1024        # settings.maxbytes
DEFAULT_FACTOR = 1.25                      # settings.factor
DEFAULT_ITEM_SIZE_MAX = 1024 * 1024        # "The famous 1MB upper limit."
DEFAULT_SLAB_PAGE_SIZE = 1024 * 1024       # "chunks are split from 1MB pages."
DEFAULT_SLAB_CHUNK_SIZE_MAX = DEFAULT_SLAB_PAGE_SIZE // 2
DEFAULT_CHUNK_SIZE = 48                    # "space for a modest key and value"

CHUNK_ALIGN_BYTES = 8                      # memcached.h
MAX_NUMBER_OF_SLAB_CLASSES = 63 + 1        # memcached.h
POWER_SMALLEST = 1                         # memcached.h
POWER_LARGEST = 256                        # "actual cap is 255"


# ---------------------------------------------------------------- item

def sizeof_item_lp64():
    """LP64 下 `sizeof(item)`。

    memcached.h 的 _stritem 字段：
      next 8 / prev 8 / h_next 8 / time 4 / exptime 4 / nbytes 4 /
      refcount 2 / it_flags 2 / slabs_clsid 1 / nkey 1  = 42
    随后是 `union { uint64_t cas; char end; } data[]`，对齐要求 8
    → 偏移补齐到 48；柔性数组不计入 → sizeof(item) = 48。
    """
    fields = [("next", 8), ("prev", 8), ("h_next", 8), ("time", 4),
              ("exptime", 4), ("nbytes", 4), ("refcount", 2),
              ("it_flags", 2), ("slabs_clsid", 1), ("nkey", 1)]
    raw = sum(w for _, w in fields)
    padded = (raw + 7) // 8 * 8             # data[] 需要 8 字节对齐
    return padded


SIZEOF_ITEM = sizeof_item_lp64()


# ---------------------------------------------------------------- slab 类

def align_up(size, align=CHUNK_ALIGN_BYTES):
    """slabs_init：Make sure items are always n-byte aligned。"""
    if size % align:
        size += align - (size % align)
    return size


def slab_classes(page_size=DEFAULT_SLAB_PAGE_SIZE, factor=DEFAULT_FACTOR,
                 chunk_size_max=DEFAULT_SLAB_CHUNK_SIZE_MAX,
                 chunk_size=DEFAULT_CHUNK_SIZE,
                 sizeof_item=SIZEOF_ITEM,
                 max_classes=MAX_NUMBER_OF_SLAB_CLASSES):
    """复现 slabs_init 的建表循环。返回 [(class_id, chunk_size, perslab), ...]。"""
    out = []
    i = POWER_SMALLEST - 1
    size = sizeof_item + chunk_size
    while True:
        i += 1
        if i >= max_classes - 1:
            break
        if size >= chunk_size_max / factor:
            break
        size = align_up(size)
        out.append((i, size, page_size // size))
        size = int(size * factor)
    # power_largest 单独处理：直接用 chunk_size_max
    out.append((i, chunk_size_max, page_size // chunk_size_max))
    return out


def class_for(ntotal, classes=None):
    """slabs_clsid()：找第一个 size >= ntotal 的 class（返回 0 表示放不下）。"""
    classes = classes if classes is not None else slab_classes()
    for cid, size, _ in classes:
        if ntotal <= size:
            return cid
    return 0


def chunk_size_of(cid, classes=None):
    classes = classes if classes is not None else slab_classes()
    for i, size, _ in classes:
        if i == cid:
            return size
    return 0


def waste_of(ntotal, classes=None):
    """返回 (class_id, chunk_size, 内部碎片, 尾部碎片, 碎片率)。"""
    classes = classes if classes is not None else slab_classes()
    cid = class_for(ntotal, classes)
    if cid == 0:
        return 0, 0, 0, 0, 0.0
    size, perslab = chunk_size_of(cid, classes), 0
    for i, s, p in classes:
        if i == cid:
            perslab = p
    internal = size - ntotal
    page = classes[0][1] * classes[0][2] if False else DEFAULT_SLAB_PAGE_SIZE
    tail = page - perslab * size
    return cid, size, internal, tail, internal / size


# ---------------------------------------------------------------- 页面分配

class SlabAllocator:
    """极简的「页 → class」分配模型，用于演示内存卡在某个 class 的问题。"""

    def __init__(self, total_pages, classes=None, page_size=DEFAULT_SLAB_PAGE_SIZE):
        self.total_pages = total_pages
        self.free_pages = total_pages
        self.classes = classes if classes is not None else slab_classes(page_size)
        self.pages_of = {}          # class_id -> 已分配页数
        self.used_chunks = {}       # class_id -> 已用 chunk 数
        self.evictions = 0

    def capacity(self, cid):
        _, size, perslab = next(c for c in self.classes if c[0] == cid)
        return self.pages_of.get(cid, 0) * perslab

    def put(self, cid):
        used = self.used_chunks.get(cid, 0)
        if used >= self.capacity(cid):
            if self.free_pages > 0:
                self.free_pages -= 1
                self.pages_of[cid] = self.pages_of.get(cid, 0) + 1
            else:
                self.evictions += 1     # 本 class 没空间，且无空闲页 → 淘汰
        self.used_chunks[cid] = min(used + 1, self.capacity(cid))

    def reassign(self, src, dst):
        """slab automove / slab_reassign：把一页从 src 挪给 dst。

        只有**该页上的 chunk 全部空闲**时才能挪（真实实现要求整页回收）。
        """
        if self.pages_of.get(src, 0) <= 0:
            return False
        _, size, perslab = next(c for c in self.classes if c[0] == src)
        if self.used_chunks.get(src, 0) > (self.pages_of[src] - 1) * perslab:
            return False                # 最后一页还有在用 chunk，挪不了
        self.pages_of[src] -= 1
        self.pages_of[dst] = self.pages_of.get(dst, 0) + 1
        return True
