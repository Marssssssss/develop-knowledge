#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""selfcheck_zgc.py —— ZGC zpointer 位布局 / 屏障 / 重定位的断言集。

全部常量来自实读的 `src/hotspot/share/gc/z/zAddress.hpp`(master)；
JEP 333 的暂停时间数字只做**文献著录**并做确定性比较，不冒充实测。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zpointer import (  # noqa: E402,F401
    z_pointer_mask, z_pointer_bit,
    ZPointerReservedShift, ZPointerReservedBits, ZPointerReservedMask,
    ZPointerRememberedShift, ZPointerRememberedBits, ZPointerRememberedMask,
    ZPointerRemembered0, ZPointerRemembered1,
    ZPointerMarkedShift, ZPointerMarkedBits, ZPointerMarkedMask,
    ZPointerFinalizable0, ZPointerFinalizable1,
    ZPointerMarkedYoung0, ZPointerMarkedYoung1,
    ZPointerMarkedOld0, ZPointerMarkedOld1,
    ZPointerRemappedShift, ZPointerRemappedBits, ZPointerRemappedMask,
    ZPointerRemapped00, ZPointerRemapped01, ZPointerRemapped10, ZPointerRemapped11,
    ZPointerLoadShiftTable,
    ZPointerLoadMetadataMask, ZPointerMarkMetadataMask,
    ZPointerStoreMetadataMask, ZPointerAllMetadataMask,
    ZAddressShift, ZAddressAlignMask, REMAP_OLD, REMAP_YOUNG,
    color, uncolor, load_shift_lookup_index, load_shift_lookup,
    overlapping_zeros, ZGlobalsPointers, ZHeap, ShenandoahHeap,
)

PASS = [0]


def ok(cond, msg):
    assert cond, "FAIL: " + msg
    PASS[0] += 1
    print("  ok  %s" % msg)


def eq(a, b, msg):
    ok(a == b, "%s  (got %r, want %r)" % (msg, a, b))


def t_shifts():
    print("[1] 四段元数据的 shift / bits（zAddress.hpp 原文）")
    eq((ZPointerReservedShift, ZPointerReservedBits), (0, 4), "Reserved: shift 0, 4 bits")
    eq((ZPointerRememberedShift, ZPointerRememberedBits), (4, 2), "Remembered: shift 4, 2 bits")
    eq((ZPointerMarkedShift, ZPointerMarkedBits), (6, 6), "Marked: shift 6, 6 bits")
    eq((ZPointerRemappedShift, ZPointerRemappedBits), (12, 4), "Remapped: shift 12, 4 bits")
    eq(ZPointerReservedMask, 0xF, "ReservedMask = 0xF")
    eq(ZPointerRememberedMask, 0x30, "RememberedMask = 0x30")
    eq(ZPointerMarkedMask, 0xFC0, "MarkedMask = 0xFC0")
    eq(ZPointerRemappedMask, 0xF000, "RemappedMask = 0xF000")


def t_bits():
    print("[2] 各元数据位的具体取值")
    eq((ZPointerRemembered0, ZPointerRemembered1), (1 << 4, 1 << 5), "rr = bit4/bit5")
    eq((ZPointerFinalizable0, ZPointerFinalizable1), (1 << 6, 1 << 7), "FF = bit6/bit7")
    eq((ZPointerMarkedYoung0, ZPointerMarkedYoung1), (1 << 8, 1 << 9), "mm = bit8/bit9")
    eq((ZPointerMarkedOld0, ZPointerMarkedOld1), (1 << 10, 1 << 11), "MM = bit10/bit11")
    eq((ZPointerRemapped00, ZPointerRemapped01, ZPointerRemapped10, ZPointerRemapped11),
       (1 << 12, 1 << 13, 1 << 14, 1 << 15), "RRRR = bit12..bit15")
    eq(ZPointerMarkedMask,
       (ZPointerFinalizable0 | ZPointerFinalizable1 | ZPointerMarkedYoung0 |
        ZPointerMarkedYoung1 | ZPointerMarkedOld0 | ZPointerMarkedOld1),
       "MarkedMask 正好是 FF|mm|MM 六个位")
    # 布局串 RRRRMMmmFFrr0000
    layout = (ZPointerRemappedMask >> 12, ZPointerMarkedMask >> 6,
              ZPointerRememberedMask >> 4, ZPointerReservedMask)
    eq(layout, (0b1111, 0b111111, 0b11, 0b1111),
       "自高到低 = RRRR(4) MMmmFF(6) rr(2) 0000(4)")


