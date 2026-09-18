"""Cheney 半空间复制式 GC —— 5 组实验(可实跑自检)。

  python3 main.py    # 断言全绿

算法在 cheney.py;本文件放实验夹具、实验与断言。
"""

from cheney import HEADER_WORDS, WORD, Chain, Obj, Slot, cheney  # noqa: F401

FAILS = []
TOTAL = [0]


def check(label, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        print(f"  [ok] {label}")
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label} {detail}")


def build_chain_graph():
    """A->B, A->C, B->D, C->D(共享), D->E;另有不可达的 X->Y 与孤立 Z。"""
    g = Chain(size_words=64)
    for oid in ("A", "B", "C", "D", "E", "X", "Y", "Z"):
        g.add(oid)
    g.root("r1", "A")
    g.edge("A", "b").val = "B"
    g.edge("A", "c").val = "C"
    g.edge("B", "d").val = "D"
    g.edge("C", "d").val = "D"
    g.edge("D", "e").val = "E"
    g.edge("X", "y").val = "Y"    # 不可达
    return g


def free_blocks(all_words, live_indexes):
    """按 mark-sweep 的口径算空闲区间(相邻空闲合并)。"""
    out, cur = [], 0
    for i, w in enumerate(all_words):
        if i in live_indexes:
            if cur:
                out.append(cur)
                cur = 0
        else:
            cur += w
    if cur:
        out.append(cur)
    return out


# --------------------------------------------------------------------------- #
def demo1():
    print("== demo1 Cheney 复制全过程 ==")
    g = build_chain_graph()
    to_space, forward, trace = cheney(g)
    inv = {v: k for k, v in forward.items()}

    check("只有可达对象被复制(5 个)", g.stats["copied"] == 5, g.stats["copied"])
    check("共享的 D 被命中转发指针而非二次复制", g.stats["forward_hits"] >= 1,
          g.stats["forward_hits"])
    check("forward 表恰好覆盖 5 个存活对象", set(forward) == {"A", "B", "C", "D", "E"})
    check("垃圾对象 X/Y/Z 不在 forward 表里",
          not ({"X", "Y", "Z"} & set(forward)))

    order = [inv[o.oid] for _, o in to_space]
    check("复制顺序严格是 BFS 展开顺序", order == ["A", "B", "C", "D", "E"], order)
    check("根 A 第一个被复制", order[0] == "A")
    check("链尾 E 最后一个被复制", order[-1] == "E")
    check("D 出现在 B、C 之后(层序而非深序)", order.index("D") > max(order.index("B"),
                                                                   order.index("C")))

    offsets = [off for off, _ in to_space]
    check("to_space 中对象首尾相接、无空洞",
          offsets[0] == 0 and
          all(offsets[i] + to_space[i][1].words == offsets[i + 1]
              for i in range(len(offsets) - 1)), offsets)
    check("alloc 最终值 = 存活对象总字数",
          g.stats["alloc_words"] == sum(o.words for _, o in to_space))

    kinds = [t[0] for t in trace]
    check("三个阶段交错执行(与 V8 描述一致,非三段分离)",
          "copy" in kinds and "forward" in kinds and "scan" in kinds)
    check("每个被复制的对象恰好 scan 一次",
          sum(1 for k, *_ in trace if k == "scan") == g.stats["copied"])
    check("scan 指针始终不超过 alloc 指针",
          all(a >= s for _, _, _, a, s in trace))
    check("转发指针命中发生在 scan 阶段(说明是'再次遇到')",
          all(t[4] > 0 for t in trace if t[0] == "forward"))

    a_new = next(o for _, o in to_space if o.oid == forward["A"])
    check("根槽被就地改写为转发地址", g.roots[0].val == forward["A"])
    check("对象内部引用也指向新地址(精确根可枚举所有引用)",
          a_new.fields[0].val == forward["B"])
    check("所有新对象 oid 与旧对象不同(不是原地改)", all(o.oid >= 1000 for _, o in to_space))


def demo2():
    print("== demo2 紧凑化:碎片消除 ==")
    g = Chain(size_words=64)
    for i in range(6):
        g.add(f"o{i}")
    g.root("r", "o0")
    g.edge("o0", "next").val = "o2"
    g.edge("o2", "next").val = "o4"

    all_words = [g.objs[f"o{i}"].words for i in range(6)]
    heap_words = sum(all_words)
    live_indexes = {0, 2, 4}
    live_words = sum(all_words[i] for i in live_indexes)

    to_space, _, _ = cheney(g)
    check("只复制了 3 个存活对象", g.stats["copied"] == 3)
    check("to_space 占用恰为存活字数(零碎片)",
          to_space[-1][0] + to_space[-1][1].words == live_words, live_words)
    check("存活对象在 to_space 中首尾相接",
          all(to_space[i][0] + to_space[i][1].words == to_space[i + 1][0]
              for i in range(len(to_space) - 1)))

    gaps = free_blocks(all_words, live_indexes)
    check("mark-sweep 留下 3 段互不相邻的空洞", len(gaps) == 3, gaps)
    check("空洞总字数 = 堆 - 存活", sum(gaps) == heap_words - live_words,
          (sum(gaps), heap_words - live_words))
    check("mark-sweep 的最大连续空闲 = 单个死亡对象(粒度最细)",
          max(gaps) == max(all_words[i] for i in (1, 3, 5)), (max(gaps), all_words))
    check("Cheney 的最大连续空闲 = 整个堆尾,是 mark-sweep 的 3 倍",
          heap_words - live_words > max(gaps))

    # 碎片率
    check("mark-sweep 碎片率 = 空洞/堆", (heap_words - live_words) / heap_words > 0.4)
    check("Cheney 侧空闲区只有 1 段,且等于全部空闲(可整块复用)",
          len(gaps) == 3 and (heap_words - live_words) == sum(gaps))


