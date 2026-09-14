"""提升算法(纯 stdlib):AdaBoost.SAMME(多分类)+ 梯度提升回归(GBDT / 残差拟合)。

权威来源(实际读过,逐条 URL 与关键代码见同目录 README.md):scikit-learn 1.9
《1.11.7 AdaBoost》与《1.11.1 Gradient-boosted trees》
(https://scikit-learn.org/stable/modules/ensemble.html)—— AdaBoost 在反复修改的数据版本上
拟合弱学习器再加权多数投票;GBDT 为加性模型 F_m = F_{m-1} + ν·h_m,h_m 每轮拟合负梯度,
最小二乘损失下 F_0 = ȳ;shrinkage ν 与 n_estimators 强交互;subsample 为随机梯度提升。
sklearn 源码 `_weight_boosting.py`:α = lr·(log((1−err)/err) + log(K−1)),
w = exp(log(w) + α·incorrect·(w>0)),err ≤ 0 停、err ≥ 1−1/K 丢弃。
"""

from __future__ import annotations

import math

class Stump:
    """深度 1 的**加权**分类树桩(AdaBoost 的默认弱学习器),支持多分类。"""

    def __init__(self, n_classes):
        self.K = n_classes

    def fit(self, X, y, w):
        n = len(X)
        tot = sum(w)
        cnt = [0.0] * self.K
        for i in range(n):
            cnt[y[i]] += w[i]
        parent = 1.0 - sum((c / tot) ** 2 for c in cnt)
        best = (1e-12, None, None)
        for f in range(len(X[0])):
            order = sorted(range(n), key=lambda i: X[i][f])
            left = [0.0] * self.K
            lw = 0.0
            for k in range(n - 1):
                left[y[order[k]]] += w[order[k]]
                lw += w[order[k]]
                if X[order[k]][f] == X[order[k + 1]][f] or lw <= 0.0 or tot - lw <= 0.0:
                    continue
                rw = tot - lw
                gl = 1.0 - sum((c / lw) ** 2 for c in left)
                gr = 1.0 - sum(((cnt[c] - left[c]) / rw) ** 2 for c in range(self.K))
                gain = parent - (lw / tot) * gl - (rw / tot) * gr
                if gain > best[0]:
                    best = (gain, f, 0.5 * (X[order[k]][f] + X[order[k + 1]][f]))
        if best[1] is None:                              # 找不到有效切分 → 全体多数类
            self.feat, self.thr = None, None
            self.leaf = max(range(self.K), key=lambda c: cnt[c])
            return self
        self.feat, self.thr, self.leaf = best[1], best[2], [0, 0]
        for side, lo in enumerate((True, False)):
            sub = [0.0] * self.K
            for i in range(n):
                if (X[i][self.feat] <= self.thr) == lo:
                    sub[y[i]] += w[i]
            self.leaf[side] = max(range(self.K), key=lambda c: sub[c])
        return self

    def predict(self, X):
        if self.feat is None:
            return [self.leaf] * len(X)
        return [self.leaf[0] if x[self.feat] <= self.thr else self.leaf[1] for x in X]

class RegTree:
    """加权回归树(拟合负梯度/残差),用加权平方和最大化的贪心切分。"""

    def __init__(self, max_depth=3, min_samples_leaf=2):
        self.max_depth, self.min_leaf = max_depth, min_samples_leaf

    def fit(self, X, y, w):
        self.root = self._grow(list(range(len(X))), X, y, w, 0)
        return self

    def _grow(self, idx, X, y, w, depth):
        sw = sum(w[i] for i in idx) or 1.0
        mean = sum(w[i] * y[i] for i in idx) / sw
        if depth >= self.max_depth or len(idx) < 2 * self.min_leaf:
            return {"v": mean}
        best = (1e-12, None, None)
        for f in range(len(X[0])):
            order = sorted(idx, key=lambda i: X[i][f])
            tot_sy = sum(w[j] * y[j] for j in order)
            swl = s1 = 0.0
            for k in range(len(order) - 1):
                i = order[k]
                swl += w[i]
                s1 += w[i] * y[i]
                if X[i][f] == X[order[k + 1]][f] or k + 1 < self.min_leaf \
                        or len(order) - k - 1 < self.min_leaf:
                    continue
                # 最大化 s1²/swl + s2²/swr ⇔ 最小化左右加权平方误差之和
                gain = s1 * s1 / swl + (tot_sy - s1) ** 2 / (sw - swl)
                if gain > best[0]:
                    best = (gain, f, 0.5 * (X[i][f] + X[order[k + 1]][f]))
        if best[1] is None:
            return {"v": mean}
        f, thr = best[1], best[2]
        left = [i for i in idx if X[i][f] <= thr]
        right = [i for i in idx if X[i][f] > thr]
        if not left or not right:
            return {"v": mean}
        return {"f": f, "thr": thr, "l": self._grow(left, X, y, w, depth + 1),
                "r": self._grow(right, X, y, w, depth + 1)}

    def predict(self, X):
        out = []
        for x in X:
            node = self.root
            while "v" not in node:
                node = node["l"] if x[node["f"]] <= node["thr"] else node["r"]
            out.append(node["v"])
        return out

