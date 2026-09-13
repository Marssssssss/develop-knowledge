"""决策树 CART 分类:基尼不纯度 + 贪心穷举 + 预剪枝。

权威来源:
- scikit-learn DecisionTreeClassifier https://scikit-learn.org/1.0/modules/generated/sklearn.tree.DecisionTreeClassifier.html
  criterion{gini, entropy},splitter{best, random},默认 gini
  Gini = 1 − Σ p_k²,Entropy = −Σ p_k log₂ p_k
  预剪枝:max_depth / min_samples_split / min_samples_leaf / max_features
  sklearn 实现优化版 CART(Breiman 1984):只二叉树、不计算规则集
  复杂度 O(n_features · n_samples² log n_samples)(平衡假设)
  ID3(1986 多叉)/C4.5(连续变量离散化+规则)/C5.0/CART(二叉回归+分类)

纯 stdlib,递归实现 + 5 demo:基本训练 / 预剪枝参数对比 / 基尼 vs 熵 /
深度/叶数过拟合度量 / 综合 grid 调优。
"""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import List, Optional, Sequence, Tuple


def gini(labels: Sequence[int]) -> float:
    """Gini 不纯度 = 1 − Σ p_k²。"""
    n = len(labels)
    if n == 0:
        return 0.0
    cnt = Counter(labels)
    return 1.0 - sum((c / n) ** 2 for c in cnt.values())


def entropy(labels: Sequence[int]) -> float:
    """信息熵 H = −Σ p_k log₂ p_k。"""
    n = len(labels)
    if n == 0:
        return 0.0
    cnt = Counter(labels)
    return -sum((c / n) * math.log2(c / n) for c in cnt.values() if c > 0)


def majority(labels: Sequence[int]) -> int:
    return Counter(labels).most_common(1)[0][0]


class Node:
    __slots__ = ("feature", "threshold", "left", "right", "label", "n_samples", "impurity")

    def __init__(self, feature=None, threshold=None, left=None, right=None,
                 label=None, n_samples=0, impurity=0.0):
        self.feature = feature
        self.threshold = threshold
        self.left = left
        self.right = right
        self.label = label
        self.n_samples = n_samples
        self.impurity = impurity

    def is_leaf(self):
        return self.label is not None


