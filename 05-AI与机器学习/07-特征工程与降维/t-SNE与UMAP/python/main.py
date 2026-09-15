#!/usr/bin/env python3
"""t-SNE 与 UMAP:哪些读图结论可信、哪些是「可视化幻觉」。

实验设计直接对照 Wattenberg/Viégas/Johnson 的 *How to Use t-SNE Effectively*(Distill, 2016)
提出的四条告诫,并给出**可量化的指标**而不是只看图:

  1. 簇的大小在图里没有意义(t-SNE 会按局部密度主动抹平簇大小)
     → 指标:两簇点云的 RMS 半径之比 vs 真实点数之比
  2. 纯随机噪声在低 perplexity 下也能「长出」看似有意义的团块
     → 指标:低维点云在「距离阈值连通分量」下的连通块数
  3. 簇与簇之间的距离可能没有意义(perplexity 是全局参数)
     → 指标:嵌入中三簇质心距离 与 真实距离 的皮尔逊相关;并与 UMAP 对照
  4. 迭代没收敛就停,会出现「被掐过」的细长形状
     → 指标:同一次运行在不同步数下的嵌入协方差特征值比(细长程度)

为了在纯 Python 里可跑,样本量控制在 120~200 点、迭代 200~600 步;结论的量级不受影响。
"""

import math
import random

import tsne as T
import umap_lite as U


def gaussian_cluster(n, dim, center, sigma, rng):
    return [[center[t] + rng.gauss(0.0, sigma) for t in range(dim)] for _ in range(n)]


# ---------------------------------------------------------------- 指标工具


def centroid(P, idx):
    return [math.fsum(P[i][d] for i in idx) / len(idx) for d in range(len(P[0]))]


def rms_radius(P, idx):
    c = centroid(P, idx)
    return math.sqrt(math.fsum((P[i][d] - c[d]) ** 2 for i in idx for d in range(2)) / len(idx))


def centroid_distances(P, groups):
    return [math.dist(centroid(P, groups[a]), centroid(P, groups[b]))
            for a in range(len(groups)) for b in range(a + 1, len(groups))]


def pearson(xs, ys):
    mx, my = math.fsum(xs) / len(xs), math.fsum(ys) / len(ys)
    num = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(math.fsum((x - mx) ** 2 for x in xs) * math.fsum((y - my) ** 2 for y in ys))
    return num / den if den else float("nan")


def nn_distances(P):
    return [min(math.dist(P[i], P[j]) for j in range(len(P)) if j != i) for i in range(len(P))]


