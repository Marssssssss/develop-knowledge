"""变分推断 + 平均场（CAVI）—— 依 Blei et al. 综述与 sklearn LDA 官方实现。

来源（本轮实读）：
  * Blei, Kucukelbir & McAuliffe, *Variational Inference: A Review for Statisticians*
    arXiv:1601.00670 —— 本 demo 的公式编号沿用该文：
    - Eq (13)：ELBO(q) = E[log p(z,x)] − E[log q(z)]
    - Eq (14)：log p(x) = KL(q(z)‖p(z|x)) + ELBO(q)
    - Algorithm 1 / Eq (17)(18)：q_j(z_j) ∝ exp{ E_{−j}[ log p(z_j | z_{−j}, x) ] }
    - Eq (36)–(40)：完全条件属指数族时，最优变分因子同族，且 ν_j = E[η_j(z_{−j},x)]
    - §2.5：ELBO 一般非凸，CAVI 只保证到局部最优；收敛判据用 ELBO 变化量
  * scikit-learn decomposition/_lda.py（本地文件 sk_lda.py）
    - _update_doc_distribution：norm_phi = exp(E[log θ])·exp(E[log β]) + eps；
      坐标更新后 **把先验加到 doc_topic_d 上**（自然参数），再就地算 exp(E[log θ])
    - _approx_bound：E[log p(docs|θ,β)] 用 logsumexp 而非 log(sum)

本文件只依赖标准库。两个模型都刻意选成「真后验可解析/可枚举」的，
这样平均场引入的误差可以被直接量出来，而不是停留在定性描述。
"""

import math
import random
from itertools import product

# ---------------------------------------------------------------- 小工具


def sigmoid(x):
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x)) if x < 700 else 1.0
    e = math.exp(x)
    return e / (1.0 + e)


def logit(p):
    return math.log(p / (1.0 - p))


def bern_entropy(p):
    """伯努利香农熵（nats）；p=0 或 1 时为 0。"""
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -(p * math.log(p) + (1.0 - p) * math.log(1.0 - p))


def logsumexp(vals):
    mx = max(vals)
    return mx + math.log(sum(math.exp(v - mx) for v in vals))


def det(a):
    """高斯消元求行列式（矩阵很小，不追求数值健壮性以外的东西）。"""
    n = len(a)
    m = [row[:] for row in a]
    d = 1.0
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-300:
            return 0.0
        if piv != c:
            m[piv], m[c] = m[c], m[piv]
            d = -d
        d *= m[c][c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n):
                m[r][k] -= f * m[c][k]
    return d


def inv(a):
    """Gauss-Jordan 求逆。"""
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)]
         for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[piv], m[c] = m[c], m[piv]
        pv = m[c][c]
        m[c] = [v / pv for v in m[c]]
        for r in range(n):
            if r != c and m[r][c] != 0.0:
                f = m[r][c]
                m[r] = [v - f * w for v, w in zip(m[r], m[c])]
    return [row[n:] for row in m]


# ------------------------------------------------- 模型一：离散 Ising 平均场


class Ising:
    """未归一化的二元 pairwise 模型：log p̃(z) = Σ_i θ_i z_i + Σ_{i<j} w_ij z_i z_j。

    z_i ∈ {0,1}，w 为对称矩阵且对角为 0。状态数 2^n，本 demo 里 n ≤ 4，
    所以真后验可以**枚举到底**，平均场的误差因此是可测量的。
    """

    def __init__(self, theta, w):
        self.n = len(theta)
        self.theta = list(theta)
        self.w = [row[:] for row in w]

    def log_tilde(self, z):
        s = sum(self.theta[i] * z[i] for i in range(self.n))
        for i in range(self.n):
            for j in range(i + 1, self.n):
                s += self.w[i][j] * z[i] * z[j]
        return s

    def log_z(self):
        return logsumexp([self.log_tilde(z) for z in product((0, 1), repeat=self.n)])

    def posterior(self):
        """返回 {状态元组: 概率}，精确后验。"""
        lz = self.log_z()
        return {z: math.exp(self.log_tilde(z) - lz)
                for z in product((0, 1), repeat=self.n)}

    def marginal(self, i):
        return sum(p for z, p in self.posterior().items() if z[i] == 1)

    def pair(self, i, j):
        return sum(p for z, p in self.posterior().items() if z[i] == 1 and z[j] == 1)

    def eta(self, j, z):
        """完全条件的自然参数 η_j(z_{−j}) = θ_j + Σ_{k≠j} w_jk z_k。

        p(z_j = 1 | z_{−j}) = sigmoid(η_j)，这正是 Eq (36) 里 z_j 自带充分统计量的形式。
        """
        return self.theta[j] + sum(self.w[j][k] * z[k]
                                   for k in range(self.n) if k != j)


