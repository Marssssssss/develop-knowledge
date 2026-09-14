"""朴素贝叶斯三变体:高斯 / 多项式 / 伯努利(纯 stdlib)。

权威来源(实际读过):
- scikit-learn 1.9 《1.9. Naive Bayes》 https://scikit-learn.org/stable/modules/naive_bayes.html
  条件独立假设 => P(y|x1..xn) ∝ P(y) * ∏ P(xi|y),MAP 决策 ŷ = argmax_y P(y)∏P(xi|y)
  GaussianNB:  P(xi|y) = 1/sqrt(2πσ²y) * exp(−(xi−μy)²/(2σ²y)),μ/σ² 用极大似然估计
  MultinomialNB: θ̂yi = (Nyi + α) / (Ny + α·n),α=1 拉普拉斯平滑、α<1 Lidstone 平滑
  BernoulliNB: P(xi|y) = P(xi=1|y)^xi * (1−P(xi=1|y))^(1−xi)
               —— 与多项式的差别在于**显式惩罚"指示性特征未出现"**,多项式只是忽略
  文档明说:朴素贝叶斯是"decent classifier but a bad estimator",predict_proba 不可全信
- H. Zhang (2004) The optimality of Naive Bayes, FLAIRS
  https://www.cs.unb.ca/~hzhang/publications/FLAIRS04ZhangH.pdf
- Manning/Raghavan/Schütze《Introduction to Information Retrieval》pp.234-265
- McCallum & Nigam (1998) A comparison of event models for Naive Bayes text classification
  https://cdn.aaai.org/Workshops/1998/WS-98-05/WS-98-05-007.pdf

三个变体的差别只在"P(xi|y) 服从什么分布":
  连续特征 -> 高斯;词频计数 -> 多项式;二值出现/不出现 -> 伯努利。
"""

from __future__ import annotations

import math
import random
from typing import List, Sequence, Tuple

LOG2PI = math.log(2.0 * math.pi)
NEG_INF = float("-inf")


def _slog(p: float) -> float:
    """log(p),p=0 时返回 -inf 而不是抛 ValueError —— 用来演示"未平滑时归零"。"""
    return math.log(p) if p > 0.0 else NEG_INF


def logsumexp(xs: Sequence[float]) -> float:
    """log Σ exp(xi),用 max-shift 防上溢(等价 sklearn 的 _logsumexp)。"""
    m = max(xs)
    if m == NEG_INF:
        return NEG_INF
    return m + math.log(sum(math.exp(v - m) for v in xs))


def _softmax_from_log(jll: Sequence[float]) -> List[float]:
    z = logsumexp(jll)
    if z == NEG_INF:
        return [0.0] * len(jll)
    return [math.exp(v - z) for v in jll]


class GaussianNB:
    """P(xi|y) 为高斯;对数似然 = −0.5·log(2πσ²) − (x−μ)²/(2σ²)。"""

    def __init__(self, var_smoothing: float = 1e-9):
        self.var_smoothing = var_smoothing

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]) -> "GaussianNB":
        n = len(X)
        self.n_features = len(X[0])
        self.classes_ = sorted(set(y))
        eps = self.var_smoothing * max(max(col) - min(col) for col in zip(*X))
        self.theta_, self.var_, self.class_prior_, self.class_count_ = {}, {}, {}, {}
        for c in self.classes_:
            rows = [X[i] for i in range(n) if y[i] == c]
            self.class_count_[c] = len(rows)
            self.class_prior_[c] = len(rows) / n          # MAP 的 P(y) = 相对频率
            self.theta_[c] = [sum(r[j] for r in rows) / len(rows)
                              for j in range(self.n_features)]
            self.var_[c] = [
                sum((r[j] - self.theta_[c][j]) ** 2 for r in rows) / len(rows) + eps
                for j in range(self.n_features)
            ]
        return self

    def _jll(self, x: Sequence[float]) -> List[float]:
        out = []
        for c in self.classes_:
            ll = math.log(self.class_prior_[c])
            for j in range(self.n_features):
                mu, var = self.theta_[c][j], self.var_[c][j]
                ll += -0.5 * (LOG2PI + math.log(var)) - (x[j] - mu) ** 2 / (2.0 * var)
            out.append(ll)
        return out

    def predict_log_proba(self, X): return [self._jll(x) for x in X]

    def predict_proba(self, X): return [_softmax_from_log(self._jll(x)) for x in X]

    def predict(self, X):
        return [self.classes_[max(range(len(self.classes_)), key=lambda k: j[k])]
                for j in (self._jll(x) for x in X)]