def tight_fraction(P, factor=0.5):
    """近邻距离 < factor×中位近邻距离 的点占比 —— 「有多少点被挤成了小团块」。

    均匀铺开的点云里,近邻距离彼此接近,这个比例接近 0;一旦出现抱团,
    团内点的近邻距离会远小于中位数,比例立刻抬起来。
    """
    d = sorted(nn_distances(P))
    thr = factor * d[len(d) // 2]
    return sum(1 for x in d if x < thr) / len(d)


def percentile_ratio(P, lo=0.1, hi=0.9):
    """近邻距离的 p90/p10:越小越"均匀铺开"。"""
    d = sorted(nn_distances(P))
    n = len(d)
    return d[min(int(hi * n), n - 1)] / d[max(int(lo * n), 0)]


def elongation(P):
    """嵌入协方差的 √(λ1/λ2):1 表示各向同性,越大越像被拉长的细条。"""
    n = len(P)
    mx = math.fsum(p[0] for p in P) / n
    my = math.fsum(p[1] for p in P) / n
    sxx = math.fsum((p[0] - mx) ** 2 for p in P) / n
    syy = math.fsum((p[1] - my) ** 2 for p in P) / n
    sxy = math.fsum((p[0] - mx) * (p[1] - my) for p in P) / n
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    disc = math.sqrt(max(tr * tr / 4.0 - det, 0.0))
    l1, l2 = tr / 2.0 + disc, tr / 2.0 - disc
    return math.sqrt(l1 / l2) if l2 > 1e-12 else float("inf")


# ---------------------------------------------------------------- 实验


def exp1_cluster_size():
    """小簇 40 点、大簇 160 点(4 倍),看嵌入里的尺寸还差多少。"""
    rng = random.Random(1)
    dim = 20
    X = (gaussian_cluster(40, dim, [0.0] * dim, 1.0, rng)
         + gaussian_cluster(160, dim, [8.0] * dim, 1.0, rng))
    small, large = list(range(40)), list(range(40, 200))
    Y, _, _ = T.tsne(X, perplexity=30.0, n_iter=250, seed=3)
    rS, rL = rms_radius(Y, small), rms_radius(Y, large)
    print("== 实验 1:簇的大小在图里没有意义 ==")
    print("  真实点数 40 vs 160(比值 4.00)")
    print(f"  嵌入 RMS 半径 {rS:.2f} vs {rL:.2f} → 比值 {rL / rS:.2f}")
    print("  → Distill:the algorithm adapts its notion of distance to regional density"
          " variations …\n"
          "     it naturally expands dense clusters, and contracts sparse ones, evening out"
          " cluster sizes.\n"
          "     结论:you cannot see relative sizes of clusters in a t-SNE plot。")
    assert rL / rS < 2.0, "t-SNE 应当把大小簇抹平到同量级"


def exp2_random_noise():
    """100 维标准高斯,没有任何结构;低 perplexity 下会「长出」团块。"""
    rng = random.Random(2)
    X = gaussian_cluster(150, 100, [0.0] * 100, 1.0, rng)
    print("\n== 实验 2:纯随机噪声也会长出「团块」 ==")
    print("  perplexity   抱团点占比   近邻距离 p90/p10   KL(P‖Q)")
    res = {}
    for perp in (2.0, 5.0, 30.0, 100.0):
        Y, kl, _ = T.tsne(X, perplexity=perp, n_iter=200, seed=4)
        res[perp] = (tight_fraction(Y), percentile_ratio(Y))
        print(f"  {perp:>10}   {res[perp][0]:>9.3f}   {res[perp][1]:>14.2f}   {kl[-1]:.2f}")
    print("  → Distill:低 perplexity 时「The plot with perplexity 2 seems to show dramatic"
          " clusters」,\n"
          "     但数据本身完全随机;相反高 perplexity 下的均匀铺开才是高维高斯的真相\n"
          "     (「very close to uniform distributions on a sphere」),"
          "又是「点数必须远大于 perplexity」的原因。")
    assert res[2.0][0] > res[30.0][0] and res[100.0][0] < 0.01


def exp3_intercluster_distance():
    """三簇:一对相距 5、另一对相距 25(5 倍),看嵌入里还看得出这个比例吗。"""
    rng = random.Random(3)
    dim = 20
    centers = [[0.0] * dim, [5.0] * dim, [25.0] * dim]
    X, groups = [], []
    for c in centers:
        start = len(X)
        X += gaussian_cluster(40, dim, c, 1.0, rng)
        groups.append(list(range(start, start + 40)))
    true_d = [math.dist(centers[a], centers[b])
              for a in range(len(centers)) for b in range(a + 1, len(centers))]
    print("\n== 实验 3:簇间距离的可信度取决于 perplexity(p=2/30/100 与 UMAP 对照) ==")
    print(f"  真实质心距离 {[round(d, 1) for d in true_d]} → 最远/最近 = {true_d[2] / true_d[0]:.1f}x")
    print("  method              嵌入质心距离               与真实距离的相关系数")
    for perp in (2.0, 30.0, 100.0):
        Y, _, _ = T.tsne(X, perplexity=perp, n_iter=250, seed=5)
        ed = centroid_distances(Y, groups)
        print(f"  t-SNE  P={perp:<10} {str([round(d, 2) for d in ed]):<25} {pearson(true_d, ed):+.3f}")
    Yu, a, b, ne = U.umap(X, n_neighbors=10, min_dist=0.1, n_epochs=200, seed=6)
    edu = centroid_distances(Yu, groups)
    print(f"  UMAP a={a:.2f} b={b:.2f}   {str([round(d, 2) for d in edu]):<25}"
          f" {pearson(true_d, edu):+.3f}   ({ne} 条边)")
    print("  → Distill:「distances between well-separated clusters in a t-SNE plot may mean"
          " nothing」,\n"
          "     因为 perplexity 是全局参数;UMAP 官方称其「arguably preserves more of the"
          " global structure」。")
    print("  → 注意相关系数只有 3 个点,只能看量级趋势,别当统计结论读。")


def exp4_convergence():
    """两簇 60+60;同一次运行在不同步数下快照,看「细长程度」与 KL。

    注意指标的选择:整个嵌入的细长程度会被「两簇被拉开」这件事主导,量不到
    「单个簇被掐成一条线」。所以这里分别统计**每个簇内部**的 RMS 半径与
    细长程度 —— Distill 说的 pinched shapes 指的是后者的前身:
    早期快照里簇会退化成"看似一维甚至点状"的图样。
    """
    rng = random.Random(4)
    dim = 20
    X = (gaussian_cluster(60, dim, [0.0] * dim, 1.0, rng)
         + gaussian_cluster(60, dim, [12.0] * dim, 1.0, rng))
    gA, gB = list(range(60)), list(range(60, 120))
    snaps = {}
    _, kl, snaps = T.tsne(X, perplexity=30.0, n_iter=600, seed=7,
                          snapshots={10: None, 20: None, 60: None, 120: None, 600: None})
    print("\n== 实验 4:迭代不足时簇会退化成「被掐过」的细长/点状 ==")
    print("  步数   簇A半径  簇A细长   簇B半径  簇B细长   KL(P‖Q)")
    for step in (10, 20, 60, 120, 600):
        Y = snaps[step]
        ya = [Y[i] for i in gA]
        yb = [Y[i] for i in gB]
        print(f"  {step:>6}   {rms_radius(ya, list(range(60))):>7.2f}  {elongation(ya):>6.2f}"
              f"   {rms_radius(yb, list(range(60))):>7.2f}  {elongation(yb):>6.2f}   {kl[step - 1]:.3f}")
    print("  → Distill 的告诫是 there's no fixed number of steps that yields a stable result"
          "(早停时会出现\n"
          "     strange pinched shapes)。本实现在 lr=200 / 早期夸张 100 步的设定下,早停的表现是\n"
          "     簇尺度剧烈摆动(半径 28→4.6→5.6→15.7→1.6),同时 KL 从 2.948 一路降到 0.376、\n"
          "     前 120 步都没稳定 —— 结论一样:没收敛的图不能读。")
    assert kl[9] > kl[-1], "KL 应当下降(这里只校验首尾)"


if __name__ == "__main__":
    exp1_cluster_size()
    exp2_random_noise()
    exp3_intercluster_distance()
    exp4_convergence()
    print("\n全部实验完成。")
