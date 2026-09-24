"""Lucene MaxScoreBulkScorer（BlockMaxWAND）核心决策模型。

忠实转写自 apache/lucene@main：
  core/src/java/org/apache/lucene/search/MaxScoreBulkScorer.java
  core/src/java/org/apache/lucene/search/MaxScoreCache.java

只建模「决策」而不建模字节：给定每个子句在一个窗口内的最大分数与代价，
回答「哪些子句是 essential」「这个候选能不能被竞争分剪掉」「外窗口取多大」。
纯算术部分（浮点上界、竞争分过滤）在 scorerutil.py。
"""

from scorerutil import B, K1, f32, sum_upper_bound, unsigned_min

INNER_WINDOW_SIZE = 1 << 12
NO_MORE_DOCS = 0x7FFFFFFF
MAX_INT = 0x7FFFFFFF


def bm25_score(idf, freq, norm):
    return idf * freq / (freq + K1 * ((1 - B) + B * norm))


def global_max_score(idf):
    """MaxScoreCache 构造里的 scorer.score(Float.MAX_VALUE, 1L)。"""
    return bm25_score(idf, 3.4028234663852886e38, 1.0)


# --------------------------------------------------------------------------
# Impacts / MaxScoreCache
# --------------------------------------------------------------------------

class Impacts(object):
    """levels[i] = (docIdUpTo, [(freq, norm), ...])。"""

    def __init__(self, levels):
        self.levels = levels

    def num_levels(self):
        return len(self.levels)

    def get_doc_id_up_to(self, level):
        return self.levels[level][0]

    def get_impacts(self, level):
        return self.levels[level][1]


def competitive_pairs(freqs, norms):
    """CompetitiveImpactAccumulator.getCompetitiveFreqNormPairs。

    对每个 norm 只留最大 freq，再按 norm 升序遍历、只保留 freq 严格递增的那些
    （freq 不升就被上一项支配，不可能产生更高的分）。
    """
    best = {}
    for f, nm in zip(freqs, norms):
        best[nm] = max(best.get(nm, 0), f)
    pairs = []
    max_freq = 0
    for nm in sorted(best):
        if best[nm] > max_freq:
            pairs.append((best[nm], nm))
            max_freq = best[nm]
    return pairs


def make_impacts(docs, freqs, norms, block=128):
    """按 Lucene 的层级组织 impacts：level 0 每 block 篇一块，level 1 覆盖全表。"""
    levels = []
    for start in range(0, len(docs), block):
        stop = min(start + block, len(docs))
        levels.append((docs[stop - 1],
                       competitive_pairs(freqs[start:stop], norms[start:stop])))
    if not levels:
        levels.append((0, []))
    levels.append((docs[-1] if docs else 0, competitive_pairs(freqs, norms)))
    return Impacts(levels)


class Clause(object):
    """一个子查询子句。docs/freqs 等长且按文档号升序。"""

    def __init__(self, name, docs, freqs, norms, impacts=None, idf=1.0):
        self.name = name
        self.docs = list(docs)
        self.freqs = list(freqs)
        self.norms = list(norms)
        self.impacts = impacts if impacts is not None else make_impacts(
            self.docs, self.freqs, self.norms)
        self.idf = idf
        self.cost = len(docs)
        self.max_window_score = 0.0
        self.doc = -1
        self._pos = {d: i for i, d in enumerate(self.docs)}
        self.shallow = 0

    def score(self, doc):
        i = self._pos[doc]
        return bm25_score(self.idf, self.freqs[i], self.norms[i])

    def scores(self):
        return [bm25_score(self.idf, f, n)
                for f, n in zip(self.freqs, self.norms)]

    def advance_shallow(self, target):
        """ImpactsSource.advanceShallow：把 impacts 挪到覆盖 target 的那一块。

        这一步**不能省**：MaxScoreCache.getLevel 是从「当前块」开始往后找层的，
        若不做 shallow advance，取到的上界只覆盖后面某一块，会把窗口前半段漏掉，
        从而剪掉本不该剪的文档。
        """
        lv = self.impacts.levels
        if target > lv[-1][0]:
            # 整个子句都在 target 之后：不约束窗口，返回 NO_MORE_DOCS
            self.shallow = len(lv)
            return NO_MORE_DOCS
        i = 0
        while i < len(lv) - 1 and lv[i][0] < target:
            i += 1
        self.shallow = i
        return lv[i][0] if i < len(lv) else NO_MORE_DOCS

    def next_doc_at_least(self, target):
        """iterator.advance(target) 的等价物：返回 >= target 的第一个文档号。"""
        lo, hi = 0, len(self.docs)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.docs[mid] < target:
                lo = mid + 1
            else:
                hi = mid
        return self.docs[lo] if lo < len(self.docs) else NO_MORE_DOCS

    def get_max_score(self, up_to):
        """MaxScoreCache.getMaxScore：从当前块起找第一个覆盖 upTo 的层。"""
        level = -1
        for i in range(self.shallow, self.impacts.num_levels()):
            if up_to <= self.impacts.get_doc_id_up_to(i):
                level = i
                break
        if level == -1:
            return f32(global_max_score(self.idf))
        best = 0.0
        for freq, norm in self.impacts.get_impacts(level):
            best = max(best, bm25_score(self.idf, freq, norm))
        return f32(best)


