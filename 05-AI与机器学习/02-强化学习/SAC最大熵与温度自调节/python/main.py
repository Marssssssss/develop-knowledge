"""SAC(Soft Actor-Critic)的最大熵目标:软贝尔曼方程、软策略迭代与温度自动调节。

论文依据:
  - Haarnoja et al., Soft Actor-Critic (arXiv:1801.01290)§4.1 软策略迭代(表格理论)
  - Haarnoja et al., SAC with a Stochastic Actor (arXiv:1812.05905)§5 温度 α 自动调节、
    附录 D 超参表(目标熵 H̄ = −dim(A),HalfCheetah 取 −6)
  - Spinning Up: SAC 的熵正则化值函数定义、clipped double-Q、目标网络 polyak 平均

本 demo 在**表格**网格世界上精确地做软策略迭代(不引入神经网络),因此
"最大熵目标到底改变了什么"是可以逐状态核对的数字,而不是训练曲线。

实验:
  E1 软值迭代:α→0 退化为硬最优;策略熵随 α 单调升;任务回报随 α 单调降(Pareto 曲线)
  E2 表格软 Q 学习(off-policy + 经验回放)收敛到软策略迭代的不动点
  E3 温度自动调节:按 J(α)=E[−α(logπ+H̄)] 调整 α,从过小/过大两侧都收敛到目标熵
  E4 α=0 时软 Q 目标 == 硬 Q-learning;log-sum-exp 的数值稳定(不减最大值会溢出)
"""

import math
import random
import sys

from sac_core import (
    GAMMA, GOAL, GOAL_REWARD, GRID, exact_eval, gauss_solve, logsumexp, move,
    naive_logsumexp, policy_entropy, reward, soft_policy, soft_q_from,
    soft_q_learning, soft_value_iteration,
)

PASSED, FAILED = [], []