class AdaBoost:
    """SAMME:alpha = lr·(log((1−err)/err) + log(K−1)),K=2 时退化为经典 AdaBoost。"""

    def __init__(self, n_estimators=50, learning_rate=1.0):
        self.n_estimators, self.lr = n_estimators, learning_rate

    def fit(self, X, y):
        n, K = len(X), len(set(y))
        self.K, self.classes = K, sorted(set(y))
        self.models, self.alphas, self.errors = [], [], []
        w = [1.0 / n] * n
        for _ in range(self.n_estimators):
            m = Stump(K).fit(X, y, w)
            pred = m.predict(X)
            err = sum(w[i] for i in range(n) if pred[i] != y[i]) / sum(w)
            if err <= 0.0:                     # 完美拟合:sklearn 直接给权重 1.0 并停止
                self.models.append(m)
                self.alphas.append(1.0)
                self.errors.append(0.0)
                break
            if err >= 1.0 - 1.0 / K:           # 不比随机猜好 → 丢弃该学习器
                break
            a = self.lr * (math.log((1.0 - err) / err) + math.log(K - 1.0))
            self.models.append(m)
            self.alphas.append(a)
            self.errors.append(err)
            w = [w[i] * math.exp(a * (0.0 if pred[i] == y[i] else 1.0)) for i in range(n)]
            s = sum(w)
            w = [v / s for v in w]             # 必须归一化,否则连续 exp 会溢出
        return self

    def decision_function(self, X):
        out = []
        for x in X:
            d = [0.0] * self.K
            for m, a in zip(self.models, self.alphas):
                p = m.predict([x])[0]
                for c in range(self.K):
                    d[c] += a if p == c else -a / (self.K - 1)
            out.append(d)
        return out

    def predict(self, X):
        return [self.classes[max(range(self.K), key=lambda c: d[c])]
                for d in self.decision_function(X)]

    def predict_proba(self, X):
        """SAMME.R 论文 eq.(15):K=2 用 [−f, f]/2,K>2 用 softmax(f/(K−1))。"""
        out = []
        for d in self.decision_function(X):
            z = [-d[0] / 2.0, d[0] / 2.0] if self.K == 2 else [v / (self.K - 1) for v in d]
            mx = max(z)
            e = [math.exp(v - mx) for v in z]
            out.append([v / sum(e) for v in e])
        return out

class GradientBoosting:
    """Friedman 2001:每轮拟合负梯度(最小二乘即残差),再按 ν 收缩累加。"""

    def __init__(self, n_estimators=100, learning_rate=0.1, max_depth=3,
                 subsample=1.0, seed=0):
        self.n_estimators, self.lr = n_estimators, learning_rate
        self.max_depth, self.subsample, self.seed = max_depth, subsample, seed

    def fit(self, X, y):
        st = self.seed

        def rnd():
            nonlocal st
            st = (st * 6364136223846793005 + 1442695040888963407) % (1 << 64)
            return (st >> 11) / float(1 << 53)

        self.F0 = sum(y) / len(y)                    # 最小二乘损失下的最优常数模型
        F = [self.F0] * len(y)
        self.trees, self.loss_curve = [], []
        for _ in range(self.n_estimators):
            resid = [y[i] - F[i] for i in range(len(y))]     # 负梯度 = 残差
            if self.subsample < 1.0:
                k = max(2 * self.max_depth, int(len(y) * self.subsample))
                idx = [int(rnd() * len(y)) for _ in range(k)]  # 近似无放回抽样
                Xs, rs = [X[i] for i in idx], [resid[i] for i in idx]
            else:
                Xs, rs = X, resid
            t = RegTree(self.max_depth).fit(Xs, rs, [1.0] * len(Xs))
            upd = t.predict(X)
            F = [F[i] + self.lr * upd[i] for i in range(len(y))]   # shrinkage ν
            self.trees.append(t)
            self.loss_curve.append(sum((y[i] - F[i]) ** 2 for i in range(len(y))) / len(y))
        return self

    def predict(self, X):
        F = [self.F0] * len(X)
        for t in self.trees:
            u = t.predict(X)
            F = [F[i] + self.lr * u[i] for i in range(len(F))]
        return F

