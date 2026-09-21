#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ZGC 着色指针演示入口：并发重定位 + load barrier 自愈，对照 Shenandoah 的 Brooks pointer。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zpointer import (  # noqa: E402
    ZGlobalsPointers, ZHeap, ShenandoahHeap, color, uncolor,
    ZPointerRemapped00, ZPointerRemapped01,
    ZPointerLoadMetadataMask, ZPointerMarkMetadataMask, ZPointerStoreMetadataMask,
    ZPointerLoadShiftTable, ZAddressShift,
)

HEAP_BASE = 1 << 20          # 演示用：64 KiB 对齐的堆基址


def demo_zgc():
    g = ZGlobalsPointers()
    heap = ZHeap(g)
    a = HEAP_BASE + (1 << ZAddressShift) * 1
    b = HEAP_BASE + (1 << ZAddressShift) * 2
    heap.fields["f"] = color(a, ZPointerRemapped00)
    print("初始颜色: 0x%X, 当前 remap 位 0x%X" % (heap.fields["f"], g.remapped))

    # 并发重定位：对象从 a 搬到 b，但字段还指向旧地址
    heap.relocate(a, b)
    g.flip_young_relocate_start()
    print("翻转后 remap 位 0x%X -> 字段颜色过期" % g.remapped)

    v1 = heap.load_barrier("f")
    print("第 1 次 load: 0x%X (慢路径 %d 次), 字段被改写为 0x%X"
          % (v1, heap.slow_paths, heap.fields["f"]))
    v2 = heap.load_barrier("f")
    print("第 2 次 load: 0x%X (慢路径仍为 %d 次)" % (v2, heap.slow_paths))


def demo_shenandoah():
    s = ShenandoahHeap()
    s.fields["f"] = HEAP_BASE + 64
    s.brooks[HEAP_BASE + 64] = HEAP_BASE + 128
    for _ in range(5):
        s.load_reference_barrier("f")
    print("Shenandoah LRB: %d 次 load -> %d 次额外的对象头读（恒 1:1）"
          % (s.loads, s.extra_reads))


def demo_layout():
    print("load  mask = 0x%04X" % ZPointerLoadMetadataMask)
    print("mark  mask = 0x%04X" % ZPointerMarkMetadataMask)
    print("store mask = 0x%04X" % ZPointerStoreMetadataMask)
    print("load shift table =", ZPointerLoadShiftTable)
    print("uncolor(color(0x%X, Remapped01)) = 0x%X"
          % (HEAP_BASE, uncolor(color(HEAP_BASE, ZPointerRemapped01))))


if __name__ == "__main__":
    demo_layout()
    demo_zgc()
    demo_shenandoah()
