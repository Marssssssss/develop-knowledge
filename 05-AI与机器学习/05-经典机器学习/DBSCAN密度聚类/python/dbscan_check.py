# -*- coding: utf-8 -*-
"""DBSCAN 自检:把 KDD-96 原论文的定义/伪码结论与 sklearn 的口径差异逐条钉住。

覆盖:eps-邻域含自身 → MinPts 口径差异 → 核心/边界/噪声三态 → directly density-reachable 的
非对称性 → 双重属点的"先发现先得" → 噪声点被改写为边界点 → 每簇至少 MinPts 点 →
每点至多一次 region query(O(n·log n) 的前提) → 任意形状 vs k-means → k-dist 启发式。
"""
import math

import dbscan as D

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


def two_blobs(n=40, seed=5, gap=8.0, noise=0):
    r = rnd(seed)
    X, truth = [], []
    for _ in range(n):
        X.append([r() * 2, r() * 2])
        truth.append(0)
    for _ in range(n):
        X.append([gap + r() * 2, r() * 2])
        truth.append(1)
    for _ in range(noise):
        X.append([gap / 2 + (r() * 2 - 1) * 30, 40 + r() * 10])
        truth.append(-1)
    return X, truth


def rings(n=90, seed=9):
    """同心圆:k-means 的球形假设在这里必然失败,DBSCAN 靠密度连通可以分开。

    角度取**均匀网格**(只用随机相位偏移与极小径向抖动),避免随机撒点出现 > eps 的空隙
    把一条环切成多段——那会让"DBSCAN 能分任意形状"的演示变成"参数没调好"。
    """
    r = rnd(seed)
    phase = r() * 2 * math.pi
    X, truth = [], []
    for c, (base, _) in enumerate(((1.0, 0), (2.6, 0))):
        for i in range(n):
            a = phase + 2 * math.pi * i / n
            rad = base + (r() - 0.5) * 0.05
            X.append([rad * math.cos(a), rad * math.sin(a)])
            truth.append(c)
    return X, truth


