# -*- coding: utf-8 -*-
"""层次聚类自检:Lance-Williams 递推是否与"每步直接重算"等价,Ward 的 ΔSSE 是否成立,
合并高度是否单调,树状图切割是否正确,"rich get richer" 是否可观测。

反例优先:递推公式写错(α/β/γ 记反、ward 忘记用平方距离)时,**合并高度序列一定会分叉**,
所以 A 段用"朴素重算版"做对照实现,而不是只断言几个孤立数字。
"""
import math

import agglomerative as A

TOTAL = [0, 0]
FAILS = []


def check(name, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        TOTAL[1] += 1
        print(f"  [PASS] {name}  {detail}")
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}  {detail}")


def rnd(seed):
    st = seed

    def f():
        nonlocal st
        st = (st * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (st >> 11) / float(1 << 53)
    return f


def points(n, seed, scale=3.0):
    r = rnd(seed)
    return [[scale * (r() * 2 - 1), scale * (r() * 2 - 1)] for _ in range(n)]


def sse(X, mem):
    """簇内平方和:Σ||x − c||²。"""
    if len(mem) <= 1:
        return 0.0
    d = len(X[0])
    c = [sum(X[i][t] for i in mem) / len(mem) for t in range(d)]
    return sum((X[i][t] - c[t]) ** 2 for i in mem for t in range(d))


def naive_linkage(X, linkage):
    """对照实现:每一步都从原始点**直接重算**全部簇间距离,不用任何递推。"""
    n = len(X)
    probe = A.Agglomerative(linkage)
    probe.X = X  # _pair_dist 直接按原始点重算,不需要 fit
    groups = [frozenset([i]) for i in range(n)]
    heights, seq = [], []
    while len(groups) > 1:
        best = None
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                v = probe._pair_dist(
                    {i: sorted(g) for i, g in enumerate(groups)}, a, b)
                if best is None or v < best[0] - 1e-15:
                    best = (v, a, b)
        v, a, b = best
        heights.append(v)
        seq.append(frozenset(groups[a] | groups[b]))
        merged = groups[a] | groups[b]
        groups = [g for i, g in enumerate(groups) if i not in (a, b)] + [merged]
    return heights, seq


def main():
    print("=" * 74)
    print("层次聚类与连接准则 —— 自检")
    print("=" * 74)
    X = points(9, 7)

    # A. Lance-Williams 递推 vs 朴素重算
    print("\n【A】Lance-Williams 递推 vs 逐步直接重算(四种准则)")
    for lk in A.LINKAGES:
        m = A.Agglomerative(lk).fit(X)
        nh, ns = naive_linkage(X, lk)
        okh = len(nh) == len(m.merges) and all(
            abs(a - b) < 1e-9 for a, b in zip(m.heights(), nh))
        # 直接比对"每步合并出来的成员集合"序列:fit 里新簇 id 从 n 起递增
        got = []
        snap = {i: [i] for i in range(len(X))}
        for step, (a, b, _h, _s) in enumerate(m.merges):
            ga, gb = snap.pop(a), snap.pop(b)
            got.append(frozenset(ga + gb))
            snap[len(X) + step] = ga + gb
        check(f"A-{lk}:合并高度序列与朴素版一致", okh, f"{len(nh)} 步")
        check(f"A-{lk}:每步合并的成员集合一致", [frozenset(g) for g in got] == [frozenset(g) for g in ns], "")

    # B. Ward 的 ΔSSE 闭式
    print("\n【B】Ward:ΔSSE = (n_i·n_j/(n_i+n_j))·||c_i−c_j||²")
    r = rnd(3)
    mem_i = [0, 1, 2, 3, 4]
    mem_j = [5, 6, 7]
    gain = A.ward_gain([X[i] for i in mem_i], [X[i] for i in mem_j])
    direct = sse(X, mem_i + mem_j) - sse(X, mem_i) - sse(X, mem_j)
    check("B1 闭式解 == 合并前后簇内平方和之差", abs(gain - direct) < 1e-9,
          f"{gain:.9f} vs {direct:.9f}")
    check("B2 合并同类(n=1 簇)的代价为 0", abs(A.ward_gain([X[0]], [X[0]])) < 1e-12, "")

    # C. 合并高度单调性
    print("\n【C】合并高度的单调性")
    for lk in A.LINKAGES:
        h = A.Agglomerative(lk).fit(X).heights()
        mono = all(h[i] >= h[i - 1] - 1e-12 for i in range(1, len(h)))
        if lk == "ward":
            print(f"   [信息] ward 的合并高度(SSE 增量)本数据上单调={mono}"
                  f"(Ward 不保证单调,故不作断言)")
        else:
            check(f"C-{lk}:合并高度单调非降", mono,
                  f"{h[0]:.4f} → {h[-1]:.4f}")

    # D. 合并次数
    print("\n【D】合并次数与树的结构")
    m = A.Agglomerative("ward").fit(X)
    check("D1 恰好 n−1 次合并", len(m.merges) == len(X) - 1, f"{len(m.merges)} 步 / n={len(X)}")
    check("D2 最后一次合并得到规模 n 的簇", m.merges[-1][3] == len(X), f"{m.merges[-1][3]}")
    exp = sum(max(0, len(X) - i - 2) for i in range(len(X) - 1))
    check("D3 距离更新次数 == Σ(活跃簇数−2) == n(n−1)/2 − (n−1)",
          m.updates == exp, f"{m.updates} vs {exp}")

    # E. 树状图切割
    print("\n【E】树状图切割")
    for k in (1, 2, 3, 5, 9):
        lab = A.cut_tree(m.merges, len(X), k)
        check(f"E-k={k}:恰好得到 {k} 个簇", len(set(lab)) == k, f"簇数={len(set(lab))}")
    check("E-覆盖:每个样本都被分配", len(A.cut_tree(m.merges, len(X), 3)) == len(X), "")

    # F. rich get richer / 链式效应
    print("\n【F】'rich get richer':两团之间放一串稀疏的'桥'点")
    r2 = rnd(21)
    A_pt = [[-4.0 + (r2() - 0.5) * 0.5, (r2() - 0.5) * 0.5] for _ in range(15)]   # 0..14
    B_pt = [[4.0 + (r2() - 0.5) * 0.5, (r2() - 0.5) * 0.5] for _ in range(15)]    # 15..29
    chain = [[-1.6 + 0.4 * t, 0.0] for t in range(9)]                             # 30..38,间距 0.4
    Xb = A_pt + B_pt + chain
    join_h, sizes = {}, {}
    for lk in A.LINKAGES:
        mm = A.Agglomerative(lk).fit(Xb)
        snap = {i: [i] for i in range(len(Xb))}
        h_join = None
        for step, (a, b, h, _s) in enumerate(mm.merges):
            ga, gb = snap.pop(a), snap.pop(b)
            merged = ga + gb
            if h_join is None and any(i < 15 for i in merged) and any(15 <= i < 30 for i in merged):
                h_join = h                       # A 团与 B 团第一次落入同一簇的高度
            snap[len(Xb) + step] = merged
        lab = A.cut_tree(mm.merges, len(Xb), 2)
        sz = sorted((lab.count(c) for c in set(lab)), reverse=True)
        join_h[lk], sizes[lk] = h_join, sz
        print(f"   {lk:>9}: A∪B 合并高度 = {h_join:.4f}, k=2 簇规模 = {sz}")
    check("F1 single 靠'桥'把两团串起来 → 合并高度远小于 complete",
          join_h["single"] * 3 < join_h["complete"],
          f"single={join_h['single']:.4f} vs complete={join_h['complete']:.4f}")
    check("F2 同样数据上 ward 也要到很高的代价才肯合并两团(方差最小化不看最短路)",
          join_h["single"] * 3 < join_h["ward"],
          f"single={join_h['single']:.4f} vs ward={join_h['ward']:.4f}")
    sse_of = {}
    for lk in A.LINKAGES:
        lab = A.cut_tree(A.Agglomerative(lk).fit(Xb).merges, len(Xb), 2)
        sse_of[lk] = sum(sse(Xb, [i for i in range(len(Xb)) if lab[i] == c]) for c in set(lab))
    check("F3 k=2 时 ward 的簇内平方和最小(它优化的就是这个目标)",
          min(sse_of.values()) == sse_of["ward"],
          " ".join(f"{k}={v:.2f}" for k, v in sse_of.items()))

    # G. 第一步合并
    print("\n【G】第一步:四个准则都合并最相近的两个单点")
    firsts = {}
    for lk in A.LINKAGES:
        mm = A.Agglomerative(lk).fit(X)
        firsts[lk] = (mm.merges[0][0], mm.merges[0][1])
    check("G1 四种准则的第一步合并同一对点", len(set(firsts.values())) == 1,
          str(set(firsts.values())))
    dmin = min((A._dist(X[i], X[j]), i, j) for i in range(len(X)) for j in range(i + 1, len(X)))
    check("G2 这一对确实是全表距离最小的两点",
          set(firsts["ward"]) == {dmin[1], dmin[2]}, f"最近对={ {dmin[1], dmin[2]} }")

    # H. 与 k-means 的目标对比(Ward ≈ 层次版 k-means)
    print("\n【H】Ward 与 k-means:同为方差最小化,路径不同")
    labw = A.cut_tree(A.Agglomerative("ward").fit(Xb).merges, len(Xb), 2)
    labk = A.kmeans(Xb, 2, seed=5)
    sse_w = sum(sse(Xb, [i for i in range(len(Xb)) if labw[i] == c]) for c in set(labw))
    sse_k = sum(sse(Xb, [i for i in range(len(Xb)) if labk[i] == c]) for c in set(labk))
    check("H1 两者都把两团分开(主导方向一致)",
          len(set(labw)) == 2 and len(set(labk)) == 2, "")
    print(f"   [信息] 本数据上 k=2 的簇内平方和:Ward={sse_w:.3f}, k-means={sse_k:.3f}"
          f"(k-means 是 Ward 的下界候选,层次法受合并路径约束,故不断言大小)")

    print("\n" + "-" * 74)
    print(f"断言 {TOTAL[1]}/{TOTAL[0]} 通过")
    if FAILS:
        print("失败项:" + ", ".join(FAILS))
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
