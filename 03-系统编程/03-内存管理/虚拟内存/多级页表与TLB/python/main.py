#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
多级页表、TLB 与缺页中断的原理级模型。

依据（实际读过的原文）：
* Linux `Documentation/arch/x86/x86_64/mm.rst`（内核文档 "Memory Management"）
  - 4 级页表：用户空间 0000000000000000 ~ 00007ffffffff000（~128 TB），
    紧接着 00007ffffffff000 ~ 00007fffffffffff 是 **4 kB guard hole**
  - 4 级的内核布局：ffff888000000000 直接映射 64 TB；ffffc90000000000 vmalloc/ioremap 32 TB；
    ffffea0000000000 vmemmap 1 TB；ffffffff80000000 内核文本 512 MB（映射到物理地址 0）
  - 5 级页表：用户空间 ~64 PB（00fffffffffff000 止），内核整体下移到 -64 PB 起
    （ff11000000000000 直接映射 32 PB；ffd4000000000000 vmemmap 0.5 PB）
* Linux `Documentation/arch/x86/x86_64/5level-paging.rst`
  - 4 级上限 256 TiB 虚拟 / 64 TiB 物理；5 级把上限抬到 **128 PiB 虚拟 / 4 PiB 物理**
  - 5 级启用 56 位用户态虚拟地址，但**默认不分配 47 位以上的地址**
    （除非显式给出高于 47 位的 hint 地址）
* Linux man-pages
  - `userfaultfd(2)`：UFFDIO_REGISTER_MODE_MISSING(4.10) / _MINOR(5.13) / _WP(5.7) / _RWP(7.2)；
    **WP 与 RWP 不能同时用**，MISSING|WP 合法；UFFDIO_COPY / ZEROPAGE / CONTINUE / WRITEPROTECT
  - `mprotect(2)`：降低权限立即生效（需要 TLB shootdown）
  - `madvise(2)`：MADV_DONTNEED 对**匿名私有**映射 -> 之后访问得到零页；
    MADV_FREE(4.5) 只能用于私有映射
  - `mincore(2)`：报告页是否在核心（resident）中
