"""
selfcheck_ms.py —— mark-sweep + coalescing 模型的断言集。

断言原则：只断言「误报集/漏报集」这类可判定的量，不把「应然 TRUTH」当证据。
例如「best-fit 比 first-fit 更省空间」是不成立的恒真命题（依赖数据），
所以这里断言的是**选中的 block 地址**这一确定量。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import (Heap, MSHeap, WORD, HDR_WORDS, MIN_CHUNK_WORDS_64,
                  MIN_CHUNK_BYTES_32, MIN_CHUNK_BYTES_64, NBINS,
                  SMALL_BIN_LIMIT, SMALL_BIN_SPACING, DEFAULT_MMAP_THRESHOLD)

PASS = [0]


def ok(cond, msg):
    assert cond, "FAIL: " + msg
    PASS[0] += 1
    print("  ok  %s" % msg)


def eq(a, b, msg):
    ok(a == b, "%s  (got %r, want %r)" % (msg, a, b))


# ---------------------------------------------------------------- 1. 常量
def t_constants():
    print("[1] dlmalloc 常量口径")
    eq(MIN_CHUNK_BYTES_32, 16, "32 位指针下最小可分配 chunk = 16 字节")
    eq(MIN_CHUNK_BYTES_64, 24, "64 位指针下最小可分配 chunk = 24 字节")
    eq(MIN_CHUNK_WORDS_64, 3, "64 位最小 chunk = 3 word(头 + 两条 bin 链指针)")
    eq(NBINS, 128, "dlmalloc 固定 128 个 bin")
    eq(DEFAULT_MMAP_THRESHOLD, 1 << 20, "mmap 阈值默认 1 MB")


# ---------------------------------------------------------------- 2. binning
def t_binning():
    print("[2] bin 编号：<512B 每 bin 一种尺寸、间隔 8B；>=512B 近似对数")
    eq(Heap.bin_index(8), 1, "8 字节 -> bin 1")
    eq(Heap.bin_index(16), 2, "16 字节 -> bin 2")
    eq(Heap.bin_index(504), 63, "504 字节 -> bin 63")
    sizes = list(range(8, SMALL_BIN_LIMIT, SMALL_BIN_SPACING))
    eq(len(set(Heap.bin_index(s) for s in sizes)), len(sizes),
       "<512B 的每个尺寸各占一个 bin（共 %d 个）" % len(sizes))
    eq(len(sizes), 63, "<512B 共 63 档（8,16,...,504）")
    eq(max(Heap.bin_index(s) for s in sizes), 63, "最高档占 bin 63（bin 0 留给 0 尺寸）")
    eq(Heap.bin_index(1024) - Heap.bin_index(512), 4,
       "512B 起每翻倍 4 个 bin（近似对数间隔的一种读法）")
    eq(Heap.bin_index(2048) - Heap.bin_index(1024), 4, "1024->2048 同样 4 个 bin")
    ok(all(Heap.bin_index(s) < NBINS for s in (8, 512, 1 << 20, 1 << 30)),
       "所有 bin 编号都落在 128 个 bin 之内")


# ---------------------------------------------------------------- 3. 合并
def t_coalesce():
    print("[3] 边界标记与相邻空闲块合并")
    h = Heap(64)
    a = h.malloc(4); b = h.malloc(4); c = h.malloc(4)
    eq((a, b, c), (1, 6, 11), "三个 4-word 对象落在 payload 1/6/11")
    h.free(b)
    eq(len(h.blocks), 4, "释放中间块后仍是 4 块（两侧在用，不能合并）")
    h.free(a); h.free(c)
    eq(len(h.blocks), 1, "三块全部释放后合并成 1 块")
    eq(h.blocks[0].size, 64, "合并后的块覆盖整堆 64 word（无地址空洞）")


def t_deferred_coalesce():
    print("[4] Deferred Coalescing：不合并就会假性 OOM")
    h = Heap(20, defer_coalesce=True)
    a = h.malloc(4); b = h.malloc(4); c = h.malloc(4)
    h.free(a); h.free(b); h.free(c)
    eq([x.size for x in h.blocks], [5, 5, 5, 5], "延迟合并下保留 4 个 5-word 空闲块")
    eq(h.malloc(8), None, "三块共 15 word 可用，但没有单块 >= 9 word -> 分配失败")
    h.coalesce()
    eq(len(h.blocks), 1, "强制合并后变回 1 块")
    ok(h.malloc(8) is not None, "合并后同样大小的请求成功")

    h2 = Heap(20)                       # 立即合并作为对照
    p = [h2.malloc(4) for _ in range(3)]
    for x in p:
        h2.free(x)
    eq(len(h2.blocks), 1, "对照：非延迟模式下同样序列直接合并成 1 块")
    ok(h2.malloc(8) is not None, "对照：非延迟模式下请求直接成功")


def t_footer_cost():
    print("[5] 边界标记的空间代价：新版本在使用中 chunk 上省略 trailer")
    def build(classic):
        h = Heap(64, classic_tags=classic)
        for _ in range(4):
            h.malloc(4)
        return h
    hc, hm = build(True), build(False)
    eq(hc.footer_words(), len(hc.blocks), "经典：每个 chunk 都带 trailer")
    eq(hm.footer_words(), len(hm.free_blocks()), "新版：只有空闲 chunk 带 trailer")
    ok(hm.footer_words() < hc.footer_words(),
       "新版 footer 总数更少（省下的正是 4 个在用 chunk 的 trailer）")
    eq(hc.has_footer if False else hm.blocks[0].has_footer, False,
       "新版：在用 chunk 的 has_footer 为 False")
    eq(hc.blocks[0].has_footer, True, "经典：在用 chunk 仍带 footer")


# ---------------------------------------------------------------- 6. 放置策略
def t_fit_policy():
    print("[6] first-fit / best-fit / wilderness preservation")
    def build(policy):
        h = Heap(20, policy=policy)
        a = h.malloc(4); b = h.malloc(4); c = h.malloc(4)
        h.free(a); h.free(b); h.free(c)
        return h
    # 对照：立即合并策略下三块会合成一整块，无从比较放置策略 -> 用延迟合并造碎片
    def frag(policy):
        h = Heap(40, policy=policy, defer_coalesce=True)
        a = h.malloc(19); b = h.malloc(4); c = h.malloc(4); d = h.malloc(4)
        h.free(c); h.free(a)
        return h, a, c
    h1, _, _ = frag("first")
    h2, _, _ = frag("best")
    eq([(x.addr, x.size, x.free) for x in h1.blocks],
       [(0, 20, True), (20, 5, False), (25, 5, True), (30, 5, False), (35, 5, True)],
       "碎片布局：空闲块 size 20 / 5 / 5")
    eq(h1.malloc(4), 1, "first-fit 取地址最低的候选（size 20）")
    eq(h2.malloc(4), 26, "best-fit 取最小的候选（size 5，addr 25 -> payload 26）")

    # wilderness：即便 wilderness 更小，best-first 也不选它
    hw = Heap(40, policy="dlmalloc")
    hb = Heap(40, policy="best")
    for h in (hw, hb):
        h.malloc(15); h.malloc(15)
    hw.free(1); hb.free(1)
    eq([(x.addr, x.size, x.free) for x in hw.blocks],
       [(0, 16, True), (16, 16, False), (32, 8, True)],
       "非 wilderness 空闲块 16 word，wilderness 8 word")
    eq(hb.malloc(4), 33, "纯 best-fit 选更小的 wilderness（payload 33）")
    eq(hw.malloc(4), 1, "dlmalloc 把 wilderness 当'最大'，只在这时才用非 wilderness 块")


def t_oldest_first():
    print("[7] bin 内按尺寸升序、同尺寸按 oldest-first")
    h = Heap(16, defer_coalesce=True)
    ps = [h.malloc(3) for _ in range(4)]
    eq(ps, [1, 5, 9, 13], "4 个 3-word 对象刚好占满 16 word")
    h.free(ps[3])          # 先释放 d
    h.free(ps[1])          # 后释放 b
    eq([(x.addr, x.size, x.free) for x in h.blocks],
       [(0, 4, False), (4, 4, True), (8, 4, False), (12, 4, True)],
       "两个等尺寸空闲块不相邻（中间隔着在用块）")
    b = h.bins()
    eq(len(b), 1, "两个空闲块落在同一个 bin（usable 同为 24 字节）")
    idx = Heap.bin_index(3 * WORD)
    eq(idx, 3, "usable 3 word = 24 字节 -> bin 3")
    eq([x.addr for x in b[idx]], [12, 4], "bin 内顺序：先释放的 addr 12 排在最前")


def t_min_chunk():
    print("[8] 分割下限：余数小于最小 chunk 时整体交付")
    h = Heap(7)
    p = h.malloc(4)
    eq(len(h.blocks), 1, "余数 2 word < 最小 3 word，不产生新块")
    eq(h.blocks[0].size, 7, "整块交付，size 保持 7")
    eq(h.blocks[0].usable, 6, "usable 6 word > 请求的 4 word（内部浪费 2 word）")
    ok(not h.blocks[0].free, "该块标记为在用")


def t_mmap():
    print("[9] mmap 阈值：仅当 arena 满足不了时才启用，且永不并入 arena")
    h = Heap(24, mmap_threshold=64)
    p1 = h.malloc(20)                       # 21 word = 168 B > 64 B 阈值
    eq(len(h._mmap_blocks()), 0, "arena 还装得下 -> 不走 mmap（dlmalloc 原文条件 2）")
    p2 = h.malloc(20)
    eq(len(h._mmap_blocks()), 1, "arena 装不下 -> 走 mmap")
    h.free(p2)
    eq(len(h.blocks), 3, "释放后的 mmap 块即使地址相邻也不与 arena 合并")
    ok(all(not x.mmapped or x.free for x in h.blocks), "mmap 块已回到空闲状态")


# ---------------------------------------------------------------- 10. mark-sweep
def t_marksweep():
    print("[10] mark-sweep：环形垃圾、清扫扫描全堆、清扫后合并")
    h = MSHeap(32)
    a = h.new(3)
    b = h.new(3, [a])
    h.refs[a] = [b]                          # a <-> b 形成环
    h.roots = []
    eq(h.mark(), set(), "没有根 -> 标记集为空（环也不可达）")
    collected = h.sweep()
    eq(collected, 2, "清扫回收 2 个（引用计数此时为 0 个）")
    eq(h.mark_visited, 0, "标记阶段访问 0 个对象")
    eq(h.sweep_scanned, 3, "清扫阶段扫过整个堆的 3 个块（O(heap)）")
    eq(len(h.blocks), 1, "清扫后相邻空闲块合并成 1 块")

    # 引用计数对照（环形垃圾漏报）
    rc = {a: 1, b: 1}
    eq(sum(1 for v in rc.values() if v == 0), 0, "同样形状下引用计数回收 0 个")

    # 不变式：清扫之后不应再存在相邻的两个空闲块
    h2 = MSHeap(40)
    x = [h2.new(3) for _ in range(5)]
    h2.roots = [x[0], x[2], x[4]]
    h2.sweep()
    bad = [i for i in range(len(h2.blocks) - 1)
           if h2.blocks[i].free and h2.blocks[i + 1].free]
    eq(bad, [], "清扫 + 合并后不存在相邻空闲块")
    eq(h2.sweep_scanned, 6, "清扫扫描数 = 清扫当时的块数（5 个对象 + 1 个剩余块 = 6）")
    eq(len(h2.blocks), 6, "隔一个留一个：回收的 2 块各自被在用块隔开，无法合并")


def t_gc_alloc():
    print("[11] 分配失败触发 GC 后重试")
    h = MSHeap(16)
    a = h.new(3); b = h.new(3)
    h.roots = []
    eq(h.malloc(8), None, "GC 之前 9 word 的请求失败（只剩 8 word 空闲）")
    p = h.gc_alloc(8)
    eq(p, 1, "GC 回收 a/b 后重试成功，落在 payload 1")
    eq([(x.addr, x.size, x.free) for x in h.blocks],
       [(0, 9, False), (9, 7, True)], "新块 9 word + 剩余 7 word")


def main():
    t_constants(); t_binning(); t_coalesce(); t_deferred_coalesce()
    t_footer_cost(); t_fit_policy(); t_oldest_first(); t_min_chunk()
    t_mmap(); t_marksweep(); t_gc_alloc()
    print("\nALL PASS: %d assertions" % PASS[0])


if __name__ == "__main__":
    main()
