"""Memcached slab 分配器自检。运行： python slab_alloc_selftest.py"""
import sys
from slab_alloc import (
    DEFAULT_MAXBYTES, DEFAULT_FACTOR, DEFAULT_ITEM_SIZE_MAX,
    DEFAULT_SLAB_PAGE_SIZE, DEFAULT_SLAB_CHUNK_SIZE_MAX, DEFAULT_CHUNK_SIZE,
    CHUNK_ALIGN_BYTES, MAX_NUMBER_OF_SLAB_CLASSES, POWER_SMALLEST, POWER_LARGEST,
    sizeof_item_lp64, SIZEOF_ITEM, align_up, slab_classes, class_for,
    chunk_size_of, waste_of, SlabAllocator,
)

FAILS = []


def check(label, cond, detail=""):
    if cond:
        print("  ok   %s" % label)
    else:
        FAILS.append("%s %s" % (label, detail))
        print("  FAIL %s %s" % (label, detail))


print("[1] item 结构体大小（LP64）")
check("sizeof(item) = 48", SIZEOF_ITEM == 48, SIZEOF_ITEM)
check("字段原始字节数 42", sizeof_item_lp64() - 42 == 6,
      "padding=%d" % (sizeof_item_lp64() - 42))
check("初始 chunk = sizeof(item) + chunk_size = 96",
      SIZEOF_ITEM + DEFAULT_CHUNK_SIZE == 96)
check("chunk_size 默认值 48（注释：space for a modest key and value）",
      DEFAULT_CHUNK_SIZE == 48)

print("[2] 全局默认值（memcached.c）")
check("maxbytes 64MB", DEFAULT_MAXBYTES == 64 * 1024 * 1024)
check("factor 1.25", DEFAULT_FACTOR == 1.25)
check("item_size_max 1MB（famous 1MB upper limit）",
      DEFAULT_ITEM_SIZE_MAX == 1024 * 1024)
check("slab_page_size 1MB", DEFAULT_SLAB_PAGE_SIZE == 1024 * 1024)
check("slab_chunk_size_max = page/2 = 512KB",
      DEFAULT_SLAB_CHUNK_SIZE_MAX == 512 * 1024)
check("CHUNK_ALIGN_BYTES 8", CHUNK_ALIGN_BYTES == 8)
check("MAX_NUMBER_OF_SLAB_CLASSES = 63+1 = 64", MAX_NUMBER_OF_SLAB_CLASSES == 64)
check("POWER_SMALLEST 1 / POWER_LARGEST 256",
      POWER_SMALLEST == 1 and POWER_LARGEST == 256)

print("[3] slab class 表（默认参数）")
cs = slab_classes()
power_largest = cs[-1][0]
check("power_largest = %d" % power_largest, power_largest == 39, power_largest)
check("class 1 = 96 字节 / perslab 10922", cs[0][1:] == (96, 10922), cs[0])
check("class 2 = 120 / 8738", cs[1][1:] == (120, 8738), cs[1])
check("class 3 = 152 / 6898（150 被对齐到 152）", cs[2][1:] == (152, 6898), cs[2])
check("最后一个 class = 512KB / perslab 2",
      cs[-1][1:] == (DEFAULT_SLAB_CHUNK_SIZE_MAX, 2), cs[-1])
check("所有 class 尺寸 8 字节对齐",
      all(s % CHUNK_ALIGN_BYTES == 0 for _, s, _ in cs))
