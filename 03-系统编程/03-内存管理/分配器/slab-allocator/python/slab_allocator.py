"""slab_allocator.py — 最小 slab 分配器(Python 版)

模型对应 Bonwick 1994 论文 + Linux Kernel Ch.8 简化版:
  - kmem_cache 管理一种固定尺寸的对象
  - 每个 cache 维护三条 slab 链表:
      slabs_partial / slabs_full / slabs_free
  - 每个 slab 内对象连续排列(slot 尺寸 = obj_size),用 bitmap 标记占/空

注意:
  - 这是教学简化版,真实 Linux SLAB 还含 slab coloring(对齐偏移错开 CPU
    缓存行)、每 CPU 数组(无锁)、回收机制(reap_timer → shrink_slab)
  - 由于 slab 是连续内存,本 demo 把对象地址编码成相对 slab mem 的索引,
    以 O(1) 算 slot 位置(避免遍历链表)
"""
from typing import Optional


SLAB_BYTES = 4096                   # 单 slab = 1 页
OBJ_SIZE   = 32                     # 每个对象大小(向上对齐到 8 字节倍数)
SLAB_OBJ_MAX = (SLAB_BYTES - 64) // OBJ_SIZE


class Slab:
    """单个 slab — 一段连续字节 + 一张 bitmap。"""

    def __init__(self, obj_size: int):
        self.mem        = bytearray(SLAB_BYTES)        # 数据段
        self.bitmap     = 0                            # 位图:0=空,1=占
        self.obj_count  = SLAB_OBJ_MAX
        self.used       = 0
        self.next: Optional["Slab"] = None


class KmemCache:
    """对应 Linux 的 kmem_cache,管理一种固定尺寸的对象。"""

    def __init__(self, obj_size: int):
        self.obj_size = obj_size
        # 三条链表:partial / full / free(未分配过)
        self.slabs_partial: Optional[Slab] = None
        self.slabs_full:    Optional[Slab] = None
        self.slabs_free:    Optional[Slab] = None
        self.alloc_total = 0
        self.free_total  = 0
        # 预创建第一个 free slab,等首次分配直接搬去 partial
        s = Slab(obj_size)
        self.slabs_free = s

    # ----------- 链表操作辅助 -----------

    def _pop_head(self, head_attr: str) -> Optional[Slab]:
        s = getattr(self, head_attr)
        if not s: return None
        setattr(self, head_attr, s.next)
        return s

    def _push_head(self, head_attr: str, s: Slab) -> None:
        s.next = getattr(self, head_attr)
        setattr(self, head_attr, s)

    # ----------- 分配 / 释放 -----------

    def _select_slab(self) -> Optional[Slab]:
        if self.slabs_partial: return self.slabs_partial
        if self.slabs_free:
            s = self._pop_head("slabs_free")
            # 从 free → partial
            self._push_head("slabs_partial", s)
            return s
        # 全 full,新建 slab 进 partial
        return self._create_slab_partial()

    def _create_slab_partial(self) -> Slab:
        s = Slab(self.obj_size)
        self._push_head("slabs_partial", s)
        return s

    def alloc(self) -> memoryview:
        """分配一个 slot,返回可写入的 memoryview;失败返回 None。"""
        s = self._select_slab()
        if not s: return None

        # 找第一个空位(0 位)
        free_mask = ~s.bitmap & ((1 << s.obj_count) - 1)
        if free_mask == 0:
            return None  # 不应该到这里
        idx = (free_mask & -free_mask).bit_length() - 1   # ctz
        s.bitmap |= (1 << idx)
        s.used   += 1
        self.alloc_total += 1

        # 满 → 从 partial 移到 full
        if s.used == s.obj_count:
            self._pop_head("slabs_partial")
            self._push_head("slabs_full", s)

        return memoryview(s.mem)[idx * self.obj_size: (idx + 1) * self.obj_size]

    def free(self, obj: memoryview) -> bool:
        """释放一个对象。需要根据地址定位 slab / idx。"""
        if not obj: return False

        # 用 ctypes 把 memoryview 的 buffer 起点转成地址,int 数值
        addr = obj.contiguous  # bool
        # 我们要 obj 指向的内存起始地址,以便做差
        import ctypes
        obj_addr = ctypes.addressof(ctypes.c_char.from_buffer(obj))
        # 到这里 obj_addr 是底层 bytearray 中 obj 起始位置的绝对地址

        for attr in ("slabs_partial", "slabs_full"):
            prev, cur = None, getattr(self, attr)
            while cur:
                base_addr = ctypes.addressof(ctypes.c_char.from_buffer(cur.mem))
                end_addr  = base_addr + cur.obj_count * self.obj_size
                if base_addr <= obj_addr < end_addr:
                    off = obj_addr - base_addr
                    idx = off // self.obj_size
                    cur.bitmap &= ~(1 << idx)
                    cur.used   -= 1
                    self.free_total += 1

                    # full → partial 升级
                    if attr == "slabs_full":
                        # 摘除 cur
                        if prev: prev.next = cur.next
                        else:    setattr(self, attr, cur.next)
                        self._push_head("slabs_partial", cur)
                    return True
                prev, cur = cur, cur.next
        return False

    def counts(self):
        def length(h):
            n, p = 0, getattr(self, h)
            while p: n += 1; p = p.next
            return n
        return (length("slabs_partial"), length("slabs_full"), length("slabs_free"))

    def stats(self):
        p, f, fr = self.counts()
        return f"partial={p} full={f} free={fr} (alloc_total={self.alloc_total} free_total={self.free_total})"


