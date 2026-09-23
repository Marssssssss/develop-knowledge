"""检索评估指标演示：两套口径并列跑同一批结果。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ieval import (  # noqa: E402
    average_precision, dcg, discount_vector, mean_average_precision, mrr,
    ndcg, precision_at, recall_at, trec_ndcg,
)

# 一个 6 条的检索结果：相关性等级分别是 2/0/1/0/3/0
RELS = [2, 0, 1, 0, 3, 0]
# 系统给出的排序分数（rank 越靠前分数越高）
PRED = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]


def main():
    print("== 折扣向量 1/log2(i+2) ==")
    d = discount_vector(6)
    print("  rank:   " + "  ".join("%6d" % (i + 1) for i in range(6)))
    print("  disc:   " + "  ".join("%6.4f" % v for v in d))
    print("  @2 截断: " + "  ".join("%6.4f" % v for v in discount_vector(6, k=2)))

    print("\n== 同一份结果的两套 NDCG ==")
    yt = [float(r) for r in RELS]
    print("  sklearn 口径 NDCG    = %.6f" % ndcg(yt, PRED))
    print("  sklearn 口径 NDCG@3  = %.6f" % ndcg(yt, PRED, k=3))
    print("  trec_eval 口径 NDCG  = %.6f" % trec_ndcg(RELS))
    print("  trec_eval 口径 NDCG@3= %.6f" % trec_ndcg(RELS, k=3))
    print("  sklearn ignore_ties  = %.6f" % ndcg(yt, PRED, ignore_ties=True))

    print("\n== 换一个更好的排序 ===")
    better = [0.6, 0.1, 0.5, 0.0, 0.9, 0.0]   # 把 3 级文档(下标 4)提到首位
    print("  原   NDCG = %.6f" % ndcg(yt, PRED))
    print("  新   NDCG = %.6f" % ndcg(yt, better))
    print("  完美 NDCG = %.6f" % ndcg(yt, yt))

    print("\n== recall / precision 截断点语义 ==")
    rlist = [1, 0, 1, 0, 1, 0, 0, 0, 0, 1]
    r = recall_at(rlist, cutoffs=[1, 3, 5, 10, 20])
    for c in (1, 3, 5, 10, 20):
        print("  @%-3d recall=%.2f precision=%.2f"
              % (c, r[c], precision_at(rlist, c)))

    print("\n== MRR / MAP ==")
    lists = [[1, 0, 0, 1], [0, 0, 1], [0, 0, 0]]
    print("  MRR = %.6f" % mrr(lists))
    print("  AP  = %s" % ["%.4f" % average_precision(x) for x in lists])
    print("  MAP = %.6f" % mean_average_precision(lists))

    print("\n== 全不相关时的行为差异 ==")
    print("  sklearn ndcg([0,0],[1,0]) = %s" % ndcg([0.0, 0.0], [1.0, 0.0]))
    print("  trec_eval ndcg([0,0])     = %s  <- 不产出值" % trec_ndcg([0, 0]))
    print("  recall_at([0,0,0])        = %s  <- 空字典" % recall_at([0, 0, 0]))

    print("\n== 并列：考慮並列 vs ignore_ties ==")
    tied_true, tied_score = [3.0, 1.0, 1.0], [1.0, 1.0, 1.0]
    print("  三条完全并列: 考虑并列 DCG=%.6f  ignore_ties DCG=%.6f"
          % (dcg(tied_true, tied_score, ignore_ties=False),
             dcg(tied_true, tied_score, ignore_ties=True)))
    no_tie = [0.5, 0.3, 0.1]
    print("  无并列:       考虑并列 DCG=%.6f  ignore_ties DCG=%.6f"
          % (dcg(tied_true, no_tie, ignore_ties=False),
             dcg(tied_true, no_tie, ignore_ties=True)))


if __name__ == "__main__":
    main()
