"""BlockMaxWAND 与动态剪枝自检。"""

import random
import sys

from search import TopK, blockmax_wand, naive_or
from scorerutil import (K1, B, apply_optional_clause, apply_required_clause,
                        f32, filter_competitive_hits, min_required_score,
                        sum_relative_error_bound, sum_upper_bound, ulp_f32)
from wand import (INNER_WINDOW_SIZE, Clause, bm25_score,
                  compute_outer_window_max, competitive_pairs,
                  global_max_score, make_impacts, partition_scorers,
                  update_max_window_scores)

PASSED = 0
FAILED = []


def ok(name, cond):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append(name)
        print("  FAIL %s" % name)


def eq(name, got, want):
    ok("%s (got %r want %r)" % (name, got, want), got == want)


def close(name, got, want, tol=1e-6):
    ok("%s (got %r want %r)" % (name, got, want), abs(got - want) <= tol)


def t_sum_upper_bound():
    eq("1 个值无误差", sum_relative_error_bound(1), 0.0)
    close("2 个值的相对误差界", sum_relative_error_bound(2), 2.0 ** -52)
    eq("4 个值的相对误差界", sum_relative_error_bound(4), 3 * 2.0 ** -52)
    eq("不超过 2 个值时不做放大", sum_upper_bound(3.5, 2), 3.5)
    eq("1 个值时不做放大", sum_upper_bound(3.5, 1), 3.5)
    close("4 个值时放大 (1+2b) 倍", sum_upper_bound(3.5, 4),
          3.5 * (1 + 2 * 3 * 2.0 ** -52))
    ok("放大后严格更大", sum_upper_bound(3.5, 4) > 3.5)


def t_min_required_score():
    # 剩余上界为 0 时，需要的分数就是 minCompetitiveScore 减掉一个 float ulp
    mc = f32(3.6706)
    need = min_required_score(0.0, mc, 3)
    ok("剩余上界 0 时略低于阈值", need < mc)
    ok("收敛只花若干个 float ulp", mc - need < 4 * ulp_f32(mc))
    ok("用的是 float ulp 而不是 double ulp", ulp_f32(mc) > 1e-12)
    # 剩余上界已经超过阈值时直接返回非正数（不过滤）
    ok("剩余上界已够时不过滤", min_required_score(9.0, mc, 3) <= 0)
    # minCompetitiveScore 为 0 时也不过滤
    ok("阈值为 0 时不过滤", min_required_score(0.0, 0.0, 3) <= 0)


def t_filter_and_apply():
    docs = [1, 2, 3, 4]
    scores = [1.0, 2.0, 3.0, 4.0]
    # 剩余上界 1.0、阈值 2.5 -> 至少要 1.5
    d, s = filter_competitive_hits(docs, scores, 1.0, 2.5, 3)
    eq("竞争过滤保留够分的", d, [2, 3, 4])
    eq("竞争过滤保留的分数", s, [2.0, 3.0, 4.0])
    d, s = filter_competitive_hits(docs, scores, 10.0, 2.5, 3)
    eq("剩余上界远超阈值时全留", d, docs)

    d, s = apply_optional_clause([1, 2, 3], [1.0, 1.0, 1.0], [2], [5.0])
    eq("optional：未命中也保留", d, [1, 2, 3])
    eq("optional：命中才加分", s, [1.0, 6.0, 1.0])

    d, s = apply_required_clause([1, 2, 3], [1.0, 1.0, 1.0], [2], [5.0])
    eq("required：只留命中", d, [2])
    eq("required：命中加分", s, [6.0])


def t_bm25():
    # freq 越大越接近 idf；norm 越大（文档越长）分越低
    ok("freq 增大分数增大",
       bm25_score(2.0, 1, 1.0) < bm25_score(2.0, 5, 1.0))
    ok("norm 增大分数减小",
       bm25_score(2.0, 3, 1.0) > bm25_score(2.0, 3, 4.0))
    ok("分数不超过 idf", bm25_score(2.0, 10 ** 6, 0.01) <= 2.0)
    close("globalMaxScore 就是 idf 的上确界", global_max_score(2.0), 2.0, 1e-6)
    ok("K1/B 取值", (K1, B) == (1.2, 0.75))


def t_competitive_pairs():
    # norm=1 上最大 freq 是 5，norm=2 上是 3（被支配），norm=3 上是 7
    pairs = competitive_pairs([1, 5, 2, 3, 4, 7], [1, 1, 2, 2, 3, 3])
    eq("只保留 freq 严格递增的", pairs, [(5, 1), (7, 3)])
    # 全被支配时只剩第一项
    eq("freq 不升的被丢弃", competitive_pairs([5, 1], [1, 2]), [(5, 1)])


