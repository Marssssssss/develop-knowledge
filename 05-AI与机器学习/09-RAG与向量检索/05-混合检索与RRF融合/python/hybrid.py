"""混合检索与结果融合 —— BM25（Lucene 官方实现）与 RRF（Cormack 2009 原论文）。

依据（本轮实读）：
  - Apache Lucene `lucene/core/src/java/org/apache/lucene/search/similarities/BM25Similarity.java`
      * `idf(df, docCount) = log(1 + (docCount - df + 0.5D) / (df + 0.5D))`
      * `avgdl = sumTotalTermFreq / docCount`
      * tf 归一化项 `k1 * ((1 - b) + b * dl / avgdl)`，**分子没有 (k1+1)**
      * `weight = boost * idf`，`score = weight - weight / (1 + freq * normInverse)`
      * doc 长度被量化成 1 字节（`SmallFloat.byte4ToInt`，256 档）
      * 默认 `k1 = 1.2`、`b = 0.75`、`discountOverlaps = true`、`k3 = -1`（禁用）
      * k3 启用时权重是 `((k3 + 1) * qtf) / (k3 + qtf)`；禁用时退化为线性
  - Cormack, Clarke & Büttcher, *Reciprocal Rank Fusion outperforms Condorcet and
    individual Rank Learning Methods*, SIGIR 2009（原文 PDF 抽取）
      * `RRFscore(d) = Σ_{r∈R} 1 / (k + r(d))`，**k = 60**，「fixed during a pilot
        investigation and not altered during subsequent validation」
      * 选这个形式的理由：靠后排名的文档重要性「不会消失」（指数函数就会）
      * k 用来「削弱离群系统给的高排名的影响」
      * CombMNZ：`|{r ∈ R | r(d) ≤ c}| × Σ_{r: r(d) ≤ c} s_r(d)`
      * Condorcet Fuse：按两两多数投票排序
      * 结论：比 Condorcet 与 CombMNZ 平均高 4%~5%；打平/胜出最佳单系统的次数
        7/7（p ≈ 0.008）与 6/7（p ≈ .04）
"""

import math

# ---------------------------------------------------------------- BM25
LUCENE_K1 = 1.2
LUCENE_B = 0.75
LUCENE_DISCOUNT_OVERLAPS = True
LUCENE_K3 = -1.0


def bm25_idf(doc_freq, doc_count):
    """`BM25Similarity::idf`：`log(1 + (N - n + 0.5) / (n + 0.5))`。

    注意分子是 `N - n + 0.5` 恒为正 ⇒ **Lucene 的 idf 永远不会是负数**，
    这与教科书里 `log(N/n)` 在 n > N/2 时变号的行为不同。
    """
    return math.log(1.0 + (doc_count - doc_freq + 0.5) / (doc_freq + 0.5))


def bm25_avgdl(sum_total_term_freq, doc_count):
    """`avgFieldLength`：`sumTotalTermFreq / docCount`（不是 numDocs）。"""
    if doc_count == 0:
        raise ValueError("docCount must be > 0")
    return sum_total_term_freq / float(doc_count)


def bm25_norm_inverse(k1, b, dl, avgdl):
    """`1f / (k1 * ((1 - b) + b * dl / avgdl))`（BM25Similarity 的 cache 元素）。"""
    return 1.0 / (k1 * ((1.0 - b) + b * dl / avgdl))


def bm25_tf(freq, k1, b, dl, avgdl):
    """Lucene 的 tf：`freq / (freq + k1 * ((1 - b) + b * dl / avgdl))`。

    **没有经典 BM25 分子上的 `(k1 + 1)`** —— 因为 (k1+1) 对所有文档是同一个
    常数因子，不影响排序，Lucene 直接省掉。
    """
    if freq == 0:
        return 0.0
    return freq / (freq + k1 * ((1.0 - b) + b * dl / avgdl))


def bm25_score(freq, doc_freq, doc_count, avgdl, dl,
               k1=LUCENE_K1, b=LUCENE_B, boost=1.0):
    """经典写法：`boost * idf * tf`。"""
    return boost * bm25_idf(doc_freq, doc_count) * bm25_tf(freq, k1, b, dl, avgdl)


def bm25_score_monotone(freq, doc_freq, doc_count, avgdl, dl,
                        k1=LUCENE_K1, b=LUCENE_B, boost=1.0):
    """Lucene `doScore` 的单调改写：`weight - weight / (1 + freq * normInverse)`。

    官方注释：这样改写是为了**在不提升到 double 的前提下保证对 freq 与 norm 都单调**。
    """
    weight = boost * bm25_idf(doc_freq, doc_count)
    norm_inverse = bm25_norm_inverse(k1, b, dl, avgdl)
    return weight - weight / (1.0 + freq * norm_inverse)


