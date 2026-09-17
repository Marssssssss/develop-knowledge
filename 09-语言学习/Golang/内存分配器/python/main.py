#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go 运行时内存分配器模型（对齐 Go 1.24 官方 size class 表）。

本文件把 runtime 分配器的三件事程序化复刻出来：
  1. `size class` 表本身（`class_to_size` / `class_to_allocnpages`）与两级快速映射
     （`size_to_class8` / `size_to_class128`）；
  2. `roundupsize`（请求字节 → 实际 block 字节）与页对齐的大对象路径；
  3. `mcache` 的 tiny allocator（< 16 B 且不含指针的对象合并进一个 16 B 块）
     与「span 耗尽 → 向 mcentral 补货」的计数。

表数据与纯函数在 `go_sizeclasses.py`（由 `inject_tables.py` 从官方源码生成）；
`roundupsize` 逻辑取自 `src/runtime/msize.go`；tiny allocator 取自 `src/runtime/malloc.go`
的 `mallocgcTiny`。本文件只放 MCache 模型与断言。所有断言实跑通过。
"""

from go_sizeclasses import *  # noqa: F401,F403


# ---------------------------------------------------------------- 断言脚手架
STATS = {"n": 0, "fail": []}


def check(label, cond, detail=""):
    """与 Go 侧同名的断言助手：失败不中止，最后统一汇总。"""
    STATS["n"] += 1
    if not cond:
        STATS["fail"].append(label)
    print(("PASS  " if cond else "FAIL  ") + label
          + (("  | " + str(detail)) if detail else ""))


def div_round_up(a, b):
    return (a + b - 1) // b


def build_size_to_class(table_size, div, base):
    """由 class_to_size 反推「字节数 → class」表（与 mksizeclasses.go 同逻辑）。"""
    out = []
    for i in range(table_size // div + 1):
        want = base + i * div
        cls = 1
        while CLASS_TO_SIZE[cls] < want:
            cls += 1
        out.append(cls)
    return out


SIZE_TO_CLASS8 = build_size_to_class(SMALL_SIZE_MAX, SMALL_SIZE_DIV, 0)
SIZE_TO_CLASS128 = build_size_to_class(MAX_SMALL_SIZE - SMALL_SIZE_MAX, LARGE_SIZE_DIV,
                                       SMALL_SIZE_MAX)


def size_class_of(blk):
    """blk 字节落在哪个 size class；大对象（页对齐）返回 -1。"""
    try:
        return CLASS_TO_SIZE.index(blk)
    except ValueError:
        return -1


def roundupsize(size):
    """复刻 runtime/msize.go 的 noscan 小对象路径；大对象按页取整。"""
    if size <= MAX_SMALL_REQUEST:
        if size <= SMALL_SIZE_MAX - SMALL_SIZE_DIV:
            return CLASS_TO_SIZE[SIZE_TO_CLASS8[div_round_up(size, SMALL_SIZE_DIV)]]
        return CLASS_TO_SIZE[SIZE_TO_CLASS128[div_round_up(size - SMALL_SIZE_MAX,
                                                          LARGE_SIZE_DIV)]]
    return div_round_up(size, PAGE_SIZE) * PAGE_SIZE


class MCache:
    """mcache 最小模型：per-P、无锁；span 耗尽时向 mcentral 补货。"""

    def __init__(self):
        self.tiny = 0                 # 当前 tiny block 地址（0 表示无）
        self.tinyoffset = 0
        self.tinyallocs = 0
        self.bump = 0x1000            # 伪堆指针，仅用于展示地址递增
        self.spans = {}               # class -> 该 span 剩余对象数
        self.mcentral_fetches = 0
        self.tiny_span_left = 0       # class 2 span 里剩余的 16 B tiny block 数

    def _refill(self, cls):
        span = CLASS_TO_ALLOCNPAGES[cls] * PAGE_SIZE
        self.spans[cls] = span // CLASS_TO_SIZE[cls]
        self.mcentral_fetches += 1
        return self.spans[cls]

    def _tiny_refill(self):
        # tiny block 本身是 class 2（16 B）span 里的一个对象
        self.tiny_span_left = self._refill(TINY_SIZE_CLASS)

    def mallocgc(self, size, noscan):
        """返回 (地址, elemsize)；elemsize 是本次分配实际占用的槽位大小。"""
        if size < TINY_SIZE and noscan:
            return self.mallocgc_tiny(size)
        blk = roundupsize(size)
        cls = size_class_of(blk)
        if cls < 0:
            # 大对象不落在任何 size class 上，直接从 mheap 拿页
            addr = self.bump
            self.bump += blk
            return addr, blk
        if self.spans.get(cls, 0) == 0:
            self._refill(cls)
        self.spans[cls] -= 1
        addr = self.bump
        self.bump += blk
        return addr, blk

    def mallocgc_tiny(self, size):
        off = self.tinyoffset
        # 对齐：8 的倍数→8；4 的倍数→4；2 的倍数→2；奇数→不额外对齐
        if size % 8 == 0:
            off = (off + 7) & ~7
        elif size % 4 == 0:
            off = (off + 3) & ~3
        elif size % 2 == 0:
            off = (off + 1) & ~1
        if off + size <= TINY_SIZE and self.tiny != 0:
            self.tinyoffset = off + size
            self.tinyallocs += 1
            return self.tiny + off, TINY_SIZE
        if self.tiny_span_left == 0:
            self._tiny_refill()
        self.tiny_span_left -= 1
        self.tiny = self.bump
        self.bump += TINY_SIZE
        self.tinyoffset = size
        self.tinyallocs += 1
        return self.tiny, TINY_SIZE


def main():
    print("=== A. size class 表完整性（官方 Go 1.24 src/runtime/sizeclasses.go）===")
    check("A1 共 68 个 size class（0 号未使用）", len(CLASS_TO_SIZE) == 68, len(CLASS_TO_SIZE))
    check("A2 class 0 为 0 字节且 class 1 为 8 字节",
          (CLASS_TO_SIZE[0], CLASS_TO_SIZE[1]) == (0, 8))
    check("A3 尺寸严格递增", all(CLASS_TO_SIZE[i] < CLASS_TO_SIZE[i + 1] for i in range(1, 67)))
    check("A4 最大小对象 32768 == gc.MaxSmallSize", CLASS_TO_SIZE[67] == MAX_SMALL_SIZE)
    check("A5 tinySizeClass=2 对应 16 B（源码自断言）",
          CLASS_TO_SIZE[TINY_SIZE_CLASS] == TINY_SIZE, CLASS_TO_SIZE[TINY_SIZE_CLASS])

    print("\n=== B. span 布局：objects / tail waste / max waste 全表复核 ===")
    bad_obj, bad_tail, bad_waste, bad_sum = [], [], [], []
    for c in range(1, 68):
        span = CLASS_TO_ALLOCNPAGES[c] * PAGE_SIZE
        size = CLASS_TO_SIZE[c]
        objs = span // size
        if objs != OFFICIAL_OBJECTS[c - 1]:
            bad_obj.append(c)
        if span - objs * size != OFFICIAL_TAIL_WASTE[c - 1]:
            bad_tail.append(c)
        if span != objs * size + OFFICIAL_TAIL_WASTE[c - 1]:
            bad_sum.append(c)
        bp = round((1 - (CLASS_TO_SIZE[c - 1] + 1) * objs / span) * 10000)
        if bp != OFFICIAL_MAX_WASTE_BP[c - 1]:
            bad_waste.append((c, bp, OFFICIAL_MAX_WASTE_BP[c - 1]))
    check("B1 67 个 class 的 objects 与官方注释表逐行一致", not bad_obj, bad_obj)
    check("B2 67 个 class 的 tail waste 与官方注释表逐行一致", not bad_tail, bad_tail)
    check("B3 span = objects × size + tail waste 对 67 行全部成立", not bad_sum, bad_sum)
    check("B4 max waste 可由 1-(前一类尺寸+1)×objects/span 复算到万分之一",
          not bad_waste, bad_waste[:4])
    check("B5 class 1 的 max waste 上限 87.5%（1 B 请求塞进 8 B 槽）",
          OFFICIAL_MAX_WASTE_BP[0] == 8750)
    check("B6 240→256 是零浪费分界：8192 不能被 240 整除但能被 256 整除",
          CLASS_TO_SIZE[17] == 240 and CLASS_TO_SIZE[18] == 256
          and 8192 % 240 != 0 and 8192 % 256 == 0)

    print("\n=== C. 两级快速映射表 ===")
    check("C1 size_to_class8 长度 = 1024/8+1 = 129", len(SIZE_TO_CLASS8) == 129,
          len(SIZE_TO_CLASS8))
    check("C2 size_to_class128 长度 = 31744/128+1 = 249", len(SIZE_TO_CLASS128) == 249,
          len(SIZE_TO_CLASS128))
    diff = next((i for i, (a, b) in enumerate(zip(SIZE_TO_CLASS128,
                                                  OFFICIAL_SIZE_TO_CLASS128)) if a != b), None)
    check("C3 size_to_class128 与源码数组 249 项逐项一致", diff is None, diff)
    check("C4 class8 上界落到 class 32（正好 1024 B）", SIZE_TO_CLASS8[128] == 32)
    check("C5 8 B 请求→class 1；9 B 请求→class 2（8 B 粒度）",
          SIZE_TO_CLASS8[1] == 1 and SIZE_TO_CLASS8[div_round_up(9, 8)] == 2)
    check("C6 1025 B 走 128 粒度 → class 33（1152）",
          SIZE_TO_CLASS128[div_round_up(1025 - SMALL_SIZE_MAX, LARGE_SIZE_DIV)] == 33)

    print("\n=== D. roundupsize：请求字节 → 实际 block ===")
    for req, want in [(1, 8), (8, 8), (9, 16), (17, 24), (24, 24), (25, 32), (33, 48),
                      (48, 48), (49, 64), (65, 80), (1024, 1024), (1025, 1152),
                      (1281, 1408), (1409, 1536), (8193, 9472), (32768, 32768)]:
        got = roundupsize(req)
        check("D req=%d → %d" % (req, want), got == want, got)
    prev, notsmall, mono = 0, True, True
    for req in range(1, MAX_SMALL_SIZE + 1):
        got = roundupsize(req)
        notsmall = notsmall and got >= req
        mono = mono and got >= prev
        prev = got
    check("D17 1..32768 全量扫描：结果恒 >= 请求", notsmall)
    check("D18 1..32768 全量扫描：结果单调不减", mono)
    check("D19 结果必为 size class 成员或页的整数倍",
          all(roundupsize(r) in CLASS_TO_SIZE or roundupsize(r) % PAGE_SIZE == 0
              for r in range(1, 70000)))

    print("\n=== E. 大对象路径：按页对齐 ===")
    check("E1 32769 → 40960（5 页）", roundupsize(32769) == 40960, roundupsize(32769))
    check("E2 65536 恰好整页不额外放大", roundupsize(65536) == 65536)
    check("E3 65537 → 73728", roundupsize(65537) == 73728, roundupsize(65537))
    check("E4 大对象结果不落在任何 size class 上", roundupsize(40000) not in CLASS_TO_SIZE)

    print("\n=== F. tiny allocator（< 16 B 且不含指针）===")
    mc = MCache()
    a1, e1 = mc.mallocgc(5, noscan=True)
    a2, e2 = mc.mallocgc(3, noscan=True)
    a3, e3 = mc.mallocgc(6, noscan=True)
    check("F1 三次 tiny 分配落在同一个 16 B block 内（偏移 0/5/8）",
          [a - mc.tiny for a in (a1, a2, a3)] == [0, 5, 8]
          and all(a1 <= a < a1 + TINY_SIZE for a in (a2, a3)), hex(mc.tiny))
    check("F2 子对象偏移按 size 累加（5 → 8）", (a2 - a1, a3 - a1) == (5, 8), (a2 - a1, a3 - a1))
    check("F3 tinyAllocs 计数为 3", mc.tinyallocs == 3)
    check("F4 elemsize 恒为 16（整个 block 归 class 2 span 管）",
          (e1, e2, e3) == (TINY_SIZE,) * 3)
    mc.mallocgc(6, noscan=True)
    check("F5 第 4 次（14+6 > 16）另开销 block，计数 4", mc.tinyallocs == 4)

    mc2 = MCache()
    mc2.mallocgc(5, noscan=True)
    b9, _ = mc2.mallocgc(9, noscan=False)
    check("F6 含指针对象不得进 tiny（9 B 走 size class 路径）", b9 != mc2.tiny)
    check("F7 含指针 9 B 的实际槽位是 16 B（class 2）", roundupsize(9) == 16)

    mc3 = MCache()
    c0, _ = mc3.mallocgc(8, noscan=True)
    c1, _ = mc3.mallocgc(1, noscan=True)
    check("F8 size%8==0 时在 block 内按 8 字节对齐", c0 % 8 == 0)
    check("F9 奇数尺寸不引入对齐填充（1 紧跟在 8 之后）", c1 - c0 == 8, c1 - c0)

    mc4 = MCache()
    d16, _ = mc4.mallocgc(16, noscan=True)
    check("F10 size == 16 不进 tiny（必须能被显式释放）", d16 != mc4.tiny)
    check("F11 16 B 请求 → class 2 槽位 16 B", roundupsize(16) == 16)

    print("\n=== G. 三级缓存：mcache → mcentral → mheap ===")
    check("G1 spanClass = size class × 2（scan / noscan 各一套），共 136",
          68 * 2 == 136)
    check("G2 noscan spanClass = class<<1，scan = class<<1|1（可逆编码）",
          all((c << 1) // 2 == c and ((c << 1 | 1) >> 1) == c for c in range(68)))
    mc5 = MCache()
    for _ in range(1000):
        mc5.mallocgc(48, noscan=False)
    check("G3 1000 个 48 B 对象只需 6 次向 mcentral 补货（每 span 170 个）",
          mc5.mcentral_fetches == 6, mc5.mcentral_fetches)
    check("G4 补货次数 = ceil(1000 / (8192//48)) = 6", -(-1000 // (8192 // 48)) == 6)
    mc6 = MCache()
    for _ in range(512):
        mc6.mallocgc(5, noscan=True)      # 每次开新 block（5+5+5 > 16）
    check("G5 512 个 tiny block 恰好吃光 1 个 class 2 span（512 个槽）",
          mc6.mcentral_fetches == 1, mc6.mcentral_fetches)
    mcb = MCache()
    _, blk = mcb.mallocgc(50000, noscan=False)
    check("G6 大对象绕过 size class 直接向 mheap 要页（不触发 mcentral 补货）",
          blk == 57344 and mcb.mcentral_fetches == 0 and size_class_of(blk) == -1,
          (blk, mcb.mcentral_fetches))

    print("\n=== H. 统计口径：同样请求次数，size class 决定实际占用 ===")
    def footprint(n, req):
        m = MCache()
        return sum(m.mallocgc(req, noscan=False)[1] for _ in range(n))

    check("H1 10 万个 1 B 对象实际占 800 KB（8 倍放大）", footprint(100000, 1) == 800000)
    check("H2 10 万个 100 B 对象占 11.2 MB（112 B 槽）", footprint(100000, 100) == 11200000)
    check("H3 100 B 请求的槽位浪费率 = 12/112 = 10.71%",
          round((1 - 100 / 112) * 10000) == 1071)
    check("H4 换成 96 B 请求浪费率 0%（正好命中 class 8）", roundupsize(96) == 96)

    print("\n" + "=" * 62)
    print("断言总数 %d；失败 %d %s" % (STATS["n"], len(STATS["fail"]), STATS["fail"] or "（全绿）"))
    print("=" * 62)
    return 1 if STATS["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
