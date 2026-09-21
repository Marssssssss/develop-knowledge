#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ZGC 着色指针（colored pointer / zpointer）与并发压缩的原理级模型。

位布局全部照抄 OpenJDK `src/hotspot/share/gc/z/zAddress.hpp`（master 分支，实读）：

    RRRRMMmmFFrr0000
    ****               : Used by load barrier
    **********         : Used by mark barrier
    ************       : Used by store barrier
                 ****   : Reserved bits

    rr   Remembered[0,1]   Finalizable[0,1] = FF
    mm   MarkedYoung[0,1]  MM = MarkedOld[0,1]
    RRRR Remapped[00,01,10,11]

以及该文件里的确切常量（见下方 `z_pointer_mask` / `z_pointer_bit` 两个 constexpr）。
Shenandoah 一侧依据 `shenandoahBarrierSet.hpp`（load_reference_barrier + SATB 队列）
与 JEP 333 / JEP 379。
"""

# ---------------------------------------------------------------- 常量
def z_pointer_mask(shift, bits):
    """源码里的 constexpr：(((uintptr_t)1 << bits) - 1) << shift"""
    return ((1 << bits) - 1) << shift


def z_pointer_bit(shift, offset):
    """源码里的 constexpr：(uintptr_t)1 << (shift + offset)"""
    return 1 << (shift + offset)


# Reserved bits
ZPointerReservedShift = 0
ZPointerReservedBits = 4
ZPointerReservedMask = z_pointer_mask(ZPointerReservedShift, ZPointerReservedBits)
ZPointerReserved0 = z_pointer_bit(ZPointerReservedShift, 0)
ZPointerReserved1 = z_pointer_bit(ZPointerReservedShift, 1)
ZPointerReserved2 = z_pointer_bit(ZPointerReservedShift, 2)
ZPointerReserved3 = z_pointer_bit(ZPointerReservedShift, 3)

# Remembered set bits
ZPointerRememberedShift = ZPointerReservedShift + ZPointerReservedBits      # 4
ZPointerRememberedBits = 2
ZPointerRememberedMask = z_pointer_mask(ZPointerRememberedShift, ZPointerRememberedBits)
ZPointerRemembered0 = z_pointer_bit(ZPointerRememberedShift, 0)             # 1 << 4
ZPointerRemembered1 = z_pointer_bit(ZPointerRememberedShift, 1)             # 1 << 5

# Marked bits
ZPointerMarkedShift = ZPointerRememberedShift + ZPointerRememberedBits      # 6
ZPointerMarkedBits = 6
ZPointerMarkedMask = z_pointer_mask(ZPointerMarkedShift, ZPointerMarkedBits)
ZPointerFinalizable0 = z_pointer_bit(ZPointerMarkedShift, 0)                # 1 << 6
ZPointerFinalizable1 = z_pointer_bit(ZPointerMarkedShift, 1)                # 1 << 7
ZPointerMarkedYoung0 = z_pointer_bit(ZPointerMarkedShift, 2)                # 1 << 8
ZPointerMarkedYoung1 = z_pointer_bit(ZPointerMarkedShift, 3)                # 1 << 9
ZPointerMarkedOld0 = z_pointer_bit(ZPointerMarkedShift, 4)                  # 1 << 10
ZPointerMarkedOld1 = z_pointer_bit(ZPointerMarkedShift, 5)                  # 1 << 11

# Remapped bits
ZPointerRemappedShift = ZPointerMarkedShift + ZPointerMarkedBits            # 12
ZPointerRemappedBits = 4
ZPointerRemappedMask = z_pointer_mask(ZPointerRemappedShift, ZPointerRemappedBits)
ZPointerRemapped00 = z_pointer_bit(ZPointerRemappedShift, 0)                # 1 << 12
ZPointerRemapped01 = z_pointer_bit(ZPointerRemappedShift, 1)                # 1 << 13
ZPointerRemapped10 = z_pointer_bit(ZPointerRemappedShift, 2)                # 1 << 14
ZPointerRemapped11 = z_pointer_bit(ZPointerRemappedShift, 3)                # 1 << 15

# The shift table is tightly coupled with the zpointer layout given above
ZPointerLoadShiftTable = [
    ZPointerRemappedShift + ZPointerRemappedShift,   # [0] Null        -> 24
    ZPointerRemappedShift + 1,                       # [1] Remapped00  -> 13
    ZPointerRemappedShift + 2,                       # [2] Remapped01  -> 14
    0,
    ZPointerRemappedShift + 3,                       # [4] Remapped10  -> 15
    0,
    0,
    0,
    ZPointerRemappedShift + 4,                       # [8] Remapped11  -> 16
]

# Barrier metadata masks
ZPointerLoadMetadataMask = ZPointerRemappedMask
ZPointerMarkMetadataMask = ZPointerLoadMetadataMask | ZPointerMarkedMask
ZPointerStoreMetadataMask = ZPointerMarkMetadataMask | ZPointerRememberedMask
ZPointerAllMetadataMask = ZPointerStoreMetadataMask

# 地址位：元数据占满低 16 位，所以地址位恒从 bit 16 开始
ZAddressShift = 16
ZAddressAlignMask = (1 << ZAddressShift) - 1

# RemappedOldMask / RemappedYoungMask 的两个取值（源码注释里的 4 位图案）
REMAP_OLD = [0b0011, 0b1100]        # RemappedOld0 / RemappedOld1
REMAP_YOUNG = [0b0101, 0b1010]      # RemappedYoung0 / RemappedYoung1


# ---------------------------------------------------------------- 指针操作
def color(addr, c):
    """ZAddress::color —— 给无着色地址上色。"""
    assert (addr & ZAddressAlignMask) == 0, "地址必须 64 KiB 对齐（元数据占低 16 位）"
    return addr | c


def uncolor(ptr):
    """ZPointer::uncolor —— 去掉全部元数据位。"""
    return ptr & ~ZPointerAllMetadataMask


def load_shift_lookup_index(value):
    """源码：ZPointer::load_shift_lookup_index(value)"""
    return (value & ZPointerLoadMetadataMask) >> ZPointerRemappedShift


def load_shift_lookup(value):
    i = load_shift_lookup_index(value)
    return ZPointerLoadShiftTable[i]


def overlapping_zeros(color_field):
    """源码注释的重叠零位数：Remapped00 -> 3, 01 -> 2, 10 -> 1, 11 -> 0。"""
    return 3 - (color_field.bit_length() - 1)


class ZGlobalsPointers:
    """ZGlobalsPointers 的当前期望位与翻转逻辑（flip_*_start）。"""

    def __init__(self):
        self.old_i = 0
        self.young_i = 0
        self.marked_young = ZPointerMarkedYoung0
        self.marked_old = ZPointerMarkedOld0
        self.refresh()

    def refresh(self):
        self.remapped_old_mask = REMAP_OLD[self.old_i]
        self.remapped_young_mask = REMAP_YOUNG[self.young_i]
        inter = self.remapped_old_mask & self.remapped_young_mask
        self.remapped = inter << ZPointerRemappedShift
        self.load_good_mask = self.remapped
        self.load_bad_mask = ZPointerLoadMetadataMask & ~self.remapped

    def flip_young_relocate_start(self):
        self.young_i ^= 1
        self.refresh()

    def flip_old_relocate_start(self):
        self.old_i ^= 1
        self.refresh()

    def flip_young_mark_start(self):
        self.marked_young = (ZPointerMarkedYoung1 if self.marked_young == ZPointerMarkedYoung0
                             else ZPointerMarkedYoung0)

    def is_load_good(self, ptr):
        return (ptr & ZPointerLoadMetadataMask) == self.remapped


class ZHeap:
    """带转发表的 ZGC 堆：并发重定位 + load barrier 自愈。"""

    def __init__(self, globals_):
        self.g = globals_
        self.forwarding = {}          # from_addr -> to_addr
        self.fields = {}              # field_name -> colored pointer
        self.slow_paths = 0

    def relocate(self, old, new):
        self.forwarding[old] = new

    def load_barrier(self, field):
        """ZGC load barrier：颜色不对 -> 慢路径修正并**写回字段**（self-healing）。"""
        p = self.fields[field]
        if p == 0 or self.g.is_load_good(p):
            return uncolor(p)
        self.slow_paths += 1
        addr = uncolor(p)
        addr = self.forwarding.get(addr, addr)
        fixed = color(addr, self.g.remapped)
        self.fields[field] = fixed          # 自愈：下次不再进慢路径
        return addr


class ShenandoahHeap:
    """Shenandoah：Brooks pointer 放在对象头里，LRB 每次都要多读一次内存。"""

    def __init__(self):
        self.brooks = {}              # obj -> to-space copy (or itself)
        self.fields = {}
        self.loads = 0
        self.extra_reads = 0

    def load_reference_barrier(self, field):
        self.loads += 1
        obj = self.fields[field]
        self.extra_reads += 1                     # 必须读对象头的转发词
        return self.brooks.get(obj, obj)


if __name__ == "__main__":
    g = ZGlobalsPointers()
    print("load metadata mask  = 0x%X" % ZPointerLoadMetadataMask)
    print("mark metadata mask  = 0x%X" % ZPointerMarkMetadataMask)
    print("store metadata mask = 0x%X" % ZPointerStoreMetadataMask)
    print("remapped cycle:", end=" ")
    seq = [g.remapped]
    for i in range(4):
        g.flip_young_relocate_start(); seq.append(g.remapped)
        g.flip_old_relocate_start(); seq.append(g.remapped)
    print([hex(x) for x in seq])
