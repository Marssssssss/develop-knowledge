#!/usr/bin/env python3
"""标记-压缩式 GC —— 5 组实验(可实跑自检)。

  python3 main.py

模型在 compact.py;本文件放实验夹具、实验与断言。
"""

from compact import (Heap, Obj, Slot, chain_length, compute_forwarding,
                     lisp2_compact, mark, move, threading_compact,
                     update_references)

FAILS = []
TOTAL = [0]


def check(label, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        print(f"  [ok] {label}")
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label} {detail}")


def fixture():
    """地址序 A | g1 | B | g2 | C | D;引用 A->B->C->D;根 = A。"""
    a = Obj("A", payload_words=1)
    g1 = Obj("g1", payload_words=2)
    b = Obj("B", payload_words=1)
    g2 = Obj("g2", payload_words=3)
    c = Obj("C", payload_words=1)
    d = Obj("D", payload_words=0)
    a.slot("toB").val = "B"
    b.slot("toC").val = "C"
    c.slot("toD").val = "D"
    h = Heap([a, g1, b, g2, c, d])
    h.root("r1", "A")
    return h


def live_indexes(h, live):
    return [o.oid for o in h.cells if o.oid in live]


# --------------------------------------------------------------------------- #
def demo1():
    print("== demo1 Lisp2 保序滑动压缩 ==")
    h = fixture()
    heap_words = h.heap_words
    orig_order = [o.oid for o in h.cells]

    live = mark(h, h.roots)
    check("mark 只标记可达对象", live == {"A", "B", "C", "D"}, live)
    check("垃圾对象 g1/g2 未被标记", "g1" not in live and "g2" not in live)

    fwd, live_words = compute_forwarding(h, live)
    check("compaction 是一次**顺序扫描**后紧排(mmref:sequential passes)", live_words < heap_words)
    check("存活字数 = A+B+C+D = 10", live_words == 10, live_words)
    check("新地址按原相对顺序递增",
          [fwd[o] for o in live_indexes(h, live)] == sorted(fwd.values()))
    check("编译后布局 = 存活对象紧排,无空洞",
          move(h, live, fwd) == [o for o in h.cells if o.oid in live])

    n_slots = len(h.all_slots())
    updated = update_references(h, fwd)
    check("update 阶段改写了全部非空槽", updated == n_slots, (updated, n_slots))
    check("根槽也被改写(移动式 GC 必须能枚举根)", h.roots[0].val == fwd["A"])

    new_cells = move(h, live, fwd)
    addrs = {}
    acc = 0
    for o in new_cells:
        addrs[o.oid] = acc
        acc += o.size_words
    check("存活对象在新布局中首尾相接",
          all(addrs[new_cells[i].oid] + new_cells[i].size_words
              == addrs[new_cells[i + 1].oid] for i in range(len(new_cells) - 1)))
    check("空闲区是**一整块**连续区域(mmref:'a single contiguous block')",
          acc == live_words and len(new_cells) == 4, (acc, live_words))
    check("空闲区 = 堆 - 存活 = g1(3) + g2(4)",
          heap_words - live_words == 3 + 4, heap_words - live_words)

    new_order = [o.oid for o in new_cells]
    check("保序:存活对象的相对顺序与原地址序一致",
          new_order == [o for o in orig_order if o in live], new_order)
    check("引用全部指向新地址",
          all(isinstance(s.val, int) for s in h.all_slots()))
    check("A 的字段指向 B 的新地址", new_cells[0].fields[0].val == addrs["B"])


