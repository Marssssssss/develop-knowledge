"""USL（Universal Scalability Law）三参数模型与最小二乘拟合。

公式与参数语义全部来自实读资料：
  * Neil J. Gunther《How to Quantify Scalability》（perfdynamics.com）
        C(N) = N / [1 + α(N−1) + βN(N−1)]                     （相对容量）
        X(N) = γN / [1 + α(N−1) + βN(N−1)]                    （三参数绝对吞吐）
        Nmax = √[(1 − α)/β]                                    （峰值负载）
        Xmax = X(Nmax)
        β = 0 且 γ = 1 时退化为 Amdahl 定律
        α/β 的相对大小分别指示 contention / coherency 主导
  * R 包 usl（CRAN，Stefan Moeding）源码 R/usl.R：
        拟合用 nls(... , algorithm="port", lower=c(0,0,0), upper=c(Inf,1,1))
        起始值 gamma = max(y/x), alpha = 0.01, beta = 0.0001
        Nmax = sqrt((1-alpha)/beta); Xmax = X(Nmax)
        Nopt = |1/alpha|; Xopt = X(Nopt); limit = gamma * Nopt
  * R 包 usl 的 vignette（usl.Rnw / usl.pdf）给出两个数据集与已发表系数：
        raytracer : alpha=0.057771  beta=0.000000  gamma=21.848843
                    RSE 9.34 on 8 df; limit 378; opt 195 @ 17.3; peak none
        specsdm91 : alpha=0.0277285 beta=0.0001044 gamma=89.9952382
                    RSE 82.8 on 4 df; limit 3250; opt 1540 @ 36.1; peak 96.5
"""

from __future__ import annotations

import math

RAYTRACER = [(1, 20.0), (4, 78.0), (8, 130.0), (12, 170.0), (16, 190.0),
             (20, 200.0), (24, 210.0), (28, 230.0), (32, 260.0),
             (48, 280.0), (64, 310.0)]

SPECDM91 = [(1, 64.9), (18, 995.9), (36, 1652.4), (72, 1853.2),
            (108, 1828.9), (144, 1775.0), (216, 1702.2)]


def denom(N: float, alpha: float, beta: float) -> float:
    return 1.0 + alpha * (N - 1.0) + beta * N * (N - 1.0)


def usl_X(N, gamma, alpha, beta):
    """绝对吞吐 X(N) = γN / [1 + α(N−1) + βN(N−1)]。"""
    d = denom(N, alpha, beta)
    if d <= 0:
        raise ValueError("分母非正：参数越界")
    return gamma * N / d


def usl_C(N: float, alpha: float, beta: float) -> float:
    """相对容量 C(N) = X(N)/X(1) = N / [1 + α(N−1) + βN(N−1)]。"""
    return N / denom(N, alpha, beta)


def jacobian(N, gamma, alpha, beta):
    """对 (γ, α, β) 的偏导（解析式）。"""
    d = denom(N, alpha, beta)
    dg = N / d
    da = -gamma * N * (N - 1.0) / (d * d)
    db = -gamma * N * N * (N - 1.0) / (d * d)
    return dg, da, db


class USLModel:
    """拟合得到的 USL 模型。"""

    def __init__(self, gamma: float, alpha: float, beta: float):
        self.gamma = gamma
        self.alpha = alpha
        self.beta = beta

    def X(self, N):
        return usl_X(N, self.gamma, self.alpha, self.beta)

    def C(self, N):
        return usl_C(N, self.alpha, self.beta)

    def efficiency(self, N, observed=None):
        """每负载发生器的效率。R 的 efficiency() 用**观测值**/(γN)。"""
        y = observed if observed is not None else self.X(N)
        return y / (self.gamma * N)

    @property
    def has_peak(self) -> bool:
        return self.beta > 0.0

    def peak(self):
        """(Nmax, Xmax)。β = 0 时峰值在无穷远，返回 (inf, γ/α)。"""
        if not self.has_peak:
            return math.inf, self.gamma / self.alpha if self.alpha > 0 else math.inf
        nmax = math.sqrt((1.0 - self.alpha) / self.beta)
        return nmax, self.X(nmax)

    def optimal(self):
        """(Nopt, Xopt)，Nopt = |1/α|。"""
        if self.alpha <= 0:
            return math.inf, math.inf
        nopt = 1.0 / self.alpha
        return nopt, self.X(nopt)

    def limit(self):
        """Amdahl 渐近线 Xlim = γ · Nopt = γ/α。"""
        if self.alpha <= 0:
            return math.inf
        return self.gamma / self.alpha

    def predict(self, N):
        return self.X(N)