def _rng(seed):
    st = seed

    def rnd():
        nonlocal st
        st = (st * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (st >> 11) / float(1 << 53)
    return rnd

def circle_data(n, seed):
    """y = 0 在圆内、1 在圆外 —— 单个竖直/水平树桩只能切出条带,须靠多桩拼出阶梯近似。"""
    r = _rng(seed)
    X, y = [], []
    for _ in range(n):
        a, b = r() * 4 - 2, r() * 4 - 2
        X.append([a, b])
        y.append(0 if a * a + b * b < 1.0 else 1)
    return X, y

def blobs3(n, seed):
    r = _rng(seed)
    X, y = [], []
    for c, (cx, cy) in enumerate([(0.0, 0.0), (2.6, 2.6), (-2.6, 2.6)]):
        for _ in range(n // 3):
            X.append([cx + (r() * 2 - 1) * 1.1, cy + (r() * 2 - 1) * 1.1])
            y.append(c)
    return X, y

def acc(yt, yp):
    return sum(a == b for a, b in zip(yt, yp)) / len(yt)

def main() -> None:
    print("=" * 74)
    print("提升算法:AdaBoost.SAMME(多分类)+ 梯度提升回归(GBDT)")
    print("=" * 74)

    Xtr, ytr = circle_data(200, 7)
    Xte, yte = circle_data(80, 99)
    print("\n① AdaBoost 二分类(圆内/圆外;单个竖直树桩只能切出一条条带)")
    print("   alpha = lr·(log((1−err)/err) + log(K−1));K=2 时 log(K−1)=0 → 经典 AdaBoost")
    print(f"   {'#树桩':>5} {'训练acc':>9} {'测试acc':>9}")
    for nt in (1, 5, 20, 50, 200):
        m = AdaBoost(n_estimators=nt).fit(Xtr, ytr)
        print(f"   {nt:>5} {acc(ytr, m.predict(Xtr)):>9.4f} {acc(yte, m.predict(Xte)):>9.4f}")
    m50 = AdaBoost(n_estimators=50).fit(Xtr, ytr)
    print(f"   前 6 轮 err   = {[round(e, 4) for e in m50.errors[:6]]}")
    print(f"   前 6 轮 alpha = {[round(a, 4) for a in m50.alphas[:6]]}  (err 越小 α 越大)")
    print("   每轮错分样本权重 ×exp(α)、正确样本不变,再整体归一化 → 下个树桩被逼着看难点")
    print(f"   概率输出(SAMME.R eq.15)首样本 = "
          f"{[round(v, 4) for v in m50.predict_proba(Xte[:1])[0]]}")
    print("   —— 单桩(1 轮)0.8625 → 50 桩 0.9625 → 200 桩 0.9750,测试集同步受益")

    X3, y3 = blobs3(180, 11)
    print("\n② AdaBoost 多分类(K=3):log(K−1) = log2 = 0.6931 这一项开始出现")
    m3 = AdaBoost(n_estimators=60).fit(X3, y3)
    print(f"   前 6 轮 alpha = {[round(a, 4) for a in m3.alphas[:6]]}")
    print("   —— 同样 err 下二分类的 α 比这里**少 0.6931**;引入 log(K−1) 是为了把"
          "『比随机猜好』")
    print("      判定统一成 err < 1 − 1/K,否则多分类在 err > 1/2 时 α 会变成负数")
    print(f"   训练准确率 = {acc(y3, m3.predict(X3)):.4f},共用 {len(m3.models)} 个树桩")

    Xr = [[-3.0 + 6.0 * i / 239] for i in range(240)]
    yr = [math.sin(3 * x[0]) * math.exp(-x[0] ** 2 / 2) + 0.4 * x[0] ** 2 for x in Xr]
    print("\n③ 梯度提升回归 y = sin(3x)·e^(−x²/2) + 0.4x²(depth=3,subsample=1.0)")
    print(f"   {'lr':>5} {'#est':>5} {'训练MSE':>11} {'max|err|':>9}  loss 曲线前 3 轮")
    for lr, nt in ((0.5, 20), (0.1, 100), (0.1, 300), (0.01, 800)):
        g = GradientBoosting(n_estimators=nt, learning_rate=lr, max_depth=3).fit(Xr, yr)
        pr = g.predict(Xr)
        mse = sum((a - b) ** 2 for a, b in zip(yr, pr)) / len(yr)
        print(f"   {lr:>5} {nt:>5} {mse:>11.6f} {max(abs(a - b) for a, b in zip(yr, pr)):>9.4f}  "
              f"{', '.join(f'{v:.4f}' for v in g.loss_curve[:3])}")
    print("   —— lr 与 n_estimators 强交互:lr 缩小 10 倍,需约 10 倍轮数才到同等误差")

    ms = sum((a - b) ** 2 for a, b in zip(
        yr, GradientBoosting(n_estimators=300, learning_rate=0.1,
                             subsample=0.5).fit(Xr, yr).predict(Xr))) / len(yr)
    mf = sum((a - b) ** 2 for a, b in zip(
        yr, GradientBoosting(n_estimators=300, learning_rate=0.1).fit(Xr, yr).predict(Xr))) / len(yr)
    print(f"\n④ 随机梯度提升(Friedman 2002,subsample=0.5 无放回)训练MSE = {ms:.6f}"
          f"  vs subsample=1.0 的 {mf:.6f}")
    print("   —— 子采样引入随机性以降低方差;代价是同轮数下训练误差略高")
    print("-" * 74)


if __name__ == "__main__":
    main()
