"""HMM 三个算法（前向-后向 / Viterbi / Baum-Welch）—— 依 SLP3 附录 A 与 hmmlearn 官方实现。

来源（本轮实读）：
  * Jurafsky & Martin, *Speech and Language Processing* (3rd ed.) 附录 A
    [Hidden Markov Models](https://web.stanford.edu/~jurafsky/slp3/A.pdf)
    - Eq A.7：P(3 1 3 | hot hot cold) = P(3|hot)P(1|hot)P(3|cold) = .4 × .2 × .1
    - Fig A.2 冰激凌 HMM：π = [.2, .8]（HOT, COLD）；
      B = P(1|HOT).2 P(2|HOT).4 P(3|HOT).4 / P(1|COLD).5 P(2|COLD).4 P(3|COLD).1
    - 前向：α1(j) = πj bj(o1)；αt(j) = Σi α_{t−1}(i) aij bj(ot)；P(O|λ) = Σi αT(i)
    - Viterbi：vt(j) = max_i v_{t−1}(i) aij bj(ot)，带回溯指针
    - 后向 β 与 γ/ξ，以及 Baum-Welch 的重估公式
  * hmmlearn/hmmlearn@main : src/hmmlearn/base.py
    - _compute_posteriors_scaling：posteriors = fwdlattice * bwdlattice 后逐行归一化
    - _compute_posteriors_log：log_gamma = fwdlattice + bwdlattice 后 log_normalize
    - _decode_map：state_sequence = argmax(posteriors)，log_prob = Σ max(后验)
      ⇒ **后验解码（map）与 Viterbi 是两件事**
    - _accumulate_sufficient_statistics_scaling：
        stats['start'] += posteriors[0]；trans 走 xi_sum；
        **长度为 1 的样本没有转移，直接 return（不更新 A）**
"""

import math
from itertools import product


class HMM:
    def __init__(self, pi, a, b):
        self.pi = [float(x) for x in pi]
        self.a = [[float(x) for x in row] for row in a]
        self.b = [[float(x) for x in row] for row in b]
        self.n = len(pi)
        self.m = len(b[0])


# ------------------------------------------------------------------ 前向
def forward(hmm, obs):
    """返回 (alpha, P(O|λ))，alpha[t][i] = P(o1..ot, qt=i)。"""
    t_len = len(obs)
    alpha = [[0.0] * hmm.n for _ in range(t_len)]
    for i in range(hmm.n):
        alpha[0][i] = hmm.pi[i] * hmm.b[i][obs[0]]
    for t in range(1, t_len):
        for j in range(hmm.n):
            alpha[t][j] = sum(alpha[t - 1][i] * hmm.a[i][j] for i in range(hmm.n))
            alpha[t][j] *= hmm.b[j][obs[t]]
    return alpha, sum(alpha[t_len - 1])


def forward_scaled(hmm, obs):
    """缩放前向：α̂_t 逐时刻归一化，c_t 为缩放因子；log P(O) = −Σ log c_t。"""
    t_len = len(obs)
    alpha = [[0.0] * hmm.n for _ in range(t_len)]
    cs = [0.0] * t_len
    for i in range(hmm.n):
        alpha[0][i] = hmm.pi[i] * hmm.b[i][obs[0]]
    cs[0] = sum(alpha[0])
    alpha[0] = [v / cs[0] for v in alpha[0]]
    for t in range(1, t_len):
        for j in range(hmm.n):
            alpha[t][j] = sum(alpha[t - 1][i] * hmm.a[i][j] for i in range(hmm.n))
            alpha[t][j] *= hmm.b[j][obs[t]]
        cs[t] = sum(alpha[t])
        alpha[t] = [v / cs[t] for v in alpha[t]]
    # c_t 取的是「除数」，故 log P(O) = Σ log c_t；
    # hmmlearn 的 scaling_factors 记的是倒数，所以那边写成 −Σ log c_t
    return alpha, cs, sum(math.log(c) for c in cs)


# ------------------------------------------------------------------ 后向
def backward(hmm, obs):
    """未缩放后向：beta[t][i] = P(o_{t+1}..oT | qt=i)。"""
    t_len = len(obs)
    beta = [[0.0] * hmm.n for _ in range(t_len)]
    for i in range(hmm.n):
        beta[t_len - 1][i] = 1.0
    for t in range(t_len - 2, -1, -1):
        for i in range(hmm.n):
            beta[t][i] = sum(
                hmm.a[i][j] * hmm.b[j][obs[t + 1]] * beta[t + 1][j]
                for j in range(hmm.n)
            )
    return beta


def backward_scaled(hmm, obs, cs):
    """用同一组缩放因子缩放后向（hmmlearn backward_scaling 的做法）。"""
    t_len = len(obs)
    beta = [[0.0] * hmm.n for _ in range(t_len)]
    # 记 P_t = ∏_{s≤t} c_s，则 α̂_t = α_t/P_t，要抵消就得令 β̂_t = β_t·P_t：
    #   β̂_{T−1} = β_{T−1}·P_{T−1} = P(O)（β_{T−1} = 1）
    #   β̂_t     = (Σ_j a_ij b_j(o_{t+1}) β̂_{t+1}(j)) / c_{t+1}
    prod = 1.0
    for c in cs:
        prod *= c
    for i in range(hmm.n):
        beta[t_len - 1][i] = prod
    for t in range(t_len - 2, -1, -1):
        for i in range(hmm.n):
            beta[t][i] = sum(
                hmm.a[i][j] * hmm.b[j][obs[t + 1]] * beta[t + 1][j]
                for j in range(hmm.n)
            ) / cs[t + 1]
    return beta


