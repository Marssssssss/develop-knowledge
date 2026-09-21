"""
jemalloc / tcmalloc / mimalloc 的**尺寸分级(size class)**与回收参数模型。

所有常量都来自本 README 参考资料里实际读过的原文：

* jemalloc(3) man page（jemalloc.net/jemalloc.3.html）
  - 4 KiB 页 + 16 字节 quantum 下的 Table 1 尺寸档位表
  - 「每次翻倍 4 个尺寸档」，除最小几档外内部碎片约 20%
  - small < 4×页大小；large 从 4×页大小到不超过 PTRDIFF_MAX 的最大档
  - opt.narenas 默认 = 4×CPU（单 CPU 时为 1）
  - opt.percpu_arena: disabled(默认) / percpu / phycpu
  - opt.dirty_decay_ms 默认 10 秒；0 = 立刻 purge；-1 = 关闭
  - opt.muzzy_decay_ms 默认关闭（0）
  - opt.oversize_threshold 默认 8 MiB
  - opt.lg_extent_max_active_fit 默认 6（即最大比例 2^6 = 64）
* TCMalloc Design Doc（google.github.io/tcmalloc/design.html）
  - 60~80 个 size class；12 字节 -> 16 字节档
  - __STDCPP_DEFAULT_NEW_ALIGNMENT__ <= 8 时用 8 字节对齐的档位（24/40 不再被抬到 32/48）
  - per-CPU 模式：静态容量 = 相邻两档数组起点之差 / 指针大小；运行期容量不得超过静态容量
  - 总量上限 MallocExtension::SetMaxPerCpuCacheSize，故总缓存随 CPU 数增长
  - 某档耗尽时可以从同 CPU 的其它档"偷"容量
* mimalloc include/mimalloc/types.h（dev3 分支，64 位、默认 MI_SECURE）
  - MI_ARENA_SLICE_SHIFT = 13 + MI_SIZE_SHIFT = 16 -> 64 KiB
  - MI_SMALL_PAGE_SIZE = MI_ARENA_MIN_OBJ_SIZE = 64 KiB
  - MI_MEDIUM_PAGE_SIZE = 8 * MI_SMALL_PAGE_SIZE = 512 KiB
  - MI_LARGE_PAGE_SIZE  = MI_SIZE_SIZE * MI_MEDIUM_PAGE_SIZE = 4 MiB
  - MI_SMALL_MAX_OBJ_SIZE  = (SMALL_PAGE  - 4 KiB)/6 = 10 KiB
  - MI_MEDIUM_MAX_OBJ_SIZE = (MEDIUM_PAGE - 4 KiB)/6 ≈ 84 KiB
  - MI_LARGE_MAX_OBJ_SIZE  = LARGE_PAGE/8 = 512 KiB
  - MI_BIN_HUGE = 73, MI_BIN_FULL = 74, MI_BIN_COUNT = 75
  - MI_MAX_ALIGN_SIZE = 16
"""

KiB = 1024
MiB = 1024 * 1024

# ------------------------------------------------------------------ jemalloc
JEM_QUANTUM = 16
JEM_PAGE = 4 * KiB
JEM_NARENAS_PER_CPU = 4
JEM_OVERSIZE_DEFAULT = 8 * MiB
JEM_LG_EXTENT_MAX_ACTIVE_FIT = 6          # 2^6 = 64
JEM_DIRTY_DECAY_MS = 10_000
JEM_MUZZY_DECAY_MS = 0                    # 默认关闭


def jem_size_classes(max_size, page=JEM_PAGE, quantum=JEM_QUANTUM):
    """按 Table 1 的递推规则生成尺寸档位。

    规则：初始档 [8] 与 quantum 的 1..8 倍；其后每个 spacing S 产生 [5S,6S,7S,8S]，S 每次翻倍。
    """
    out = [8]
    s = quantum
    out.extend(s * k for k in range(1, 9))     # 16,32,...,128
    s *= 2                                     # 32
    while True:
        nxt = [s * k for k in (5, 6, 7, 8)]
        for v in nxt:
            if v > max_size:
                return out
            out.append(v)
        s *= 2


# Table 1 原文（4 KiB 页、16 字节 quantum）
JEM_TABLE_SMALL = [
    ("lg", [8]),
    ("16", [16, 32, 48, 64, 80, 96, 112, 128]),
    ("32", [160, 192, 224, 256]),
    ("64", [320, 384, 448, 512]),
    ("128", [640, 768, 896, 1024]),
    ("256", [1280, 1536, 1792, 2048]),
    ("512", [2560, 3072, 3584, 4096]),
    ("1 KiB", [5 * KiB, 6 * KiB, 7 * KiB, 8 * KiB]),
]
JEM_TABLE_LARGE = [
    ("2 KiB", [16 * KiB]),
    ("4 KiB", [20 * KiB, 24 * KiB, 28 * KiB, 32 * KiB]),
    ("8 KiB", [40 * KiB, 48 * KiB, 56 * KiB, 64 * KiB]),
    ("16 KiB", [80 * KiB, 96 * KiB, 112 * KiB, 128 * KiB]),
    ("32 KiB", [160 * KiB, 192 * KiB, 224 * KiB, 256 * KiB]),
    ("64 KiB", [320 * KiB, 384 * KiB, 448 * KiB, 512 * KiB]),
    ("128 KiB", [640 * KiB, 768 * KiB, 896 * KiB, 1 * MiB]),
    ("256 KiB", [1280 * KiB, 1536 * KiB, 1792 * KiB, 2 * MiB]),
    ("512 KiB", [2560 * KiB, 3 * MiB, 3584 * KiB, 4 * MiB]),
    ("1 MiB", [5 * MiB, 6 * MiB, 7 * MiB, 8 * MiB]),
    ("2 MiB", [10 * MiB, 12 * MiB, 14 * MiB, 16 * MiB]),
    ("4 MiB", [20 * MiB, 24 * MiB, 28 * MiB, 32 * MiB]),
    ("8 MiB", [40 * MiB, 48 * MiB, 56 * MiB, 64 * MiB]),
]


