#!/usr/bin/env python3
"""BM25 评分算法演示 —— 用纯标准库实现 Lucene/Elasticsearch 的 BM25 评分公式。

权威公式（Elasticsearch 博客 *Practical BM25 — Part 2*）：

    score(D, Q) = Σ_{q ∈ Q}  IDF(q) · (tf(q, D) · (k1 + 1))
                                          ───────────────────────
                              (tf(q, D) + k1 · (1 - b + b · |D|/avgdl))

其中 IDF(q) = ln(1 + (N − n(q) + 0.5) / (n(q) + 0.5))

变量说明：
    N          — 文档总数
    n(q)       — 包含词 q 的文档数（document frequency）
    tf(q, D)   — 词 q 在文档 D 中出现的次数（term frequency）
    |D|        — 文档 D 的词数（filed length, in tokens, 不是字符数）
    avgdl      — 所有文档长度的平均
    k1         — 词频饱和度参数，默认 1.2；越大 → 重复出现贡献越线性
    b          — 文档长度归一化强度，默认 0.75；越大 → 长文档被惩罚越重

本 demo 用一个 4 篇 doc 的小语料演示：
  1. 对每个词计算 df（包含它的文档数）= n(q)
  2. 按 Lucene BM25 公式计算 score(D, "shane")
  3. 对比 BM25 vs TF-IDF 的词频饱和曲线 + 文档长度归一化的可视化

对比可在控制台打印表格中看出：
  * 重复出现的词，在 BM25 下"边际收益递减"
  * 长文档会因为 b 因子而被往下压
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 默认参数（与 Elasticsearch 完全一致）
K1 = 1.2
B = 0.75

# 4 篇 demo 文档，全部来自 Elasticsearch *Practical BM25 — Part 2* 一文
# 为方便回溯，文段与原文逐字一致
DOCS = [
    {"id": 0, "title": "Shane Connelly",                       "len": 2},
    {"id": 1, "title": "Shane is the best",                    "len": 4},
    {"id": 2, "title": "Connelly is a name",                   "len": 4},
    {"id": 3, "title": "Shane and Connelly are names together","len": 5},
]


def tokenize(doc: dict) -> list[str]:
    """最朴素分词：标题转小写、按空格切分（demo 用足够）。"""
    return doc["title"].lower().split()


@dataclass
class Index:
    """最简化的"倒排表 + 长度表"（为复用 elasticsearch demo 的结构化思路）。"""

    docs: list[dict]
    postings: dict[str, list[int]]   # term -> 包含此词的全部 doc id（升序）
    doc_len: list[int]               # 每个 doc 的词数（terms）
    avgdl: float                     # 平均长度

    @classmethod
    def build(cls, docs: list[dict]) -> "Index":
        postings: dict[str, list[int]] = {}
        doc_len: list[int] = []
        for d in docs:
            tokens = tokenize(d)
            doc_len.append(len(tokens))
            for t in set(tokens):  # set => 同一 doc 内重复只算一次（df 而非 tf）
                postings.setdefault(t, []).append(d["id"])
        avgdl = sum(doc_len) / len(doc_len)
        return cls(docs, postings, doc_len, avgdl)


def idf_lucene(n_q: int, N: int) -> float:
    """Lucene/Elasticsearch 的 BM25 IDF，与 TF·IDF 的 IDF 不同。

    来源：Elastic blog *Found: Similarity in Elasticsearch* 与 *Pluggable Similarity Algorithms*。
    公式：ln(1 + (N - n + 0.5) / (n + 0.5))
    """
    return math.log(1 + (N - n_q + 0.5) / (n_q + 0.5))


def bm25_score(index: Index, doc_id: int, query_terms: list[str],
               k1: float = K1, b: float = B) -> float:
    """对单一文档计算 BM25 score（同公式 sum_query_terms）。

    关键三件事：
      * tf 饱和：(tf · (k1+1)) / (tf + k1 · norm)   —— 反复出现不会无限增长
      * norm(D)：   (1 - b + b · |D|/avgdl)         —— 长文档的 norm 大，分母大，分数低
      * IDF 校正： 罕见词 > 常见词（"shane" 出现在全部文档里 → IDF 低）
    """
    score = 0.0
    N = len(index.docs)
    dl = index.doc_len[doc_id]
    norm = 1 - b + b * dl / index.avgdl
    for q in query_terms:
        # BM25 用 document frequency（一个 doc 里只算一次）
        n_q = len(index.postings.get(q, []))
        if n_q == 0:
            continue
        # 用 posting list 算出这个 doc 的 tf（实际计算时直接扫一次 tokenize 即可）
        tf = sum(1 for tok in tokenize(index.docs[doc_id]) if tok == q)
        score += idf_lucene(n_q, N) * (tf * (k1 + 1)) / (tf + k1 * norm)
    return score


def tf_saturation_curve(tf_max: int = 30, k1: float = K1) -> list[tuple[int, float]]:
    """词频饱和曲线：在平均长度的文档（|D|=avgdl ⇒ norm=1）下，
    BM25 相对 tf 的贡献随 tf 增大如何变化。

    BM25:  tf → tf·(k1+1)/(tf+k1)   —— 当 tf=k1 时恰好 = (k1+1)/2 ≈ 0.91（半饱和点）
    TFIDF: tf → ln(tf+1)            —— 无上限
    """
    return [(tf, tf * (k1 + 1) / (tf + k1), math.log(tf + 1))
            for tf in range(1, tf_max + 1)]


def main() -> None:
    print("=" * 68)
    print("Demo 1 · BM25 评分算法（基于 Elastic blog Practical BM25 Part 2）")
    print("=" * 68)

    # 构建索引
    index = Index.build(DOCS)
    print(f"\n语料: {len(DOCS)} 篇文档，平均长度 avgdl = {index.avgdl:.3f}")
    print(f"\n倒排表（term → 包含它的文档 id 列表）：")
    for term in sorted(index.postings):
        print(f"  {term:10s}  →  n({term}) = {len(index.postings[term])} docs, postings = {index.postings[term]}")

    # --- 演示 1：对查询 "shane connelly" 计算每篇文档 BM25 分数 ---
    query = ["shane", "connelly"]
    print(f"\n查询 query = {query}（k1=1.2, b=0.75，Elasticsearch 默认）")

    print("\n  doc | len |   shane(t,f) | con..(t,f) |  IDF(s)  | IDF(c)  | BM25 score")
    print("  " + "-" * 78)
    scores = []
    for d in DOCS:
        tf_s = sum(1 for t in tokenize(d) if t == "shane")
        tf_c = sum(1 for t in tokenize(d) if t == "connelly")
        idf_s = idf_lucene(len(index.postings["shane"]), len(DOCS))
        idf_c = idf_lucene(len(index.postings["connelly"]), len(DOCS))
        s = bm25_score(index, d["id"], query)
        scores.append((d["id"], s))
        print(f"  {d['id']:>3d} | {d['len']:>3d} |     {tf_s}        |     {tf_c}        |"
              f"  {idf_s:+.3f} | {idf_c:+.3f} | {s:+.4f}")

    # 排序：分数高的相关性高
    ranked = sorted(scores, key=lambda x: -x[1])
    print(f"\n排序结果（高 → 低）：{[d for d, _ in ranked]}")

    # --- 演示 2：词频饱和曲线 ---
    print("\n" + "-" * 68)
    print("Demo 2 · 词频饱和曲线 (tf saturation) —— 解释 k1 参数的作用")
    print(f"假设文档长度等于 avgdl，此时 norm=1，BM25 tf 部分 = tf·(k1+1)/(tf+k1)")
    print(f"           TF·IDF tf 部分 = ln(tf+1)（无上界）\n")
    print(f"  {'tf':>4s} | {'BM25':>8s} | {'TF·IDF':>8s} | {'BM25 半饱和点':>16s}")
    print("  " + "-" * 50)
    for tf, bm, tfi in tf_saturation_curve(30):
        half = "<-- BM25 半饱和" if tf == int(K1) else ""
        print(f"  {tf:>4d} | {bm:>8.4f} | {tfi:>8.4f} | {half}")

    # --- 演示 3：文档长度归一化（看 b 的作用）---
    print("\n" + "-" * 68)
    print("Demo 3 · 文档长度归一化 —— 解释 b 参数的作用")
    print(f"对词 t (tf=1)，score ∝ 1 / (tf + k1·norm)，norm = 1 − b + b·|D|/avgdl")
    print(f"\n  假设 avgdl = 4，k1 = 1.2\n")
    print(f"  {'|D|':>4s} | {'b=0':>6s} | {'b=0.5':>6s} | {'b=0.75':>6s} | {'b=1.0':>6s} | 说明")
    print("  " + "-" * 78)
    for length in [1, 2, 4, 8, 16, 32]:
        line = f"  {length:>4d} |"
        for b in [0, 0.5, 0.75, 1.0]:
            norm = 1 - b + b * length / 4
            contrib = 1.0 / (1 + K1 * norm)  # tf=1
            line += f" {contrib:>6.4f} |"
        explain = "短文档优势" if length < 4 else ("平均") if length == 4 else "长文档劣势"
        line += f" {explain}"
        print(line)

    print("\n要点：b=0 时归一化失效（所有列相等）；b=1 时长文档被重度惩罚。")


if __name__ == "__main__":
    main()
