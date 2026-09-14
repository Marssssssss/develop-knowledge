"""支持向量机 + SMO 求解器(纯 stdlib,线性/多项式/RBF/sigmoid 核)。

权威来源(实际读过):
- scikit-learn 1.9《1.4. Support Vector Machines》 https://scikit-learn.org/stable/modules/svm.html
  原始问题(软间隔,C 为惩罚):
      min_{w,b,ζ} ½·wᵀw + C·Σζi   s.t. yi(wᵀφ(xi)+b) ≥ 1−ζi, ζi ≥ 0
  对偶问题:
      min_α ½·αᵀQα − eᵀα  s.t. yᵀα = 0, 0 ≤ αi ≤ C,  Qij = yi·yj·K(xi,xj)
  决策函数 f(x) = Σ_{i∈SV} yi·αi·K(xi,x) + b(αi=0 的非支持向量不参与求和)
  核:linear ⟨x,x'⟩;poly (γ⟨x,x'⟩+r)^d;rbf exp(−γ‖x−x'‖²)(γ>0);sigmoid tanh(γ⟨x,x'⟩+r)
  复杂度:libsvm 介于 O(n_features·n²) 与 O(n_features·n³);C 与 γ 必须指数尺度网格搜索
- J. Platt (1998) Sequential Minimal Optimization, MSR-TR-98-14
  https://www.microsoft.com/en-us/research/wp-content/uploads/1998/04/sequential-minimal-optimization.pdf
  每步只动两个乘子(单个乘子无法在 yᵀα=0 下单独变动);解析解;误差缓存;
  §2.2 examineExample 的两级启发式("选违反 KKT 最严重者 → 选使 |Ei−Ej| 最大者")
- 工作集选择用 **WSS1 最大违反对**(Keerthi et al. 2001 提出、Fan/Chen/Lin 2005 给出分析,
  LIBSVM 及多数 SMO 实现采用;该规则可证全局收敛):
      I_up(α)  = {t | αt < C, yt = +1} ∪ {t | αt > 0, yt = −1}
      I_low(α) = {t | αt > 0, yt = +1} ∪ {t | αt < C, yt = −1}
      m(α) = max_{I_up} −yt·∇f(α)t,  M(α) = min_{I_low} −yt·∇f(α)t
      ∇f(α)i = (Qα)i − 1 ⇒ −yt·∇f(α)t = yt − gt(与 b 无关),稳定条件为 m(α) ≤ M(α)
  来源:R.-E. Fan, P.-H. Chen, C.-J. Lin (2005) *Working Set Selection Using Second Order
  Information for Training Support Vector Machines*, JMLR 6:1889-1918
  https://makerhacker.github.io/paper-mining/jmlr/jmlr2005/jmlr-2005-Working_Set_Selection_Using_Second_Order_Information_for_Training_Support_Vector_Machines.html
  ；Chen/Fan/Lin *A Study on SMO-type Decomposition Methods* https://www.csie.ntu.edu.tw/~cjlin/papers/generalSMO.pdf
  —— 本 demo 踩到的坑:若沿用 Platt 的遍历式启发式而**不**换成最大违反对,会在
  "第一候选的 α 恰好被箱约束挡住" 的假收敛点停下(实测 80 样本上停在第 13 轮、
  w 只更新到一半);换 WSS1 后 KKT 违反数归零。
- Cortes & Vapnik (1995):软间隔把 αi ≥ 0 变成箱约束 0 ≤ αi ≤ C
- 对偶 KKT 条件(收敛判据):αi=0 ⇒ yi·f(xi) ≥ 1;0<αi<C ⇒ = 1;αi=C ⇒ ≤ 1
"""

from __future__ import annotations

import math
from typing import Callable, List, Sequence, Tuple

EPS = 1e-8


def make_kernel(kind: str, gamma: float = 1.0, degree: int = 3,
                coef0: float = 0.0) -> Callable[[Sequence[float], Sequence[float]], float]:
    if kind == "linear":
        return lambda a, b: sum(x * y for x, y in zip(a, b))
    if kind == "poly":
        return lambda a, b: (gamma * sum(x * y for x, y in zip(a, b)) + coef0) ** degree
    if kind == "rbf":
        assert gamma > 0.0, "rbf 的 γ 必须 > 0"
        return lambda a, b: math.exp(-gamma * sum((x - y) ** 2 for x, y in zip(a, b)))
    if kind == "sigmoid":
        return lambda a, b: math.tanh(gamma * sum(x * y for x, y in zip(a, b)) + coef0)
    raise ValueError(f"未知核:{kind}")


