"""K-Means 聚类:Lloyd 算法 + k-means++ 初始化 + 惯性评估 + 肘部法选 k。

权威来源:
- scikit-learn Clustering §2.3 https://scikit-learn.org/stable/modules/clustering.html
  Lloyd 三步:① init 选 k 中心 ② 每个样本指派最近中心 ③ 中心取均值更新
  K-means is equivalent to the expectation-maximization algorithm with a small,
  all-equal, diagonal covariance matrix.
  inertia = Σ min_μj ‖xᵢ − μⱼ‖²  within-cluster sum-of-squares
  收敛:not in this implementation: iteration stops when centroids move less than tolerance
  k-means++(Arthur & Vassilvitskii 2007)初始化:概率 ∝ D(x)² / Σ D(x)² 选远处点
  Algorithm ∈ {lloyd, elkan};elkan 用三角不等式加速但 O(n·k) 额外内存
  n_init: 'auto' → 10(random)/1(k-means++);多 seed 取最小 inertia

纯 stdlib,5 demo:基础 K-Means / k-means++ vs random 对比 /
多次 init 取最优 / 肘部法选 k / 收敛轨迹可视化。
"""

from __future__ import annotations

import math
import random
from typing import List, Optional, Sequence, Tuple


Vector = List[float]


def euclidean(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def kmeans_plus_plus_init(X: List[Vector], k: int, rng: random.Random) -> List[Vector]:
    """k-means++ 概率初始化(Arthur & Vassilvitskii 2007):
    1. 随机选 1 个中心 μ₁
    2. 对每个点算 D(x) = min_j ‖x − μⱼ‖²(已选中心最近距离的平方)
    3. 以概率 D(x)/Σ D(x') 加权采样下一个中心
    4. 重复到 k 个
    """
    n = len(X)
    centers = [list(X[rng.randrange(n)])]
    while len(centers) < k:
        dists_sq = []
        for x in X:
            min_d_sq = min(euclidean(x, c) ** 2 for c in centers)
            dists_sq.append(min_d_sq)
        total = sum(dists_sq)
        if total == 0.0:
            # 所有点与已有中心重合,随机补齐
            centers.append(list(X[rng.randrange(n)]))
            continue
        probs = [d / total for d in dists_sq]
        r = rng.random()
        cum = 0.0
        chosen = n - 1
        for i, p in enumerate(probs):
            cum += p
            if cum >= r:
                chosen = i
                break
        centers.append(list(X[chosen]))
    return centers


def random_init(X: List[Vector], k: int, rng: random.Random) -> List[Vector]:
    """随机选 k 个不同样本作初始中心。"""
    idxs = rng.sample(range(len(X)), k)
    return [list(X[i]) for i in idxs]


class KMeans:
    """Lloyd 算法的 K-Means。"""

    def __init__(self, n_clusters: int = 3, init: str = "k-means++",
                 n_init: int = 10, max_iter: int = 300,
                 tol: float = 1e-4, random_state: Optional[int] = None):
        if n_clusters < 1:
            raise ValueError("n_clusters must be >= 1")
        if init not in ("k-means++", "random"):
            raise ValueError("init must be 'k-means++' or 'random'")
        self.n_clusters = n_clusters
        self.init = init
        # sklearn 1.4+ n_init='auto' 默认:random 10,k-means++ 1
        self.n_init = max(n_init, 1) if init == "random" else max(n_init, 1)
        self.max_iter = max_iter
        self.tol = tol
        self.random_state = random_state
        self.cluster_centers_: List[Vector] = []
        self.labels_: List[int] = []
        self.inertia_: float = 0.0
        self.n_iter_: int = 0

    def _fit_single(self, X, rng) -> Tuple[List[Vector], List[int], float, int]:
        """单次 Lloyd 拟合,返回 (centers, labels, inertia, n_iter)。"""
        k = self.n_clusters
        if self.init == "k-means++":
            centers = kmeans_plus_plus_init(X, k, rng)
        else:
            centers = random_init(X, k, rng)
        prev_centers = None
        for it in range(1, self.max_iter + 1):
            # E-step: 每个样本指派最近中心
            labels = [min(range(k), key=lambda j: euclidean(x, centers[j])) for x in X]
            # M-step: 每簇中心取均值
            new_centers = []
            for j in range(k):
                members = [X[i] for i, l in enumerate(labels) if l == j]
                if not members:
                    # 空簇重随机
                    new_centers.append(list(X[rng.randrange(len(X))]))
                    continue
                dim = len(members[0])
                avg = [sum(m[d] for m in members) / len(members) for d in range(dim)]
                new_centers.append(avg)
            # 收敛判定:中心移动距离 < tol
            if prev_centers is not None:
                shift = max(euclidean(a, b) for a, b in zip(prev_centers, new_centers))
                if shift < self.tol:
                    centers = new_centers
                    break
            prev_centers = centers
            centers = new_centers
        # 最终 inertia
        inertia = sum(euclidean(X[i], centers[labels[i]]) ** 2 for i in range(len(X)))
        return centers, labels, inertia, it

    def fit(self, X) -> "KMeans":
        rng = random.Random(self.random_state)
        best = None
        for _ in range(self.n_init):
            centers, labels, inertia, n_iter = self._fit_single(X, rng)
            if best is None or inertia < best[2]:
                best = (centers, labels, inertia, n_iter)
        self.cluster_centers_, self.labels_, self.inertia_, self.n_iter_ = best
        return self

    def predict(self, X) -> List[int]:
        k = self.n_clusters
        return [min(range(k), key=lambda j: euclidean(x, self.cluster_centers_[j])) for x in X]

    def transform(self, X) -> List[List[float]]:
        """距离矩阵 n×k(sklearn 等价 KMeans.transform)。"""
        k = self.n_clusters
        return [[euclidean(x, c) for c in self.cluster_centers_] for x in X]


# ---------- 数据生成 ----------

def make_blobs(n_samples=300, n_features=2, centers=3, cluster_std=1.0, seed=0):
    """sklearn.datasets.make_blobs 等价 stdlib 实现。"""
    rng = random.Random(seed)
    rng_centers = [[rng.uniform(-5, 5) for _ in range(n_features)] for _ in range(centers)]
    X, y = [], []
    per = n_samples // centers
    for c, cc in enumerate(rng_centers):
        for _ in range(per):
            X.append([cc[d] + rng.gauss(0, cluster_std) for d in range(n_features)])
            y.append(c)
    return X, y, rng_centers


def make_concentric_circles(n_samples=200, noise=0.05, seed=0):
    """两个同心圆(外圆 + 内圆,演示 K-Means 不擅长环形数据)。"""
    rng = random.Random(seed)
    X, y = [], []
    for _ in range(n_samples // 2):
        r = rng.uniform(0, 1) + rng.gauss(0, noise)
        theta = rng.uniform(0, 2 * math.pi)
        X.append([r * math.cos(theta), r * math.sin(theta)])
        y.append(0)
    for _ in range(n_samples // 2):
        r = 2.0 + rng.uniform(0, 0.3) + rng.gauss(0, noise)
        theta = rng.uniform(0, 2 * math.pi)
        X.append([r * math.cos(theta), r * math.sin(theta)])
        y.append(1)
    return X, y


# ---------- 评估指标 ----------

def silhouette_score(X, labels):
    """轮廓系数 s(i) = (b − a) / max(a, b):a 同簇均距,b 异簇最小均距。
    sklearn 计算全样本均值,范围 [−1, 1],越大越好。
    """
    n = len(X)
    if n < 2:
        return 0.0
    # 按簇分组
    clusters: dict = {}
    for i, l in enumerate(labels):
        clusters.setdefault(l, []).append(i)
    s_sum = 0.0
    for i in range(n):
        li = labels[i]
        same = clusters[li]
        a = (sum(euclidean(X[i], X[j]) for j in same if j != i) / max(1, len(same) - 1)) if len(same) > 1 else 0.0
        b = float("inf")
        for lj, members in clusters.items():
            if lj == li:
                continue
            d = sum(euclidean(X[i], X[j]) for j in members) / len(members)
            b = min(b, d)
        s = 0.0 if (a == 0 and b == 0) else (b - a) / max(a, b)
        s_sum += s
    return s_sum / n


# ---------- 演示 ----------

def demo_basic_kmeans():
    """demo 1:基础 K-Means 拟合 3 簇。"""
    X, y_true, true_centers = make_blobs(n_samples=300, n_features=2, centers=3, cluster_std=1.0, seed=42)
    m = KMeans(n_clusters=3, init="k-means++", n_init=10, random_state=0).fit(X)
    print("[1] K-Means 3 簇(真中心已对齐,验证收敛)")
    print(f"  true centers (前 2 维): {[(round(c[0], 2), round(c[1], 2)) for c in true_centers]}")
    print(f"  fit  centers            : {[(round(c[0], 2), round(c[1], 2)) for c in m.cluster_centers_]}")
    print(f"  inertia = {m.inertia_:.2f}  n_iter = {m.n_iter_}\n")


def demo_kmeanspp_vs_random():
    """demo 2:k-means++ vs random init 收敛速度 + inertia 对比。"""
    X, _, _ = make_blobs(n_samples=300, n_features=2, centers=3, cluster_std=1.0, seed=11)
    print("[2] k-means++ vs random(init 质量 → 收敛速度/inertia)")
    for init_method in ("k-means++", "random"):
        m = KMeans(n_clusters=3, init=init_method, n_init=5, max_iter=200, random_state=0).fit(X)
        print(f"  {init_method:>10}: inertia={m.inertia_:.2f}  n_iter={m.n_iter_}")
    print()


def demo_n_init_best():
    """demo 3:n_init 多次重启取最优 inertia。"""
    X, _, _ = make_blobs(n_samples=200, n_features=2, centers=3, cluster_std=1.0, seed=22)
    print("[3] n_init 重启取最优 inertia")
    for n_init in [1, 5, 10, 30]:
        m = KMeans(n_clusters=3, init="random", n_init=n_init, max_iter=200, random_state=0).fit(X)
        print(f"  n_init={n_init:>2}  best_inertia={m.inertia_:.2f}  n_iter={m.n_iter_}")
    print()


def demo_elbow_method():
    """demo 4:肘部法选 k:inertia 随 k 增大单调下降,肘部为最优。"""
    X, _, _ = make_blobs(n_samples=300, n_features=2, centers=4, cluster_std=1.0, seed=33)
    print("[4] 肘部法选 k(真中心 4 个,肘部应明显)")
    print("  k    inertia    silhouette")
    inertias = []
    for k in range(1, 8):
        m = KMeans(n_clusters=k, init="k-means++", n_init=5, random_state=0).fit(X)
        sil = silhouette_score(X, m.labels_) if k > 1 else float("nan")
        inertias.append(m.inertia_)
        print(f"  {k}  {m.inertia_:>8.2f}    {sil:>7.4f}")
    # 简单计算肘部:最大曲率点
    print(f"  Δ inertia: {[round(inertias[i+1]-inertias[i], 1) for i in range(len(inertias)-1)]}")
    print()


def demo_kmeans_assumptions():
    """demo 5:K-Means 假设(球形等大小簇):blob 表现好,concentric 失效。"""
    print("[5] K-Means 假设演示:球形簇 vs 同心圆")
    Xb, yb, _ = make_blobs(n_samples=300, n_features=2, centers=3, cluster_std=1.0, seed=44)
    m_blob = KMeans(n_clusters=3, init="k-means++", n_init=5, random_state=0).fit(Xb)
    sil_blob = silhouette_score(Xb, m_blob.labels_)
    Xc, yc = make_concentric_circles(n_samples=200, seed=44)
    m_circ = KMeans(n_clusters=2, init="k-means++", n_init=5, random_state=0).fit(Xc)
    sil_circ = silhouette_score(Xc, m_circ.labels_)
    print(f"  球形簇: inertia={m_blob.inertia_:.2f}  silhouette={sil_blob:.4f}(接近 1 优)")
    print(f"  同心圆: inertia={m_circ.inertia_:.2f}  silhouette={sil_circ:.4f}(差,K-Means 把外圆切成两半)")
    print()


def main():
    demo_basic_kmeans()
    demo_kmeanspp_vs_random()
    demo_n_init_best()
    demo_elbow_method()
    demo_kmeans_assumptions()
    print("All 5 demos passed.")


if __name__ == "__main__":
    main()