class MultinomialNB:
    """词频计数模型;θ̂yi = (Nyi+α)/(Ny+α·n)。α 越大越强平滑,α→0 退化为未平滑 MLE。"""

    def __init__(self, alpha: float = 1.0):
        assert alpha >= 0.0
        self.alpha = alpha

    def fit(self, X: Sequence[Sequence[int]], y: Sequence[int]) -> "MultinomialNB":
        self.n_features = len(X[0])
        self.classes_ = sorted(set(y))
        self.feature_count_, self.class_count_ = {}, {}
        for c in self.classes_:
            cnt = [0] * self.n_features
            k = 0
            for i, yi in enumerate(y):
                if yi == c:
                    k += 1
                    for j in range(self.n_features):
                        cnt[j] += X[i][j]
            self.feature_count_[c], self.class_count_[c] = cnt, k
        self.class_log_prior_ = {c: math.log(self.class_count_[c] / len(y))
                                 for c in self.classes_}
        self.feature_log_prob_ = {}
        for c in self.classes_:
            denom = sum(self.feature_count_[c]) + self.alpha * self.n_features
            self.feature_log_prob_[c] = [
                _slog((self.feature_count_[c][j] + self.alpha) / denom)
                for j in range(self.n_features)
            ]
        return self

    def _jll(self, x):
        out = []
        for c in self.classes_:
            fl = self.feature_log_prob_[c]
            ll = self.class_log_prior_[c]
            for j in range(self.n_features):
                if x[j]:                       # 跳过 0 计数,既省算力又避免 0·(−inf)=nan
                    ll += x[j] * fl[j]
            out.append(ll)
        return out

    def predict_log_proba(self, X): return [self._jll(x) for x in X]

    def predict_proba(self, X): return [_softmax_from_log(self._jll(x)) for x in X]

    def predict(self, X):
        return [self.classes_[max(range(len(self.classes_)), key=lambda k: j[k])]
                for j in (self._jll(x) for x in X)]


class BernoulliNB:
    """二值模型;θ = (Nyi+α)/(Ny+2α),对数似然 = Σ[x·logθ + (1−x)·log(1−θ)]。"""

    def __init__(self, alpha: float = 1.0, binarize: float = 0.0):
        self.alpha, self.binarize = alpha, binarize

    def _binarize(self, X):
        return [[1 if v > self.binarize else 0 for v in row] for row in X]

    def fit(self, X, y):
        X = self._binarize(X)
        self.n_features = len(X[0])
        self.classes_ = sorted(set(y))
        self.feature_count_, self.class_count_ = {}, {}
        for c in self.classes_:
            cnt = [0] * self.n_features
            k = 0
            for i, yi in enumerate(y):
                if yi == c:
                    k += 1
                    for j in range(self.n_features):
                        cnt[j] += X[i][j]
            self.feature_count_[c], self.class_count_[c] = cnt, k
        self.class_log_prior_ = {c: math.log(self.class_count_[c] / len(y))
                                 for c in self.classes_}
        self.feature_log_prob_ = {}
        for c in self.classes_:
            n_c = self.class_count_[c]
            self.feature_log_prob_[c] = [
                _slog((self.feature_count_[c][j] + self.alpha) / (n_c + 2.0 * self.alpha))
                for j in range(self.n_features)
            ]
        return self

    def _jll(self, x):
        xb = self._binarize([x])[0]
        out = []
        for c in self.classes_:
            th = self.feature_log_prob_[c]
            ll = self.class_log_prior_[c]
            for j in range(self.n_features):
                # 显式惩罚"指示词没出现" —— 这是伯努利与多项式的唯一实质差别
                ll += xb[j] * th[j] + (1 - xb[j]) * _slog(1.0 - math.exp(th[j]))
            out.append(ll)
        return out

    def predict_log_proba(self, X): return [self._jll(x) for x in X]

    def predict_proba(self, X): return [_softmax_from_log(self._jll(x)) for x in X]

    def predict(self, X):
        return [self.classes_[max(range(len(self.classes_)), key=lambda k: j[k])]
                for j in (self._jll(x) for x in X)]


def accuracy(y_true: Sequence[int], y_pred: Sequence[int]) -> float:
    return sum(a == b for a, b in zip(y_true, y_pred)) / len(y_true)


# --------------------------------------------------------------------------- #
# 演示数据
# --------------------------------------------------------------------------- #

def gaussian_dataset():
    """两簇二维高斯(固定种子,保证跨机器可复现)。"""
    rng = random.Random(20260914)
    X, y = [], []
    for label, (mx, my) in ((0, (0.0, 0.0)), (1, (2.5, 2.0))):
        for _ in range(60):
            X.append((mx + rng.gauss(0.0, 1.0), my + rng.gauss(0.0, 1.2)))
            y.append(label)
    return X, y


VOCAB = ["free", "money", "win", "click", "meeting",
         "report", "project", "lunch", "urgent", "team"]
