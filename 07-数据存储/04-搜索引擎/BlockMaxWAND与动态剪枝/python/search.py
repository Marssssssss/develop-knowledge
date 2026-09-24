"""BlockMaxWAND 与朴素 OR 检索的对拍。"""

from scorerutil import (apply_optional_clause, apply_required_clause,
                        filter_competitive_hits)
from wand import (compute_outer_window_max, partition_scorers,
                  update_max_window_scores)


class TopK(object):
    """一个最小的 top-K 收集器，只负责维护 minCompetitiveScore。"""

    def __init__(self, k):
        self.k = k
        self.hits = []          # [(score, doc)]，按分数升序
        self.min_competitive_score = 0.0

    def collect(self, doc, score):
        if self.k > 0 and len(self.hits) >= self.k and score <= self.min_competitive_score:
            return
        self.hits.append((score, doc))
        self.hits.sort()
        if len(self.hits) > self.k:
            self.hits = self.hits[-self.k:]
        self.min_competitive_score = self.hits[0][0] if len(self.hits) >= self.k else 0.0

    def top(self):
        return list(reversed(self.hits))


def naive_or(clauses, k, max_doc):
    """朴素做法：枚举所有子句的并集，逐篇求全部分数之和。"""
    top = TopK(k)
    all_docs = set()
    for c in clauses:
        all_docs.update(c.docs)
    scored = 0
    for doc in sorted(all_docs):
        if doc >= max_doc:
            continue
        s = 0.0
        for c in clauses:
            if doc in c._pos:
                s += c.score(doc)
        scored += 1
        top.collect(doc, s)
    return top, scored


def blockmax_wand(clauses, k, max_doc):
    """MaxScoreBulkScorer.score 的等价实现，附带统计信息。"""
    n = len(clauses)
    top = TopK(k)
    order = list(range(n))
    min_window_size = 1
    num_outer_windows = 0
    num_candidates = 0
    fully_scored = 0
    window_min = 0
    partitions = 0
    empty_windows = 0

    while window_min < max_doc:
        outer_max, min_window_size = compute_outer_window_max(
            clauses, order, 0, window_min, num_outer_windows, num_candidates,
            min_window_size)
        outer_max = min(outer_max, max_doc)

        costs = [c.cost for c in clauses]
        part = None
        while True:
            update_max_window_scores(clauses, order, window_min, outer_max)
            mws = [c.max_window_score for c in clauses]
            part = partition_scorers(mws, costs, order,
                                     top.min_competitive_score)
            partitions += 1
            if part is None:
                break
            order = part["order"]
            new_max, min_window_size = compute_outer_window_max(
                clauses, order, part["first_essential"], window_min,
                num_outer_windows, num_candidates, min_window_size)
            if new_max >= outer_max:
                break
            outer_max = new_max

        if part is None:
            empty_windows += 1
            window_min = outer_max
            num_outer_windows += 1
            continue

        first_ess = part["first_essential"]
        max_score_sums = part["max_score_sums"]
        first_required = part["first_required"]

        # essential 子句的并集构成候选；这里等价于 fillScoreBufferViaLeapFrog
        cand = set()
        for i in order[first_ess:]:
            c = clauses[i]
            for d in c.docs:
                if window_min <= d < outer_max:
                    cand.add(d)
        ess_docs = sorted(cand)
        ess_scores = []
        for d in ess_docs:
            s = 0.0
            for i in order[first_ess:]:
                c = clauses[i]
                if d in c._pos:
                    s += c.score(d)
            ess_scores.append(s)

        docs, scores = list(ess_docs), list(ess_scores)
        num_candidates += len(docs)
        for j in range(first_ess - 1, -1, -1):
            c = clauses[order[j]]
            docs, scores = filter_competitive_hits(
                docs, scores, max_score_sums[j],
                top.min_competitive_score, n)
            if j >= first_required:
                docs, scores = apply_required_clause(docs, scores, c.docs, c.scores())
            else:
                docs, scores = apply_optional_clause(docs, scores, c.docs, c.scores())
        fully_scored += len(docs)
        for d, s in zip(docs, scores):
            top.collect(d, s)

        window_min = outer_max
        num_outer_windows += 1

    stats = {
        "outer_windows": num_outer_windows,
        "partitions": partitions,
        "empty_windows": empty_windows,
        "candidates": num_candidates,
        "fully_scored": fully_scored,
        "final_min_window_size": min_window_size,
    }
    return top, stats
