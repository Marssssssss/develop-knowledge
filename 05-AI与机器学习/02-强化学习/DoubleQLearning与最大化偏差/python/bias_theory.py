"""最大化偏差的估计论刻画:单估计量 / 双估计量 / Theorem 1 的下界。

只依赖标准库。这里是「纯估计问题」那一层:把 Q 的估计误差当成随机变量,研究 `max`
算子作用其上会产生多大的偏差 —— 与具体的 RL 循环无关,因此有闭式或半闭式答案,
可以当教科书例题逐条核对。

  - 单估计量 `max_a (V* + ε_a)` 的偏差:均匀误差下有闭式 σ(m−1)/(m+1)
  - 双估计量 `μ^B_{a*}`(用 μ^A 选动作、用独立的 μ^B 打分)的偏差与 Lemma 1 的充要条件
  - Theorem 1 的下界 √(C/(m−1)) 及其紧性构造

参考文献(见 README「参考资料」):
  - van Hasselt, Double Q-learning, NIPS 2010 §2(单/双估计量的偏差分析、Lemma 1)
  - van Hasselt et al., Deep RL with Double Q-learning(arXiv:1509.06461)§3 Theorem 1
"""

import math
import random

SQRT2 = math.sqrt(2.0)


# ==========================================================================
# 1. 纯估计问题:max 算子的偏差
# ==========================================================================


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def expected_max_uniform(m, sigma=1.0):
    """m 个 iid U[−σ, σ] 的最大值期望:E = σ(m−1)/(m+1)(闭式)。"""
    return sigma * (m - 1.0) / (m + 1.0)


def expected_max_gauss(m, sigma=1.0, upper=14.0, steps=40000):
    """m 个 iid N(0,σ²) 的最大值期望(数值积分)。

    E[max] = ∫₀^∞ P(max>x) dx − ∫₋∞^0 P(max≤x) dx
           = ∫₀^∞ [1 − Φ(x)^m − (1−Φ(x))^m] dx
    第二项在 m 大时可忽略,但 m=2 时它占 E[max] 的 1/6,漏掉会把 1/√π 算成 0.68。
    """
    h = upper / steps
    tot = 0.0
    for k in range(steps):
        x = (k + 0.5) * h
        p = norm_cdf(x)
        tot += 1.0 - p ** m - (1.0 - p) ** m
    return sigma * tot * h


def draw_error(rng, dist):
    if dist == "uniform":
        return rng.uniform(-1.0, 1.0)
    if dist == "gauss":
        return rng.gauss(0.0, 1.0)
    raise ValueError(dist)


def single_estimator_bias(m, sigma, n_trials, rng, dist="uniform"):
    """单估计量:直接用 max_a (V* + ε_a) 当 max_a Q(s,a) 的估计。

    误差 = max_a ε_a(真实最大值是 0,因为所有动作的期望值都等于 V*)。
    """
    tot = 0.0
    for _ in range(n_trials):
        best = -1e18
        for _ in range(m):
            e = draw_error(rng, dist)
            if e > best:
                best = e
        tot += best
    return sigma * tot / n_trials


def double_estimator_bias(m, sigma, n_trials, rng, dist="uniform", n_sample=1):
    """双估计量:用 μ^A 选动作 a* = argmax_a μ^A_a,再用**独立的** μ^B 给它的值打分。

    误差 = μ^B_{a*}。μ^A、μ^B 是各自独立的 n_sample 个样本的均值。
    """
    tot = 0.0
    for _ in range(n_trials):
        best, astar = -1e18, 0
        for i in range(m):
            s = 0.0
            for _ in range(n_sample):
                s += draw_error(rng, dist)
            v = s / n_sample
            if v > best:
                best, astar = v, i
        s = 0.0
        for _ in range(n_sample):
            s += draw_error(rng, dist)
        tot += s / n_sample
    return sigma * tot / n_trials


def min_max_bias(C, m):
    """Theorem 1:若 Σ_a(Q_a−V*) = 0 且 (1/m)Σ_a(Q_a−V*)² = C,

    则 max_a Q_a ≥ V* + √(C/(m−1));该下界是紧的(见 README 证明梗概)。
    返回 (下界, 达到下界的构造 [ε_a])。
    """
    bound = math.sqrt(C / (m - 1.0))
    # 构造:一个动作取 −(m−1)·u,其余 m−1 个取 u,u = √(C/(m−1))
    u = bound
    eps = [u] * (m - 1) + [-(m - 1.0) * u]
    return bound, eps


def feasible(C, m, eps, tol=1e-9):
    """检查 ε 是否落在 Theorem 1 的可行集合上。"""
    s1 = sum(eps)
    s2 = sum(e * e for e in eps) / m
    return abs(s1) < tol and abs(s2 - C) < tol * max(1.0, C)


def bound_decomposition(C, m, eps):
    """Theorem 1 的代数核对:令 t = max ε,δ_i = t − ε_i(全部 ≥ 0),则

      Σδ_i = m·t(因为 Σε = 0),Σδ_i² = m·C + m·t²(代入 Σε² = mC)。
      又由 Cauchy–Schwarz,Σδ_i² ≤ (Σδ_i)² = m²t²(δ 只有一个非零时取等)。
      合并得 m·C + m·t² ≤ m²t² ⇒ t ≥ √(C/(m−1))。
    返回 (t, Σδ, m·t, Σδ², m·C+m·t², 上界 m²t²)。等号条件:δ 只有一个非零,
    即 m−1 个动作取 ε=t、1 个取 ε=−(m−1)t。
    """
    t = max(eps)
    d = [t - e for e in eps]
    return t, sum(d), m * t, sum(x * x for x in d), m * C + m * t * t, (m * t) ** 2


def lemma1_underestimation(m, sigma, delta, n_trials, rng, dist="uniform", n_sample=1):
    """Lemma 1:双估计量会**低估**(当最优动作之外的动作可能被选中)。

    构造:m−1 个动作期望 0,1 个动作期望 δ>0。用 μ^A 选 a*,用 μ^B 打分。
    返回 E[μ^B_{a*}] 与 max_a E[X_a] = δ 的对比。
    """
    tot = 0.0
    missed = 0
    for _ in range(n_trials):
        means = []
        for i in range(m):
            s = 0.0
            for _ in range(n_sample):
                s += draw_error(rng, dist)
            means.append(sigma * s / n_sample + (delta if i == m - 1 else 0.0))
        astar = max(range(m), key=lambda i: means[i])
        if astar != m - 1:
            missed += 1
        s = 0.0
        for _ in range(n_sample):
            s += draw_error(rng, dist)
        tot += delta if astar == m - 1 else 0.0
        tot += sigma * s / n_sample
    return tot / n_trials, missed / n_trials