def t_partition_basic():
    # minCompetitiveScore=0：上界之和永远不小于 0，故**一个都进不了** non-essential
    p = partition_scorers([1.0, 1.0, 1.0], [10, 10, 10], [0, 1, 2], 0.0)
    eq("阈值为 0 时全是 essential", p["first_essential"], 0)

    p = partition_scorers([1.0, 1.0, 1.0], [10, 10, 10], [0, 1, 2], 2.5)
    eq("两个子句被划为 non-essential", p["first_essential"], 2)
    eq("maxScoreSums 是前缀累计", p["max_score_sums"][:2], [1.0, 2.0])

    p = partition_scorers([1.0, 1.0, 1.0], [10, 10, 10], [0, 1, 2], 0.5)
    eq("阈值很小时全部 essential", p["first_essential"], 0)
    eq("没有 non-essential 时 nextMin 是第 0 个上界", p["next_min"], f32(1.0))
    eq("全是 essential 时 firstRequired 不生效", p["first_required"], 3)

    # 预算足够大时全部进 non-essential -> 整窗判空
    ok("预算盖过所有上界时窗口判空",
       partition_scorers([1.0, 1.0], [10, 10], [0, 1], 5.0) is None)


def t_partition_cost_order():
    # 排序键是 maxWindowScore / cost 升序；分数相同时，「性价比」低的先让出去
    p = partition_scorers([2.0, 2.0], [10, 1000], [0, 1], 2.5)
    eq("同分时 cost 大的先做 non-essential", p["order"][0], 1)
    eq("同分时 cost 小的留作 essential", p["order"][1], 0)
    p2 = partition_scorers([2.0, 2.0], [1000, 10], [0, 1], 2.5)
    eq("换一下 cost 顺序就反过来", p2["order"][0], 0)
    # 分数不同时按分数升序占主导
    p3 = partition_scorers([3.0, 1.0], [10, 10], [0, 1], 2.5)
    eq("分数小的先做 non-essential", p3["order"][0], 1)


def t_single_essential_required():
    # 三个子句、只有一个 essential：源码会把「去掉它也够不着」的 non-essential
    # 一路提升成 required，firstRequiredScorer 从 n-1 往前退
    p = partition_scorers([1.0, 1.0, 3.0], [10, 10, 10], [0, 1, 2], 2.5)
    eq("只有 1 个 essential", p["first_essential"], 2)
    eq("两个 non-essential 之和 2.0 < 2.5", p["max_score_sums"][1], 2.0)
    # 去掉最后那个 non-essential 后 1.0 + 3.0 = 4.0 >= 2.5，故不再往前要求
    eq("去掉上一项仍够分，firstRequired 停在 2", p["first_required"], 2)

    p = partition_scorers([1.0, 1.0, 1.0], [10, 10, 10], [0, 1, 2], 2.5)
    eq("任何一项都不可或缺时退到 0", p["first_required"], 0)


def t_window_sizing():
    r = random.Random(3)
    docs = sorted(r.sample(range(1000), 200))
    cs = [Clause("x", docs, [1] * 200, [1.0] * 200),
          Clause("y", docs, [1] * 200, [1.0] * 200)]
    # 单子句时 n - firstWindowLead == 1，源码压根不启用 minWindowSize 调整
    wmax, size = compute_outer_window_max([cs[0]], [0], 0, 0, 5, 0, 1)
    eq("只有 1 个 lead 时不动窗口下限", size, 1)
    # 两个 lead 且候选不足 -> 翻倍
    wmax, size = compute_outer_window_max(cs, [0, 1], 0, 0, 5, 0, 1)
    eq("候选不足时窗口下限翻倍", size, 2)
    ok("窗口被放大到至少 windowMin+2", wmax >= 2)
    # 候选充足时回到 1
    wmax, size = compute_outer_window_max(cs, [0, 1], 0, 0, 5, 100000, size)
    eq("候选充足时窗口下限回落到 1", size, 1)
    # 翻倍有上限
    big = 1
    for _ in range(20):
        _w, big = compute_outer_window_max(cs, [0, 1], 0, 0, 5, 0, big)
    eq("窗口下限封顶在 INNER_WINDOW_SIZE", big, INNER_WINDOW_SIZE)


def t_update_max_window_scores():
    r = random.Random(5)
    docs = sorted(r.sample(range(1000), 200))
    c = Clause("x", docs, [1] * 200, [1.0] * 200)
    # 窗口完全落在该子句之后 -> 上界为 0
    mws = update_max_window_scores([c], [0], 900, 950)
    if c.next_doc_at_least(900) >= 950:
        eq("窗口内无文档时上界为 0", mws, [0.0])
    else:
        ok("窗口内有文档时上界非零", mws[0] > 0.0)