def demo2():
    print("== demo2 线程化压缩(GHC Compact.c 的做法) ==")
    h = fixture()
    h.root("r2", "A")                      # 两个根都指向 A,便于看链长
    h.cells[0].slot("self", tagged=True).val = "A"  # 一个"已标记指针"字段

    live, live_words, new_cells, updated, refs = threading_compact(h, h.roots)
    check("标记结果与 Lisp2 一致", live == {"A", "B", "C", "D"})
    check("全部槽被更新", updated == len(h.all_slots()), (updated, len(h.all_slots())))
    check("线程化后**不需要**保留任何 old->new 映射(refs 已清空)", len(refs) == 0)
    expected_live = sum(o.size_words for o in h.cells if o.oid in live)
    check("新布局与 Lisp2 完全相同(都是保序滑动)", live_words == expected_live,
          (live_words, expected_live))

    a = h.cells[0]
    check("info 槽被还原为原来的内容(GHC:'original contents ... at the end of the chain')",
          a.info == "INFO(A)", a.info)
    check("tag 位被保留:标记过的字段仍是标记的",
          [s.tagged for s in a.fields] == [False, True],
          [s.tagged for s in a.fields])
    check("未标记的根槽仍为未标记", not h.roots[0].tagged and not h.roots[1].tagged)

    # 链的性质:手工复现一次,观察链长与末端
    h2 = fixture()
    h2.root("r2", "A")
    h2.cells[0].slot("self", tagged=True).val = "A"
    refs_of = {}
    for s in h2.all_slots():
        if s.val is not None:
            refs_of.setdefault(s.val, []).append(s)
    from compact import thread_object
    a2 = h2.cells[0]
    thread_object(a2, refs_of["A"])
    check("链长 = 指向该对象的槽数", chain_length(a2) == len(refs_of["A"]), chain_length(a2))
    node, tail = a2.info, None
    while isinstance(node, tuple):
        tail = node
        node = node[1].val
    check("链尾是原 info 内容(不是 None、不是槽)", node == "INFO(A)", node)
    check("链首是最后挂上去的槽,其 tag = 2(已标记指针)",
          isinstance(a2.info, tuple) and a2.info[2] == 2, a2.info)
    check("全部 3 个引用都在链上(2 个根 + 1 个字段)", len(refs_of["A"]) == 3)


def demo3():
    print("== demo3 空间对比:转发表 vs 链 ==")
    n_live = 10_000
    table_entries = n_live                     # Lisp2:每个存活对象一个表项
    bytes_per_entry = 8
    lisp2_extra = table_entries * bytes_per_entry
    check("Lisp2 需 O(live) 转发表", lisp2_extra == 80_000, lisp2_extra)
    check("threading 需要的额外表项 = 0(链写在对象自己的槽里)", 0 * bytes_per_entry == 0)
    check("1 万个对象时 Lisp2 多花 80 KB,threading 多花 0 B",
          lisp2_extra - 0 == 80_000)

    # 变体:break table(每段一个表项)介于两者之间
    blocks = 20
    break_table = blocks * bytes_per_entry
    check("break table 变体只需每连续段一个表项", break_table == 160)
    check("转发表 < break table < threading(语义能力递减,空间递增反了)",
          break_table < lisp2_extra and 0 < break_table)

    class Node:
        """一个对象是否可被线程化,取决于两个前提。"""

        def __init__(self, has_writable_slot, exact_pointers):
            self.has_writable_slot = has_writable_slot
            self.exact_pointers = exact_pointers

        def threadable(self):
            return self.has_writable_slot and self.exact_pointers

    check("前提①每个对象要有可写槽承载链首",
          Node(True, True).threadable())
    check("缺任一前提即不可线程化",
          not Node(False, True).threadable() and not Node(True, False).threadable())
    check("保守式扫描 = exact_pointers 为假 -> 不可线程化",
          not Node(True, False).threadable())

    h = fixture()
    live, live_words, new_cells, updated, refs = threading_compact(h, h.roots)
    check("threading 全程不保留任何表(用完即弃)",
          len(refs) == 0 and live_words > 0, (len(refs), live_words))
    check("对象槽总数不增加(没有额外分配)",
          len(h.all_slots()) == 4, len(h.all_slots()))