# --------------------- demo ---------------------

def demo_basic(c: KmemCache):
    print("[1] basic alloc/free — same-size objects reuse a single slab")
    print(f"    obj_size = {OBJ_SIZE}B, slab = {SLAB_BYTES}B (1 page)")

    objs = []
    for i in range(4):
        v = c.alloc()
        v[0:4] = (100 + i).to_bytes(4, "little")
        objs.append(v)
        print(f"    alloc[{i}] -> id={int.from_bytes(v[:4], 'little')}")
    print(f"    {c.stats()}")
    for v in objs: c.free(v)
    print(f"    freed all 4\n    {c.stats()}")


def demo_grow(c: KmemCache):
    print("\n[2] slab chain growth — alloc past SLAB_OBJ_MAX triggers a new slab")
    print(f"    SLAB_OBJ_MAX = {SLAB_OBJ_MAX} objs per slab")

    N = SLAB_OBJ_MAX + 5
    batch = [c.alloc() for _ in range(N)]
    print(f"    allocated {N} objects\n    {c.stats()}")

    # 部分释放,创造 partial/full 混合
    for i in range(0, N, 3):
        c.free(batch[i]); batch[i] = None
    print(f"    after freeing every 3rd object:\n    {c.stats()}")

    for v in batch:
        if v: c.free(v)
    print(f"    freed all, slabs stay in chains (no reap in this demo)\n    {c.stats()}")


def demo_objects_are_isolated(c: KmemCache):
    print("\n[3] per-slab continuity — adjacent objs in one slab land size apart")
    objs = [c.alloc() for _ in range(5)]
    import ctypes
    addrs = [ctypes.addressof(ctypes.c_char.from_buffer(v)) for v in objs]
    for i, a in enumerate(addrs):
        v = objs[i]; v[0:4] = (200 + i).to_bytes(4, "little")
    for i in (1, 3):
        print(f"    objs[0..{i}] distance = {addrs[i] - addrs[0]} bytes (expected {OBJ_SIZE * i})")
    for v in objs: c.free(v)


def main():
    print("=== slab allocator demo (simplified, Python) ===")
    c = KmemCache(OBJ_SIZE)

    demo_basic(c)
    demo_grow(c)
    demo_objects_are_isolated(c)

    print("\n[ok] cache destroyed (all slabs GCed).")


if __name__ == "__main__":
    main()