def main():
    print("=" * 74)
    print("DBSCAN 密度聚类 —— 自检")
    print("=" * 74)

    # A. 邻域与 MinPts 口径
    print("\n【A】Definition 1/2:eps-邻域含自身,MinPts 计自身")
    X2 = [[0.0], [1.0]]
    nb = D.region_query(X2, 0, 1.5)
    check("A1 NEps(p) 含 p 自身(dist=0 ≤ eps)", 0 in nb and len(nb) == 2, f"NEps={nb}")
    lab, core = D.dbscan(X2, 1.5, 2)
    check("A2 MinPts=2 时这条 2 点线段的两点都是核心点(含自身口径)",
          sorted(core) == [0, 1], f"core={core}")
    check("A3 它们被判为同一簇(不是噪声)", lab[0] == lab[1] and lab[0] >= 0, f"labels={lab}")
    print("   —— 口径提醒:sklearn 用户指南写「min_samples 个**其他**样本」,按该读法这两点"
          "\n      需要 3 个点才成核心;API 文档与原论文均为「含自身」,本实现取后者")

    # B. directly density-reachable 的非对称性
    print("\n【B】Definition 2:核心点对称,核心-边界不对称")
    # 0/0.5/1.0 成核心点;1.9 只够到 1.0(距离 0.9),是边界点
    Xb = [[0.0], [0.5], [1.0], [1.9]]
    eps, mp = 1.2, 3
    check("B1 |NEps(0)| ≥ MinPts → 0 是核心点",
          D.is_core(Xb, 0, eps, mp), f"|NEps(0)|={len(D.region_query(Xb, 0, eps))}")
    check("B2 |NEps(3)| < MinPts → 3 不是核心点",
          not D.is_core(Xb, 3, eps, mp), f"|NEps(3)|={len(D.region_query(Xb, 3, eps))}")
    # 注意:邻域成员关系本身是对称的(dist 对称);不对称来自**核心点条件**
    check("B3 3 ∈ NEps(2) 且 2 是核心点 → 3 由 2 直接密度可达",
          3 in D.region_query(Xb, 2, eps) and D.is_core(Xb, 2, eps, mp), "")
    check("B4 反向不成立:2 ∈ NEps(3) 但 3 不是核心点 → 2 不能由 3 直接密度可达",
          2 in D.region_query(Xb, 3, eps) and not D.is_core(Xb, 3, eps, mp),
          "成员关系对称,核心点条件不对称")

    # C. 双重属点归"先发现的簇"
    print("\n【C】论文原文:同属两簇的点归**先被发现**的簇(结果可能依赖访问顺序)")
    # 论文 Figure 场景:两簇"靠得很近"但核心点之间距离 > eps,共享点只能是**两簇共有的边界点**。
    # eps=1.0, MinPts=4:共享点 0 的邻域只有 {自身, -0.95, 0.95} = 3 < 4 → 边界点(不是核心)
    A = [[-0.95, 0.0], [-1.05, 0.0], [-1.15, 0.0], [-1.25, 0.0]]
    B = [[0.95, 0.0], [1.05, 0.0], [1.15, 0.0], [1.25, 0.0]]
    S = [[0.0, 0.0]]
    eps, mp = 1.0, 4
    Xf = A + S + B          # 先访问 A
    Xr = B + S + A          # 先访问 B
    lf, _ = D.dbscan(Xf, eps, mp)
    lr, _ = D.dbscan(Xr, eps, mp)
    check("C1 共享点确实是边界点(|NEps| = 3 < MinPts = 4)",
          not D.is_core(Xf, 4, eps, mp), f"|NEps|={len(D.region_query(Xf, 4, eps))}")
    check("C2 两簇核心点互不相邻,保持 2 个簇",
          len(set(l for l in lf if l >= 0)) == 2 and len(set(l for l in lr if l >= 0)) == 2,
          f"簇数 A序={len(set(l for l in lf if l >= 0))}, B序={len(set(l for l in lr if l >= 0))}")
    check("C3 先访问 A 时共享点归 A", lf[4] == lf[0], f"共享点={lf[4]}, A[0]={lf[0]}")
    check("C4 先访问 B 时共享点归 B(同一条数据的结果依赖访问顺序)",
          lr[4] == lr[0] and lr[0] != lr[8], f"共享点={lr[4]}, B[0]={lr[0]}, A[0]={lr[8]}")

    # D. 噪声点后来被改写为边界点
    print("\n【D】伪码:先被标 NOISE 的点,之后可被核心点收编为边界点")
    Xd = [[5.0], [0.0], [0.4], [0.8]]  # 索引 0 先被访问:孤立 → NOISE
    ld, cd = D.dbscan(Xd, 0.6, 3)
    check("D1 先访问的孤立点最终仍是噪声(它邻域里没有核心点)", ld[0] == -1, f"labels={ld}")
    Xe = [[0.9], [0.0], [0.4], [0.8]]  # 索引 0 在核心点邻域内 → 应被收编
    le, _ = D.dbscan(Xe, 0.6, 3)
    check("D2 位于核心点邻域内的先访问点被改写为边界点(不再是 -1)", le[0] >= 0, f"labels={le}")
    check("D3 收编后它与核心点同簇", le[0] == le[1], f"{le[0]} vs {le[1]}")

    # E. 每簇至少 MinPts 个点
    print("\n【E】论文:'a cluster contains at least MinPts points'")
    X, truth = two_blobs(40, 5, gap=8.0, noise=6)
    for mp in (2, 3, 4, 5):
        lab, _ = D.dbscan(X, 1.2, mp)
        sizes = {}
        for l in lab:
            if l >= 0:
                sizes[l] = sizes.get(l, 0) + 1
        check(f"E-MinPts={mp} 每个簇的规模都 ≥ {mp}",
              all(v >= mp for v in sizes.values()) and len(sizes) == 2,
              f"簇规模={sorted(sizes.values())}")

    # F. 参数敏感性
    print("\n【F】eps / min_samples 的敏感性")
    base, _ = D.dbscan(X, 1.2, 4)
    check("F1 合适的 eps 下噪声点数 == 注入的 6 个",
          sum(1 for l in base if l == -1) == 6, f"噪声={sum(1 for l in base if l == -1)}")
    small, _ = D.dbscan(X, 0.05, 4)
    check("F2 eps 过小 → 几乎全部是噪声", sum(1 for l in small if l == -1) >= len(X) * 0.9,
          f"噪声={sum(1 for l in small if l == -1)}/{len(X)}")
    big, _ = D.dbscan(X, 12.0, 4)
    merged = sum(1 for l in big if l == big[0])
    check("F3 eps 过大 → 两簇被合并进同一个簇",
          merged == 80, f"最大簇规模={merged}(两簇各 40 点)")
    check("F3b 同时部分噪声点自己抱团成簇 → 噪声数从 6 降到 "
          f"{sum(1 for l in big if l == -1)}(“低密度”是相对 eps 而言的)",
          sum(1 for l in big if l == -1) < 6, f"噪声={sum(1 for l in big if l == -1)}")
    _, core4 = D.dbscan(X, 1.2, 4)
    _, core25 = D.dbscan(X, 1.2, 25)
    check("F4 min_samples 从 4 提到 25 → 核心点数显著减少", len(core25) < len(core4),
          f"{len(core4)} → {len(core25)}")

    # G. region query 次数:每点至多一次
    print("\n【G】论文:For each point we have at most one region query → O(n·log n)")
    stats = [0]
    D.dbscan(X[:60], 1.2, 4, stats)
    check("G1 region query 调用次数 ≤ 样本数", stats[0] <= 60, f"调用 {stats[0]} 次 / n=60")
    check("G2 确实发生了查询(不是 0 次)", stats[0] > 0, f"{stats[0]}")

    # H. 任意形状 vs k-means
    print("\n【H】任意形状:同心圆")
    Xr, tr = rings()
    lring, _ = D.dbscan(Xr, 0.4, 3)
    lkm = D.kmeans(Xr, 2, seed=1)
    check("H1 DBSCAN 完美分离两个同心圆(ARI == 1)", abs(D.ari(tr, lring) - 1.0) < 1e-9,
          f"ARI={D.ari(tr, lring):.4f}")
    check("H2 同样数据上 k-means(球形假设)近乎随机划分(ARI < 0.2)", D.ari(tr, lkm) < 0.2,
          f"ARI={D.ari(tr, lkm):.4f}")
    check("H3 DBSCAN 不需要预先告诉它有几个簇", len(set(l for l in lring if l >= 0)) == 2,
          f"簇数={len(set(l for l in lring if l >= 0))}")

    # I. k-dist 启发式
    print("\n【I】§4.2 k-dist 启发式")
    kd = D.kdist(X, 4)
    inner = sorted(kd[:80])   # 两簇内部共 80 点
    noise_kd = kd[80:]
    check("I1 簇内点的 4-dist 显著小于孤立点的 4-dist",
          max(inner) < min(noise_kd), f"簇内最大 {max(inner):.3f} vs 噪声最小 {min(noise_kd):.3f}")
    check("I2 论文:k-dist 距离 d 对应的 d-邻域含 k+1 = 5 个点",
          len(D.region_query(X, 0, D.kdist(X, 4)[0])) >= 5,
          f"|NEps|={len(D.region_query(X, 0, D.kdist(X, 4)[0]))}")
    thr = max(inner)          # 取"第一个谷"的谷底作为 eps
    labk, _ = D.dbscan(X, thr + 1e-9, 4)
    check("I3 以簇内最大 4-dist 为 eps,恰好把注入的 6 个噪声点全部排除",
          sum(1 for l in labk if l == -1) == 6, f"噪声={sum(1 for l in labk if l == -1)}")

    print("\n" + "-" * 74)
    print(f"断言 {TOTAL[1]}/{TOTAL[0]} 通过")
    if FAILS:
        print("失败项:" + ", ".join(FAILS))
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
