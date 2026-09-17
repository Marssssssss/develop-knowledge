"""Actor-Critic 与 GAE(广义优势估计):优势估计的偏差-方差,以及基线的方差缩减。

教材/论文依据:
  - Schulman et al., High-Dimensional Continuous Control Using Generalized Advantage
    Estimation (arXiv:1506.02438)§2:Â_t = Σ_{l≥0}(γλ)^l δ^V_{t+l},
    GAE(γ,λ) 是 k 步估计量 Â^{(k)}_t 的指数加权平均
  - Sutton & Barto, RL: An Introduction 1st ed. §7.1 的 n 步回报(Â^{(k)} 的来源)

实验:
  E1 恒等式:GAE 的"折扣 δ 求和"形式 == "指数加权的 k 步估计量"形式
  E2 真值 V^π 作基线:各 λ 的偏差≈0、方差随 λ 上升(优势估计的方差来源)
  E3 欠拟合的 V̂ 作基线:各 λ 的偏差/方差/MSE(值函数误差如何转化为优势误差)
  E4 策略梯度估计量的方差:全回报 / 加基线 / TD 残差 / GAE 四种估计量对比
"""

import math
import random
import sys

from mdp import CorridorMDP, lstsq_linear_fit

GAMMA = 0.95
PASSED, FAILED = [], []


