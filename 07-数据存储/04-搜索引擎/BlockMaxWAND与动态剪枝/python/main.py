"""BlockMaxWAND 与动态剪枝 —— 演示入口。"""

import random

from search import blockmax_wand, naive_or
from scorerutil import f32, min_required_score, sum_upper_bound, ulp_f32
from wand import (INNER_WINDOW_SIZE, Clause, bm25_score, partition_scorers)


def show(title):
    print("\n== %s ==" % title)


def build(seed, maxd, spec):
    rnd = random.Random(seed)
    out = []
    for name, n, idf in spec:
        docs = sorted(rnd.sample(range(maxd), n))
        freqs = [1 + rnd.randrange(5) for _ in docs]
        norms = [round(0.5 + rnd.random() * 2, 2) for _ in docs]
        out.append(Clause(name, docs, freqs, norms, idf=idf))
    return out


def main():
    show("1. 分句划分：预算内塞进尽可能多的 non-essential")
    mws = [0.9, 1.4, 2.2, 3.0]
    costs = [900, 700, 400, 200]
    for mc in (0.0, 1.5, 3.0, 5.0, 9.0):
        p = partition_scorers(mws, costs, [0, 1, 2, 3], mc)
        if p is None:
            print("   minCompetitive=%.1f -> 整窗判空（全是 non-essential）" % mc)
        else:
            print("   minCompetitive=%.1f -> %d 个 essential（顺序 %s），"
                  "firstRequired=%d"
                  % (mc, len(mws) - p["first_essential"], p["order"],
                     p["first_required"]))

    show("2. 竞争分过滤：只减一个 float ulp")
    mc = f32(3.6706)
    print("   minCompetitive=%.7f  float ulp=%.3e  double ulp=%.3e"
          % (mc, ulp_f32(mc), 2.0 ** -52))
    for rem in (0.0, 1.0, 2.0, 4.0):
        need = min_required_score(rem, mc, 3)
        print("   剩余上界 %.1f -> 本子句至少要 %.7f%s"
              % (rem, need, "（不过滤）" if need <= 0 else ""))

    show("3. sumUpperBound：3 个以上子句才放大")
    for n in (1, 2, 3, 8):
        print("   %d 个子句: sumUpperBound(6.0) = %.17g" % (n, sum_upper_bound(6.0, n)))

    show("4. 端到端对拍：朴素 OR vs BlockMaxWAND")
    spec = [("a", 1200, 3.0), ("b", 800, 2.0), ("c", 500, 1.0),
            ("d", 300, 0.5)]
    for seed in (11, 12, 13):
        cl = build(seed, 4000, spec)
        tn, n_scored = naive_or(cl, 10, 4000)
        tb, st = blockmax_wand(cl, 10, 4000)
        same = [d for _, d in tn.top()] == [d for _, d in tb.top()]
        print("   seed=%d: 朴素评 %d 篇，BlockMaxWAND 只全量评 %d 篇（%.1f%%），"
              "候选 %d，外窗口 %d 个，结果一致=%s"
              % (seed, n_scored, st["fully_scored"],
                 100.0 * st["fully_scored"] / n_scored, st["candidates"],
                 st["outer_windows"], same))

    show("5. top-10 对照（seed=11）")
    cl = build(11, 4000, spec)
    tn, _ = naive_or(cl, 10, 4000)
    tb, _ = blockmax_wand(cl, 10, 4000)
    print("   朴素    :", [(d, round(s, 4)) for s, d in tn.top()])
    print("   MaxScore:", [(d, round(s, 4)) for s, d in tb.top()])

    show("6. 常量")
    print("   INNER_WINDOW_SIZE = %d（位打包窗口，非外窗口）" % INNER_WINDOW_SIZE)
    print("   BM25 取值 k1=1.2, b=0.75（本 demo 只取 tf 部分，idf 按子句给定）")
    print("   score(freq, norm) = idf * freq / (freq + k1*((1-b) + b*norm))")
    print("   例：idf=3.0, freq=4, norm=1.0 -> %.4f"
          % bm25_score(3.0, 4, 1.0))


if __name__ == "__main__":
    main()