def jem_round_up(size, classes):
    for c in classes:
        if c >= size:
            return c
    return None


def jem_internal_frag(request, classes):
    c = jem_round_up(request, classes)
    if c is None:
        return None
    return (c - request) / float(c)


def jem_narenas(ncpu, percpu_arena="disabled"):
    """opt.narenas 与 opt.percpu_arena 的组合语义。"""
    if ncpu <= 1:
        base = 1
    else:
        base = JEM_NARENAS_PER_CPU * ncpu
    if percpu_arena == "percpu":
        return ncpu
    if percpu_arena == "phycpu":            # 一个物理核（两个超线程）共享一个 arena
        return max(1, ncpu // 2)
    return base


def jem_decay_rate(u):
    """sigmoidal 衰减：原文称"starts and ends with zero purge rate"。

    取 smoothstep 累积曲线 S(u)=3u^2-2u^3 的导数作为 purge 速率，
    u = t / decay_time ∈ [0,1]；两端速率均为 0，中点为峰值。
    """
    if u <= 0.0 or u >= 1.0:
        return 0.0
    return 6.0 * u * (1.0 - u)


def jem_decay_cumulative(u):
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    return 3 * u * u - 2 * u * u * u


def jem_purge_fraction(t_ms, decay_ms, unused_pages):
    """返回 (t_ms 时刻) 已 purge 的比例与页数；0 = 立刻，-1 = 永不。"""
    if decay_ms < 0:
        return 0.0, 0
    if decay_ms == 0:
        return 1.0, unused_pages
    u = min(1.0, t_ms / float(decay_ms))
    f = jem_decay_cumulative(u)
    return f, int(unused_pages * f)


# ------------------------------------------------------------------ tcmalloc
TCM_NUM_CLASSES_RANGE = (60, 80)


def tcm_round_up(size, align):
    """align 为 8 或 16（取决于 __STDCPP_DEFAULT_NEW_ALIGNMENT__）。"""
    return (size + align - 1) // align * align


def tcm_round_up_small(size, align):
    """小对象：按 align 对齐后再取不小于 16 的下限。"""
    v = tcm_round_up(size, align)
    return max(16, v)


# ------------------------------------------------------------------ mimalloc
MI_SIZE_SHIFT = 3                # 64 位：MI_SIZE_SIZE = 8 -> shift 3
MI_SIZE_SIZE = 1 << MI_SIZE_SHIFT
MI_ARENA_SLICE_SHIFT = 13 + MI_SIZE_SHIFT
MI_ARENA_SLICE_SIZE = 1 << MI_ARENA_SLICE_SHIFT          # 64 KiB
MI_PAGE_OSPAGE_BLOCK_ALIGN2 = 4 * KiB

MI_SMALL_PAGE_SIZE = MI_ARENA_SLICE_SIZE                 # 64 KiB
MI_MEDIUM_PAGE_SIZE = 8 * MI_SMALL_PAGE_SIZE             # 512 KiB
MI_LARGE_PAGE_SIZE = MI_SIZE_SIZE * MI_MEDIUM_PAGE_SIZE  # 4 MiB

MI_SMALL_MAX_OBJ_SIZE = (MI_SMALL_PAGE_SIZE - MI_PAGE_OSPAGE_BLOCK_ALIGN2) // 6
MI_MEDIUM_MAX_OBJ_SIZE = (MI_MEDIUM_PAGE_SIZE - MI_PAGE_OSPAGE_BLOCK_ALIGN2) // 6
MI_LARGE_MAX_OBJ_SIZE = MI_LARGE_PAGE_SIZE // 8

MI_BIN_HUGE = 73
MI_BIN_FULL = MI_BIN_HUGE + 1
MI_BIN_COUNT = MI_BIN_FULL + 1
MI_MAX_ALIGN_SIZE = 16
MI_PADDING = 0                    # 默认 MI_SECURE=0 / MI_DEBUG=0 -> 不启用
MI_PADDING_SIZE = 0


def mi_bin_count_note():
    """mimalloc 注释称 size class 按 12.5% 指数递增到 MI_BIN_HUGE=73 档。

    注意：原文只给了"12.5%"这个比例，没有给档位表；
    按纯 1.125 倍递推从 8 字节走到 512 KiB 需要约 95 步（> 73），
    说明实际的 _mi_bin 还混了小尺寸的按字递推段。此处**只记录不反推总数**。
    """
    n, v = 0, 8
    while v < MI_LARGE_MAX_OBJ_SIZE:
        v = v + v // 8
        n += 1
        if n > 1000:
            break
    return n


if __name__ == "__main__":
    print("jemalloc classes <= 64MiB:", len(jem_size_classes(64 * MiB)))
    print("mimalloc small/medium/large max:",
          MI_SMALL_MAX_OBJ_SIZE, MI_MEDIUM_MAX_OBJ_SIZE, MI_LARGE_MAX_OBJ_SIZE)