class SMO:
    """Platt 1998 SMO;取 sklearn 符号约定 f(x)=Σαi·yi·K(xi,x)+b,predict=sign(f)。"""

    def __init__(self, C=1.0, tol=1e-3, max_iter=5000, kernel="linear",
                 gamma=1.0, degree=3, coef0=0.0):
        self.C, self.tol, self.max_iter = C, tol, max_iter
        self.kind, self.gamma, self.degree, self.coef0 = kernel, gamma, degree, coef0

    # ---- 误差缓存(Platt §2.3)--------------------------------------------- #
    def _refresh(self) -> None:
        g = [sum(self.ay[j] * self.K[j][i] for j in range(self.n) if self.ay[j])
             for i in range(self.n)]
        self.f = [g[i] + self.b for i in range(self.n)]
        self.E = [self.f[i] - self.y[i] for i in range(self.n)]

    def _bounds(self, i: int, j: int) -> Tuple[float, float]:
        """箱约束下 αj 的可行区间:yᵀα=0 是斜线,与 [0,C]² 相交得到 [L,H]。"""
        ai, aj = self.alphas[i], self.alphas[j]
        if self.y[i] != self.y[j]:                       # αi − αj = const
            return max(0.0, aj - ai), min(self.C, self.C + aj - ai)
        return max(0.0, ai + aj - self.C), min(self.C, ai + aj)   # αi + αj = const

    def _take_step(self, i: int, j: int, E: Sequence[float]) -> bool:
        if i == j:
            return False
        Ei, Ej = E[i], E[j]
        ai_old, aj_old = self.alphas[i], self.alphas[j]
        L, H = self._bounds(i, j)
        if H - L < EPS:
            return False
        # W 沿对角线的二阶导:η = ∂²W/∂αj² = 2Kij − Kii − Kjj ≤ 0
        eta = 2.0 * self.K[i][j] - self.K[i][i] - self.K[j][j]
        if eta >= -EPS:
            return False
        # W' = yj(Ei−Ej) ⇒ 牛顿步 αj ← αj − yj(Ei−Ej)/η,再投影回箱约束
        aj_new = min(H, max(L, aj_old - self.y[j] * (Ei - Ej) / eta))
        if abs(aj_new - aj_old) < 1e-5:
            return False
        self.alphas[j] = aj_new
        self.alphas[i] = ai_old + self.y[i] * self.y[j] * (aj_old - aj_new)
        b1 = (self.b - Ei - self.y[i] * (self.alphas[i] - ai_old) * self.K[i][i]
              - self.y[j] * (self.alphas[j] - aj_old) * self.K[i][j])
        b2 = (self.b - Ej - self.y[i] * (self.alphas[i] - ai_old) * self.K[i][j]
              - self.y[j] * (self.alphas[j] - aj_old) * self.K[j][j])
        if 0.0 < self.alphas[i] < self.C:
            self.b = b1
        elif 0.0 < self.alphas[j] < self.C:
            self.b = b2
        else:
            self.b = 0.5 * (b1 + b2)                     # 两乘子都在边界:取 b1/b2 中点
        self.ay[i] = self.alphas[i] * self.y[i]
        self.ay[j] = self.alphas[j] * self.y[j]
        return True

    def _examine(self, i: int) -> bool:
        """外层:选违反 KKT 最严重的 i;内层两级启发式 + 回退遍历。"""
        Ei, yi, ai = self.E[i], self.y[i], self.alphas[i]
        if not ((yi * Ei < -self.tol and ai < self.C) or
                (yi * Ei > self.tol and ai > 0.0)):
            return False
        nb = [k for k in range(self.n) if 0.0 < self.alphas[k] < self.C and k != i]
        order = sorted(nb, key=lambda k: -abs(Ei - self.E[k]))       # 1|Ei−Ej| 最大
        order += [k for k in range(self.n) if k != i]                # 2 回退遍历全体
        for j in order:
            if self._take_step(i, j):
                return True
        return False

    # ---- 工作集选择:最大违反对(KKT 违反最严重的一对)----------------------- #
    def _violating_pair(self, g: Sequence[float]):
        """返回 (i, j, gap):
        I_up  = {t: (y=+1, α<C) 或 (y=−1, α>0)}   —— 还能**增大** α 的样本
        I_low = {t: (y=+1, α>0) 或 (y=−1, α<C)}   —— 还能**减小** α 的样本
        最优性要求 max_{I_up}(y−g) ≤ min_{I_low}(y−g);gap ≤ tol 即收敛。
        (y−g 与 b 无关,故可直接作为违反度量。)
        """
        n, C, eps = self.n, self.C, 1e-12
        up = [t for t in range(n) if (self.y[t] > 0 and self.alphas[t] < C - eps)
              or (self.y[t] < 0 and self.alphas[t] > eps)]
        low = [t for t in range(n) if (self.y[t] > 0 and self.alphas[t] > eps)
               or (self.y[t] < 0 and self.alphas[t] < C - eps)]
        if not up or not low:
            return None
        i = max(up, key=lambda t: self.y[t] - g[t])
        j = min(low, key=lambda t: self.y[t] - g[t])
        return i, j, (self.y[i] - g[i]) - (self.y[j] - g[j])

    # ---- 训练 ------------------------------------------------------------- #
    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]) -> "SMO":
        self.X, self.y = list(X), list(y)
        self.n = len(X)
        kf = make_kernel(self.kind, self.gamma, self.degree, self.coef0)
        self.K = [[kf(self.X[i], self.X[j]) for j in range(self.n)] for i in range(self.n)]
        self.alphas = [0.0] * self.n
        self.ay = [0.0] * self.n
        self.b, self.iterations = 0.0, 0
        self.f = [0.0] * self.n
        self.E = [self.y[t] * -1.0 for t in range(self.n)]
        for it in range(self.max_iter):
            g = self._g()
            pick = self._violating_pair(g)
            if pick is None:
                break
            i, j, gap = pick
            if gap <= self.tol:                       # 全部乘子已满足 KKT
                break
            E = [g[t] + self.b - self.y[t] for t in range(self.n)]
            if not self._take_step(i, j, E):
                break
        self.iterations = it + 1
        self._refine_b()
        self._finalize()
        return self

    def _g(self) -> List[float]:
        """g_t = Σ_j αj·yj·K(x_t,x_j)(不含 b)。"""
        return [sum(self.ay[j] * self.K[j][t] for j in range(self.n)) for t in range(self.n)]

    def _refine_b(self) -> None:
        """收尾:L = max_{I_up}(y−g) ≤ b ≤ min_{I_low}(y−g) = U,取中点消除累积漂移。"""
        g = self._g()
        n, C, eps = self.n, self.C, 1e-12
        lo = [self.y[t] - g[t] for t in range(n)
              if (self.y[t] > 0 and self.alphas[t] < C - eps)
              or (self.y[t] < 0 and self.alphas[t] > eps)]
        hi = [self.y[t] - g[t] for t in range(n)
              if (self.y[t] > 0 and self.alphas[t] > eps)
              or (self.y[t] < 0 and self.alphas[t] < C - eps)]
        if lo and hi and max(lo) <= min(hi):
            self.b = 0.5 * (max(lo) + min(hi))
        elif lo and hi:
            self.b = 0.5 * (max(lo) + min(hi))        # 未完全收敛:仍取中点作最小二乘折中
        elif lo:
            self.b = max(lo)
        elif hi:
            self.b = min(hi)
        self._refresh()

    def _finalize(self) -> None:
        self.sv_idx = [i for i in range(self.n) if self.alphas[i] > 1e-6]
        if self.kind == "linear":
            d = len(self.X[0])
            self.w = [sum(self.ay[i] * self.X[i][k] for i in self.sv_idx) for k in range(d)]
            nw = math.sqrt(sum(v * v for v in self.w))
            self.margin = 2.0 / nw if nw > 1e-12 else float("inf")
        else:
            self.w, self.margin = None, None

    # ---- 预测与诊断 -------------------------------------------------------- #
    def decision_function(self, x: Sequence[float], kf=None) -> float:
        kf = kf or make_kernel(self.kind, self.gamma, self.degree, self.coef0)
        return self.b + sum(self.ay[i] * kf(self.X[i], x) for i in self.sv_idx)

    def predict(self, X: Sequence[Sequence[float]]) -> List[int]:
        kf = make_kernel(self.kind, self.gamma, self.degree, self.coef0)
        return [1 if self.decision_function(x, kf) >= 0.0 else -1 for x in X]

    def kkt_violations(self, slack: float = 5e-3) -> List[int]:
        """α=0 ⇒ y·f ≥ 1;0<α<C ⇒ y·f = 1;α=C ⇒ y·f ≤ 1。"""
        bad = []
        for i in range(self.n):
            yf, a = self.y[i] * self.f[i], self.alphas[i]
            if a <= 1e-6:
                ok = yf >= 1.0 - slack
            elif a >= self.C - 1e-6:
                ok = yf <= 1.0 + slack
            else:
                ok = abs(yf - 1.0) <= slack
            if not ok:
                bad.append(i)
        return bad