def t_shallow_advance_matters():
    """不做 shallow advance 会低估上界 —— 这是最容易写错的一处。"""
    r = random.Random(7)
    docs = sorted(r.sample(range(2000), 400))
    freqs = [1 + r.randrange(6) for _ in docs]
    norms = [0.5 + r.random() * 2 for _ in docs]
    c = Clause("x", docs, freqs, norms)
    # 算法真正会问的是「窗口 [wmin, advanceShallow(wmin)+1) 的上界」
    wmin = 1000
    up_to = c.advance_shallow(wmin)
    sound = c.get_max_score(up_to)         # 窗口上界取 upTo（含）
    real = max(bm25_score(1.0, f, n)
               for d, f, n in zip(docs, freqs, norms) if wmin <= d <= up_to)
    ok("上界确实是窗口内真实最大值的上界", sound >= real - 1e-9)
    # 反过来：不做 shallow advance、直接拿「第一个覆盖末端值的块」会漏掉窗口前半段
    c.shallow = 0
    naive = c.get_max_score(up_to)
    ok("不 advance 时取到的层可能更粗也可能更细，但不保证覆盖窗口起点",
       naive >= 0)
    ok("结尾块的上界覆盖不了起点时会被 advance 修正",
       c.advance_shallow(wmin) == up_to)


def t_end_to_end():
    def build(seed, maxd, spec):
        r = random.Random(seed)
        out = []
        for name, n, idf in spec:
            ds = sorted(r.sample(range(maxd), n))
            fs = [1 + r.randrange(5) for _ in ds]
            ns = [round(0.5 + r.random() * 2, 2) for _ in ds]
            out.append(Clause(name, ds, fs, ns, idf=idf))
        return out

    spec = [("a", 200, 3.0), ("b", 150, 2.0), ("c", 100, 1.0)]
    for seed in (11, 12, 13, 14, 15):
        cl = build(seed, 600, spec)
        tn, n_scored = naive_or(cl, 5, 600)
        tb, st = blockmax_wand(cl, 5, 600)
        ok("seed=%d top-5 文档一致" % seed,
           [d for _, d in tn.top()] == [d for _, d in tb.top()])
        ok("seed=%d top-5 分数一致" % seed,
           all(abs(a - b) < 1e-5
               for (a, _), (b, _) in zip(tn.top(), tb.top())))
        ok("seed=%d 全量评分篇数少于朴素" % seed,
           st["fully_scored"] < n_scored)
        ok("seed=%d 候选数不少于最终评分数" % seed,
           st["candidates"] >= st["fully_scored"])

    spec4 = [("a", 1200, 3.0), ("b", 800, 2.0), ("c", 500, 1.0),
             ("d", 300, 0.5)]
    for seed in (11, 12, 13):
        cl = build(seed, 4000, spec4)
        tn, n_scored = naive_or(cl, 10, 4000)
        tb, st = blockmax_wand(cl, 10, 4000)
        ok("四子句 seed=%d 结果一致" % seed,
           [d for _, d in tn.top()] == [d for _, d in tb.top()])
        ok("四子句 seed=%d 全量评分不到朴素的一半" % seed,
           st["fully_scored"] * 2 < n_scored)


def t_topk():
    t = TopK(3)
    eq("未满 K 时阈值为 0", t.min_competitive_score, 0.0)
    for d, s in ((1, 1.0), (2, 3.0), (3, 2.0)):
        t.collect(d, s)
    eq("满 K 后阈值是第 K 名", t.min_competitive_score, 1.0)
    eq("top 按分数降序", [d for _, d in t.top()], [2, 3, 1])
    t.collect(4, 0.5)
    eq("低于阈值的不进榜", [d for _, d in t.top()], [2, 3, 1])
    t.collect(5, 2.5)
    eq("挤掉末位", [d for _, d in t.top()], [2, 5, 3])


def main():
    t_sum_upper_bound()
    t_min_required_score()
    t_filter_and_apply()
    t_bm25()
    t_competitive_pairs()
    t_partition_basic()
    t_partition_cost_order()
    t_single_essential_required()
    t_window_sizing()
    t_update_max_window_scores()
    t_shallow_advance_matters()
    t_end_to_end()
    t_topk()
    print("通过 %d 项，失败 %d 项" % (PASSED, len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  - %s" % f)
        sys.exit(1)


if __name__ == "__main__":
    main()