# -------------------------------------------------------------- γ 与 ξ
def gamma_unscaled(hmm, obs):
    """γ_t(i) = P(qt=i | O, λ) = α_t(i)β_t(i)/P(O)。"""
    alpha, prob = forward(hmm, obs)
    beta = backward(hmm, obs)
    g = []
    for t in range(len(obs)):
        g.append([alpha[t][i] * beta[t][i] / prob for i in range(hmm.n)])
    return g, prob


def gamma_scaled(alphahat, betahat):
    """hmmlearn _compute_posteriors_scaling：逐元素相乘后逐行归一化。"""
    out = []
    for t in range(len(alphahat)):
        row = [alphahat[t][i] * betahat[t][i] for i in range(len(alphahat[t]))]
        z = sum(row)
        out.append([v / z for v in row])
    return out


def xi(hmm, obs, alpha, beta, prob):
    """ξ_t(i,j) = P(qt=i, q_{t+1}=j | O, λ)，t = 0..T−2。"""
    out = []
    for t in range(len(obs) - 1):
        row = []
        for i in range(hmm.n):
            row.append(
                [
                    alpha[t][i] * hmm.a[i][j] * hmm.b[j][obs[t + 1]] * beta[t + 1][j]
                    / prob
                    for j in range(hmm.n)
                ]
            )
        out.append(row)
    return out


# ---------------------------------------------------------------- Viterbi
def viterbi(hmm, obs):
    """对数域 Viterbi：返回 (log 概率, 最优路径)。"""
    t_len = len(obs)
    n = hmm.n
    lp = [[math.log(max(hmm.pi[i] * hmm.b[i][obs[0]], 1e-300)) for i in range(n)]]
    bp = [[0] * n for _ in range(t_len)]
    for t in range(1, t_len):
        cur = []
        for j in range(n):
            best, arg = None, 0
            for i in range(n):
                v = lp[t - 1][i] + math.log(max(hmm.a[i][j], 1e-300))
                if best is None or v > best:
                    best, arg = v, i
            bp[t][j] = arg
            cur.append(best + math.log(max(hmm.b[j][obs[t]], 1e-300)))
        lp.append(cur)
    last = max(range(n), key=lambda i: lp[t_len - 1][i])
    score = lp[t_len - 1][last]
    path = [0] * t_len
    path[t_len - 1] = last
    for t in range(t_len - 1, 0, -1):
        path[t - 1] = bp[t][path[t]]
    return score, path


def posterior_decode(gamma_rows):
    """hmmlearn 的 map 解码：逐时刻取后验最大，逐点独立。"""
    return [max(range(len(row)), key=lambda i: row[i]) for row in gamma_rows]


# ------------------------------------------------------------ Baum-Welch
def baum_welch(hmm, obs, iterations=20):
    """EM：返回 (每轮 log 似然列表, 最终模型)。

    重估：π̂_i = γ_1(i)；â_ij = Σ_t ξ_t(i,j) / Σ_t γ_t(i)（t < T−1）；
    b̂_j(v) = Σ_{t: o_t=v} γ_t(j) / Σ_t γ_t(j)。
    长度为 1 的序列没有转移 ⇒ 不动 A（hmmlearn 的官方规则）。
    """
    cur = hmm
    logs = []
    for _ in range(iterations):
        alpha, prob = forward(cur, obs)
        beta = backward(cur, obs)
        g, prob = gamma_unscaled(cur, obs)
        x = xi(cur, obs, alpha, beta, prob)
        logs.append(math.log(prob))
        n, m = cur.n, cur.m
        pi = list(g[0])
        a = [[0.0] * n for _ in range(n)]
        if len(obs) > 1:
            for i in range(n):
                denom = sum(g[t][i] for t in range(len(obs) - 1))
                for j in range(n):
                    a[i][j] = (sum(x[t][i][j] for t in range(len(obs) - 1)) / denom
                               if denom > 0 else cur.a[i][j])
        else:
            a = [row[:] for row in cur.a]
        b = [[0.0] * m for _ in range(n)]
        for j in range(n):
            denom = sum(g[t][j] for t in range(len(obs)))
            for v in range(m):
                num = sum(g[t][j] for t in range(len(obs)) if obs[t] == v)
                b[j][v] = num / denom if denom > 0 else cur.b[j][v]
        cur = HMM(pi, a, b)
    alpha, prob = forward(cur, obs)
    logs.append(math.log(prob))
    return logs, cur


# ------------------------------------------------------------ 暴力枚举参照
def brute_force(hmm, obs):
    """枚举全部 N^T 条状态路径：返回 (P(O|λ), 最优路径, 最优路径概率)。"""
    best, best_path, total = None, None, 0.0
    for path in product(range(hmm.n), repeat=len(obs)):
        p = hmm.pi[path[0]] * hmm.b[path[0]][obs[0]]
        for t in range(1, len(obs)):
            p *= hmm.a[path[t - 1]][path[t]] * hmm.b[path[t]][obs[t]]
        total += p
        if best is None or p > best:
            best, best_path = p, list(path)
    return total, best_path, best


def ice_cream():
    """SLP3 Fig A.2 的冰激凌 HMM：状态顺序 [HOT, COLD]，观测 1/2/3 映射到 0/1/2。

    A 矩阵按图读取为 [[.6, .4], [.5, .5]]（图内数字排布有歧义，故本 demo 的
    所有定量断言都另由暴力枚举独立验证，不依赖这一处读数）。
    """
    return HMM(
        pi=[0.2, 0.8],
        a=[[0.6, 0.4], [0.5, 0.5]],
        b=[[0.2, 0.4, 0.4], [0.5, 0.4, 0.1]],
    )
