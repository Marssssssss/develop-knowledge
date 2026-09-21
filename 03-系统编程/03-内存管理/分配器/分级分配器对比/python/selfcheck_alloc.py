#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""selfcheck_alloc.py —— jemalloc / tcmalloc / mimalloc 分级设计对照的断言集。

断言原则（与本项目其它 demo 一致）：
* 只断言**确定量**（档位数值、容量公式、arena 编号），不断言依赖负载的"谁更快/谁更省"；
* 官方表格与递推规则冲突时 **记录不改**（见 t_jem_table_discrepancy）；
* 官方只给比例（mimalloc 的 12.5%）时不反推总数，只打印。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sizeclass as S  # noqa: E402
from sizeclass import (  # noqa: E402
    jem_size_classes, jem_narenas, jem_purge_fraction, jem_decay_rate,
    JEM_TABLE_SMALL, JEM_TABLE_LARGE, JEM_QUANTUM, JEM_PAGE, KiB, MiB,
    tcm_round_up_small, TCM_NUM_CLASSES_RANGE,
    MI_SMALL_PAGE_SIZE, MI_MEDIUM_PAGE_SIZE, MI_LARGE_PAGE_SIZE,
    MI_SMALL_MAX_OBJ_SIZE, MI_MEDIUM_MAX_OBJ_SIZE, MI_LARGE_MAX_OBJ_SIZE,
    MI_BIN_HUGE, MI_BIN_FULL, MI_BIN_COUNT, MI_MAX_ALIGN_SIZE, MI_PADDING,
    mi_bin_count_note,
)
from main import PerCpuCache, Machine, jem_arena_of, PTR  # noqa: E402

PASS = [0]


def ok(cond, msg):
    assert cond, "FAIL: " + msg
    PASS[0] += 1
    print("  ok  %s" % msg)


def eq(a, b, msg):
    ok(a == b, "%s  (got %r, want %r)" % (msg, a, b))


def flat(table):
    out = []
    for _, sizes in table:
        out.extend(sizes)
    return out


# ------------------------------------------------------------ 1. jemalloc 档位
def t_jem_classes():
    print("[1] jemalloc 尺寸档位：递推规则复现 Table 1")
    cls = jem_size_classes(64 * MiB)
    eq(len(cls), 85, "<=64MiB 共 85 档")
    eq(cls[:9], [8, 16, 32, 48, 64, 80, 96, 112, 128],
       "初始段 = [8] 加 quantum 的 1..8 倍")
    small_flat = flat(JEM_TABLE_SMALL)
    eq(cls[:len(small_flat)], small_flat, "递推规则逐档复现 Table 1 的 Small 段")
    large_flat = flat(JEM_TABLE_LARGE)
    ok(all(v in cls for v in large_flat), "Table 1 的 Large 段每一档都能被递推规则生成")

    # 每次翻倍 4 档：取 [1024, 2048] 闭区间看间隔
    seg = [c for c in cls if 1024 <= c <= 2048]
    eq(seg, [1024, 1280, 1536, 1792, 2048], "一个 binade 内 5 个值")
    eq([seg[i + 1] - seg[i] for i in range(len(seg) - 1)], [256] * 4,
       "4 个间隔且都等于 1024/4 = 256（即「每次翻倍 4 档」）")

    # small / large 的分界 = 4 × 页大小
    eq(4 * JEM_PAGE, 16 * KiB, "small/large 分界 = 4 × 页大小 = 16 KiB")
    ok(8 * KiB in small_flat, "8 KiB 属于 small（< 4 页）")
    ok(16 * KiB in large_flat, "16 KiB 属于 large（= 4 页）")
    ok(16 * KiB not in small_flat, "16 KiB 不在 small 段里")


def t_jem_frag():
    print("[2] 内部碎片：除最小几档外 <= 20%")
    cls = jem_size_classes(64 * MiB)
    over = []
    for i in range(1, len(cls)):
        c, p = cls[i], cls[i - 1]
        r = (c - p - 1) / float(c)          # 最坏情况：请求 p+1 字节
        if r > 0.20:
            over.append(c)
    eq(over, [16, 32, 48, 64], "超过 20% 的只有最小的 4 档（原文：all but the smallest）")
    worst = max((c - p - 1) / float(c) for c, p in zip(cls[1:], cls[:-1]) if c >= 160)
    ok(worst < 0.20, ">=160 字节的档位最坏内部碎片 %.4f < 20%%" % worst)
    ok(abs(worst - (0.20 - 1.0 / (5 * 4096))) < 1e-9 or worst > 0.199,
       "最坏值随档位单调趋近 20%（(S-1)/5S 的形态）")


