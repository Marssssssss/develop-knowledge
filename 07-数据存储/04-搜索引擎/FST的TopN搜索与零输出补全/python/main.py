"""FST 的 TopN 搜索与零输出补全 —— 演示入口。"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fstsearch import (FST, Node, TopNSearcher, Outputs, NO_OUTPUT, END_LABEL)


def build(entries, push=True):
    fst = FST()
    for inp, out in entries:
        n = fst.root
        for ch in inp:
            nxt = None
            for lb, o, t in n.arcs:
                if lb == ch:
                    nxt = t
            if nxt is None:
                nxt = Node()
                n.add_arc(ch, NO_OUTPUT, nxt)
            n = nxt
        n.add_arc(END_LABEL, out, Node())
    if push:
        fst.push_outputs_to_root()
    return fst


def all_paths(fst):
    out = []

    def walk(n, acc_out, acc_in):
        for lb, o, t in n.arcs:
            if lb == END_LABEL:
                out.append((tuple(acc_in), acc_out + o))
            else:
                walk(t, acc_out + o, acc_in + [lb])
    walk(fst.root, 0, [])
    return sorted(out)


def run(fst, top_n, depth, allow_empty=False):
    s = TopNSearcher(fst, top_n, depth)
    s.add_start_paths(fst.root, NO_OUTPUT, [], allow_empty_string=allow_empty)
    return s.search(), s


def main():
    print("=" * 72)
    print("Lucene FST 的 Util.TopNSearcher")
    print("=" * 72)

    fst = build([([1, 2], 5), ([1, 3], 7), ([9], 2)])
    print("\n[1] 输出前推：让每个非根节点都有一条零输出弧")
    raw = build([([1, 2], 5), ([1, 3], 7), ([9], 2)], push=False)
    print("    前推前零弧不变式 : %s" % raw.check_zero_arc_invariant())
    print("    前推后零弧不变式 : %s" % fst.check_zero_arc_invariant())
    print("    前推不改变任何路径的输出: %s"
          % (all_paths(raw) == all_paths(fst)))
    print("    全部路径: %s" % all_paths(fst))

    print("\n[2] top-N 搜索（按 output 升序取最小的 N 个）")
    for tn, depth in ((1, 50), (2, 50), (3, 50)):
        r, s = run(fst, tn, depth)
        print("    topN=%d depth=%d → %s  isComplete=%s"
              % (tn, depth, [(tuple(x.input), x.output) for x in r.results], r.isComplete))

    print("\n[3] 队列深度不够会丢候选 —— admissibility 不保证的成因")
    for depth in (1, 2, 3, 50):
        r, s = run(fst, 3, depth)
        print("    topN=3 depth=%-2d → 收到 %d 条，isComplete=%s"
              % (depth, len(r.results), r.isComplete))
    print("    isComplete = (rejectCount + topN <= maxQueueDepth)")

    print("\n[4] 同分按 input 字典序（TieBreakByInputComparator）")
    tie = build([([2], 0), ([1], 0), ([3], 0)])
    r, _ = run(tie, 3, 50)
    print("    结果顺序: %s" % [tuple(x.input) for x in r.results])

    print("\n[5] 空串：根上的 END_LABEL 弧只在 allowEmptyString=true 时入队")
    empty = build([([], 4), ([7], 9)])
    r0, _ = run(empty, 5, 50, allow_empty=False)
    r1, _ = run(empty, 5, 50, allow_empty=True)
    print("    allowEmptyString=false → %s" % [(tuple(x.input), x.output) for x in r0.results])
    print("    allowEmptyString=true  → %s" % [(tuple(x.input), x.output) for x in r1.results])

    print("\n[6] 零弧不变式被破坏 → assert foundZero")
    bad = FST()
    mid = Node()
    bad.root.add_arc(1, NO_OUTPUT, mid)
    mid.add_arc(END_LABEL, 5, Node())
    sb = TopNSearcher(bad, 1, 5)
    sb.add_start_paths(bad.root, NO_OUTPUT, [])
    try:
        sb.search()
        print("    未触发断言（不符合预期）")
    except AssertionError as e:
        print("    AssertionError: %s" % e)

    print("\n[7] 大样本对拍")
    rnd = random.Random(11)
    entries = [([rnd.randint(1, 5), rnd.randint(1, 5)], rnd.randint(0, 50))
               for _ in range(12)]
    big = build(entries)
    ap = all_paths(big)
    rb, _ = run(big, len(ap), 200)
    print("    路径总数 %d，全量搜索收到 %d，输出一致=%s"
          % (len(ap), len(rb.results),
             sorted(x.output for x in rb.results) == sorted(o for _, o in ap)))
    rt, _ = run(big, 3, 200)
    print("    topN=3 → %s ；暴力最小的 3 个 → %s"
          % ([x.output for x in rt.results], sorted(o for _, o in ap)[:3]))

    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
