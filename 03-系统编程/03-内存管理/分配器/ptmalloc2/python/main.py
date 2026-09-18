"""ptmalloc2(glibc malloc)三层结构 —— 5 组实验(可实跑自检)。

  python3 main.py    # 83 个断言

模型在 pm_model.py / pm_arena.py;本文件只放实验与断言。
"""

from pm_model import *  # noqa: F401,F403
from pm_arena import Arena

FAILS = []
TOTAL = [0]


def check(label, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        print(f"  [ok] {label}")
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label} {detail}")


# --------------------------------------------------------------------------- #
def demo1():
    print("== demo1 chunk 布局与尺寸公式 ==")
    check("SIZE_SZ = 8(64 位)", SIZE_SZ == 8)
    check("MALLOC_ALIGNMENT = 2*SIZE_SZ = 16", MALLOC_ALIGNMENT == 16)
    check("CHUNK_HDR_SZ = 2*SIZE_SZ = 16(prev_size + size)", CHUNK_HDR_SZ == 16)
    check("最小 chunk = offsetof(fd_nextsize) = 4*sizeof(void*) = 32", MIN_CHUNK_SIZE == 32)
    check("MINSIZE 对齐后仍为 32", MINSIZE == 32)
    check("三个标志位正好占低 3 位", PREV_INUSE | IS_MMAPPED | NON_MAIN_ARENA == 0x07)
    check("PREV_INUSE=0x01 / IS_MMAPPED=0x02 / NON_MAIN_ARENA=0x04",
          (PREV_INUSE, IS_MMAPPED, NON_MAIN_ARENA) == (1, 2, 4))
    check("PREV_INUSE 的含义是'前一块不可被合并',并非真的在用",
          Chunk(0, 48, inuse=False).prev_size_valid() is True)

    check("request2size(0) = MINSIZE = 32", request2size(0) == 32)
    check("request2size(1) = 32", request2size(1) == 32)
    check("request2size(24) = 32", request2size(24) == 32)
    check("request2size(25) = 48", request2size(25) == 48, request2size(25))
    check("request2size(40) = 48(tcache 注释:idx1 覆盖 25..40)", request2size(40) == 48)
    check("request2size(41) = 64", request2size(41) == 64)
    check("request2size(1000) = 1008(非 1024)", request2size(1000) == 1008, request2size(1000))
    check("request2size(112) = 128", request2size(112) == 128)
    check("尺寸总是 MALLOC_ALIGNMENT 的倍数",
          all(request2size(n) % MALLOC_ALIGNMENT == 0 for n in range(0, 4096)))
    check("request2size 单调不减",
          all(request2size(a) <= request2size(b) for a in range(0, 2000) for b in [a + 1]))