# --------------------------------------------------------------------------
# MaxScoreBulkScorer.partitionScorers
# --------------------------------------------------------------------------

def partition_scorers(max_window_scores, costs, order, min_competitive_score):
    """重排 allScorers：前 first_essential 个是 non-essential。

    `max_window_scores` 与 `costs` **按子句原始下标索引**；`order` 是本轮开始时的
    allScorers 顺序（元素也是原始下标）。返回的 `order` 同样是原始下标 ——
    源码里 scratch 装的是 scorer 对象本身，这里用下标代替，两者必须保持同一套
    坐标，否则会出现「双重映射」把子句认错。

    全部子句都变成 non-essential（即 first_essential == n）时返回 None，
    对应源码的「这个窗口没有匹配」。
    """
    n = len(order)
    idx = sorted(order,
                 key=lambda i: max_window_scores[i] / max(1, costs[i]))
    max_score_sums = [0.0] * n
    max_score_sum = 0.0
    first_essential = 0
    next_min = float("inf")
    non_ess, ess = [], []
    for i in idx:
        new_sum = max_score_sum + max_window_scores[i]
        # 注意：累加个数的参数是 firstEssentialScorer + 1
        s_float = f32(sum_upper_bound(new_sum, first_essential + 1))
        if s_float < min_competitive_score:
            max_score_sum = new_sum
            max_score_sums[first_essential] = max_score_sum
            first_essential += 1
            non_ess.append(i)
        else:
            ess.append(i)
            next_min = min(s_float, next_min)
    if first_essential == n:
        return None
    # 源码把 essential 子句从数组尾部往前填，故要反转
    ess.reverse()
    order = non_ess + ess
    first_required = n
    if first_essential == n - 1:
        first_required = n - 1
        max_required = max_window_scores[order[first_essential]]
        while first_required > 0:
            without_prev = max_required
            if first_required > 1:
                without_prev += max_score_sums[first_required - 2]
            if f32(without_prev) >= min_competitive_score:
                break
            first_required -= 1
            max_required += max_window_scores[order[first_required]]
    return {
        "first_essential": first_essential,
        "order": order,
        "max_score_sums": max_score_sums,
        "next_min": next_min,
        "first_required": first_required,
    }


def compute_outer_window_max(clauses, order, first_essential, window_min,
                             num_outer_windows, num_candidates, min_window_size):
    """MaxScoreBulkScorer.computeOuterWindowMax。"""
    n = len(clauses)
    first_window_lead = min(first_essential, n - 1)
    window_max = NO_MORE_DOCS
    for k in range(first_window_lead, n):
        c = clauses[order[k]]
        up_to = c.advance_shallow(max(c.doc, window_min))
        window_max = unsigned_min(window_max, up_to + 1)
    if n - first_window_lead > 1:
        threshold = num_outer_windows * 32 * n
        if num_candidates < threshold:
            min_window_size = min(min_window_size << 1, INNER_WINDOW_SIZE)
        else:
            min_window_size = 1
        window_max = max(window_max, min(MAX_INT, window_min + min_window_size))
    return window_max, min_window_size


def update_max_window_scores(clauses, order, window_min, window_max):
    """MaxScoreBulkScorer.updateMaxWindowScores。

    源码的判据是 `scorer.doc < windowMax`：子句在窗口里一篇都不命中时上界取 0。
    这一点不能省 —— 否则已经走到表尾的子句会拿 globalMaxScore 当上界，
    把窗口撑成一个永不推进的死区间。
    """
    for i in order:
        c = clauses[i]
        if c.doc < window_min:
            c.doc = c.next_doc_at_least(window_min)
        if c.doc < window_max:
            if c.doc < window_min:
                c.advance_shallow(window_min)
            c.max_window_score = c.get_max_score(window_max - 1)
        else:
            c.max_window_score = 0.0
    return [clauses[i].max_window_score for i in order]