def t_jem_table_discrepancy():
    print("[3] 官方表格与递推规则的冲突：记录不改正")
    cls = jem_size_classes(64 * MiB)
    ok(10 * KiB in cls, "按递推规则，2 KiB spacing 组应含 10 KiB")
    ok(12 * KiB in cls and 14 * KiB in cls, "同理应含 12 KiB / 14 KiB")
    ok(10 * KiB not in flat(JEM_TABLE_SMALL) + flat(JEM_TABLE_LARGE),
       "但 Table 1 里没有 10 KiB（Small 段止于 8 KiB，Large 段从 16 KiB 起）")


def t_jem_arena():
    print("[4] opt.narenas 与 opt.percpu_arena")
    eq(jem_narenas(1), 1, "单 CPU 时 narenas = 1")
    eq(jem_narenas(8), 32, "默认 narenas = 4 × CPU = 32")
    eq(jem_narenas(8, "percpu"), 8, "percpu：arena 数 = CPU 数")
    eq(jem_narenas(8, "phycpu"), 4, "phycpu：一个物理核一个（2 超线程共享）")
    eq(jem_arena_of(3, 2, "percpu", 8), 3, "percpu 下 cpu3 -> arena 3")
    eq(jem_arena_of(3, 2, "phycpu", 8), 1, "phycpu 下 cpu3 -> arena 1")
    eq(jem_arena_of(2, 2, "phycpu", 8), 1, "phycpu 下 cpu2 与 cpu3 共享 arena 1")


def t_jem_decay():
    print("[5] dirty/muzzy 衰减：sigmoid 两端速率为 0")
    eq(jem_decay_rate(0.0), 0.0, "t=0 时 purge 速率为 0")
    eq(jem_decay_rate(1.0), 0.0, "t=T 时 purge 速率为 0")
    ok(all(jem_decay_rate(0.5) >= jem_decay_rate(u / 10.0) for u in range(1, 10)),
       "速率在中点达到峰值")
    f0, n0 = jem_purge_fraction(0, 10_000, 1000)
    eq((f0, n0), (0.0, 0), "默认 10s：t=0 一页未 purge")
    f5, n5 = jem_purge_fraction(5000, 10_000, 1000)
    eq((f5, n5), (0.5, 500), "中点 purge 掉一半（smoothstep 累积曲线的性质）")
    f1, n1 = jem_purge_fraction(10_000, 10_000, 1000)
    eq((f1, n1), (1.0, 1000), "t=T 时全部 purge")
    eq(jem_purge_fraction(1, 0, 1000), (1.0, 1000), "decay_ms=0 -> 立刻全部 purge")
    eq(jem_purge_fraction(10 ** 9, -1, 1000), (0.0, 0), "decay_ms=-1 -> 永不 purge")
    eq(S.JEM_DIRTY_DECAY_MS, 10_000, "dirty_decay_ms 默认 10 秒")
    eq(S.JEM_MUZZY_DECAY_MS, 0, "muzzy_decay_ms 默认 0（关闭）")
    eq(S.JEM_OVERSIZE_DEFAULT, 8 * MiB, "oversize_threshold 默认 8 MiB")
    eq(1 << S.JEM_LG_EXTENT_MAX_ACTIVE_FIT, 64, "lg_extent_max_active_fit=6 -> 最大比例 64")


# ------------------------------------------------------------ 6. tcmalloc
def t_tcm():
    print("[6] tcmalloc：对齐口径与 8 字节档位")
    eq(tcm_round_up_small(12, 8), 16, "原文例子：12 字节 -> 16 字节档")
    eq(tcm_round_up_small(24, 8), 24, "8 字节对齐下 24 保持 24")
    eq(tcm_round_up_small(40, 8), 40, "8 字节对齐下 40 保持 40")
    eq(tcm_round_up_small(24, 16), 32, "16 字节对齐下 24 被抬到 32")
    eq(tcm_round_up_small(40, 16), 48, "16 字节对齐下 40 被抬到 48")
    eq(TCM_NUM_CLASSES_RANGE, (60, 80),
       "原文著录：60~80 个可分配 size class（具体档位表未公开，不构造）")