def check(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  |  {detail}" if detail else ""))


def pi_const(p_right=0.75):
    return lambda s, a: (p_right if a == 1 else 1.0 - p_right)


def deltas(S, R, D, V, gamma):
    """δ_t = r_t + γV(s_{t+1}) − V(s_t);终止后 V(下一状态)=0。"""
    out = []
    for t in range(len(R)):
        v_next = 0.0 if D[t] or t + 1 >= len(S) else V[S[t + 1]]
        out.append(R[t] + gamma * v_next - V[S[t]])
    return out


def gae_discounted(dl, gamma, lam):
    """Â_t = Σ_{l≥0} (γλ)^l δ_{t+l}(截断到轨迹末尾)。"""
    out = [0.0] * len(dl)
    acc = 0.0
    for t in range(len(dl) - 1, -1, -1):
        acc = dl[t] + gamma * lam * acc
        out[t] = acc
    return out


def k_step_advantages(dl, gamma, k):
    """Â^{(k)}_t = Σ_{l=0}^{k-1} γ^l δ_{t+l}(δ 求和形式;与"k 步回报 − V(s_t)"等价)。"""
    out = []
    for t in range(len(dl)):
        acc, g = 0.0, 1.0
        for l in range(k):
            if t + l < len(dl):
                acc += g * dl[t + l]
                g *= gamma
        out.append(acc)
    return out


def weighted_k_step(dl, gamma, lam, kmax=None):
    """(1-λ) Σ_{k≥1} λ^{k-1} Â^{(k)}_t(§2 的指数加权平均形式)。

    轨迹有限时,剩余长度为 m = T-t 的 δ 都已被用尽,故 k ≥ m 的估计量全部等于
    Â^{(m)}_t,级数尾部可求和:
        (1-λ) Σ_{k=1}^{∞} λ^{k-1} Â^{(k)} = (1-λ) Σ_{k=1}^{m-1} λ^{k-1} Â^{(k)} + λ^{m-1} Â^{(m)}
    (早期版本把 k ≤ kmax 的截断项与尾部项同时计入,导致 λ 大时结果偏大 ~9e-2。)
    """
    T = len(dl)
    total = [0.0] * T
    for t in range(T):
        m = T - t
        acc = 0.0
        for k in range(1, m):
            acc += (1.0 - lam) * lam ** (k - 1) * k_step_advantages(dl, gamma, k)[t]
        acc += (lam ** (m - 1)) * k_step_advantages(dl, gamma, m)[t]
        total[t] = acc
    return total


def stats(errs):
    m = sum(errs) / len(errs)
    var = sum((e - m) ** 2 for e in errs) / len(errs)
    se = math.sqrt(var / len(errs)) if len(errs) > 1 else 0.0
    return m, math.sqrt(var), se, m * m + var


def first_decision_samples(env, pi, V, lam_list, gamma=GAMMA, N=4000, seed=0,
                           horizon=60):
    """随机起点采样 N 条轨迹,只取**第一次决策**的 Â(避免同轨迹内样本相关)。"""
    rng = random.Random(seed)
    samples = {lam: [] for lam in lam_list}
    refs = []
    for _ in range(N):
        s0 = rng.randrange(1, env.n + 1)
        S, A, R, D = env.rollout(pi, rng, s0, horizon=horizon)
        dl = deltas(S, R, D, V, gamma)
        for lam in lam_list:
            samples[lam].append(gae_discounted(dl, gamma, lam)[0])
        refs.append((S[0], A[0]))   # 必须用 rollout 实际采到的首个动作
    return samples, refs


def make_fitted_value(env, pi, Vexact):
    """用一条直线拟合 V^π(内部状态),故意欠拟合 —— 模拟"值函数有系统误差"。"""
    xs = env.states
    a, b = lstsq_linear_fit([float(s) for s in xs], [Vexact[s] for s in xs])
    Vhat = {s: a + b * s for s in xs}
    for t in env.terminals:
        Vhat[t] = 0.0
    return Vhat, a, b


def experiment_1(env, pi, V):
    print("\n=== E1 GAE 的两种等价写法 ===")
    rng = random.Random(3)
    S, A, R, D = env.rollout(pi, rng, 6, horizon=30)
    dl = deltas(S, R, D, V, GAMMA)
    disc = gae_discounted(dl, GAMMA, 0.9)
    weighted = weighted_k_step(dl, GAMMA, 0.9, kmax=len(dl) + 2)
    worst = max(abs(x - y) for x, y in zip(disc, weighted))
    print(f"  折扣求和形式 vs 指数加权 k 步形式:最大差 = {worst:.2e}(轨迹长 {len(S)})")
    # λ=0 时退化为 TD 残差
    d0 = gae_discounted(dl, GAMMA, 0.0)
    worst0 = max(abs(x - y) for x, y in zip(d0, dl))
    print(f"  λ=0 时 Â == δ:最大差 = {worst0:.2e}")
    return worst, worst0


def sweep(env, pi, V, Adv, label, N=8000, seed=11, min_cell=40):
    """按 (s0,a0) 分层统计偏差与方差。

    注意:如果只在"随机起点"上做整体平均,偏差会被起点分布抹平——最小二乘拟合的
    残差均值为 0,于是 λ=1 的 −e(s_t) 这一项在平均后几乎消失,得出"λ=1 更无偏"的
    假结论。所以这里对每个 (s,a) 单元单独求偏差,再对单元取平均。
    """
    lams = [0.0, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0]
    samples, refs = first_decision_samples(env, pi, V, lams, N=N, seed=seed)
    print(f"\n=== {label} ===")
    print("    λ       平均|偏差|    平均标准误   偏差/标准误   平均标准差    MSE")
    rows = {}
    for lam in lams:
        cells = {}
        for i, val in enumerate(samples[lam]):
            cells.setdefault(refs[i], []).append(val - Adv[refs[i]])
        cells = {k: v for k, v in cells.items() if len(v) >= min_cell}
        biases, ses, sds = [], [], []
        for v in cells.values():
            m = sum(v) / len(v)
            sd = math.sqrt(sum((x - m) ** 2 for x in v) / len(v))
            biases.append(m)
            sds.append(sd)
            ses.append(sd / math.sqrt(len(v)))
        allerr = [x for v in cells.values() for x in v]
        mse = sum(x * x for x in allerr) / len(allerr)
        mb = sum(abs(b) for b in biases) / len(biases)
        mse_ = sum(ses) / len(ses)
        rows[lam] = (mb, mse_, mse, len(cells), mb / mse_, sum(sds) / len(sds))
        print(f"  {lam:<6} {mb:>11.4f} {mse_:>13.4f} {rows[lam][4]:>12.2f} "
              f"{rows[lam][5]:>13.4f} {mse:>10.5f}")
    return rows


def gradient_variance(env, theta, n_batches=300, batch=12, seed=5, horizon=40,
                      gamma=GAMMA, verbose=True):
    """比较四种策略梯度估计量在**同一策略**下的批间方差。

    softmax 二动作策略:π(a|s) = σ(θ_a − θ_{1-a});
    ∇_θ log π(a|s) 在 θ 上是 (1[a=0]−π(0|s), 1[a=1]−π(1|s))。
    基线 V 用**精确**策略评估(隔离"值函数准不准"这个干扰项)。
    """
    rng = random.Random(seed)

    def probs(s):
        z = theta[1] - theta[0]
        p1 = 1.0 / (1.0 + math.exp(-z))
        return 1.0 - p1, p1

    V = env.policy_eval(lambda s, a: probs(s)[a])
    ests = {"full_return": [], "return_minus_V": [], "td_residual": [],
            "gae_lambda_0.95": []}
    for _ in range(n_batches):
        g = {k: [0.0, 0.0] for k in ests}
        for _ in range(batch):
            s0 = rng.randrange(1, env.n + 1)
            S, A, R, D = env.rollout(lambda s, a: probs(s)[a], rng, s0, horizon=horizon)
            dl = deltas(S, R, D, V, gamma)
            gae95 = gae_discounted(dl, gamma, 0.95)
            G = 0.0
            for t in range(len(S) - 1, -1, -1):
                G = R[t] + gamma * G
            for t, a in enumerate(A):
                p0, p1 = probs(S[t])
                score = [(1.0 if a == 0 else 0.0) - p0, (1.0 if a == 1 else 0.0) - p1]
                for key, val in (("full_return", G),
                                 ("return_minus_V", G - V[S[t]]),
                                 ("td_residual", dl[t]),
                                 ("gae_lambda_0.95", gae95[t])):
                    g[key][0] += val * score[0]
                    g[key][1] += val * score[1]
        for k in ests:
            ests[k].append(g[k])
    var = {}
    for k, vs in ests.items():
        m0 = sum(v[0] for v in vs) / len(vs)
        m1 = sum(v[1] for v in vs) / len(vs)
        var[k] = math.sqrt(sum((v[0] - m0) ** 2 + (v[1] - m1) ** 2 for v in vs) / len(vs))
    if verbose:
        print(f"\n=== E4 策略梯度估计量的批间方差(n={env.n}, γ={gamma}, "
              f"horizon={horizon}, noise_sd={env.noise_sd}, 每批 {batch} 条 × {n_batches} 批) ===")
        for k in ("full_return", "return_minus_V", "td_residual", "gae_lambda_0.95"):
            print(f"  {k:<18s} 梯度标准差 = {var[k]:.4e}")
    return var


def main():
    env = CorridorMDP(n=11, slip=0.15, gamma=GAMMA)
    pi = pi_const(0.75)
    V = env.policy_eval(pi)
    Q = env.q_values(pi, V)
    Adv = env.advantage(pi, V, Q)

    # ---- A1 精确解自检:Bellman 残差 ----
    res = 0.0
    for s in env.states:
        lhs = V[s]
        rhs = sum(pi(s, a) * sum(p * (r + GAMMA * V[s2])
                                 for s2, p, r in env.outcomes(s, a))
                  for a in range(2))
        res = max(res, abs(lhs - rhs))
    check("精确策略评估:Bellman 残差 < 1e-10", res < 1e-10, f"max={res:.2e}")
    check("优势函数的期望为 0(Σ_a π(a|s)A(s,a)=0)",
          max(abs(sum(pi(s, a) * Adv[(s, a)] for a in range(2))) for s in env.states) < 1e-12)

    # ---- E1 ----
    worst, worst0 = experiment_1(env, pi, V)

    # ---- E2 真值基线 ----
    rows_true = sweep(env, pi, V, Adv, "E2 用真实 V^π 作基线(所有 λ 都无偏,方差随 λ 上升)")

    # ---- E3 欠拟合基线 ----
    Vhat, a0, b0 = make_fitted_value(env, pi, V)
    fit_err = max(abs(Vhat[s] - V[s]) for s in env.states)
    print(f"\n  V̂ = {a0:.4f} + {b0:.5f}·s,与 V^π 的最大偏差 = {fit_err:.4f}")
    rows_hat = sweep(env, pi, Vhat, Adv, "E3 用欠拟合 V̂ 作基线(偏差被值函数误差放大)")

    # ---- E4 ----
    var = gradient_variance(env, [0.0, 0.0], n_batches=300, batch=12, horizon=40)
    env_noisy = CorridorMDP(n=21, slip=0.15, gamma=0.99, noise_sd=1.0)
    var_noisy = gradient_variance(env_noisy, [0.0, 0.0], n_batches=300, batch=12,
                                  horizon=150, gamma=0.99)

    # ---- 断言 ----
    check("E1 折扣求和形式 == 指数加权的 k 步形式(< 1e-10)", worst < 1e-10,
          f"max={worst:.2e}")
    check("E1 λ=0 时 Â == TD 残差 δ", worst0 < 1e-12, f"max={worst0:.2e}")
    check("E2 真实基线下所有 λ 的偏差都不显著(偏差/标准误 < 3)",
          max(rows_true[lam][4] for lam in rows_true) < 3.0,
          f"max 比值={max(rows_true[lam][4] for lam in rows_true):.2f}")
    check("E2 方差随 λ 单调上升",
          all(rows_true[b][1] >= rows_true[a][1] * 0.98
              for a, b in zip(sorted(rows_true), sorted(rows_true)[1:])),
          "标准差: λ=0 %.4f -> λ=1 %.4f" % (rows_true[0.0][1], rows_true[1.0][1]))
    check("E2 λ=1 的 MSE 大于 λ=0(无偏时 MSE 完全由方差决定)",
          rows_true[1.0][2] > rows_true[0.0][2],
          f"MSE λ=0:{rows_true[0.0][2]:.5f} λ=1:{rows_true[1.0][2]:.5f}")
    check("E3 值函数有误差时所有 λ 都出现显著偏差(偏差/标准误 > 3)",
          min(rows_hat[lam][4] for lam in rows_hat) > 3.0,
          f"min 比值={min(rows_hat[lam][4] for lam in rows_hat):.2f}")
    check("E3 欠拟合基线的偏差量级明显大于真实基线",
          max(rows_hat[lam][0] for lam in rows_hat) > 2 * max(rows_true[lam][0] for lam in rows_true),
          f"V̂:{max(rows_hat[lam][0] for lam in rows_hat):.4f} vs "
          f"V:{max(rows_true[lam][0] for lam in rows_true):.4f}")
    check("E4 稀疏回报任务上减去基线降低梯度估计方差",
          var["return_minus_V"] < var["full_return"],
          f"{var['return_minus_V']:.3e} < {var['full_return']:.3e}")
    check("E4 稠密噪声奖励下 TD 残差/GAE 的方差远低于全回报",
          var_noisy["td_residual"] < var_noisy["full_return"] / 3
          and var_noisy["gae_lambda_0.95"] < var_noisy["full_return"] / 2,
          f"td={var_noisy['td_residual']:.3e} gae95={var_noisy['gae_lambda_0.95']:.3e} "
          f"full={var_noisy['full_return']:.3e}")

    print(f"\n断言汇总: PASS={len(PASSED)}  FAIL={len(FAILED)}")
    if FAILED:
        print("失败项: " + "; ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