def t_masks():
    print("[3] 三层屏障的元数据掩码：load ⊆ mark ⊆ store = all")
    eq(ZPointerLoadMetadataMask, ZPointerRemappedMask, "load 只用 Remapped 四档")
    eq(ZPointerMarkMetadataMask, 0xFFC0, "mark = load | marked")
    eq(ZPointerStoreMetadataMask, 0xFFF0, "store = mark | remembered")
    eq(ZPointerAllMetadataMask, ZPointerStoreMetadataMask, "all == store")
    ok(ZPointerLoadMetadataMask & ZPointerMarkMetadataMask == ZPointerLoadMetadataMask,
       "load 掩码是 mark 掩码的子集")
    ok(ZPointerMarkMetadataMask & ZPointerStoreMetadataMask == ZPointerMarkMetadataMask,
       "mark 掩码是 store 掩码的子集")
    eq(ZPointerMarkMetadataMask & ZPointerRememberedMask, 0,
       "mark 屏障不看 remembered 位（两者不相交）")
    eq(ZPointerStoreMetadataMask & ZPointerRememberedMask, ZPointerRememberedMask,
       "store 屏障才纳入 remembered 位")
    eq(ZPointerAllMetadataMask & ZPointerReservedMask, 0,
       "reserved 四位不参与任何屏障")


def t_shift_table():
    print("[4] load shift table：把「查表 + 移位」合成一条推测移位指令")
    eq(ZPointerLoadShiftTable[0], ZPointerRemappedShift + ZPointerRemappedShift,
       "[0] Null = 12+12 = 24")
    eq(ZPointerLoadShiftTable[1], 13, "[1] Remapped00 -> 13")
    eq(ZPointerLoadShiftTable[2], 14, "[2] Remapped01 -> 14")
    eq(ZPointerLoadShiftTable[4], 15, "[4] Remapped10 -> 15")
    eq(ZPointerLoadShiftTable[8], 16, "[8] Remapped11 -> 16")
    eq([ZPointerLoadShiftTable[i] for i in (3, 5, 6, 7)], [0, 0, 0, 0],
       "非法的 remap 组合在位表里为 0")
    eq(load_shift_lookup_index(ZPointerRemapped11), 8, "Remapped11 的查表下标 = 8")
    eq(load_shift_lookup(ZPointerRemapped11), 16, "对应移位量 16")


def t_overlap():
    print("[5] 地址位与元数据零位的重叠（x86 推测移位的前提）")
    eq([overlapping_zeros(x) for x in (1, 2, 4, 8)], [3, 2, 1, 0],
       "Remapped00/01/10/11 分别重叠 3/2/1/0 个零位")
    addr = 1 << 20
    for c in (ZPointerRemapped00, ZPointerRemapped01,
              ZPointerRemapped10, ZPointerRemapped11):
        p = color(addr, c)
        eq(p & 0xFFFF, c, "上色后低 16 位只剩元数据 0x%X" % c)
        eq(uncolor(p), addr, "去色后地址复原 0x%X" % addr)
    eq(ZAddressShift, 16, "元数据占满低 16 位 -> 地址位恒从 bit16 开始（64 KiB 对齐）")
    try:
        color(0x1000, 0)
        ok(False, "未对齐地址应当被拒绝")
    except AssertionError:
        ok(True, "非 64 KiB 对齐的地址无法承载 16 位元数据（被断言拦下）")