class DecisionTreeClassifier:
    """CART 分类树:基尼不纯度 + 贪心穷举 (feature, threshold) 找最优二分。"""

    def __init__(self, max_depth=None, min_samples_split=2, min_samples_leaf=1,
                 max_features=None, criterion="gini", random_state=None):
        if criterion not in ("gini", "entropy"):
            raise ValueError("criterion must be 'gini' or 'entropy'")
        self.max_depth = max_depth
        self.min_samples_split = min_samples_split
        self.min_samples_leaf = min_samples_leaf
        self.max_features = max_features
        self.criterion = criterion
        self.random_state = random_state
        self.tree_: Optional[Node] = None
        self.n_features_: int = 0
        self._impurity = gini if criterion == "gini" else entropy

    def fit(self, X, y):
        self.n_features_ = len(X[0])
        rng = random.Random(self.random_state)
        sorted_idx = [sorted(range(len(X)), key=lambda i: X[i][j])
                      for j in range(self.n_features_)]
        self.tree_ = self._grow(X, y, sorted_idx, depth=0, rng=rng)
        return self

    def _grow(self, X, y, sorted_idx, depth, rng):
        n = len(y)
        cur_impurity = self._impurity(y)
        if ((self.max_depth is not None and depth >= self.max_depth)
                or n < self.min_samples_split or cur_impurity == 0.0):
            return Node(label=majority(y), n_samples=n, impurity=cur_impurity)
        feats = list(range(self.n_features_))
        if self.max_features is not None and self.max_features < self.n_features_:
            feats = rng.sample(feats, self.max_features)
        best_gain, best = 0.0, None
        for j in feats:
            order = sorted_idx[j]
            for k in range(len(order) - 1):
                i, i_next = order[k], order[k + 1]
                if X[i][j] == X[i_next][j]:
                    continue
                thr = (X[i][j] + X[i_next][j]) / 2.0
                left_idx = order[: k + 1]
                right_idx = order[k + 1 :]
                if (len(left_idx) < self.min_samples_leaf
                        or len(right_idx) < self.min_samples_leaf):
                    continue
                weighted = (len(left_idx) / n) * self._impurity([y[i] for i in left_idx]) + \
                           (len(right_idx) / n) * self._impurity([y[i] for i in right_idx])
                gain = cur_impurity - weighted
                if gain > best_gain:
                    best_gain = gain
                    best = (j, thr, left_idx, right_idx)
        if best is None:
            return Node(label=majority(y), n_samples=n, impurity=cur_impurity)
        j, thr, left_idx, right_idx = best
        # 递归建子树
        left_X = [X[i] for i in left_idx]
        right_X = [X[i] for i in right_idx]
        left_y = [y[i] for i in left_idx]
        right_y = [y[i] for i in right_idx]
        ls = [sorted(range(len(left_X)), key=lambda ii: left_X[ii][jj])
              for jj in range(self.n_features_)]
        rs = [sorted(range(len(right_X)), key=lambda ii: right_X[ii][jj])
              for jj in range(self.n_features_)]
        return Node(
            feature=j, threshold=thr,
            left=self._grow(left_X, left_y, ls, depth + 1, rng),
            right=self._grow(right_X, right_y, rs, depth + 1, rng),
            n_samples=n, impurity=cur_impurity,
        )

    def predict_one(self, row):
        node = self.tree_
        while not node.is_leaf():
            node = node.left if row[node.feature] <= node.threshold else node.right
        return node.label

    def predict(self, X):
        return [self.predict_one(row) for row in X]

    def get_depth(self):
        def _d(n):
            if n is None or n.is_leaf():
                return 0
            return 1 + max(_d(n.left), _d(n.right))
        return _d(self.tree_)

    def count_leaves(self):
        def _c(n):
            if n is None:
                return 0
            if n.is_leaf():
                return 1
            return _c(n.left) + _c(n.right)
        return _c(self.tree_)


# ---------- 数据生成 ----------

def make_iris_like(n_samples=150, seed=0):
    rng = random.Random(seed)
    centers = [(0, 0), (2, 1.5), (-2, 1.5)]
    X, y = [], []
    n_per = n_samples // 3
    for c, (cx, cy) in enumerate(centers):
        for _ in range(n_per):
            X.append([cx + rng.gauss(0, 0.6), cy + rng.gauss(0, 0.6)])
            y.append(c)
    return X, y


def train_test_split(X, y, test_ratio=0.3, seed=0):
    rng = random.Random(seed)
    idx = list(range(len(X)))
    rng.shuffle(idx)
    cut = int(len(X) * (1 - test_ratio))
    Xt = [X[i] for i in idx[:cut]]
    yt = [y[i] for i in idx[:cut]]
    Xv = [X[i] for i in idx[cut:]]
    yv = [y[i] for i in idx[cut:]]
    return Xt, yt, Xv, yv


def accuracy(yt, yp):
    return sum(int(a == b) for a, b in zip(yt, yp)) / len(yt) if yt else 0.0


# ---------- 演示 ----------

def demo_basic_fit():
    X, y = make_iris_like(n_samples=150, seed=42)
    Xt, yt, Xv, yv = train_test_split(X, y, seed=1)
    m = DecisionTreeClassifier(max_depth=5, random_state=0).fit(Xt, yt)
    print("[1] CART 训练(默认基尼 + 预剪枝 max_depth=5)")
    print(f"  train acc = {accuracy(yt, m.predict(Xt)):.4f}")
    print(f"  test  acc = {accuracy(yv, m.predict(Xv)):.4f}")
    print(f"  tree depth = {m.get_depth()}  leaves = {m.count_leaves()}\n")