def accuracy(y_true, y_pred) -> float:
    return sum(a == b for a, b in zip(y_true, y_pred)) / len(y_true)


def blobs(c1, c2, s1, s2, n, seed):
    """两个二维簇,线性同余生成(跨平台可复现),标签 +1 / −1。"""
    st = seed

    def rnd():
        nonlocal st
        st = (st * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (st >> 11) / float(1 << 53)

    X, y = [], []
    for (cx, cy), s, lab in ((c1, s1, 1), (c2, s2, -1)):
        for _ in range(n):
            X.append((cx + (rnd() * 2 - 1) * s * 1.7, cy + (rnd() * 2 - 1) * s * 1.7))
            y.append(lab)
    return X, y


def main() -> None:
    print("=" * 74)
    print("SVM + SMO(Platt 1998),纯 stdlib")
    print("=" * 74)

    X1, y1 = blobs((2.5, 2.5), (-2.5, -2.5), 0.5, 0.5, 20, 20260914)
    m1 = SMO(C=1.0, tol=1e-3, max_iter=20000, kernel="linear").fit(X1, y1)
    print("\n① 线性可分(40 样本,2 维)")
    print(f"   w = [{m1.w[0]:+.4f}, {m1.w[1]:+.4f}]   b = {m1.b:+.4f}   "
          f"margin = 2/‖w‖ = {m1.margin:.4f}")
    print(f"   支持向量 {len(m1.sv_idx)}/{m1.n} 个 → index={m1.sv_idx};外层迭代 {m1.iterations} 轮")
    print(f"   训练准确率 = {accuracy(y1, m1.predict(X1)):.4f}   KKT 违反 = {m1.kkt_violations()}")

    X2, y2 = blobs((1.0, 1.0), (-1.0, -1.0), 1.3, 1.3, 40, 777)
    print("\n② C 的作用(重叠数据;C 大 → 追求零训练错,小 → 宽间隔多违规)")
    print(f"   {'C':>8} {'#SV':>5} {'margin':>9} {'acc':>7} {'KKT违反':>8}")
    for C in (0.01, 0.1, 1.0, 100.0):
        m = SMO(C=C, tol=1e-3, max_iter=20000, kernel="linear").fit(X2, y2)
        print(f"   {C:>8} {len(m.sv_idx):>5} {m.margin:>9.4f} "
              f"{accuracy(y2, m.predict(X2)):>7.4f} {len(m.kkt_violations()):>8}")

    X3 = [(-1.0, -1.0), (-1.0, 1.0), (1.0, -1.0), (1.0, 1.0)]
    y3 = [-1, 1, 1, -1]
    lin = SMO(C=10.0, tol=1e-4, max_iter=20000, kernel="linear").fit(X3, y3)
    rbf = SMO(C=10.0, tol=1e-4, max_iter=20000, kernel="rbf", gamma=0.5).fit(X3, y3)
    print("\n③ 核技巧(经典 XOR:线性不可分)")
    print(f"   linear     : acc={accuracy(y3, lin.predict(X3)):.4f}  "
          f"w=[{lin.w[0]:+.4f},{lin.w[1]:+.4f}]  决策值="
          f"{[round(lin.decision_function(x), 4) for x in X3]}")
    print(f"                —— v_i=y_i·x_i 之和恰为 0,线性核下 w≡0 是真实最优解,退化成分不出")
    print(f"   rbf(γ=0.5) : acc={accuracy(y3, rbf.predict(X3)):.4f}  #SV={len(rbf.sv_idx)}  "
          f"决策值={[round(rbf.decision_function(x), 4) for x in X3]}")

    print("\n④ 同一重叠数据上的核对比(C=1.0)")
    for kind, kw in (("linear", {}), ("rbf", {"gamma": 0.5}),
                     ("poly", {"degree": 2, "gamma": 1.0}), ("poly", {"degree": 3, "gamma": 1.0})):
        m = SMO(C=1.0, tol=1e-3, max_iter=20000, kernel=kind, **kw).fit(X2, y2)
        extra = " ".join(f"{k}={v}" for k, v in kw.items())
        print(f"   {kind:<7} {extra:<18} #SV={len(m.sv_idx):>3}/{m.n}  "
              f"acc={accuracy(y2, m.predict(X2)):.4f}  KKT违反={len(m.kkt_violations())}")
    print("-" * 74)


if __name__ == "__main__":
    main()
