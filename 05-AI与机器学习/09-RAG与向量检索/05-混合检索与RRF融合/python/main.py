"""混合检索演示：BM25 打分 + RRF 融合，以及与 CombMNZ / Condorcet 的对照。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hybrid import (  # noqa: E402
    LUCENE_B, LUCENE_K1, PAPER_TABLE1_MAP, PAPER_TABLE2_MAP, RRF_K,
    bm25_idf, bm25_score, bm25_score_monotone, bm25_tf, comb_mnz,
    condorcet_fuse, rrf_fuse,
)

DOCS = {
    "d1": {"dl": 120, "tf": 4},
    "d2": {"dl": 60, "tf": 3},
    "d3": {"dl": 300, "tf": 5},
    "d4": {"dl": 100, "tf": 1},
}
N = 1000
DF = 12
AVGDL = 100.0


def main():
    print("== Lucene BM25：idf 恒为正 ==")
    for df in (1, 5, 12, 100, 500, 900, 1000):
        print("  df=%4d  idf=%.6f" % (df, bm25_idf(df, N)))
    print("  经典 log(N/df) 在 df>N/2 时变负，Lucene 的 log(1+(N-df+0.5)/(df+0.5)) 不会")

    print("\n== tf 归一化：b 的作用 ==")
    for b in (0.0, 0.3, 0.75, 1.0):
        row = "  b=%.2f  " % b
        for name in sorted(DOCS):
            d = DOCS[name]
            row += "%s(tf=%d,dl=%3d)=%.4f  " % (
                name, d["tf"], d["dl"],
                bm25_tf(d["tf"], LUCENE_K1, b, d["dl"], AVGDL))
        print(row)

    print("\n== 两种写法给出同一个分 ==")
    for name in sorted(DOCS):
        d = DOCS[name]
        a = bm25_score(d["tf"], DF, N, AVGDL, d["dl"])
        c = bm25_score_monotone(d["tf"], DF, N, AVGDL, d["dl"])
        print("  %s  boost*idf*tf=%.10f   weight-weight/(1+f*n)=%.10f  差=%.3e"
              % (name, a, c, abs(a - c)))

    print("\n== 稠密召回 vs 稀疏召回 → RRF 融合 ==")
    dense = ["d3", "d1", "d5", "d2"]        # 语义相近但可能漏词
    sparse = ["d1", "d4", "d2", "d7"]       # 词面命中
    print("  稠密路: %s" % dense)
    print("  稀疏路: %s" % sparse)
    fused = rrf_fuse([dense, sparse])
    for doc, sc in fused:
        print("    %-4s RRF=%.6f  (1/%d+r 求和)" % (doc, sc, RRF_K))
    print("  单系统时不改变顺序: %s" % [d for d, _ in rrf_fuse([dense])])

    print("\n== k 的作用：相邻名次贡献差 ==")
    for k in (0, 10, 60, 100, 500):
        print("  k=%3d  1/(k+1)=%.6f  1/(k+2)=%.6f  差=%.6f"
              % (k, 1 / (k + 1.0), 1 / (k + 2.0),
                 1 / (k + 1.0) - 1 / (k + 2.0)))

    print("\n== CombMNZ 需要归一化，RRF 不需要 ==")
    s1 = {"d3": 0.9, "d1": 0.8, "d5": 0.7, "d2": 0.6}
    s2 = {"d1": 0.95, "d4": 0.85, "d2": 0.5, "d7": 0.4}
    cm = comb_mnz([dense, sparse], [s1, s2], cutoff=4)
    cm_big = comb_mnz([dense, sparse],
                      [{k: v * 100 for k, v in s1.items()},
                       {k: v * 100 for k, v in s2.items()}], cutoff=4)
    print("  CombMNZ 原尺度 : %s" % {k: round(v, 4) for k, v in sorted(cm.items())})
    print("  CombMNZ ×100   : %s  <- 随尺度放大"
          % {k: round(v, 2) for k, v in sorted(cm_big.items())})
    print("  RRF            : %s  <- 只吃秩，与分数无关"
          % {k: round(v, 6) for k, v in fused})

    print("\n== Condorcet 多数投票 ==")
    cf = condorcet_fuse([dense, sparse], ["d1", "d2", "d3", "d4", "d5", "d7"])
    print("  %s" % cf)

    print("\n== 论文 Table 1：k 的敏感性（MAP） ==")
    for k in sorted(PAPER_TABLE1_MAP):
        print("  k=%3d  MAP=%.4f" % (k, PAPER_TABLE1_MAP[k]))
    print("  论文结论：k=60 near-optimal，但选择并不关键（10~100 波动 < 2%%）")

    print("\n== 论文 Table 2：RRF vs 最佳单系统 ==")
    for track, v in PAPER_TABLE2_MAP.items():
        print("  %-12s RRF=%.4f  最佳单系统=%.4f  %s"
              % (track, v["RRF"], v["best individual"],
                 "RRF 胜" if v["RRF"] > v["best individual"] else "RRF 负（人工参与）"))


if __name__ == "__main__":
    main()
