"""离线检索评估指标 —— 按 scikit-learn 与 trec_eval 官方实现转写两套口径。

依据（本轮实读）：
  - scikit-learn `sklearn/metrics/_ranking.py`（main 分支）
      * `_dcg_sample_scores`：`discount = 1 / (np.log(np.arange(n) + 2) / np.log(log_base))`
      * 截断方式是 `discount[k:] = 0`（**不是切片**）
      * `ignore_ties=False`（默认）走 `_tie_averaged_dcg`；`True` 走 `argsort`
      * IDCG 用 `y_score=y_true` 且强制 `ignore_ties=True`
      * `all_irrelevant = normalizing_gain == 0`，此时结果置 0 而不是除零
  - trec_eval `m_ndcg.c`（main 分支）
      * gain 默认是**相关性等级本身**，可用 `-m ndcg.1=3.5,2=9.0` 覆盖；
        文档明确 level 3 保持默认 ⇒ 默认增益 = 3.0（**不是 2^rel − 1**）
      * discount 同样是 `log2(i + 2)`，注释写明「i+2 since doc i has rank i+1」
      * ideal DCG 只在 `ideal_gain > 0.0` 时累加
  - trec_eval `m_recall.c`
      * 默认截断点是 `5,10,15,20,30,100,200,500,1000`
      * 循环里 `if (i == cutoffs[cutoff_index])` **在计数该条之前** ⇒ recall@k 统计的是 rank 1..k
      * `res_rels.num_rel == 0` 时直接 `return 0`（不产出该 topic 的值）
      * 超出召回长度的截断点沿用最终计数

语言差异显式落地：
  - Python `math.log` 与 numpy 的逐元素 `np.log` 在 double 上一致；
  - 本文件不用 numpy，求和一律朴素累加（与 trec_eval 的 C 循环同理）。
"""

import itertools
import math


# ------------------------------------------------------------ sklearn 口径
def discount_vector(n, k=None, log_base=2.0):
    """`_dcg_sample_scores` 的折扣向量。

    ```python
    discount = 1 / (np.log(np.arange(y_true.shape[1]) + 2) / np.log(log_base))
    if k is not None:
        discount[k:] = 0
    ```
    """
    d = [1.0 / (math.log(i + 2) / math.log(log_base)) for i in range(n)]
    if k is not None:
        for i in range(k, n):
            d[i] = 0.0
    return d


def argsort_desc(scores):
    """复刻 `np.argsort(y_score)[:, ::-1]` 的一行版本。

    降序按下标稳定：分数相同时下标小的在前（Python 的 sorted 稳定，
    key 只取 -score，故相等时保持原序 —— 与 numpy argsort(kind='stable') 一致）。
    """
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))


def dcg(y_true, y_score, k=None, log_base=2.0, ignore_ties=False):
    """单个 query 的 DCG（`_dcg_sample_scores`）。"""
    n = len(y_true)
    discount = discount_vector(n, k, log_base)
    if ignore_ties:
        ranking = argsort_desc(y_score)
        ranked = [y_true[i] for i in ranking]
        return sum(d * g for d, g in zip(discount, ranked))
    return tie_averaged_dcg(y_true, y_score, discount)


def tie_averaged_dcg(y_true, y_score, discount):
    """`_tie_averaged_dcg`：并列组内部取平均增益，再乘该组的折扣之和。

    官方实现（转成可读写法）：

    ```python
    _, inv, counts = np.unique(-y_score, return_inverse=True, return_counts=True)
    ranked = np.zeros(len(counts))
    np.add.at(ranked, inv, y_true)      # 组内增益求和
    ranked /= counts                     # 组内平均增益
    groups = np.cumsum(counts) - 1       # 每组的最后一个秩（0-based）
    discount_sums[0] = discount_cumsum[groups[0]]
    discount_sums[1:] = np.diff(discount_cumsum[groups])
    return (ranked * discount_sums).sum()
    ```

    官方注释：这等价于对并列组的所有可能排列取平均。
    """
    order = sorted(range(len(y_score)), key=lambda i: (-y_score[i], i))
    groups, prev = [], None
    for i in order:
        if prev is not None and y_score[i] == prev:
            groups[-1].append(i)
        else:
            groups.append([i])
            prev = y_score[i]

    cumsum = []
    acc = 0.0
    for d in discount:
        acc += d
        cumsum.append(acc)

    total, start = 0.0, 0
    for grp in groups:
        end = start + len(grp)                      # 半开区间 [start, end)
        avg_gain = sum(y_true[i] for i in grp) / float(len(grp))
        before = cumsum[start - 1] if start > 0 else 0.0
        total += avg_gain * (cumsum[end - 1] - before)
        start = end
    return total