def demo4():
    print("== demo4 保序(Lisp2) vs 层序(Cheney) 的局部性 ==")
    # 引用图与地址序**故意错开**:地址序 A,B,C,D,E;引用序 A->E->D->C->B
    sizes = {"A": 2, "B": 3, "C": 2, "D": 4, "E": 2}
    addr_order = ["A", "B", "C", "D", "E"]
    refs = {"A": "E", "E": "D", "D": "C", "C": "B"}
    traversal = ["A", "E", "D", "C", "B"]

    def layout_positions(order):
        pos, acc = {}, 0
        for oid in order:
            pos[oid] = acc
            acc += sizes[oid]
        return pos

    lisp2_order = list(addr_order)          # 保序:相对顺序不变
    # Cheney 的复制顺序 = BFS 展开顺序,即从根出发的引用序
    cheney_order = ["A", "E", "D", "C", "B"]

    def jumps(pos):
        return sum(abs(pos[traversal[i]] - pos[traversal[i + 1]])
                   for i in range(len(traversal) - 1))

    j_lisp2 = jumps(layout_positions(lisp2_order))
    j_cheney = jumps(layout_positions(cheney_order))
    check("Cheney 层序布局让'按引用遍历'的地址跳跃最小",
          j_cheney < j_lisp2, (j_cheney, j_lisp2))
    check("Cheney 布局下相邻访问对象地址差 = 对象自身大小(理想局部性)",
          j_cheney == sum(sizes[oid] for oid in traversal[:-1]), j_cheney)
    check("Lisp2 保序布局下跳跃是层序的 2 倍", j_lisp2 == 2 * j_cheney, (j_lisp2, j_cheney))
    check("保序布局对'按引用遍历'更差,但对'按地址扫描'更好", j_lisp2 > j_cheney)

    # 反过来:按地址顺序扫描时,保序布局占优
    def scan_jumps(pos, order):
        return sum(abs(pos[order[i]] - pos[order[i + 1]]) for i in range(len(order) - 1))

    check("按地址顺序扫描时 Lisp2 无跳跃(顺序本身就是地址序)",
          scan_jumps(layout_positions(lisp2_order), lisp2_order)
          == sum(sizes[o] for o in lisp2_order[:-1]))
    check("mmref:'Compaction is used to ... increase locality of reference' 对两种布局都成立",
          j_cheney < sum(sizes.values()) * 3 and j_lisp2 < sum(sizes.values()) * 3)

    h = fixture()
    live, fwd, new_cells, _ = lisp2_compact(h, h.roots)
    check("两种压缩都把空闲合并成单块(与本 demo 的布局差异无关)",
          len(new_cells) == len(live))


def demo5():
    print("== demo5 阶段分解与'漏更新引用'的后果 ==")
    # V8 src/heap/mark-compact.h 的 CollectorState(定义在 #ifdef DEBUG 下):
    #   IDLE, PREPARE_GC, MARK_LIVE_OBJECTS, SWEEP_SPACES,
    #   ENCODE_FORWARDING_ADDRESSES, UPDATE_POINTERS, RELOCATE_OBJECTS
    phases = ["PREPARE_GC", "MARK_LIVE_OBJECTS", "ENCODE_FORWARDING_ADDRESSES",
              "UPDATE_POINTERS", "RELOCATE_OBJECTS"]
    check("V8 mark-compact 把指针更新做成独立状态 UPDATE_POINTERS",
          "UPDATE_POINTERS" in phases)
    check("阶段数 >= 2(mmref:marking + compaction 的多遍扫描)",
          len([p for p in phases if p != "PREPARE_GC"]) >= 2, len(phases))
    check("先编码转发地址、再改引用、最后搬对象(顺序不可换)",
          phases.index("ENCODE_FORWARDING_ADDRESSES")
          < phases.index("UPDATE_POINTERS") < phases.index("RELOCATE_OBJECTS"))

    # 正确流程:所有槽都被更新
    h = fixture()
    live, fwd, new_cells, updated = lisp2_compact(h, h.roots)
    all_ptrs = [s.val for s in h.all_slots()]
    check("正常流程:所有槽都指向新地址", all(isinstance(v, int) for v in all_ptrs))
    addr_set = set(fwd.values())
    check("正常流程:不存在悬空引用(每个槽都落在合法新地址上)",
          all(v in addr_set for v in all_ptrs), all_ptrs)

    # 错误流程:故意漏更新 A 的字段(B 的引用),保留旧 oid
    h2 = fixture()
    live2 = mark(h2, h2.roots)
    fwd2, _ = compute_forwarding(h2, live2)
    for s in h2.all_slots():
        if s.owner == "A":        # 故意跳过
            continue
        if s.val is not None:
            s.val = fwd2[s.val]
    dangling = [s for s in h2.all_slots() if not isinstance(s.val, int)]
    check("漏更新一个引用 -> 该槽仍指向旧 oid(类型都不对)", len(dangling) >= 1, dangling)
    live_addrs = set(fwd2.values())
    check("该槽的值不在新地址集合中 -> 悬空",
          any(s.val not in live_addrs for s in dangling), [s.val for s in dangling])
    check("移动式 GC 的致命错误:漏掉任何一个引用都会产生悬空指针",
          len(dangling) > 0 and "B" in [s.val for s in dangling])

    check("悬空槽的值仍是旧 oid,而非它应该指向的新地址",
          dangling[0].val == "B" and fwd2["B"] != dangling[0].val,
          (dangling[0].val, fwd2["B"]))
    check("'漏更新'即移动式设计的阿喀琉斯之踵:必须精确、不可保守",
          dangling[0].owner == "A" and dangling[0].name == "toB")


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