def t_remap_cycle():
    print("[6] RemappedOld/Young 掩码交替 -> remap 位四步一循环")
    g = ZGlobalsPointers()
    eq(g.remapped, ZPointerRemapped00, "初态 = RemappedOld0 & RemappedYoung0 = 0001")
    seq = [g.remapped]
    g.flip_young_relocate_start(); seq.append(g.remapped)
    g.flip_old_relocate_start(); seq.append(g.remapped)
    g.flip_young_relocate_start(); seq.append(g.remapped)
    g.flip_old_relocate_start(); seq.append(g.remapped)
    eq(seq, [ZPointerRemapped00, ZPointerRemapped01, ZPointerRemapped11,
             ZPointerRemapped10, ZPointerRemapped00],
       "循环 00 -> 01 -> 11 -> 10 -> 00（每次只翻一侧）")
    eq(REMAP_OLD, [0b0011, 0b1100], "RemappedOldMask 在 0011 / 1100 之间交替")
    eq(REMAP_YOUNG, [0b0101, 0b1010], "RemappedYoungMask 在 0101 / 1010 之间交替")


def t_barrier():
    print("[7] load barrier 自愈（self-healing）")
    g = ZGlobalsPointers()
    heap = ZHeap(g)
    a, b = 1 << 20, 1 << 21
    heap.fields["f"] = color(a, ZPointerRemapped00)
    ok(g.is_load_good(heap.fields["f"]), "翻转前字段颜色是 good 的")
    heap.relocate(a, b)
    g.flip_young_relocate_start()
    ok(not g.is_load_good(heap.fields["f"]), "翻转后字段颜色变成 bad（触发慢路径）")
    v1 = heap.load_barrier("f")
    eq(v1, b, "第 1 次 load 经转发表得到新地址")
    eq(heap.slow_paths, 1, "走了 1 次慢路径")
    eq(heap.fields["f"], color(b, ZPointerRemapped01),
       "字段被就地改写（self-healing），新颜色为当前 remap 位")
    v2 = heap.load_barrier("f")
    eq(v2, b, "第 2 次 load 得到同一地址")
    eq(heap.slow_paths, 1, "第 2 次不再进慢路径（已被修好）")
    # null 不触发慢路径
    heap.fields["n"] = 0
    eq(heap.load_barrier("n"), 0, "null 直接返回 0")
    eq(heap.slow_paths, 1, "null 不增加慢路径计数")


def t_shenandoah():
    print("[8] 对照：Shenandoah 的 Brooks pointer 每次 load 都要多读一次内存")
    s = ShenandoahHeap()
    s.fields["f"] = 1 << 20
    s.brooks[1 << 20] = 1 << 21
    for _ in range(5):
        s.load_reference_barrier("f")
    eq(s.extra_reads, s.loads, "LRB 的额外读次数与 load 次数恒为 1:1")
    eq(s.loads, 5, "5 次 load -> 5 次额外读")
    # ZGC 一侧：颜色 good 时零额外开销
    g = ZGlobalsPointers()
    h = ZHeap(g)
    h.fields["f"] = color(1 << 20, g.remapped)
    for _ in range(5):
        h.load_barrier("f")
    eq(h.slow_paths, 0, "ZGC：颜色一致时 5 次 load 零慢路径")


def t_jep333_record():
    print("[9] JEP 333 文献数值（只做著录与确定性比较）")
    zgc_max, g1_avg = 1.681, 156.806
    ok(zgc_max <= 10.0, "ZGC 实测最大暂停 1.681 ms <= 10 ms 目标")
    ok(g1_avg > 10.0, "同基准下 G1 平均暂停 156.806 ms 远超 10 ms")
    ok(1.091 <= zgc_max, "ZGC 平均 1.091 ms <= 自身最大 1.681 ms")
    ok(543.846 > g1_avg, "G1 最大 543.846 ms > 其平均 156.806 ms")


def main():
    t_shifts(); t_bits(); t_masks(); t_shift_table(); t_overlap()
    t_remap_cycle(); t_barrier(); t_shenandoah(); t_jep333_record()
    print("\nALL PASS: %d assertions" % PASS[0])


if __name__ == "__main__":
    main()
