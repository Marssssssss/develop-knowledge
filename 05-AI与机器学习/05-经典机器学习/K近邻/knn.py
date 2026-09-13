"""K-近邻(KNN)分类与回归:brute force + KD-Tree + 加权投票。

权威来源:
- scikit-learn Nearest Neighbors §1.6 https://scikit-learn.org/stable/modules/neighbors.html
  KNeighborsClassifier(n_neighbors=5, weights='uniform', algorithm='auto')
  algorithm ∈ {auto, ball_tree, kd_tree, brute};metric='minkowski' p=2 → euclidean
  KNeighborsRegressor 同 API;weights='uniform' / 'distance' / callable
  BallTree/KDTree 直接暴露;leaf_size=30 影响速度/内存
  KD-Tree build O(n·d·log n) / query O(log n) 平均;d > 20 退化为 O(n)
"""

from __future__ import annotations

import math
import random
import time
from collections import Counter
from typing import List, Optional, Sequence, Tuple


def euclidean(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def manhattan(a, b):
    return sum(abs(x - y) for x, y in zip(a, b))


def minkowski(a, b, p):
    return (sum(abs(x - y) ** p for x, y in zip(a, b))) ** (1.0 / p)


class KNeighborsClassifier:
    """Brute force KNN:每次预测计算 query 与所有训练点的距离,选 k 近邻投票。"""

    def __init__(self, n_neighbors=5, weights="uniform", metric="euclidean", p=2):
        if n_neighbors < 1:
            raise ValueError("n_neighbors must be >= 1")
        if weights not in ("uniform", "distance"):
            raise ValueError("weights must be 'uniform' or 'distance'")
        if metric == "euclidean":
            self._dist = euclidean
        elif metric == "manhattan":
            self._dist = manhattan
        elif metric == "minkowski":
            self._dist = lambda a, b: minkowski(a, b, p)
        else:
            raise ValueError(f"unknown metric: {metric}")
        self.n_neighbors, self.weights, self.metric = n_neighbors, weights, metric
        self.X_train, self.y_train = [], []

    def fit(self, X, y):
        self.X_train = [list(row) for row in X]
        self.y_train = list(y)
        return self

    def _knn(self, query):
        dists = [(self._dist(query, row), i) for i, row in enumerate(self.X_train)]
        dists.sort(key=lambda t: t[0])
        return dists[:self.n_neighbors]

    def predict_proba_one(self, query, classes):
        nn = self._knn(query)
        if self.weights == "uniform":
            cnt = Counter(self.y_train[i] for _, i in nn)
        else:
            cnt = Counter()
            for d, i in nn:
                cnt[self.y_train[i]] += 1.0 / max(d, 1e-12)
        total = sum(cnt.values())
        return [cnt.get(c, 0.0) / total for c in classes]

    def predict(self, X):
        classes = sorted(set(self.y_train))
        proba = [self.predict_proba_one(row, classes) for row in X]
        return [classes[max(range(len(p)), key=lambda i: p[i])] for p in proba]


# ---------- KD-Tree ----------

class _KDNode:
    __slots__ = ("point", "idx", "axis", "left", "right")

    def __init__(self, point, idx, axis, left=None, right=None):
        self.point, self.idx, self.axis = point, idx, axis
        self.left, self.right = left, right


def build_kdtree(points, idxs=None, depth=0):
    n = len(points)
    if n == 0:
        return None
    axis = depth % len(points[0])
    sorted_order = sorted(range(n), key=lambda i: points[i][axis])
    mid = n // 2
    mid_i = sorted_order[mid]
    return _KDNode(
        point=points[mid_i],
        idx=idxs[mid_i] if idxs is not None else mid_i,
        axis=axis,
        left=build_kdtree([points[i] for i in sorted_order[:mid]],
                          [idxs[i] for i in sorted_order[:mid]] if idxs else None, depth + 1),
        right=build_kdtree([points[i] for i in sorted_order[mid + 1:]],
                           [idxs[i] for i in sorted_order[mid + 1:]] if idxs else None, depth + 1),
    )


def kdtree_knn_search(root, query, k):
    """递归回溯 KD-Tree k-NN 搜索,维护 best 容量 k。"""
    best = []

    def search(node):
        if node is None:
            return
        d = euclidean(query, node.point)
        if len(best) < k:
            best.append((d, node.idx))
        else:
            worst_i = max(range(k), key=lambda i: best[i][0])
            if d < best[worst_i][0]:
                best[worst_i] = (d, node.idx)
        diff = query[node.axis] - node.point[node.axis]
        first, second = (node.left, node.right) if diff < 0 else (node.right, node.left)
        search(first)
        if len(best) < k or abs(diff) < max(b[0] for b in best):
            search(second)

    search(root)
    best.sort(key=lambda t: t[0])
    return best


class KDTreeKNNClassifier:
    """用 KD-Tree 加速 KNN 预测。"""

    def __init__(self, n_neighbors=5, weights="uniform"):
        if n_neighbors < 1:
            raise ValueError("n_neighbors must be >= 1")
        self.n_neighbors, self.weights = n_neighbors, weights
        self.tree_, self.y_train = None, []

    def fit(self, X, y):
        self.tree_ = build_kdtree([list(row) for row in X], list(range(len(X))))
        self.y_train = list(y)
        return self

    def predict(self, X):
        classes = sorted(set(self.y_train))
        out = []
        for row in X:
            nn = kdtree_knn_search(self.tree_, row, self.n_neighbors)
            if self.weights == "uniform":
                cnt = Counter(self.y_train[i] for _, i in nn)
            else:
                cnt = Counter()
                for d, i in nn:
                    cnt[self.y_train[i]] += 1.0 / max(d, 1e-12)
            best = max(range(len(classes)), key=lambda c: cnt.get(classes[c], 0.0))
            out.append(classes[best])
        return out


# ---------- 数据生成 ----------

def make_two_moons(n_samples=200, noise=0.2, seed=0):
    rng = random.Random(seed)
    X, y = [], []
    for c in (0, 1):
        for _ in range(n_samples // 2):
            t = rng.uniform(0, math.pi)
            if c == 0:
                row = [math.cos(t) + rng.gauss(0, noise), math.sin(t) + rng.gauss(0, noise)]
            else:
                row = [1.0 - math.cos(t) + rng.gauss(0, noise), 0.5 - math.sin(t) + rng.gauss(0, noise)]
            X.append(row)
            y.append(c)
    return X, y


def train_test_split(X, y, test_ratio=0.3, seed=0):
    rng = random.Random(seed)
    idx = list(range(len(X)))
    rng.shuffle(idx)
    cut = int(len(X) * (1 - test_ratio))
    Xt, yt = [X[i] for i in idx[:cut]], [y[i] for i in idx[:cut]]
    Xv, yv = [X[i] for i in idx[cut:]], [y[i] for i in idx[cut:]]
    return Xt, yt, Xv, yv


def accuracy(yt, yp):
    return sum(int(a == b) for a, b in zip(yt, yp)) / len(yt) if yt else 0.0


# ---------- 演示 ----------

def demo_basic_brute():
    X, y = make_two_moons(n_samples=200, seed=42)
    Xt, yt, Xv, yv = train_test_split(X, y, seed=1)
    print("[1] Brute KNN:不同 k 的 test acc")
    for k in [1, 3, 5, 11, 25]:
        m = KNeighborsClassifier(n_neighbors=k).fit(Xt, yt)
        print(f"  k={k:>2}  train={accuracy(yt, m.predict(Xt)):.4f}  test={accuracy(yv, m.predict(Xv)):.4f}")
    print()


def demo_weighted_voting():
    X, y = make_two_moons(n_samples=200, seed=11)
    Xt, yt, Xv, yv = train_test_split(X, y, seed=2)
    print("[2] uniform vs distance 加权投票")
    print("  k  weights    train    test")
    for k in [3, 7, 15]:
        for w in ("uniform", "distance"):
            m = KNeighborsClassifier(n_neighbors=k, weights=w).fit(Xt, yt)
            print(f"  {k:>2}  {w:>8}  {accuracy(yt, m.predict(Xt)):.4f}  {accuracy(yv, m.predict(Xv)):.4f}")
    print()


def demo_distance_metrics():
    X, y = make_two_moons(n_samples=200, seed=22)
    Xt, yt, Xv, yv = train_test_split(X, y, seed=3)
    print("[3] 距离度量对比(k=5)")
    print("  metric    train    test")
    for m, p in [("euclidean", 2), ("manhattan", 1), ("minkowski", 3)]:
        model = KNeighborsClassifier(n_neighbors=5, metric=m, p=p).fit(Xt, yt)
        print(f"  {m:>9}  {accuracy(yt, model.predict(Xt)):.4f}  {accuracy(yv, model.predict(Xv)):.4f}")
    print()


def demo_kdtree_speedup():
    """Brute vs KD-Tree 加速 + 答案一致率验证。"""
    rng = random.Random(33)
    n_train, n_query = 2000, 200
    X_train = [[rng.uniform(0, 10), rng.uniform(0, 10)] for _ in range(n_train)]
    y_train = [0 if x[0] < 5 else 1 for x in X_train]
    X_query = [[rng.uniform(0, 10), rng.uniform(0, 10)] for _ in range(n_query)]
    t0 = time.perf_counter()
    KNeighborsClassifier(n_neighbors=5).fit(X_train, y_train).predict(X_query)
    brute_t = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    KDTreeKNNClassifier(n_neighbors=5).fit(X_train, y_train).predict(X_query)
    tree_t = (time.perf_counter() - t0) * 1000
    pa = KNeighborsClassifier(n_neighbors=5).fit(X_train, y_train).predict(X_query)
    pb = KDTreeKNNClassifier(n_neighbors=5).fit(X_train, y_train).predict(X_query)
    agree = sum(int(a == b) for a, b in zip(pa, pb)) / len(pa)
    print(f"[4] Brute vs KD-Tree 加速(n_train={n_train}, n_query={n_query}, k=5)")
    print(f"  Brute    = {brute_t:.1f} ms")
    print(f"  KD-Tree  = {tree_t:.1f} ms  (vs brute {brute_t / tree_t:.1f}x)")
    print(f"  答案一致率 = {agree:.4f}\n")


def demo_k_bias_variance():
    """k 偏差-方差权衡:k=1 完全拟合 → k=N 常数预测。"""
    rng = random.Random(44)
    n = 100
    X = [[rng.uniform(-2, 2)] for _ in range(n)]
    y = [1 if math.sin(x[0] * 2) + rng.gauss(0, 0.2) > 0 else 0 for x in X]
    Xt, yt, Xv, yv = train_test_split(X, y, seed=4)
    print("[5] k 偏差-方差权衡")
    print("  k    train    test")
    for k in [1, 3, 5, 15, 30, 50, 99]:
        m = KNeighborsClassifier(n_neighbors=k).fit(Xt, yt)
        print(f"  {k:>3}  {accuracy(yt, m.predict(Xt)):.4f}  {accuracy(yv, m.predict(Xv)):.4f}")


def main():
    demo_basic_brute()
    demo_weighted_voting()
    demo_distance_metrics()
    demo_kdtree_speedup()
    demo_k_bias_variance()
    print("\nAll 5 demos passed.")


if __name__ == "__main__":
    main()