def ndcg(y_true, y_score, k=None, ignore_ties=False):
    """`ndcg_score` 的单 query 版本。

    IDCG 用 `y_score = y_true` 且 `ignore_ties=True`（官方：相同 y_true 之间换序
    不影响 y_true 的重排结果）。
    """
    gain = dcg(y_true, y_score, k, ignore_ties=ignore_ties)
    ideal = dcg(y_true, y_true, k, ignore_ties=True)
    if ideal == 0.0:
        return 0.0
    return gain / ideal


# ------------------------------------------------------------ trec_eval 口径
def trec_ndcg(results_rel_list, k=None, relevance_level=1):
    """`te_calc_ndcg`：gain = 相关性等级本身，discount = log2(i+2)。

    `results_rel_list` 是按 rank 排好序的相关性等级列表。
    ideal 部分按官方文档描述构造：把全部相关文档的等级降序排列。
    """
    results_dcg = 0.0
    for i, rel in enumerate(results_rel_list):
        if k is not None and i >= k:
            break
        gain = rel
        if gain != 0:
            results_dcg += gain / math.log(i + 2, 2)

    ideal_rels = sorted((r for r in results_rel_list if r >= relevance_level),
                        reverse=True)
    if k is not None:
        ideal_rels = ideal_rels[:k]
    ideal_dcg = 0.0
    for i, rel in enumerate(ideal_rels):
        if rel <= 0:
            break
        ideal_dcg += rel / math.log(i + 2, 2)
    if ideal_dcg == 0.0:
        return None          # trec_eval 不产出该 topic 的值
    return results_dcg / ideal_dcg


DEFAULT_RECALL_CUTOFFS = [5, 10, 15, 20, 30, 100, 200, 500, 1000]


def recall_at(results_rel_list, cutoffs=None, relevance_level=1, num_rel=None):
    """`te_calc_recall`：在每个截断点处的相关召回比例。

    返回 {cutoff: recall}；`num_rel` 为 0 时返回空 dict（trec_eval 直接 return 0）。
    """
    if cutoffs is None:
        cutoffs = DEFAULT_RECALL_CUTOFFS
    if num_rel is None:
        num_rel = sum(1 for r in results_rel_list if r >= relevance_level)
    if num_rel == 0:
        return {}
    out = {}
    rel_so_far = 0
    ci = 0
    cutoffs = sorted(cutoffs)
    for i, rel in enumerate(results_rel_list):
        while ci < len(cutoffs) and i == cutoffs[ci]:
            out[cutoffs[ci]] = rel_so_far / float(num_rel)
            ci += 1
        if rel >= relevance_level:
            rel_so_far += 1
    while ci < len(cutoffs):
        out[cutoffs[ci]] = rel_so_far / float(num_rel)
        ci += 1
    return out


def precision_at(results_rel_list, k, relevance_level=1):
    """Precision@k：trec_eval 的 P@k 同口径（截断前条里相关的比例）。"""
    top = results_rel_list[:k]
    return sum(1 for r in top if r >= relevance_level) / float(k)


def mrr(ranked_lists):
    """MRR：对每条结果列表取第一个相关结果的秩倒数，再平均。"""
    if not ranked_lists:
        return None
    tot = 0.0
    for rels in ranked_lists:
        rr = 0.0
        for i, rel in enumerate(rels):
            if rel >= 1:
                rr = 1.0 / (i + 1)
                break
        tot += rr
    return tot / len(ranked_lists)


def average_precision(results_rel_list, relevance_level=1):
    """AP：每个命中位置处的 precision 取平均，分母是总相关数。"""
    num_rel = sum(1 for r in results_rel_list if r >= relevance_level)
    if num_rel == 0:
        return 0.0
    hits, tot = 0, 0.0
    for i, rel in enumerate(results_rel_list):
        if rel >= relevance_level:
            hits += 1
            tot += hits / float(i + 1)
    return tot / num_rel


def mean_average_precision(ranked_lists, relevance_level=1):
    return sum(average_precision(r, relevance_level) for r in ranked_lists) / len(ranked_lists)