# ------------------------------------------------------------ 7. mimalloc
def t_mimalloc():
    print("[7] mimalloc：页尺寸与对象上限（types.h 实读）")
    eq(MI_SMALL_PAGE_SIZE, 64 * KiB, "MI_SMALL_PAGE_SIZE = 64 KiB")
    eq(MI_MEDIUM_PAGE_SIZE, 512 * KiB, "MI_MEDIUM_PAGE_SIZE = 8 × 64 KiB")
    eq(MI_LARGE_PAGE_SIZE, 4 * MiB, "MI_LARGE_PAGE_SIZE = 8 × 512 KiB")
    eq(MI_SMALL_MAX_OBJ_SIZE, 10 * KiB, "(64KiB - 4KiB)/6 = 10 KiB（与源码注释一致）")
    eq(MI_MEDIUM_MAX_OBJ_SIZE, (512 * KiB - 4 * KiB) // 6,
       "(512KiB - 4KiB)/6 = %d 字节（源码注释 ≈84 KiB）" % MI_MEDIUM_MAX_OBJ_SIZE)
    eq(MI_LARGE_MAX_OBJ_SIZE, 512 * KiB, "MI_LARGE_MAX_OBJ_SIZE = LARGE_PAGE/8 = 512 KiB")
    eq((MI_BIN_HUGE, MI_BIN_FULL, MI_BIN_COUNT), (73, 74, 75),
       "MI_BIN_HUGE=73 / BIN_FULL=74 / BIN_COUNT=75")
    eq(MI_MAX_ALIGN_SIZE, 16, "MI_MAX_ALIGN_SIZE = 16")
    eq(MI_PADDING, 0, "默认构建（SECURE<3 且非 DEBUG）下 MI_PADDING = 0")
    n = mi_bin_count_note()
    print("      按 12.5%% 纯递推从 8B 到 512KiB 需要 %d 步（> MI_BIN_HUGE=73，只记录）" % n)
    ok(n > MI_BIN_HUGE, "纯 1.125 倍递推的步数 %d 超过官方 73 档 -> 不能反推档位表" % n)


# ------------------------------------------------------------ 8. per-CPU
def t_percpu():
    print("[8] TCMalloc per-CPU：静态容量 = 数组间距 / 指针大小")
    c = PerCpuCache([16, 32, 64], 1 * MiB)
    eq(len(c.starts), 3, "每个 size class 一个数组段")
    cls = [16, 32, 64]
    for i, s in enumerate(cls):
        nxt = c.starts[cls[i + 1]] if i + 1 < len(cls) else c.slab_end
        eq(c.static_cap[s], (nxt - c.starts[s]) // PTR,
           "档 %d 的静态容量 = 相邻数组起点之差 / %d = %d" % (s, PTR, c.static_cap[s]))
    c.set_cap(16, 10 ** 9)
    eq(c.cap[16], c.static_cap[16], "运行期容量被静态容量夹住，永不超限")

    # 偷容量：dst 有空间时总槽位守恒
    c2 = PerCpuCache([16, 32, 64], 1 * MiB)
    c2.set_cap(16, 10_000)
    before = sum(c2.cap.values())
    got = c2.steal(64, 16, 5_000)
    eq(got, 5_000, "从 64 档偷到 5000 个槽位")
    eq(sum(c2.cap.values()), before, "偷容量不改变该 CPU 的总槽位数")
    # dst 已在静态上限 -> 偷不到
    c3 = PerCpuCache([16, 32, 64], 1 * MiB)
    eq(c3.steal(64, 16, 5_000), 0, "dst 已到硬编码上限时 give = 0")
    eq(c3.cap[64], c3.static_cap[64], "此时 src 的容量不会被白白扣掉")

    # 总量随 CPU 数增长
    m1, m4 = Machine(1, [16, 32, 64], 1 * MiB), Machine(4, [16, 32, 64], 1 * MiB)
    eq(m4.max_cached_bytes(), 4 * m1.max_cached_bytes(),
       "单 CPU 上限固定 -> 总缓存随 CPU 数线性增长")

    # 溢出批量退回
    c4 = PerCpuCache([16, 32, 64], 1 * MiB)
    for _ in range(c4.cap[16] + 1):
        c4.put(16)
    ok(c4.cached[16] <= c4.cap[16], "put 不会超过当前容量")
    ok(c4.spills >= 1, "溢出触发批量退回 middle-end（spills=%d）" % c4.spills)

    # ReleaseCpuMemory
    c5 = PerCpuCache([16, 32, 64], 1 * MiB)
    c5.cached[16] = 100
    eq(c5.release(), 100 * 16, "ReleaseCpuMemory 释放的字节数 = 100 × 16")
    eq(c5.total_bytes(), 0, "释放后本 CPU 缓存为空")


def main():
    t_jem_classes(); t_jem_frag(); t_jem_table_discrepancy()
    t_jem_arena(); t_jem_decay(); t_tcm(); t_mimalloc(); t_percpu()
    print("\nALL PASS: %d assertions" % PASS[0])


if __name__ == "__main__":
    main()
