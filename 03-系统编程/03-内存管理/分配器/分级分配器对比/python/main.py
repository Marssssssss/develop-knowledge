#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
per-CPU 缓存（TCMalloc）与 arena/线程绑定（jemalloc）的模型 + 演示入口。

依据（实际读过的原文）：
* TCMalloc Design Doc
  - per-CPU 模式：一块大 slab，每个逻辑 CPU 一段；每 size class 一个 header，
    header 里有「数组起点 / 当前动态最大容量 / 当前位置」三个量
  - **静态最大容量 = 本档数组起点与下一档数组起点之差 / 指针大小**，运行期容量不得超过它
  - 数组耗尽 -> 从 middle-end 批量补充；溢出 -> 批量退回
  - 单 CPU 缓存上限 MallocExtension::SetMaxPerCpuCacheSize，
    因此机器上 CPU 越多，能缓存的总内存越多（"machines with higher CPU counts can cache more memory"）
  - 某档耗尽且未到硬编码上限时，可以**从同 CPU 的其它档偷容量**
  - MallocExtension::ReleaseCpuMemory 释放指定 CPU 的缓存
  - 原文还提到：实际缓存量平均约为上限的一半
* jemalloc(3)
  - opt.narenas 默认 4×CPU（单 CPU 为 1）
  - opt.percpu_arena = percpu：按 CPU 数定 arena 数，并按线程当前所在 CPU 绑定
  - opt.percpu_arena = phycpu：一个物理核一个 arena（两个超线程共享）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sizeclass import (  # noqa: E402
    jem_size_classes, jem_round_up, jem_internal_frag, jem_narenas,
    jem_purge_fraction, JEM_QUANTUM, JEM_PAGE, KiB, MiB,
    tcm_round_up_small, MI_SMALL_MAX_OBJ_SIZE, MI_MEDIUM_MAX_OBJ_SIZE,
    MI_LARGE_MAX_OBJ_SIZE, MI_SMALL_PAGE_SIZE, MI_MEDIUM_PAGE_SIZE,
    MI_LARGE_PAGE_SIZE, MI_BIN_HUGE, MI_BIN_COUNT,
)

PTR = 8
HDR_BYTES = 24          # 每档 header：数组起点 + 容量 + 当前位置（各 8 字节）


class PerCpuCache:
    """一个逻辑 CPU 的 slab 缓存。"""

    def __init__(self, classes, slab_bytes):
        self.classes = list(classes)
        self.slab = slab_bytes
        usable = slab_bytes - HDR_BYTES * len(self.classes)
        if usable <= 0:
            raise ValueError("slab too small for headers")
        share = usable // len(self.classes)          # 每档等分字节预算
        self.starts = {}
        off = HDR_BYTES * len(self.classes)
        for c in self.classes:
            self.starts[c] = off
            off += share
        self.slab_end = off
        self.static_cap = {}
        cls = self.classes
        for i, c in enumerate(cls):
            nxt = self.starts[cls[i + 1]] if i + 1 < len(cls) else self.slab_end
            self.static_cap[c] = (nxt - self.starts[c]) // PTR
        self.cap = dict(self.static_cap)             # 运行期动态容量
        self.cached = {c: 0 for c in cls}            # 当前缓存对象数
        self.refills = 0
        self.spills = 0

    # ---- 运行期容量不得超过静态容量 ----
    def set_cap(self, c, v):
        self.cap[c] = max(0, min(v, self.static_cap[c]))

    def get(self, c):
        if self.cached[c] > 0:
            self.cached[c] -= 1
            return True
        # 数组耗尽 -> 从 middle-end 批量补充
        batch = max(1, self.cap[c] // 2)
        self.refills += 1
        self.cached[c] += batch - 1
        return self.cached[c] >= 0

    def put(self, c):
        if self.cached[c] >= self.cap[c]:
            # 溢出 -> 批量退回 middle-end
            self.spills += 1
            self.cached[c] -= max(1, self.cap[c] // 2)
            return
        self.cached[c] += 1

    def steal(self, src, dst, slots):
        """从同 CPU 的 src 档偷 slots 个槽位给 dst 档。

        原文：dst 未到**硬编码上限**时才能偷；故 give 同时受 src 余量与 dst 静态余量约束，
        三者取小。dst 已满时 give 为 0（不会白白扣掉 src 的容量）。
        """
        give = min(slots, self.cap[src], self.static_cap[dst] - self.cap[dst])
        if give <= 0:
            return 0
        self.set_cap(src, self.cap[src] - give)
        self.set_cap(dst, self.cap[dst] + give)
        return give

    def total_bytes(self):
        return sum(self.cached[c] * c for c in self.classes)

    def budget_bytes(self):
        return sum(self.cap[c] * c for c in self.classes)

    def release(self):
        """MallocExtension::ReleaseCpuMemory 语义：清空本 CPU 缓存。"""
        n = self.total_bytes()
        for c in self.classes:
            self.cached[c] = 0
        return n


class Machine:
    """多 CPU 机器：总缓存量随 CPU 数增长。"""

    def __init__(self, ncpu, classes, per_cpu_limit):
        self.ncpu = ncpu
        self.caches = [PerCpuCache(classes, per_cpu_limit) for _ in range(ncpu)]

    def max_cached_bytes(self):
        return sum(c.budget_bytes() for c in self.caches)


def jem_arena_of(cpu, threads_per_core, percpu_arena, ncpu):
    """线程当前所在 CPU -> arena 编号。"""
    if percpu_arena == "percpu":
        return cpu
    if percpu_arena == "phycpu":
        return cpu // threads_per_core
    return cpu % max(1, jem_narenas(ncpu))       # disabled：按 4×CPU 取模


def demo():
    classes = jem_size_classes(64 * MiB)
    print("jemalloc size classes (<=64MiB): %d 档" % len(classes))
    print("  前 12 档:", classes[:12])
    print("mimalloc: small=%d B(10KiB) medium=%d B large=%d B  bins=%d"
          % (MI_SMALL_MAX_OBJ_SIZE, MI_MEDIUM_MAX_OBJ_SIZE,
             MI_LARGE_MAX_OBJ_SIZE, MI_BIN_HUGE))
    small = [16, 32, 48, 64, 80, 96, 112, 128]
    print("tcmalloc 8B 对齐:", [tcm_round_up_small(s, 8) for s in small])
    print("tcmalloc 16B 对齐:", [tcm_round_up_small(s, 16) for s in small])
    m = Machine(4, [16, 32, 64], 1 * MiB)
    print("4 CPU × 1MiB 上限 -> 可缓存总字节上限:", m.max_cached_bytes())
    print("10s 衰减到一半时已 purge:", jem_purge_fraction(5000, 10_000, 1000))


if __name__ == "__main__":
    demo()