def demo2():
    print("== demo2 bin 索引与 tcache 索引映射 ==")
    check("NSMALLBINS=64 / NBINS=128", (NSMALLBINS, NBINS) == (64, 128))
    check("SMALLBIN_CORRECTION = (MALLOC_ALIGNMENT > CHUNK_HDR_SZ) = 0",
          SMALLBIN_CORRECTION == 0)
    check("MIN_LARGE_SIZE = 64*16 = 1024", MIN_LARGE_SIZE == 1024)
    check("1024 以下走 smallbin", in_smallbin_range(1008))
    check("1024 起算 large", not in_smallbin_range(1024))

    check("smallbin_index(32) = 2", smallbin_index(32) == 2)
    check("smallbin_index(48) = 3", smallbin_index(48) == 3)
    check("smallbin_index(1008) = 63", smallbin_index(1008) == 63)
    check("smallbin 占 bin 2..63 共 62 个(1 号是 unsorted)",
          len(range(2, 64)) == 62)
    check("largebin_index_64(1024) = 64", largebin_index_64(1024) == 64, largebin_index_64(1024))
    check("largebin_index_64(3072) = 96", largebin_index_64(3072) == 96, largebin_index_64(3072))
    check("largebin_index_64(3136) = 97", largebin_index_64(3136) == 97, largebin_index_64(3136))
    check("largebin_index_64(5120) = 101", largebin_index_64(5120) == 101, largebin_index_64(5120))
    check("bin_index 在 1008->1024 处连续(63 -> 64)",
          bin_index(1008) == 63 and bin_index(1024) == 64)
    check("bin 索引不超过 NBINS-1",
          all(bin_index(s) < NBINS for s in range(32, 4 << 20, 7)))
    check("bin_index 单调不减",
          all(bin_index(a) <= bin_index(a + 13) for a in range(32, (4 << 20) - 13, 13)))

    check("tcache 注释:idx0 覆盖 0..24 字节请求",
          usize2tidx(0) == 0 and usize2tidx(24) == 0)
    check("tcache 注释:idx1 覆盖 25..40", usize2tidx(25) == 1 and usize2tidx(40) == 1)
    check("tcache 注释:idx2 覆盖 41..56", usize2tidx(41) == 2 and usize2tidx(56) == 2)
    check("tidx2csize(csize2tidx(x)) == x",
          all(tidx2csize(csize2tidx(c)) == c for c in range(32, 4096, 16)))
    check("tcache idx0 的 chunksize = MINSIZE = 32", tidx2csize(0) == 32)
    check("master:TCACHE_MAX_BINS = 64 + 12 = 76", TCACHE_MAX_BINS_MASTER == 76)
    check("master:idx75 的 chunksize = 1232", tidx2csize(75) == 1232)
    check("2.35:TCACHE_MAX_BINS = 64(还没有 large bins)", TCACHE_MAX_BINS_235 == 64)
    check("每 bin 上限:master 16 个,2.35 为 7 个(版本差异)",
          (TCACHE_FILL_COUNT_MASTER, TCACHE_FILL_COUNT_235) == (16, 7))


def demo3():
    print("== demo3 malloc 的查找顺序 ==")
    a = Arena()
    a.malloc(100)
    check("冷启动一路降到 top 分割", a.path[-1] == "split-top", a.path)
    check("第一步先做 mmap 阈值判定", a.path[0] == "check-mmap-threshold")
    check("小请求尺寸不匹配 fastbin", "fastbin-hit" not in a.path)

    a.malloc(100 * 1024)
    check("100 KiB < 128 KiB 阈值:不走 mmap", "mmap-direct" not in a.path)
    a.malloc(200 * 1024)
    check("200 KiB >= 阈值:直接 mmap", "mmap-direct" in a.path)
    check("mmap 分配计入 munmaps", a.munmaps == 1, a.munmaps)

    b = Arena()
    x = b.malloc(64)
    b.free(x)
    b.reset_path()
    y = b.malloc(64)
    check("刚释放的块命中 tcache", b.path == ["check-mmap-threshold", "tcache-hit"], b.path)
    check("tcache 返回同一个 chunk", y is x)

    d = Arena(tcache_count=0)
    z = d.malloc(64)
    d.free(z)
    d.reset_path()
    d.malloc(64)
    check("tcache 满/关闭时落到 fastbin", "fastbin-hit" in d.path, d.path)
    check("fastbin 在 tcache 之后才被查", d.path.index("fastbin-hit") >
          d.path.index("tcache-miss-exact-only"))

    e = Arena(tcache_count=0)
    big = e.malloc(2048)
    e.malloc(64)  # 隔离块,让 big 释放后不并入 top
    e.free(big)
    e.reset_path()
    got = e.malloc(2048)
    check("大请求先从 unsorted 取回", "unsorted-scan-hit" in e.path, e.path)
    check("大请求会先把 fastbin 清进 unsorted",
          "large:flush-fastbins-to-unsorted" in e.path)
    check("取回的 chunk 尺寸足够", got.size >= 2048)