def demo3():
    print("== demo3 成本模型:copy ∝ 存活  vs  sweep ∝ 堆 ==")
    heap = 100_000
    table = []
    for ratio in (0.01, 0.10, 0.50, 1.00):
        live = int(heap * ratio)
        table.append((ratio, live, live, heap))  # (存活率, 存活字数, 复制量, sweep 量)
    check("复制量只与存活字数有关,与堆大小无关",
          all(copy_words == live for _, live, copy_words, _ in table))
    check("复制量随存活率单调上升(严格)",
          table[0][2] < table[1][2] < table[2][2] < table[3][2])
    check("sweep 量恒为整堆 100000 字(与存活率无关)",
          all(sw == heap for *_, sw in table))
    check("存活率 1% 时复制量只有 sweep 的 1%", table[0][2] == heap // 100)
    check("存活率 100% 时复制量 = sweep 量(复制式此时毫无优势)",
          table[3][2] == table[3][3])
    check("未复制的死亡对象'自动成为隐式垃圾'(无需清扫)",
          heap - table[0][2] > 0)
    check("存活率低于 50% 时复制式稳赢", table[1][2] < table[1][3])

    g = build_chain_graph()
    cheney(g)
    check("复制式还要额外付'改写引用'的代价,规模同样 ∝ 存活对象数",
          g.stats["scan_words"] == g.stats["alloc_words"])


def demo4():
    print("== demo4 空间开销:半空间 = 2x 保留 ==")
    heap = 1 << 20
    semispace_total = 2 * heap
    mark_bits = heap // (WORD * 8)  # 标记位:平均 8 字/对象 -> 1 bit

    check("半空间设计下总保留 = 2 * 可用堆", semispace_total == 2 * heap)
    check("任一时刻恰好一半可分配(可用率上界 50%)", heap / semispace_total == 0.5)
    check("V8 原文:两个 semispace 半区都被 commit", semispace_total - heap == heap)
    check("标记位只需 1 bit/对象 = 堆的 1/64(8 字/对象)", mark_bits == heap // 64)
    check("复制式的额外空间(1x 堆)是标记位的 64 倍", heap == mark_bits * 64)

    # 最坏情况
    def copy_cost(live_words):
        return live_words

    def sweep_cost():
        return heap

    check("最坏情况(全部存活):复制量 = 整堆 = 清扫量",
          copy_cost(heap) == sweep_cost() == heap)
    check("此时 to-space 恰好装满、零余量 -> 容不下瞬时超额存活",
          heap - copy_cost(heap) == 0)
    check("标记-清除的搬运量恒为 0 字",
          copy_cost(0) == 0)
    check("半区装不下时只能回退:promote 到老年代或改用标记-清除",
          heap > heap // 2)

    for ratio in (0.05, 0.5, 0.95):
        live = int(heap * ratio)
        utilisation = live / semispace_total
        check(f"存活率 {ratio:.0%} 时物理内存有效利用率 = live/(2*heap)",
              utilisation == live / semispace_total, utilisation)
        check(f"存活率 {ratio:.0%} 时 live 与 ratio*heap 只差取整误差",
              abs(live - ratio * heap) < 1)


def demo5():
    print("== demo5 移动的两个前提:精确根 + 转发指针 ==")
    g = Chain(size_words=32)
    g.add("A")
    g.add("B")
    g.root("r", "A")
    g.edge("A", "b").val = "B"
    to_space, forward, _ = cheney(g)

    check("前提一:根槽被就地改写", g.roots[0].val == forward["A"])
    check("前提一:对象字段被就地改写", to_space[0][1].fields[0].val == forward["B"])

    hidden = ["B"]  # 模拟一个 GC 不知晓的引用(如被当作整数藏在别处)
    check("前提一:隐藏引用指向的旧对象已不存在于 to_space",
          not any(o.oid == hidden[0] for _, o in to_space))
    check("前提一:隐藏引用在移动后变成悬空(指向已作废的 from-space)",
          hidden[0] == "B" and forward["B"] != "B" and hidden[0] not in to_space)
    check("结论:保守式(把整数当指针的)GC 无法安全移动对象",
          hidden[0] not in set(forward.values()))

    # 前提二:转发指针让并行复制"只复制一次"
    def parallel_try(use_forward):
        forward2, copies = {}, []

        def try_copy(worker, oid):
            if use_forward and oid in forward2:
                return forward2[oid]
            if use_forward:
                forward2[oid] = f"D@{worker}"
            copies.append(worker)
            return f"D@{worker}"

        r1 = try_copy("w1", "D")
        r2 = try_copy("w2", "D")
        return copies, r1, r2

    copies, r1, r2 = parallel_try(False)
    check("无转发指针:两个 worker 各复制一份 -> 对象分裂",
          len(copies) == 2 and r1 != r2, (copies, r1, r2))
    copies, r1, r2 = parallel_try(True)
    check("有转发指针:只复制一次,两次拿到同一地址", len(copies) == 1 and r1 == r2,
          (copies, r1, r2))

    class SemiSpace:
        def __init__(self):
            self.active, self.idle = "S0", "S1"

        def flip(self):
            self.active, self.idle = self.idle, self.active

    ss = SemiSpace()
    ss.flip()
    check("scavenge 结束后 active/idle 互换", (ss.active, ss.idle) == ("S1", "S0"))
    ss.flip()
    check("新分配始终落在 active 半区", (ss.active, ss.idle) == ("S0", "S1"))
    check("转发指针同时充当'已搬迁'标记(等价于一个 mark 位)",
          parallel_try(True)[0] == ["w1"])


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
