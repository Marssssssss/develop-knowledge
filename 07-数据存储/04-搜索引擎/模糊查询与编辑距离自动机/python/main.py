"""模糊查询与编辑距离自动机 —— 演示入口。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fuzzy import (FuzzyQuery, float_to_edits, LevenshteinAutomata,
                   Lev1ParametricDescription, Lev1TParametricDescription,
                   Lev2ParametricDescription,
                   MAXIMUM_SUPPORTED_DISTANCE, CHARACTER_MAX_CODE_POINT)
from editdist import levenshtein, damerau_osa, distance


def main():
    print("=" * 72)
    print("Lucene FuzzyQuery 与 Levenshtein 自动机")
    print("=" * 72)

    print("\n[1] FuzzyQuery 默认值与上限")
    q = FuzzyQuery("lucene")
    print("    defaultMaxEdits=%d  defaultPrefixLength=%d  defaultMaxExpansions=%d"
          % (q.max_edits, q.prefix_length, q.max_expansions))
    print("    defaultTranspositions=%s  MAXIMUM_SUPPORTED_DISTANCE=%d"
          % (q.transpositions, MAXIMUM_SUPPORTED_DISTANCE))
    print("    maxEdits=0 → %s（只能精确匹配）；maxEdits=1 → %s"
          % (FuzzyQuery("x", max_edits=0).terms_enum_kind,
             FuzzyQuery("x", max_edits=1).terms_enum_kind))
    for kw, msg in ((dict(max_edits=3), "maxEdits 越界"),
                    (dict(prefix_length=-1), "prefixLength 为负"),
                    (dict(max_expansions=0), "maxExpansions 非正")):
        try:
            FuzzyQuery("x", **kw)
        except ValueError as e:
            print("    %-16s → %s" % (msg, e))

    print("\n[2] floatToEdits：相似度 → 编辑距离")
    for sim, tl in ((0.0, 10), (0.5, 10), (0.7, 10), (0.8, 10), (0.9, 5),
                    (1.0, 10), (2.0, 10), (5.0, 10)):
        print("    similarity=%-4s termLen=%-3d → maxEdits=%d"
              % (sim, tl, float_to_edits(sim, tl)))
    print("    注意 0.8/10：1-0.8=0.19999999999999996，×10=1.9999… → 截断成 1")

    print("\n[3] transpositions 开关：OSA vs 经典 Levenshtein")
    for a, b in (("ab", "ba"), ("ca", "abc"), ("kitten", "sitting")):
        print("    %-8r → %-10r  经典=%d  OSA=%d"
              % (a, b, levenshtein(a, b), damerau_osa(a, b)))
    print("    'ca'→'abc'：真 Damerau 给 2，**OSA 给 3**（一个字符只能参与一次交换）")

    word = [ord(c) for c in "abca"]
    la = LevenshteinAutomata(word)
    print("\n[4] LevenshteinAutomata('abca')")
    print("    alphabet=%s  numRanges=%d" % ([chr(c) for c in la.alphabet], la.num_ranges))
    for i in range(la.num_ranges):
        print("      补集区间 [%d, %d]" % (la.range_lower[i], la.range_upper[i]))
    print("    alphaMax=0x%X" % CHARACTER_MAX_CODE_POINT)

    print("\n[5] 参数化描述：状态数 = |minErrors| × (w+1)")
    for name, d in (("Lev1 ", Lev1ParametricDescription(4)),
                    ("Lev1T", Lev1TParametricDescription(4)),
                    ("Lev2 ", Lev2ParametricDescription(4))):
        print("    %s w=4：|minErrors|=%-3d → size=%-4d 接受态 %d 个"
              % (name, len(d.min_errors), d.size(), len(d.accept_states())))
    d1 = Lev1ParametricDescription(4)
    print("    isAccept 判据：w - offset + minErrors[state] <= n")
    print("    前 10 个状态的接受情况：%s"
          % [1 if d1.is_accept(s) else 0 for s in range(10)])

    print("\n[6] toAutomaton 的计划量")
    print("    n=0 → %r" % la.to_automaton_plan(0))
    for n in (1, 2):
        p = la.to_automaton_plan(n)
        print("    n=%d：range=%d states=%d transitions=%d"
              % (n, p["range"], p["numStates"], p["numTransitions"]))
    print("    n=3 → %r（超过 descriptions 长度，源码 return null）"
          % la.to_automaton_plan(3)["kind"])

    print("\n[7] 特征向量（先左移再判等置位，最早进来的位在最高位）")
    for x in "abcz":
        v, end = la.vector_window(ord(x), 0, 1)
        print("    X(%r, 0, %d) = %s" % (x, end, format(v, "03b")))

    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