"""

PAGE_BITS = 12
PAGE_SIZE = 1 << PAGE_BITS                 # 4096
ENTRIES_PER_LEVEL = 512                    # 4 KiB / 8 B
LEVEL_BITS = 9                             # log2(512)
HUGE_PAGE_SIZE = 2 * 1024 * 1024           # 2 MiB

# ---- 内核文档里的地址布局常量 ----
USER_END_4L = 0x00007ffffffff000           # 4 级：最后一个用户页
GUARD_HOLE_4L = 0x00007fffffffffff
USER_END_5L = 0x00fffffffffff000           # 5 级
DIRECT_MAP_4L = 0xffff888000000000
VMALLOC_4L = 0xffffc90000000000
VMEMMAP_4L = 0xffffea0000000000
KERNEL_TEXT_4L = 0xffffffff80000000
DIRECT_MAP_5L = 0xff11000000000000
VMEMMAP_5L = 0xffd4000000000000

# 4 级 / 5 级的地址空间上限（5level-paging.rst）
VA_LIMIT_4L = 256 * (1 << 40)              # 256 TiB
PA_LIMIT_4L = 64 * (1 << 40)               # 64 TiB
VA_LIMIT_5L = 128 * (1 << 50)              # 128 PiB
PA_LIMIT_5L = 4 * (1 << 50)                # 4 PiB

# ---- userfaultfd 注册模式 ----
UFFDIO_REGISTER_MODE_MISSING = 1 << 0
UFFDIO_REGISTER_MODE_WP = 1 << 1
UFFDIO_REGISTER_MODE_MINOR = 1 << 2
UFFDIO_REGISTER_MODE_RWP = 1 << 3


# ---------------------------------------------------------------- 地址
def is_canonical(va, levels=4):
    """x86-64 规范地址：高位必须是符号扩展。"""
    bits = 48 if levels == 4 else 57
    sign_bits = 64 - bits + 1
    top = va >> (bits - 1)
    return top == 0 or top == (1 << sign_bits) - 1


def vpn_indices(va, levels=4):
    """返回自顶向下的各级页表下标（每级 9 位）。"""
    out = []
    shift = PAGE_BITS + LEVEL_BITS * (levels - 1)
    for _ in range(levels):
        out.append((va >> shift) & (ENTRIES_PER_LEVEL - 1))
        shift -= LEVEL_BITS
    return out


def page_offset(va):
    return va & (PAGE_SIZE - 1)


def build_va(indices, offset=0, levels=4):
    va = offset & (PAGE_SIZE - 1)
    shift = PAGE_BITS
    for i in range(levels - 1, -1, -1):
        va |= (indices[i] & (ENTRIES_PER_LEVEL - 1)) << shift
        shift += LEVEL_BITS
    return va


# ---------------------------------------------------------------- 页表
class PageTable:
    def __init__(self, levels=4):
        self.levels = levels
        self.walk_accesses = 0

    def walk(self, va):
        """返回本次遍历需要的**内存访问次数**：levels 级页表 + 1 次数据访问。"""
        idx = vpn_indices(va, self.levels)
        assert all(0 <= i < ENTRIES_PER_LEVEL for i in idx), "下标越界"
        self.walk_accesses += self.levels + 1
        return idx, self.levels + 1


def tlb_reach(entries, page_size):
    """TLB 覆盖的虚拟内存量。"""
    return entries * page_size


class TLB:
    """极简 TLB：FIFO 替换，记录 hit/miss 与 stale 访问。"""

    def __init__(self, entries):
        self.cap = entries
        self.store = {}
        self.hits = 0
        self.misses = 0
        self.stale_allowed = 0
        self.perms = {}

    def lookup(self, vpn, need_write=False):
        if vpn in self.store:
            if self.perms.get(vpn, True) or not need_write:
                self.hits += 1
                return True
            self.stale_allowed += 1        # 权限已改但 TLB 未刷新 -> 陈旧条目放行
            return True
        self.misses += 1
        if len(self.store) >= self.cap:
            self.store.pop(next(iter(self.store)))
        self.store[vpn] = True
        self.perms[vpn] = True
        return False

    def shootdown(self, vpn=None):
        if vpn is None:
            self.store.clear()
            self.perms.clear()
        else:
            self.store.pop(vpn, None)
            self.perms.pop(vpn, None)

    def downgrade(self, vpn):
        """mprotect 降权：只改页表不改 TLB 就会留下陈旧条目。"""
        self.perms[vpn] = False


# ---------------------------------------------------------------- 缺页
MINOR, MAJOR, SIGSEGV, UFFD = "minor", "major", "SIGSEGV", "uffd"


class VMA:
    """一段匿名私有映射：present / cow / prot / uffd 模式 / 内容。"""

    def __init__(self, npages, prot="rw", size=PAGE_SIZE):
        self.npages = npages
        self.prot = prot                       # VMA 级权限（vm_flags）
        self.pte_writable = [True] * npages    # PTE 级写位：COW 只清它，不动 prot
        self.resident = [False] * npages
        self.cow = [False] * npages
        self.data = [0] * npages
        self.uffd = 0
        self.zeroed = [True] * npages
        self.faults = []

    def register_uffd(self, modes):
        if modes & UFFDIO_REGISTER_MODE_WP and modes & UFFDIO_REGISTER_MODE_RWP:
            raise ValueError("EINVAL: WP 与 RWP 不能同时注册")
        self.uffd = modes

    def access(self, idx, write=False):
        """返回本次访问的缺页类型（或 None 表示不缺页）。"""
        if not self.resident[idx]:
            if self.uffd & UFFDIO_REGISTER_MODE_MISSING:
                self.faults.append(UFFD)
                return UFFD                      # 交给用户态 handler
            self.resident[idx] = True
            self.faults.append(MINOR)            # 匿名首次访问 -> minor（零页）
            return MINOR
        if self.prot == "none":
            self.faults.append(SIGSEGV)
            return SIGSEGV
        if write and self.prot == "r":
            self.faults.append(SIGSEGV)
            return SIGSEGV
        if write and self.cow[idx] and not self.pte_writable[idx]:
            # COW：VMA 可写但 PTE 写位被清 -> minor fault，复制后恢复写位
            self.cow[idx] = False
            self.pte_writable[idx] = True
            self.faults.append(MINOR)
            return MINOR
        if write and self.uffd & UFFDIO_REGISTER_MODE_WP:
            self.faults.append(UFFD)             # 写保护缺页 -> 用户态
            return UFFD
        return None

    def fork_cow(self):
        """fork：父子共享物理页，双方 PTE 都变成只读 + COW。"""
        child = VMA(self.npages, self.prot)
        child.resident = list(self.resident)
        child.data = list(self.data)
        child.cow = [True] * self.npages
        self.cow = [True] * self.npages
        # fork 只把两边 PTE 的写位清掉（VMA 权限不变），写时再复制
        self.pte_writable = [False] * self.npages
        child.pte_writable = [False] * self.npages
        return child

    def madvise_dontneed(self):
        """MADV_DONTNEED：匿名私有映射 -> 之后访问得到零页。"""
        self.resident = [False] * self.npages
        self.data = [0] * self.npages
        self.zeroed = [True] * self.npages
        self.cow = [False] * self.npages

    def mincore(self):
        return list(self.resident)


if __name__ == "__main__":
    pt = PageTable(4)
    va = build_va([1, 2, 3, 4], 0x123)
    print("va = 0x%016X, indices = %s" % (va, vpn_indices(va)))
    print("walk accesses =", pt.walk(va)[1])
    print("canonical(0x800000000000) =", is_canonical(0x0000800000000000))
    print("TLB reach 4K/2M ratio =", tlb_reach(512, HUGE_PAGE_SIZE) // tlb_reach(512, PAGE_SIZE))