DOCS: List[Tuple[str, List[int]]] = [
    ("free money win click", [3, 2, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("win free money click", [2, 1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("click win free", [1, 0, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("free money urgent", [1, 1, 0, 0, 0, 0, 0, 0, 1, 0]),
    ("win money free", [1, 1, 1, 0, 0, 0, 0, 0, 0, 0]),
    ("project report meeting", [0, 0, 0, 0, 1, 1, 1, 0, 0, 0]),
    ("team meeting lunch", [0, 0, 0, 0, 1, 0, 0, 1, 0, 1]),
    ("project report team", [0, 0, 0, 0, 0, 1, 1, 0, 0, 1]),
    ("meeting project urgent", [0, 0, 0, 0, 1, 0, 1, 0, 1, 0]),
    ("report project lunch", [0, 0, 0, 0, 0, 1, 1, 1, 0, 0]),
    ("team report project", [0, 0, 0, 0, 0, 1, 1, 0, 0, 1]),
    ("lunch team meeting", [0, 0, 0, 0, 1, 0, 0, 1, 0, 1]),
]
DOC_LABELS = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]   # 1=spam 0=ham


def main() -> None:
    line = "-" * 74
    print("=" * 74)
    print("朴素贝叶斯三变体(纯 stdlib 参考实现)")
    print("=" * 74)

    # --- ① GaussianNB -------------------------------------------------------
    X, y = gaussian_dataset()
    gnb = GaussianNB().fit(X, y)
    print(f"\n① GaussianNB  训练 {len(X)} 样本 / 2 类 / 2 特征")
    for c in gnb.classes_:
        print(f"   class {c}: prior={gnb.class_prior_[c]:.4f} "
              f"theta=({gnb.theta_[c][0]:+.4f}, {gnb.theta_[c][1]:+.4f})  "
              f"var=({gnb.var_[c][0]:.4f}, {gnb.var_[c][1]:.4f})")
    print(f"   训练集准确率 = {accuracy(y, gnb.predict(X)):.4f}")
    p = gnb.predict_proba([(1.0, 1.0)])[0]
    print(f"   x=(1.0,1.0) => P = {[round(v, 6) for v in p]}   ΣP = {sum(p):.15f}")

    # --- ② MultinomialNB ---------------------------------------------------
    Xc = [d[1] for d in DOCS]
    print(f"\n② MultinomialNB  文本分类 {len(DOCS)} 篇 / 词表 {len(VOCAB)} 词")
    for alpha, tag in ((1.0, "Laplace"), (0.1, "Lidstone"), (0.0, "未平滑 MLE")):
        m = MultinomialNB(alpha=alpha).fit(Xc, DOC_LABELS)
        print(f"   alpha={alpha:<4} {tag:<12} 训练准确率 = "
              f"{accuracy(DOC_LABELS, m.predict(Xc)):.4f}")
    lap = MultinomialNB(alpha=1.0).fit(Xc, DOC_LABELS)
    print(f"   Laplace 下 {VOCAB[0]!r} 的 logθ: spam={lap.feature_log_prob_[1][0]:+.4f} "
          f"ham={lap.feature_log_prob_[0][0]:+.4f}")

    # --- ③ 零概率 / 平滑的作用 ---------------------------------------------
    print("\n③ 平滑的必要性:类 c 从未见过词 v => logθ(v|c) = −inf => P(c|x) 被单点证据钉死为 0")
    win_only = [0, 0, 1, 0, 0, 0, 0, 0, 0, 0]          # 只出现 "win"(只在 spam 文档里出现过)
    for alpha in (0.0, 1.0):
        m = MultinomialNB(alpha=alpha).fit(Xc, DOC_LABELS)
        jll = m._jll(win_only)
        pr = _softmax_from_log(jll)
        show = " ".join("−inf" if v == NEG_INF else f"{v:+.4f}" for v in jll)
        print(f"   alpha={alpha:<4} joint_ll(ham,spam)=[{show}]  "
              f"P(ham)={pr[0]:.6f} P(spam)={pr[1]:.6f}")

    # --- ④ BernoulliNB vs MultinomialNB -----------------------------------
    print("\n④ BernoulliNB(二值 + 惩罚未出现) vs MultinomialNB")
    bnb = BernoulliNB(alpha=1.0).fit(Xc, DOC_LABELS)
    print(f"   BernoulliNB   训练准确率 = {accuracy(DOC_LABELS, bnb.predict(Xc)):.4f}")
    print(f"   MultinomialNB 训练准确率 = {accuracy(DOC_LABELS, lap.predict(Xc)):.4f}")
    print(f"   仅含 'win' 的极短文档 => Bernoulli P(spam)="
          f"{_softmax_from_log(bnb._jll(win_only))[1]:.6f}  "
          f"Multinomial P(spam)={_softmax_from_log(lap._jll(win_only))[1]:.6f}")
    print("   —— 伯努利把其余 9 个词都算作'明确没出现'并逐个乘 (1−θ),长文档下更保守;")
    print("      多项式只看出现过的词,故短文档不惩罚。文档越短,差别越明显。")
    print(line)


if __name__ == "__main__":
    main()