def sse(data, gamma, alpha, beta) -> float:
    return sum((y - usl_X(N, gamma, alpha, beta)) ** 2 for N, y in data)


def residual_std_error(data, gamma, alpha, beta) -> float:
    """R 的 summary() 里 'Residual standard error'：sqrt(SSE / df)，df = n − 3。"""
    df = len(data) - 3
    if df <= 0:
        raise ValueError("数据点太少")
    return math.sqrt(sse(data, gamma, alpha, beta) / df)


def _solve3(a, rhs):
    """3x3 线性方程组（高斯消元）。"""
    m = [row[:] + [rhs[i]] for i, row in enumerate(a)]
    n = 3
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(m[r][i]))
        if abs(m[piv][i]) < 1e-300:
            return None
        m[i], m[piv] = m[piv], m[i]
        for r in range(i + 1, n):
            f = m[r][i] / m[i][i]
            for c in range(i, n + 1):
                m[r][c] -= f * m[i][c]
    x = [0.0] * n
    for i in reversed(range(n)):
        s = m[i][n] - sum(m[i][c] * x[c] for c in range(i + 1, n))
        x[i] = s / m[i][i]
    return x


def fit(data, iterations=400, tol=1e-14):
    """Levenberg–Marquardt 最小二乘拟合 (γ, α, β)，边界 α,β ∈ [0,1]、γ > 0。

    起始值与 R 包 usl 的 nls 调用一致：gamma = max(y/x)、alpha = 0.01、beta = 1e-4。
    """
    if len(data) < 4:
        raise ValueError("至少需要 4 个点（3 参数 + 残差自由度）")
    gamma = max(y / N for N, y in data)
    alpha, beta = 0.01, 0.0001
    lam = 1e-3

    def clip(p):
        g, a, b = p
        return (max(g, 1e-12), min(max(a, 0.0), 1.0), min(max(b, 0.0), 1.0))

    cur = [gamma, alpha, beta]
    best = sse(data, *cur)

    for _ in range(iterations):
        g, a, b = cur
        JT_J = [[0.0] * 3 for _ in range(3)]
        JT_r = [0.0] * 3
        for N, y in data:
            dg, da, db = jacobian(N, g, a, b)
            r = y - usl_X(N, g, a, b)
            grads = (dg, da, db)
            for i in range(3):
                JT_r[i] += grads[i] * r
                for j in range(3):
                    JT_J[i][j] += grads[i] * grads[j]
        A = [[JT_J[i][j] + (lam * JT_J[i][i] if i == j else 0.0)
              for j in range(3)] for i in range(3)]
        step = _solve3(A, JT_r)
        if step is None:
            lam *= 10
            continue
        cand = clip([cur[k] + step[k] for k in range(3)])
        try:
            s_new = sse(data, *cand)
        except ValueError:
            lam *= 10
            continue
        if s_new < best - 1e-15:
            delta = best - s_new
            cur, best = cand, s_new
            lam = max(lam * 0.3, 1e-12)
            if delta < tol * max(1.0, best):
                break
        else:
            lam *= 10
            if lam > 1e12:
                break
    # LM 可能停在阻尼过大的半途（β 撞到 0 边界时常见），再补一轮坐标下降打磨
    steps = [abs(x) * 1e-2 + 1e-8 for x in cur]
    for _ in range(400):
        improved = False
        for k in range(3):
            for sgn in (1.0, -1.0):
                cand = list(cur)
                cand[k] = cand[k] + sgn * steps[k]
                cand = clip(cand)
                try:
                    s_new = sse(data, *cand)
                except ValueError:
                    continue
                if s_new < best - 1e-15:
                    cur, best = cand, s_new
                    improved = True
        if not improved:
            steps = [h * 0.5 for h in steps]
            if max(steps) < 1e-15:
                break
    g, a, b = cur
    return USLModel(g, a, b), best