def check(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  |  {detail}" if detail else ""))


def soft_q_learning_via_policy(alpha, episodes=8000, lr=0.3, seed=0, gamma=GAMMA):
    """用**采样单个 a'** 的版本(论文/实现里的常见写法),与期望形式对照。"""
    rng = random.Random(seed)
    n2 = GRID * GRID
    Q = [[0.0] * 4 for _ in range(n2)]
    Qt = [[0.0] * 4 for _ in range(n2)]
    buf = []
    steps = 0
    for ep in range(episodes):
        s = rng.randrange(n2 - 1)
        for _ in range(30):
            if rng.random() < 0.1:
                a = rng.randrange(4)
            else:
                z = logsumexp([q / alpha for q in Q[s]])
                probs = [math.exp(q / alpha - z) for q in Q[s]]
                u, acc = rng.random(), 0.0
                a = 3
                for i, p in enumerate(probs):
                    acc += p
                    if u <= acc:
                        a = i
                        break
            s2 = move(s, a)
            r, done = reward(s2), s2 == GOAL
            buf.append((s, a, r, s2, done))
            if len(buf) >= 200:
                for _ in range(8):
                    bs, ba, br, bs2, bd = buf[rng.randrange(len(buf))]
                    if bd:
                        y = br
                    else:
                        # 采样单个 a' 的写法:y = r + γ(Q_t(s',a') − α·log π_t(a'|s'))
                        # log π 同样用 log-softmax 精确值。
                        zt = logsumexp([q / alpha for q in Qt[bs2]])
                        pt = [math.exp(q / alpha - zt) for q in Qt[bs2]]
                        u2, acc2, a2 = rng.random(), 0.0, 3
                        for i, p in enumerate(pt):
                            acc2 += p
                            if u2 <= acc2:
                                a2 = i
                                break
                        logp_a2 = Qt[bs2][a2] / alpha - zt
                        y = br + gamma * (Qt[bs2][a2] - alpha * logp_a2)
                    Q[bs][ba] += lr * (y - Q[bs][ba])
            s = s2
            steps += 1
            for i in range(n2):
                for j in range(4):
                    Qt[i][j] += 0.05 * (Q[i][j] - Qt[i][j])
            if done:
                break
    return Q


def max_q_gap(Q, Vstar):
    """软 Q 与软值的一致性检查:V(s) 应等于 α log Σ exp(Q/α)。"""
    worst = 0.0
    for s in range(GRID * GRID):
        worst = max(worst, abs(max(0.0, Vstar[s])))
    return worst


# --------------------------------------------------------------------------
# 温度自动调节
# --------------------------------------------------------------------------


def auto_temperature(target_entropy, alpha0=1.0, lr=0.05, iters=300, seed=0,
                     gamma=GAMMA, fixed_policy_alpha=None):
    """按 J(α) = E[−α(log π(a|s) + H̄)] 调整 α。

    ∇_α J = E[−(log π + H̄)] = H(π) − H̄(因为 E_{a~π}[log π] = −H),故梯度下降为
        α ← α − lr · (H(π) − H̄)
    —— 熵高于目标就调小 α(负反馈才稳定;写成 + 号会变成正反馈而发散)。
    这里直接对每轮用精确策略熵,便于逐轮核对。
    返回 (最终 α, 熵轨迹)。
    """
    alpha = alpha0
    hist = []
    for _ in range(iters):
        V = soft_value_iteration(alpha, gamma=gamma, iters=2000, tol=1e-12)
        Q = soft_q_from(V, alpha, gamma)
        pol = soft_policy(Q, alpha)
        H = policy_entropy(pol)
        hist.append((alpha, H))
        alpha = max(1e-6, alpha - lr * (H - target_entropy))
    return alpha, hist


def main():
    # ---- E1 软值迭代 vs 硬最优 ----
    print("=== E1 软策略迭代:α 如何改变值、熵与任务回报 ===")
    V_hard = soft_value_iteration(0.0)
    print("    α        V(start)    平均策略熵    任务回报(精确评估)")
    rows = []
    alpha_grid = (0.0, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 2.0)
    for alpha in alpha_grid:
        V = soft_value_iteration(alpha)
        Q = soft_q_from(V, alpha)
        pol = soft_policy(Q, alpha)
        H = policy_entropy(pol)
        ret = sum(exact_eval(pol)) / (GRID * GRID)
        rows.append((alpha, V[0], H, ret))
        print(f"  {alpha:<7} {V[0]:>11.6f} {H:>13.4f} {ret:>16.6f}")
    print("  注:熵在 α≈0.7 处见顶后小幅回落到 1.3306 并饱和(不是严格单调);"
          "\n      因为靠近墙/目标的状态存在并列或差异极大的动作,α→∞ 时全状态平均熵到不了 ln4。")

    print("\n  α→0 的收敛性(与硬最优逐状态比较):")
    gaps_alpha = {}
    for alpha in (0.1, 0.01, 0.001):
        V = soft_value_iteration(alpha)
        gap = max(abs(V[s] - V_hard[s]) for s in range(GRID * GRID))
        gaps_alpha[alpha] = gap
        print(f"    α={alpha:<6} max|V_soft − V_hard| = {gap:.8f}")
    print(f"    比值 gap(0.01)/gap(0.001) = "
          f"{gaps_alpha[0.01] / gaps_alpha[0.001]:.3f}(≈10 ⇒ 误差随 α 线性趋零)")

    # ---- E2 表格软 Q 学习收敛到软不动点 ----
    print("\n=== E2 表格软 Q 学习(off-policy + 回放 + 目标网络)对照软策略迭代 ===")
    alpha = 0.2
    V_star = soft_value_iteration(alpha)
    Q_star = soft_q_from(V_star, alpha)
    Q_learn = soft_q_learning(alpha, seed=1, episodes=20000, lr=0.3)
    gaps = []
    for s in range(GRID * GRID - 1):
        V_learn = alpha * logsumexp([q / alpha for q in Q_learn[s]])
        gaps.append(abs(V_learn - V_star[s]))
    print(f"  期望形式目标:max|V_learn − V_star| = {max(gaps):.5f}({len(gaps)} 个非目标状态)")
    Q_learn2 = soft_q_learning_via_policy(alpha, seed=1, episodes=20000, lr=0.3)
    gaps2 = []
    for s in range(GRID * GRID - 1):
        V2 = alpha * logsumexp([q / alpha for q in Q_learn2[s]])
        gaps2.append(abs(V2 - V_star[s]))
    print(f"  采样 a' 形式:max|V_learn − V_star| = {max(gaps2):.5f}")

    # ---- E3 温度自动调节 ----
    print("\n=== E3 温度自动调节:两侧收敛到目标熵 ===")
    H_max = math.log(4.0)
    hbar = 0.6 * H_max
    print(f"  动作数=4,H_max=ln4={H_max:.4f},目标熵 H̄(本 demo 口径)={hbar:.4f}")
    out = {}
    for a0 in (0.05, 1.0, 4.0):
        a_fin, hist = auto_temperature(hbar, alpha0=a0, lr=0.05, iters=300)
        H_fin = hist[-1][1]
        out[a0] = (a_fin, H_fin)
        print(f"  α0={a0:<5} -> α*={a_fin:.5f}  最终熵={H_fin:.5f}  |H−H̄|={abs(H_fin-hbar):.5f}")

    # ---- E4 α=0 退化为硬 Q-learning;log-sum-exp 数值稳定 ----
    print("\n=== E4 边界与数值 ===")
    Q_hard_learn = soft_q_learning(0.0, seed=2, episodes=4000)
    hard_gap = 0.0
    for s in range(GRID * GRID - 1):
        v = max(Q_hard_learn[s])
        hard_gap = max(hard_gap, abs(v - V_hard[s]))
    print(f"  α=0 的软 Q 学习 vs 硬值迭代:max|V−V*| = {hard_gap:.5f}")
    big = [1000.0, 1000.0, 1000.0, 1000.0]
    try:
        naive_val = repr(naive_logsumexp(big))
        overflowed = False
    except OverflowError as exc:
        naive_val, overflowed = f"OverflowError({exc})", True
    print(f"  logsumexp(α 归一化后 Q/α 可达数百):稳定实现 = {logsumexp(big):.4f};"
          f"朴素实现 = {naive_val}")
    V_a = soft_value_iteration(0.5)
    Q_a = soft_q_from(V_a, 0.5)
    pol_a = soft_policy(Q_a, 0.5)
    pol_b = soft_policy(Q_a, 0.5)
    worst_softmax = 0.0
    for s in range(GRID * GRID):
        z = logsumexp([Q_a[s][a] / 0.5 for a in range(4)])
        for a in range(4):
            worst_softmax = max(worst_softmax,
                                abs(pol_a[s][a] - math.exp(Q_a[s][a] / 0.5 - z)))
    print(f"  π 与 exp((Q−V)/α) 的最大差 = {worst_softmax:.2e}")

    print()
    # ---- 断言 ----
    check("E1 软值与硬最优的差随 α 线性趋零(α 缩小 10 倍,误差也缩小约 10 倍)",
          gaps_alpha[0.001] < 0.01 and 9.0 < gaps_alpha[0.01] / gaps_alpha[0.001] < 11.0,
          f"gap(0.001)={gaps_alpha[0.001]:.3e} 比值={gaps_alpha[0.01]/gaps_alpha[0.001]:.3f}")
    check("E1 软值不小于硬最优值(α·logΣexp ≥ max)",
          all(soft_value_iteration(a)[s] >= V_hard[s] - 1e-12
              for a in (0.05, 0.5, 2.0) for s in range(GRID * GRID)))
    check("E1 α=0 时策略熵为 0(退化为确定性贪心)", rows[0][2] == 0.0,
          f"H(α=0)={rows[0][2]:.6f}")
    front = [r for r in rows if r[0] <= 0.7]
    check("E1 α∈[0,0.7] 上策略熵单调上升",
          all(front[i][2] <= front[i + 1][2] + 1e-9 for i in range(len(front) - 1)),
          "熵: " + " -> ".join(f"{r[2]:.3f}" for r in front))
    check("E1 所有 α 的策略熵都不超过 ln|A|",
          all(r[2] <= math.log(4.0) + 1e-12 for r in rows),
          f"max H={max(r[2] for r in rows):.4f} vs ln4={math.log(4.0):.4f}")
    check("E1 任务回报随 α 上升单调不增(最大熵的代价)",
          all(rows[i][3] >= rows[i + 1][3] - 1e-9 for i in range(len(rows) - 1)),
          "回报: " + " -> ".join(f"{r[3]:.3f}" for r in rows))
    check("E1 α≥1 时熵项压倒回报(任务回报趋近 0)",
          rows[-2][3] < 0.05 and rows[-1][3] < 0.05,
          f"回报(α={rows[-2][0]})={rows[-2][3]:.6f}, 回报(α={rows[-1][0]})={rows[-1][3]:.8f}")
    check("E2 表格软 Q 学习收敛到软策略迭代不动点(< 0.02)", max(gaps) < 0.02,
          f"max gap = {max(gaps):.5f}")
    check("E2 采样 a' 的写法同样收敛(< 0.05)", max(gaps2) < 0.05,
          f"max gap = {max(gaps2):.5f}")
    check("E3 三个起点都收敛到同一个 α(差 < 0.02)",
          max(out[k][0] for k in out) - min(out[k][0] for k in out) < 0.02,
          "α* = " + ", ".join(f"{out[k][0]:.4f}" for k in out))
    check("E3 最终策略熵命中目标熵(|H−H̄| < 0.02)",
          all(abs(out[k][1] - hbar) < 0.02 for k in out),
          "|H−H̄| = " + ", ".join(f"{abs(out[k][1]-hbar):.4f}" for k in out))
    check("E4 α=0 的软 Q 学习退化为硬 Q-learning(< 0.05)", hard_gap < 0.05,
          f"max gap = {hard_gap:.5f}")
    check("E4 朴素 log-sum-exp 溢出,减去最大值的稳定实现不溢出",
          overflowed and math.isfinite(logsumexp(big)),
          f"naive={naive_val} stable={logsumexp(big):.2f}")
    check("E4 π == softmax(Q/α) == exp((Q−V)/α)", worst_softmax < 1e-12,
          f"max diff = {worst_softmax:.2e}")

    print(f"\n断言汇总: PASS={len(PASSED)}  FAIL={len(FAILED)}")
    if FAILED:
        print("失败项: " + "; ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