def bm25_query_term_weight(qtf, k3=LUCENE_K3):
    """查询词频权重：k3 < 0 时线性（qtf），否则 `((k3+1)*qtf)/(k3+qtf)`。"""
    if k3 < 0:
        return float(qtf)
    return ((k3 + 1.0) * qtf) / (k3 + qtf)


# Lucene 把 doc 长度压缩成 1 字节（SmallFloat.byte4ToInt），只有 256 档
LENGTH_TABLE_SIZE = 256
# 官方 explain 里：norm > 39 时长度是「近似」值
LENGTH_EXACT_UP_TO = 39


def small_float_byte4_to_int(b):
    """`SmallFloat.byte4ToInt` 的等价实现（1 字节 4 位尾数的小浮点）。

    本 demo 只用它说明「doc 长度被量化」这件事，不追求与 Lucene 逐位一致。
    """
    mantissa = b & 0x07
    exponent = (b >> 3) & 0x1F
    if exponent == 0:
        return int(mantissa)
    return int((8 + mantissa) << (exponent - 1))


# ---------------------------------------------------------------- RRF
RRF_K = 60


def rrf_score(ranks, k=RRF_K):
    """`RRFscore(d) = Σ_{r∈R} 1 / (k + r(d))`，r 从 1 开始计数。

    `ranks` 是各路召回里该文档的秩（未出现的系统不贡献）。
    """
    total = 0.0
    for r in ranks:
        if r is None or r <= 0:
            continue
        total += 1.0 / (k + r)
    return total


def rrf_fuse(ranked_lists, k=RRF_K, top_n=None):
    """把多份有序结果融合成一份；返回 [(doc_id, score), ...]（降序）。"""
    scores = {}
    for lst in ranked_lists:
        for pos, doc in enumerate(lst):
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (k + pos + 1)
    out = sorted(scores.items(), key=lambda t: (-t[1], t[0]))
    if top_n is not None:
        out = out[:top_n]
    return out


def comb_mnz(ranked_lists, scores_by_list, cutoff):
    """`CMNZscore(d) = |{r ∈ R | r(d) ≤ c}| × Σ_{r: r(d) ≤ c} s_r(d)`。

    需要**归一化后的分数**，这一点与 RRF 只吃秩不同。
    """
    out = {}
    for lst, smap in zip(ranked_lists, scores_by_list):
        for pos, doc in enumerate(lst):
            if pos + 1 > cutoff:
                continue
            out.setdefault(doc, [0, 0.0])
            out[doc][0] += 1
            out[doc][1] += smap.get(doc, 0.0)
    return {d: cnt * s for d, (cnt, s) in out.items()}


def condorcet_fuse(ranked_lists, candidates):
    """Condorcet Fuse：按两两多数投票排序（票数 = 赢过的对手数）。"""
    pos = [{d: i for i, d in enumerate(lst)} for lst in ranked_lists]
    wins = {d: 0 for d in candidates}
    for a in candidates:
        for b in candidates:
            if a == b:
                continue
            vote = 0
            for p in pos:
                if a in p and b in p:
                    vote += 1 if p[a] < p[b] else -1
            if vote > 0:
                wins[a] += 1
    return sorted(wins.items(), key=lambda t: (-t[1], t[0]))


# 论文 Table 1 的 MAP 读数（30 个 Wumpus 配置融合，TREC topics 351-400）
PAPER_TABLE1_MAP = {
    0: 0.2072, 10: 0.2123, 20: 0.2134, 30: 0.2139, 40: 0.2138,
    50: 0.2144, 60: 0.2145, 70: 0.2146, 80: 0.2147, 90: 0.2145,
    100: 0.2145, 500: 0.2142,
}
# 论文 Table 2 的 MAP（融合 TREC 各 track 的 submitted runs）
PAPER_TABLE2_MAP = {
    "TREC Robust": {"RRF": 0.3686, "best individual": 0.3586,
                    "Condorcet": 0.3652, "CombMNZ": 0.3575},
    "TREC3": {"RRF": 0.4350, "best individual": 0.4226,
              "Condorcet": 0.4256, "CombMNZ": 0.4381},
    "TREC5": {"RRF": 0.3394, "best individual": 0.3165,
              "Condorcet": 0.3213, "CombMNZ": 0.3237},
    # TREC9 这一行论文原文的数字较多且 PDF 文本层错格，本 demo 只登记两个能
    # 与正文结论互相印证的数：RRF 0.2830、最佳单系统 0.3519。正文明确写着
    # RRF 在每个实验里都胜过最佳单系统，**唯独 TREC9 例外**（该最佳来自人工参与）。
    "TREC9": {"RRF": 0.2830, "best individual": 0.3519},
}