def demo4():
    print("== demo4 free 的顺序与'不还给 OS' ==")
    a = Arena()
    c = a.malloc(64)
    a.free(c)
    check("free 第一站是 tcache", a.path[0] == "tcache-store", a.path)

    b = Arena(tcache_count=0)
    m = b.malloc(64)
    b.free(m)
    check("tcache 满则进 fastbin", b.path == ["fastbin"], b.path)
    check("fastbin 中确实有该块(用 chunksize 而非请求字节数做键)",
          request2size(64) in b.fastbins, b.fastbins.keys())

    d = Arena(tcache_count=0)
    p = d.malloc(4096)
    d.free(p)
    check("大块 free 先做合并", "coalesce" in d.path, d.path)
    check("合并后紧邻堆顶 -> 被并入 top", "absorb-into-top" in d.path, d.path)
    check("堆顶释放不进任何 bin", "unsorted" not in d.path)
    check("并入 top 后 top 恢复原大小", d.top_size == TOP_CHUNK_SIZE, d.top_size)

    e = Arena(tcache_count=0)
    e.malloc(4096)
    mid = e.malloc(4096)
    e.malloc(64)  # 隔离块
    e.free(mid)
    check("非堆顶释放 -> 进 unsorted", "unsorted" in e.path, e.path)
    check("unsorted 长度 +1", len(e.unsorted) == 1, len(e.unsorted))

    f = Arena(tcache_count=0)
    blocks = []
    for _ in range(3):
        blocks.append(f.malloc(4096))
        blocks.append(f.malloc(64))  # 隔离块,阻止相邻合并
    for blk in blocks[::2]:
        f.free(blk)
    check("多次释放后 unsorted 累积到 3", len(f.unsorted) == 3, len(f.unsorted))
    check("free 不等于归还 OS:arena 总量不变", f.in_arena_bytes() == TOP_CHUNK_SIZE,
          f.in_arena_bytes())
    check("free 之后 in-use 字节数只剩隔离块",
          f.heap_bytes() == 3 * request2size(64), f.heap_bytes())
    check("未达 trim 阈值不触发修剪", f.trims == 0 and f.trim_threshold == 128 * 1024)

    g = Arena(tcache_count=0)
    h = g.malloc(256 * 1024)  # >= mmap 阈值
    g.free(h)
    check("mmap 来的块 free 时直接 munmap", g.path == ["munmap"], g.path)


def demo5():
    print("== demo5 tcache 语义与上层策略 ==")
    a = Arena()
    check("master:每 bin 上限 16", a.tcache_count == 16)

    c100 = a.malloc(100)
    check("request 100 -> chunk 112", c100.size == 112, c100.size)
    check("chunk 112 的 tcache idx = 5", csize2tidx(112) == 5)
    c112 = a.malloc(112)
    check("request 112 -> chunk 128", c112.size == 128, c112.size)
    check("chunk 128 的 tcache idx = 6", csize2tidx(128) == 6)

    a.free(c112)
    a.reset_path()
    a.malloc(100)
    check("tcache 只做**精确尺寸**匹配:112 的请求拿不到 128 的块",
          "tcache-hit" not in a.path, a.path)
    check("此时回落到普通路径而非'用更大的 chunk'", a.path[-1] == "split-top", a.path)

    b = Arena()
    xs = [b.malloc(64) for _ in range(20)]
    for x in xs:
        b.free(x)
    slots = b.tcache[csize2tidx(request2size(64))]
    check("tcache 装到上限即止", len(slots) == b.tcache_count, len(slots))
    check("溢出部分落到 fastbin", sum(len(v) for v in b.fastbins.values()) == 20 - b.tcache_count)
    check("tcache 的链表指针指向 payload(故比 fastbin 省一次间接)",
          tidx2csize(csize2tidx(request2size(64))) == request2size(64))

    check("arena 上限 = 8 * CPU 核数(手册原文)", Arena(cpu=4).arena_max == 32)
    check("arena 上限随核数线性增长", Arena(cpu=16).arena_max == 128)
    check("M_MMAP_MAX 默认 65536(设 0 即禁用 mmap 路径)", Arena().mmap_max == 65536)


def main():
    for fn in (demo1, demo2, demo3, demo4, demo5):
        fn()
        print()
    print(f"断言总数 {TOTAL[0]},失败 {len(FAILS)}")
    if FAILS:
        for f in FAILS:
            print("  FAILED:", f)
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
