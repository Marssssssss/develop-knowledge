"""随机森林(纯 stdlib):bootstrap 自助采样 + 逐节点随机特征子集 + OOB 估计 + 变量重要性。

权威来源(实际读过):
- L. Breiman (2001)《Random Forests》 https://www.stat.berkeley.edu/~breiman/randomforest2001.pdf
  Def.1.1 一组树 {h(x,Θk)},{Θk} 独立同分布;每棵树在 x 投一票
  边缘函数  mg(X,Y) = av_k I(h_k(X)=Y) − max_{j≠Y} av_k I(h_k(X)=j)   (2)
  泛化误差  PE* = P_{X,Y}(mg(X,Y) < 0)                                 (1)
  定理 2.3  PE* ≤ ρ̄·(1−s²)/s²,s = E_{X,Y}·mr(X,Y) 为强度(3),ρ̄ 为树间平均相关(7)
           "large number of trees ⇒ 依强大数律收敛,∴ 加树不会过拟合"
  §3.1 OOB:对每个训练样本 (x,y) **只在 bootstrap 集不含 (x,y) 的树上聚合投票**,得到
       out-of-bag 分类器,其在训练集上的错误率即 OOB 误差;每个 bootstrap 集平均留下约
       1/3 样本在外 ⇒ OOB 只用约 1/3 的树,故**略高估**当前误差,但估计本身**无偏**
       (对照:交叉验证有偏差且幅度未知)
  §3 随机特征:at a given node, F variables are randomly selected;F=1 或 F=int(log2 M +1)
  §10 变量重要性:把某变量逐列打乱后看 OOB 误分类比例的上升
- scikit-learn 1.9《1.11. Ensembles》 https://scikit-learn.org/stable/modules/ensemble.html
  随机森林在**每个分裂点**只看 max_features 个随机特征;分类默认 "sqrt"(=√n_features),
  回归默认 1.0/None(即 bagged trees);常用 max_depth=None + min_samples_split=2 长满
  n_estimators 越大越好,超过某临界值后不再显著改善
  **sklearn 与原文的差异**:sklearn 对概率预测取平均,而不是让每棵树投单一类别的票
  Extra-Trees 默认 bootstrap=False(用整个数据集),分裂阈值也随机
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Sequence


def gini(labels: Sequence[int]) -> float:
    """不纯度 1 − Σ p_k²。"""
    n = len(labels)
    if n == 0:
        return 0.0
    return 1.0 - sum((v / n) ** 2 for v in Counter(labels).values())


class Rng:
    """线性同余(避免依赖 random 模块的实现细节,保证跨版本可复现)。"""

    def __init__(self, s):
        self.s = s & 0xFFFFFFFFFFFFFFFF

    def next(self):
        self.s = (self.s * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return self.s >> 11

    def randrange(self, n):
        return self.next() % n

    def sample(self, pop, k):
        pool, out = list(pop), []
        for _ in range(k):
            out.append(pool.pop(self.randrange(len(pool))))
        return out

    def uniform(self, lo, hi):
        return lo + (hi - lo) * self.next() / float(1 << 53)


class Tree:
    def __init__(self, classes, max_depth, min_samples_leaf, max_features, rng):
        self.classes, self.max_depth = classes, max_depth
        self.min_leaf, self.max_features, self.rng = min_samples_leaf, max_features, rng

    def fit(self, X, y):
        self.n_features, self.n = len(X[0]), len(X)
        self.impurity_drop = {f: 0.0 for f in range(self.n_features)}
        self.root = self._build(list(range(self.n)), X, y, 0)
        return self

    def _leaf(self, idx, y):
        c = Counter(y[i] for i in idx)
        return {"leaf": max(self.classes, key=lambda k: (c.get(k, 0), -k)),
                "p": {k: c.get(k, 0) / len(idx) for k in self.classes}}

    def _features(self) -> List[int]:
        """Breiman §3 / sklearn:每个分裂点抽 max_features 个特征,而非全用。"""
        if self.max_features is None or self.max_features >= self.n_features:
            return list(range(self.n_features))
        return self.rng.sample(range(self.n_features), self.max_features)

    def _build(self, idx, X, y, depth):
        if len({y[i] for i in idx}) == 1 or depth >= self.max_depth \
                or len(idx) < 2 * self.min_leaf:
            return self._leaf(idx, y)
        best = self._best_split(idx, X, y)
        if best is None:
            return self._leaf(idx, y)
        f, thr = best
        left = [i for i in idx if X[i][f] <= thr]
        if not left or len(left) == len(idx):
            return self._leaf(idx, y)
        right = [i for i in idx if X[i][f] > thr]
        return {"f": f, "thr": thr, "left": self._build(left, X, y, depth + 1),
                "right": self._build(right, X, y, depth + 1)}

    def _best_split(self, idx, X, y):
        """贪心穷举特征子集上的阈值,取加权基尼下降最大者;一次排序 + 前缀计数扫描。"""
        n_idx = len(idx)
        parent = gini([y[i] for i in idx])
        total = Counter(y[i] for i in idx)
        best_gain, best = 1e-12, None
        for f in self._features():
            pairs = sorted((X[i][f], y[i]) for i in idx)
            left: Counter = Counter()
            for k in range(n_idx - 1):
                left[pairs[k][1]] += 1
                if pairs[k][0] == pairs[k + 1][0]:            # 同值不可切
                    continue
                nl = k + 1
                if nl < self.min_leaf or n_idx - nl < self.min_leaf:
                    continue
                nr = n_idx - nl
                gl = 1.0 - sum((v / nl) ** 2 for v in left.values())
                gr = 1.0 - sum(((total[c] - left.get(c, 0)) / nr) ** 2 for c in total)
                gain = parent - (nl / n_idx) * gl - (nr / n_idx) * gr
                if gain > best_gain:
                    best_gain, best = gain, (f, 0.5 * (pairs[k][0] + pairs[k + 1][0]))
        if best is not None:
            self.impurity_drop[best[0]] += n_idx / self.n * best_gain
        return best

    def proba(self, x) -> Dict[int, float]:
        node = self.root
        while "leaf" not in node:
            node = node["left"] if x[node["f"]] <= node["thr"] else node["right"]
        return node["p"]


class RandomForest:
    def __init__(self, n_estimators=50, max_features="sqrt", max_depth=12,
                 min_samples_leaf=1, bootstrap=True, split="proba", seed=0):
        self.n_estimators, self.max_features = n_estimators, max_features
        self.max_depth, self.min_leaf = max_depth, min_samples_leaf
        self.bootstrap, self.split, self.seed = bootstrap, split, seed

    def fit(self, X, y):
        self.X, self.y = [list(r) for r in X], list(y)
        self.classes = sorted(set(y))
        n, d = len(X), len(X[0])
        mf = {"sqrt": max(1, int(math.sqrt(d))), "log2": max(1, int(math.log2(d))),
              None: d}.get(self.max_features, self.max_features)
        self.trees, self.in_bag = [], []
        for t in range(self.n_estimators):
            rng = Rng(self.seed * 1000003 + t)
            idx = [rng.randrange(n) for _ in range(n)] if self.bootstrap else list(range(n))
            self.in_bag.append(set(idx))
            self.trees.append(Tree(self.classes, self.max_depth, self.min_leaf, mf, rng)
                              .fit([X[i] for i in idx], [y[i] for i in idx]))
        # "样本 i 在哪些树上属于袋外"——OOB 投票与置换重要性都要反复用,预计算一次
        self.oob_trees = [[t for t in range(self.n_estimators) if i not in self.in_bag[t]]
                          for i in range(n)]
        self._oob_votes()
        return self

    # -- OOB(Breiman §3.1)--------------------------------------------------- #
    def _oob_votes(self):
        n = len(self.X)
        self.oob_proba: List = [None] * n
        for i in range(n):
            vs = self.oob_trees[i]                       # 只用"没见过 i"的那些树
            if not vs:
                continue
            self.oob_proba[i] = self._aggregate(self.X[i], vs)
        usable = [i for i in range(n) if self.oob_proba[i]]
        self.oob_pred = {i: max(self.classes, key=lambda c: self.oob_proba[i][c]) for i in usable}
        self.oob_score_ = (sum(1 for i in usable if self.oob_pred[i] == self.y[i]) / len(usable)
                           if usable else float("nan"))
        # 边缘函数 mg(X,Y) = P_Θ(h=Y) − max_{j≠Y} P_Θ(h=j)
        self.margin = {i: self.oob_proba[i][self.y[i]]
                       - max(self.oob_proba[i][c] for c in self.classes if c != self.y[i])
                       for i in usable}
        self.strength = sum(self.margin.values()) / len(self.margin) if self.margin else 0.0

    def oob_error(self) -> float:
        return 1.0 - self.oob_score_

    def _aggregate(self, x, trees) -> Dict[int, float]:
        agg, cnt = {c: 0.0 for c in self.classes}, 0
        for t in trees:
            p = self.trees[t].proba(x)
            cnt += 1
            for c in self.classes:
                agg[c] += p[c]
        return {c: v / cnt for c, v in agg.items()}

    def predict(self, X):
        if self.split == "vote":                        # 原论文:每棵树投单一类别
            out = []
            for x in X:
                votes = Counter(max(self.classes, key=lambda k: t.proba(x)[k]) for t in self.trees)
                out.append(max(self.classes, key=lambda c: votes.get(c, 0)))
            return out
        return [max(self.classes, key=lambda c: p[c])                     # sklearn:概率平均
                for p in (self._aggregate(x, range(self.n_estimators)) for x in X)]

    # -- 变量重要性 ---------------------------------------------------------- #
    def permutation_importance(self, reps=5):
        """Breiman §10:逐列打乱后 OOB 误分类比例的相对上升(负值=该列无贡献)。"""
        idxs = [i for i in range(len(self.X)) if self.oob_proba[i]]
        base_bad = sum(1 for i in idxs if self.oob_pred[i] != self.y[i])
        rng, imp = Rng(self.seed + 987654321), {}
        for f in range(len(self.X[0])):
            tot = 0.0
            for _ in range(reps):
                order = list(idxs)
                for k in range(len(order) - 1, 0, -1):                # Fisher–Yates
                    s = rng.randrange(k + 1)
                    order[k], order[s] = order[s], order[k]
                swap = {idxs[k]: order[k] for k in range(len(idxs))}
                bad = 0
                for i in idxs:
                    x = list(self.X[i])
                    x[f] = self.X[swap[i]][f]                          # 第 f 列换成别的样本的值
                    if max(self.classes,
                           key=lambda c: self._aggregate(x, self.oob_trees[i])[c]) != self.y[i]:
                        bad += 1
                tot += (bad - base_bad) / len(idxs)
            imp[f] = tot / reps
        return imp

    def gini_importance(self):
        tot = {f: sum(t.impurity_drop[f] for t in self.trees) for f in range(len(self.X[0]))}
        s = sum(tot.values()) or 1.0
        return {f: v / s for f, v in tot.items()}


def make_data(n=240, seed=20260914):
    """y = 1 当 f0²+f1² < 1(圆内);f2/f3 是纯噪声特征。"""
    rng = Rng(seed)
    X, y = [], []
    for _ in range(n):
        f0, f1 = rng.uniform(-2, 2), rng.uniform(-2, 2)
        X.append([f0, f1, rng.uniform(-2, 2), rng.uniform(-2, 2)])
        y.append(1 if f0 * f0 + f1 * f1 < 1.0 else 0)
    return X, y


def accuracy(yt, yp) -> float:
    return sum(a == b for a, b in zip(yt, yp)) / len(yt)


def main() -> None:
    X, y = make_data()
    Xtr, ytr, Xte, yte = X[:-60], y[:-60], X[-60:], y[-60:]
    acc = lambda m: accuracy(yte, m.predict(Xte))                          # noqa: E731
    fmt = lambda v: "nan" if v != v else f"{v:.4f}"                        # noqa: E731
    print("=" * 74)
    print("随机森林(Breiman 2001):bootstrap + 随机特征子集 + OOB")
    print("=" * 74)

    rf = RandomForest(n_estimators=120, max_features="sqrt", seed=7).fit(Xtr, ytr)
    print(f"\n① 基线:训练 {len(Xtr)} / 测试 {len(Xte)},特征 4(f0,f1 有效;f2,f3 纯噪声)")
    print(f"   测试准确率 = {acc(rf):.4f}   OOB 准确率 = {rf.oob_score_:.4f}"
          f"(OOB 误差 {rf.oob_error():.4f})")
    print(f"   强度 s = E·mg(X,Y) = {rf.strength:.4f}  (定理 2.3 上界 ρ̄(1−s²)/s²,s 越大越好)")

    print("\n② 树数收敛性:依强大数律加树不过拟合,但收益递减")
    print(f"   {'#trees':>7} {'OOB误差':>9} {'测试误差':>9} {'强度 s':>8}")
    for nt in (1, 5, 20, 60, 120, 300):
        m = RandomForest(n_estimators=nt, seed=7).fit(Xtr, ytr)
        print(f"   {nt:>7} {m.oob_error():>9.4f} {1 - acc(m):>9.4f} {m.strength:>8.4f}")

    print("\n③ max_features:小 → 树间相关 ρ̄ 低但单树弱(Breiman §3 的 F)")
    print(f"   {'max_features':>13} {'OOB误差':>9} {'测试误差':>9}")
    for mf in (1, 2, 3, 4):
        m = RandomForest(n_estimators=120, max_features=mf, seed=7).fit(Xtr, ytr)
        print(f"   {mf:>13} {m.oob_error():>9.4f} {1 - acc(m):>9.4f}")

    print("\n④ 变量重要性(§10 逐列加噪 vs 树内不纯度下降)")
    pi, gi = rf.permutation_importance(reps=5), rf.gini_importance()
    print(f"   {'feature':>8} {'置换重要性':>11} {'Gini 重要性':>12}")
    for f in range(4):
        print(f"   f{f:<7} {pi[f]:>11.4f} {gi[f]:>12.4f}")
    print("   —— f0/f1 高、f2/f3 接近 0;置换重要性可略为负(噪声列本就不影响 OOB)")

    print("\n⑤ 投票方式 / 是否 bootstrap")
    for sp in ("vote", "proba"):
        m = RandomForest(n_estimators=120, split=sp, seed=7).fit(Xtr, ytr)
        print(f"   split={sp:<6} 测试 {acc(m):.4f}  OOB {m.oob_score_:.4f}")
    m = RandomForest(n_estimators=120, bootstrap=False, seed=7).fit(Xtr, ytr)
    print(f"   bootstrap=False(Extra-Trees 默认,用整个数据集)  测试 {acc(m):.4f}  "
          f"OOB {fmt(m.oob_score_)} —— 无袋外样本,OOB 不可用")
    print("-" * 74)


if __name__ == "__main__":
    main()