check("perslab = page // size（向下取整）",
      all(p == DEFAULT_SLAB_PAGE_SIZE // s for _, s, p in cs))
check("尾部碎片 < 一个 chunk（否则还能再塞一个）",
      all(DEFAULT_SLAB_PAGE_SIZE - p * s < s for _, s, p in cs))
check("尺寸单调不减", all(cs[i][1] <= cs[i + 1][1] for i in range(len(cs) - 1)))
check("perslab 单调不增", all(cs[i][2] >= cs[i + 1][2] for i in range(len(cs) - 1)))
ratios = [cs[i + 1][1] / cs[i][1] for i in range(len(cs) - 2)]
check("相邻类倍率接近 1.25（受 8 字节对齐扰动）",
      all(1.20 <= r <= 1.30 for r in ratios),
      "min=%.3f max=%.3f" % (min(ratios), max(ratios)))
check("建表上限由 chunk_size_max/factor 决定，不是 MAX_NUMBER_OF_SLAB_CLASSES",
      len(cs) < MAX_NUMBER_OF_SLAB_CLASSES)

print("[4] 内部碎片：为什么 factor 是内存与浪费的取舍")
cid, size, internal, tail, frac = waste_of(100)
check("100 字节的 item 落在 class 2（120）", (cid, size) == (2, 120), (cid, size))
check("内部碎片 20 字节（16.7%）", internal == 20 and abs(frac - 20 / 120) < 1e-9,
      (internal, frac))
cid, size, internal, tail, frac = waste_of(500)
check("500 字节落在 class 9（600）", (cid, size) == (9, 600), (cid, size))
check("内部碎片 100 字节（16.7%）", internal == 100, internal)
cid, size, internal, tail, frac = waste_of(97)
check("97 字节落在 class 2（class 1 只有 96，差 1 字节就要跳级）",
      (cid, size, internal) == (2, 120, 23), (cid, size, internal))
# 最坏情况：刚好比某 class 大 1 字节
worst = 0.0
for _, s, _ in cs[:-1]:
    worst = max(worst, waste_of(s + 1)[4])
check("最坏碎片率接近 factor-1 = 25%", worst > 0.20 and worst < 0.26,
      "worst=%.4f" % worst)
# 落在 class i 的尺寸区间是 (size_{i-1}, size_i]，取区间中点作为代表
fractions = []
for i in range(1, len(cs) - 1):
    mid = (cs[i - 1][1] + cs[i][1]) // 2 + 1
    fractions.append(waste_of(mid)[4])
avg = sum(fractions) / len(fractions)
# 理论值：1 - (1 + 1/factor)/2 = (factor-1)/(2*factor) = 0.1
check("均匀分布下平均碎片率 ≈ (factor-1)/(2·factor) = 10%",
      abs(avg - 0.10) < 0.03, "avg=%.4f" % avg)

print("[5] 1MB 限制与 chunked item")
check("item_size_max = 1MB", DEFAULT_ITEM_SIZE_MAX == 1024 * 1024)
check("但单个 chunk 上限只有 512KB", DEFAULT_SLAB_CHUNK_SIZE_MAX == 512 * 1024)
check("超过 512KB 的 item 必须跨多个 chunk（ITEM_CHUNKED）",
      class_for(DEFAULT_SLAB_CHUNK_SIZE_MAX) == power_largest
      and class_for(DEFAULT_SLAB_CHUNK_SIZE_MAX + 1) == 0)
check("恰好 512KB 可放入最后一个 class",
      class_for(DEFAULT_SLAB_CHUNK_SIZE_MAX) == power_largest)

print("[6] 内存卡死在某个 class（calc'ing / slab 不均衡）")
# 64 页 × 1MB = 64MB。阶段一全部写 100 字节（class 2），阶段二全部写 500 字节（class 9）
a = SlabAllocator(total_pages=64)
cid_small, cid_big = 2, 9
for _ in range(64 * 8738):            # 把 64MB 全填满小 item
    a.put(cid_small)
check("小 item 阶段吃满 64 页", a.free_pages == 0 and a.pages_of[cid_small] == 64,
      (a.free_pages, a.pages_of))
check("大 item 所在 class 一页都没有", a.pages_of.get(cid_big, 0) == 0)
before = a.evictions
for _ in range(1000):
    a.put(cid_big)
check("写入大 item 全部触发淘汰（内存其实还空着）", a.evictions - before == 1000,
      a.evictions - before)
check("大 item 一个都没存下", a.used_chunks.get(cid_big, 0) == 0)
# 腾空 class 2 的最后一页后再做 slab reassignment
a.used_chunks[cid_small] = (a.pages_of[cid_small] - 1) * 8738
moved = a.reassign(cid_small, cid_big)
check("腾空整页后可 reassignment", moved is True)
check("大 item class 拿到 1 页", a.pages_of[cid_big] == 1, a.pages_of)
before = a.evictions
for _ in range(1000):
    a.put(cid_big)
check("拿到页后不再淘汰", a.evictions == before, a.evictions - before)
check("大 item 已存入", a.used_chunks[cid_big] > 0, a.used_chunks.get(cid_big))
check("最后一页仍有在用 chunk 时挪不动",
      SlabAllocator(2).reassign(cid_small, cid_big) is False)

print()
if FAILS:
    print("FAILED %d:" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