def demo_pre_pruning_sweep():
    X, y = make_iris_like(seed=7)
    Xt, yt, Xv, yv = train_test_split(X, y, seed=2)
    print("[2] 预剪枝参数对比(深度 ↑ → 过拟合)")
    print("  max_depth  min_leaf  train_acc  test_acc  leaves")
    for d in [1, 2, 3, 5, 10]:
        for msl in [1, 5, 10]:
            m = DecisionTreeClassifier(max_depth=d, min_samples_leaf=msl, random_state=0).fit(Xt, yt)
            print(f"  {d:>3}        {msl:>3}      {accuracy(yt, m.predict(Xt)):.4f}     "
                  f"{accuracy(yv, m.predict(Xv)):.4f}    {m.count_leaves()}")
    print()


def demo_gini_vs_entropy():
    X, y = make_iris_like(seed=11)
    Xt, yt, Xv, yv = train_test_split(X, y, seed=3)
    m_g = DecisionTreeClassifier(criterion="gini", max_depth=5, random_state=0).fit(Xt, yt)
    m_e = DecisionTreeClassifier(criterion="entropy", max_depth=5, random_state=0).fit(Xt, yt)
    print("[3] Gini vs Entropy 同一数据对比")
    print(f"  Gini:    train={accuracy(yt, m_g.predict(Xt)):.4f}  test={accuracy(yv, m_g.predict(Xv)):.4f}")
    print(f"  Entropy: train={accuracy(yt, m_e.predict(Xt)):.4f}  test={accuracy(yv, m_e.predict(Xv)):.4f}")
    print(f"  等分 (5/5) Gini={gini([0]*5+[1]*5):.4f}  Entropy={entropy([0]*5+[1]*5):.4f}\n")


def demo_pure_vs_noisy():
    rng = random.Random(13)
    X, y = make_iris_like(n_samples=150, seed=99)
    y_noisy = y[:]
    n_flip = len(y) // 10
    for i in rng.sample(range(len(y)), n_flip):
        choices = [c for c in (0, 1, 2) if c != y_noisy[i]]
        y_noisy[i] = rng.choice(choices)
    Xt, yt, Xv, yv = train_test_split(X, y_noisy, seed=4)
    m_pure = DecisionTreeClassifier(random_state=0).fit(Xt, yt)
    print("[4] 10% 标签噪声 → 树深度/叶数激增(过拟合度量)")
    print(f"  噪 train acc={accuracy(yt, m_pure.predict(Xt)):.4f}  test acc={accuracy(yv, m_pure.predict(Xv)):.4f}")
    print(f"  depth={m_pure.get_depth()}  leaves={m_pure.count_leaves()}\n")


def demo_grid_tuning():
    X, y = make_iris_like(seed=17)
    Xt, yt, Xv, yv = train_test_split(X, y, seed=5)
    print("[5] Grid 调优(等价 sklearn GridSearchCV 简化版)")
    print("  depth  min_split  min_leaf  test_acc")
    best_acc, best_params = 0.0, None
    for d in [3, 4, 5, 7]:
        for mss in [2, 5, 10]:
            for msl in [1, 3, 5]:
                m = DecisionTreeClassifier(max_depth=d, min_samples_split=mss,
                                          min_samples_leaf=msl, random_state=0).fit(Xt, yt)
                a = accuracy(yv, m.predict(Xv))
                if a > best_acc:
                    best_acc, best_params = a, (d, mss, msl)
                print(f"  {d:>3}    {mss:>3}        {msl:>3}      {a:.4f}")
    print(f"  best: depth={best_params[0]} min_split={best_params[1]} "
          f"min_leaf={best_params[2]}  test_acc={best_acc:.4f}\n")


def main():
    demo_basic_fit()
    demo_pre_pruning_sweep()
    demo_gini_vs_entropy()
    demo_pure_vs_noisy()
    demo_grid_tuning()
    print("All 5 demos passed.")


if __name__ == "__main__":
    main()