def q_prob(m, z):
    """平均场族 q(z) = ∏_i m_i^{z_i} (1−m_i)^{1−z_i}。"""
    p = 1.0
    for i, zi in enumerate(z):
        p *= m[i] if zi == 1 else (1.0 - m[i])
    return p


def elbo_ising(model, m):
    """Eq (13)：E_q[log p̃(z)] + H(q)（这里 p̃ 未归一化，故 ELBO 下界的是 log Z）。"""
    e = sum(model.theta[i] * m[i] for i in range(model.n))
    for i in range(model.n):
        for j in range(i + 1, model.n):
            e += model.w[i][j] * m[i] * m[j]
    return e + sum(bern_entropy(x) for x in m)


def kl_ising(model, m):
    """KL(q ‖ p)，p 为精确后验；按定义枚举 2^n 个状态。"""
    lz = model.log_z()
    tot = 0.0
    for z in product((0, 1), repeat=model.n):
        qp = q_prob(m, z)
        if qp <= 0.0:
            continue
        tot += qp * (math.log(qp) - (model.log_tilde(z) - lz))
    return tot


def cavi_ising(model, iters=60, m0=None, order=None):
    """Algorithm 1：按给定顺序逐个更新因子，返回 (m, 每轮结束后的 ELBO 序列)。"""
    m = list(m0) if m0 is not None else [0.5] * model.n
    order = list(order) if order else list(range(model.n))
    hist = []
    for _ in range(iters):
        for j in order:
            # Eq (40)：ν_j = E_{−j}[ η_j(z_{−j}) ]；伯努利族里 ν_j = logit(m_j)
            nu = model.theta[j] + sum(model.w[j][k] * m[k]
                                      for k in range(model.n) if k != j)
            m[j] = sigmoid(nu)
        hist.append(elbo_ising(model, m))
    return m, hist


# ------------------------------------------------- 模型二：二元高斯平均场


def gauss_log_tilde(x, mu, lam):
    d = [x[i] - mu[i] for i in range(len(x))]
    s = 0.0
    for i in range(len(x)):
        s += lam[i][i] * d[i] * d[i]
        for j in range(i + 1, len(x)):
            s += 2.0 * lam[i][j] * d[i] * d[j]
    return -0.5 * s


def gauss_log_z(lam):
    """∫ exp(−½(x−μ)ᵀΛ(x−μ)) dx = (2π)^{k/2} |Λ|^{−1/2}，与 μ 无关。"""
    k = len(lam)
    return 0.5 * k * math.log(2.0 * math.pi) - 0.5 * math.log(det(lam))


def gauss_entropy(cov_diag):
    """对角高斯的熵 = Σ ½ ln(2πe σ²)。"""
    return sum(0.5 * math.log(2.0 * math.pi * math.e * v) for v in cov_diag)


def gauss_elbo(m, var, mu, lam):
    """E_q[log p̃] + H(q)，q = ∏ N(m_i, var_i)。"""
    d = [m[i] - mu[i] for i in range(len(m))]
    quad = 0.0
    for i in range(len(m)):
        quad += lam[i][i] * var[i]
        quad += lam[i][i] * d[i] * d[i]
        for j in range(i + 1, len(m)):
            quad += 2.0 * lam[i][j] * d[i] * d[j]
    return -0.5 * quad + gauss_entropy(var)


def gauss_kl(m, var, mu, lam):
    """KL(N(m,diag(var)) ‖ N(mu, Λ^{−1})) 的闭式。"""
    k = len(m)
    d = [m[i] - mu[i] for i in range(k)]
    quad = sum(lam[i][i] * var[i] for i in range(k))
    for i in range(k):
        for j in range(k):
            quad += lam[i][j] * d[i] * d[j]
    log_ratio = -math.log(det(lam)) - sum(math.log(v) for v in var)
    return 0.5 * (quad - k + log_ratio)


def cavi_gauss(mu, lam, iters=60, m0=None):
    """坐标更新：s_j = 1/Λ_jj（一步到位，与别的因子无关），
    m_j = μ_j − (1/Λ_jj) Σ_{k≠j} Λ_jk (m_k − μ_k)。"""
    k = len(mu)
    m = list(m0) if m0 is not None else [0.0] * k
    var = [1.0 / lam[i][i] for i in range(k)]
    hist = []
    for _ in range(iters):
        for j in range(k):
            shift = sum(lam[j][i] * (m[i] - mu[i]) for i in range(k) if i != j)
            m[j] = mu[j] - shift / lam[j][j]
        hist.append(gauss_elbo(m, var, mu, lam))
    return m, var, hist
