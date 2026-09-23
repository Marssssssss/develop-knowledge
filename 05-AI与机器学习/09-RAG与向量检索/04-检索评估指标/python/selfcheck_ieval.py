"""检索评估指标自检：sklearn 与 trec_eval 两套口径的可断言差异。"""

import itertools
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ieval import (  # noqa: E402
    DEFAULT_RECALL_CUTOFFS, argsort_desc, average_precision, dcg,
    discount_vector, mean_average_precision, mrr, ndcg, precision_at,
    recall_at, tie_averaged_dcg, trec_ndcg,
)

PASS = 0
FAIL = []


def ck(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(msg)


def near(a, b, tol=1e-12):
    return abs(a - b) <= tol


# ============================================ 1. 折扣向量
d = discount_vector(5)
ck(near(d[0], 1.0), "rank 1 的折扣 = 1/log2(2) = 1")
ck(near(d[1], 1.0 / math.log(3, 2)), "rank 2 的折扣 = 1/log2(3)")
ck(near(d[2], 0.5), "rank 3 的折扣 = 1/log2(4) = 0.5")
ck(all(d[i] > d[i + 1] for i in range(4)), "折扣严格递减")

d4 = discount_vector(5, k=2)
ck(near(d4[2], 0.0) and near(d4[3], 0.0) and near(d4[4], 0.0),
   "k 截断是把 discount[k:] 置零")
ck(near(d4[0], 1.0) and near(d4[1], d[1]), "k 内的折扣不变")
d10 = discount_vector(5, k=1, log_base=10.0)
ck(near(d10[0], 1.0 / math.log(2, 10)), "log_base=10 时首项 = 1/log10(2)")

# ============================================ 2. DCG / NDCG
yt = [3.0, 2.0, 3.0, 0.0, 0.0]
# y_score 无并列时，DCG 就是按 y_score 降序后的 gain 加权和
score = [0.4, 0.3, 0.2, 0.1, 0.0]
ck(near(dcg(yt, score, ignore_ties=True),
        3.0 + 2.0 / math.log(3, 2) + 3.0 * 0.5, 1e-12), "DCG 按 rank 加权")
ck(near(ndcg(yt, yt), 1.0, 1e-9), "完美排序 NDCG = 1（浮点比较要留容差）")
ck(ndcg(yt, list(reversed(yt))) < 1.0, "逆序 NDCG < 1")
ck(near(ndcg(yt, yt, k=2), 1.0, 1e-9), "完美排序的 NDCG@2 也是 1")
ck(ndcg(yt, [0.0, 0.0, 0.0, 1.0, 1.0], k=2) == 0.0, "前 2 全不相关 -> NDCG@2 = 0")

# all_irrelevant：sklearn 置 0 而不是除零
ck(ndcg([0.0, 0.0], [1.0, 0.0]) == 0.0, "全不相关时 NDCG = 0（不是 NaN）")
ck(isinstance(ndcg([0.0], [1.0]), float), "全不相关返回 float 而非抛错")

# 负相关度可以让 NDCG 跑出 [0,1]
neg = [1.0, -1.0]
nv = ndcg(neg, neg)
ck(nv > 1.0, "含负相关时 NDCG 可以 > 1（实测 %.4f），官方文档明确警告" % nv)

# ============================================ 3. 并列处理
tie_y_true = [3.0, 1.0, 1.0]
tie_score = [1.0, 1.0, 1.0]          # 三条完全并列
ck(argsort_desc(tie_score) == [0, 1, 2], "分数全等时按原下标顺序")
a = dcg(tie_y_true, tie_score, ignore_ties=False)
b = dcg(tie_y_true, tie_score, ignore_ties=True)
ck(not near(a, b), "默认（考虑并列）与 ignore_ties=True 结果不同")

# 核心性质：并列平均 DCG == 对该组全部排列的 DCG 取平均
perms = []
for p in itertools.permutations(range(3)):
    yt_p = [tie_y_true[i] for i in p]
    perms.append(dcg(yt_p, [3.0 - i for i in range(3)], ignore_ties=True))
avg_perm = sum(perms) / len(perms)
ck(near(a, avg_perm, 1e-12),
   "并列平均 DCG == 全部 %d 种排列 DCG 的平均（%.12f vs %.12f）"
   % (len(perms), a, avg_perm))

# 无并列时两条路径必须一致
no_tie = [0.5, 0.3, 0.1]
ck(near(dcg(tie_y_true, no_tie, ignore_ties=False),
        dcg(tie_y_true, no_tie, ignore_ties=True), 1e-12),
   "无并列时 ignore_ties 不影响结果")

# 分组 NV：不同分数必须分到不同组
groups_y_true = [4.0, 2.0, 2.0]
gs = [0.9, 0.5, 0.5]
disc = discount_vector(3)
ck(near(tie_averaged_dcg(groups_y_true, gs, disc),
        4.0 * disc[0] + ((2.0 + 2.0) / 2) * (disc[1] + disc[2]), 1e-12),
   "组内平均增益 × 组内折扣之和")

# ============================================ 4. trec_eval NDCG
rel_list = [2, 0, 1, 0, 3]
tn = trec_ndcg(rel_list)
ideal_order = [3, 2, 1]
manual_ideal = sum(r / math.log(i + 2, 2) for i, r in enumerate(ideal_order))
manual_dcg = sum(r / math.log(i + 2, 2) for i, r in enumerate(rel_list) if r)
ck(near(tn, manual_dcg / manual_ideal, 1e-12),
   "trec_eval NDCG = （按位加权）/（相关等级降序的理想加权）")
ck(trec_ndcg([0, 0, 0]) is None, "无相关文档 -> trec_eval 不产出值")
ck(near(trec_ndcg(rel_list, k=2), 2 / 1.0 / (3 / 1.0 + 2 / math.log(3, 2)), 1e-12),
   "trec_eval 的 k 截断是硬截断（只算前 k 条）")

# 增益口径差异：sklearn 用 y_true 本身，有人习惯用 2^rel - 1
bm25_gain = [pow(2, r) - 1 for r in rel_list]
ck(dcg(bm25_gain, [float(x) for x in rel_list]) !=
   dcg([float(x) for x in rel_list], [float(x) for x in rel_list]),
   "2^rel-1 增益与原始等级增益给出不同的 DCG")
# trec_eval 文档明确 level 3 保持默认增益 3.0，即默认增益 == 等级
ck(near(dcg([3.0], [1.0], ignore_ties=True), 3.0),
   "trec_eval 默认增益 = 相关性等级本身（level 3 -> 3.0）")

# ============================================ 5. recall@k 的位置语义
rlist = [1, 0, 1, 0, 1, 0, 0, 0, 0, 1]     # rank 1,3,5,10 相关，共 4 条
r = recall_at(rlist, cutoffs=[1, 3, 5, 10, 20])
ck(near(r[1], 1 / 4.0), "recall@1 = 1/4（第 1 条相关）")
ck(near(r[3], 2 / 4.0), "recall@3 = 2/4（含 rank 3）")
ck(near(r[5], 3 / 4.0), "recall@5 = 3/4")
ck(near(r[10], 4 / 4.0), "recall@10 = 4/4")
ck(near(r[20], 4 / 4.0), "超出召回长度的截断点沿用最终计数")
ck(near(precision_at(rlist, 5), 3 / 5.0), "P@5 = 3/5")
ck(near(precision_at(rlist, 10), 4 / 10.0), "P@10 = 4/10")
ck(recall_at([0, 0, 0]) == {}, "无相关文档 -> recall 空字典（trec_eval return 0）")
ck(DEFAULT_RECALL_CUTOFFS == [5, 10, 15, 20, 30, 100, 200, 500, 1000],
   "trec_eval recall 的默认截断点")

# ============================================ 6. RR / MAP
lists = [[1, 0, 0, 1], [0, 0, 1], [0, 0, 0]]
ck(near(mrr(lists), (1.0 + 1 / 3.0 + 0.0) / 3), "MRR = (1 + 1/3 + 0)/3")
ck(mrr([]) is None, "空查询集 -> MRR 不给值")
ck(near(average_precision([1, 0, 0, 1]), (1 / 1.0 + 2 / 4.0) / 2, 1e-12),
   "AP = 各命中位置 precision 的平均 / 相关总数")
ck(average_precision([0, 0, 0]) == 0.0, "无相关 -> AP = 0")
ck(near(mean_average_precision(lists),
        (average_precision([1, 0, 0, 1]) + average_perform([0, 0, 1])
         + 0.0) / 3 if False else
        (average_precision([1, 0, 0, 1]) + average_precision([0, 0, 1])
         + 0.0) / 3, 1e-12), "MAP = AP 的算术平均")

print("PASS=%d  FAIL=%d" % (PASS, len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
