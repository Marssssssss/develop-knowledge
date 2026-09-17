"""Double Q-learning 与最大化偏差:四个实验 + 结构断言。

  E1 估计论层面:单估计量 max 的偏差随动作数 m 增长(对照闭式解),双估计量无偏
  E2 Theorem 1 下界 √(C/(m−1)):随机可行点全满足,且"极端构造"取到等号(紧)
  E3 原文 §4.2 网格世界:Q-learning 把起点的 max Q 高估到真值之上
  E4 Lemma 1 低估方向:双估计量的低估幅度随噪声减小而消失

运行: python main.py
"""

import math
import random
import sys

from bias_theory import (
    bound_decomposition, double_estimator_bias, expected_max_gauss,
    expected_max_uniform, feasible, lemma1_underestimation, min_max_bias,
    single_estimator_bias,
)
from double_q_core import (
    DoubleQLearning, QLearning, StochasticGridWorld, alpha_linear, alpha_poly,
    eps_by_visits,
)

PASSED = []
FAILED = []


def check(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  |  {detail}" if detail else ""))


M_LIST = (2, 5, 10, 50, 200)
N_TRIALS = 20000
DELTA = 0.5   # E4 中"最优动作"的期望值高出其余动作的幅度


# ============================== E1:单估计量 vs 双估计量 ==============================


def experiment_1():
    print(f"\n=== E1 估计偏差:单 vs 双估计量(均匀噪声 σ=1,{N_TRIALS} 次试验) ===")
    rng = random.Random(20260917)
    single, double, exact = {}, {}, {}
    for m in M_LIST:
        single[m] = single_estimator_bias(m, 1.0, N_TRIALS, rng, "uniform")
        double[m] = double_estimator_bias(m, 1.0, N_TRIALS, rng, "uniform")
        exact[m] = expected_max_uniform(m, 1.0)
        print(f"  m={m:<4} 单估计量偏差={single[m]:+.4f} (闭式 {exact[m]:+.4f})  "
              f"双估计量偏差={double[m]:+.4f}")
    return single, double, exact


def experiment_1b():
    """同一件事在高斯噪声下再走一遍,并用数值积分给出闭式参照。"""
    print("\n=== E1b 高斯噪声对照(σ=1) ===")
    rng = random.Random(7)
    rows = []
    for m in (2, 5, 20, 100):
        mc = single_estimator_bias(m, 1.0, N_TRIALS, rng, "gauss")
        ref = expected_max_gauss(m, 1.0)
        rows.append((m, mc, ref))
        print(f"  m={m:<4} 单估计量偏差(MC)={mc:+.4f}  数值积分 E[max]={ref:+.4f}")
    return rows


# ============================== E2:Theorem 1 的下界及其紧性 ==============================


def random_feasible(m, C, rng):
    """在 {Σε=0, (1/m)Σε²=C} 上均匀取一点:先取随机方向再投影到 Σ=0 子空间并归一化。"""
    v = [rng.gauss(0.0, 1.0) for _ in range(m)]
    mean = sum(v) / m
    v = [x - mean for x in v]
    nrm = math.sqrt(sum(x * x for x in v))
    if nrm < 1e-15:
        return None
    scale = math.sqrt(m * C) / nrm
    return [x * scale for x in v]


def experiment_2():
    print("\n=== E2 Theorem 1:max_a Q_a ≥ V* + √(C/(m−1)) 的紧性 ===")
    rng = random.Random(99)
    rows = []
    worst_res = 0.0
    for m in (3, 5, 10, 50):
        C = 1.0
        bound, eps = min_max_bias(C, m)
        ok_feasible = feasible(C, m, eps)
        attained = max(eps)
        worst = 1e18
        for _ in range(4000):
            e = random_feasible(m, C, rng)
            if e is None:
                continue
            t = max(e)
            worst = min(worst, t)
            # 代数核对:mC + mt² ≤ m²t²  ⇒  t ≥ √(C/(m−1))
            t2, sd, mt, sd2, lhs, rhs = bound_decomposition(C, m, e)
            worst_res = max(worst_res, abs(sd - mt), abs(sd2 - lhs) / max(1.0, lhs))
            if not (lhs <= rhs + 1e-9 and t2 >= bound - 1e-12):
                worst_res = 1e9
        rows.append((m, bound, attained, worst))
        print(f"  m={m:<4} 下界 √(C/(m−1))={bound:.6f}  构造取到 max ε={attained:.6f} "
              f"(可行={ok_feasible})  随机可行点最小 max ε={worst:.6f}")
    return rows, worst_res


# ============================== E3:随机奖励网格世界 ==============================


def one_run(kind, seed, total_steps, alpha_mode, rows=3, cols=4, gamma=0.95,
            max_steps=200, tail=200):
    """跑 total_steps 个**环境步**(不是回合数),返回末段平均回报与起点 max Q。"""
    rng = random.Random(seed)
    env = StochasticGridWorld(rows, cols, gamma, random.Random(seed + 1))
    alpha_fn = alpha_linear if alpha_mode == "linear" else alpha_poly
    agent = (QLearning if kind == "q" else DoubleQLearning)(
        env.n_states, env.N_ACTIONS, gamma, alpha_fn, rng)
    epsf = eps_by_visits(agent)
    rewards = []
    s = env.start
    for step in range(total_steps):
        a = agent.act(s, epsf(s))
        s2, r, done = env.step(s, a)
        agent.update(s, a, r, s2, done)
        rewards.append(r)
        if done:
            s = env.start
        else:
            s = s2
    tail = min(tail, len(rewards))
    return {
        "avg_reward": sum(rewards[-tail:]) / tail,
        "max_q_start": agent.max_at(env.start),
    }


def experiment_3(n_runs=200, total_steps=10000, alpha_modes=("poly", "linear")):
    env = StochasticGridWorld(3, 4, 0.95, random.Random(0))
    truth = env.true_start_value()
    print(f"\n=== E3 {env.rows}×{env.cols} 随机奖励网格(原文 §4.2):真值 V*(S)={truth:.4f} ===")
    out = {}
    for am in alpha_modes:
        for kind in ("q", "double"):
            res = [one_run(kind, s, total_steps, am) for s in range(n_runs)]
            ar = sum(r["avg_reward"] for r in res) / n_runs
            mq = sum(r["max_q_start"] for r in res) / n_runs
            out[(am, kind)] = (ar, mq)
            print(f"  α={am:<7}{kind:>7s} 平均每步回报={ar:+.4f}  "
                  f"起点 max Q={mq:+.4f}  高估={mq - truth:+.4f}")
        print()
    return out, truth


# ============================== E4:Lemma 1 的低估方向 ==============================


def experiment_4():
    print("\n=== E4 Lemma 1:双估计量在最优动作可能被选错时**低估** ===")
    rng = random.Random(5)
    m = 4
    rows = []
    for sigma in (0.2, 0.5, 1.0, 2.0):
        val, missed = lemma1_underestimation(m, sigma, DELTA, 20000, rng, "uniform")
        rows.append((sigma, val, missed))
        print(f"  σ={sigma:<4} E[μ^B_a*]={val:+.4f}  (max_a E[X_a]={DELTA:+.2f})  "
              f"选错最优动作的比例={missed:.3f}")
    return rows


# ==========================================================================
# 结构断言
# ==========================================================================


def test_structures():
    C, m = 4.0, 7
    bound, eps = min_max_bias(C, m)
    check("Theorem 1 构造:下界 √(C/(m−1)) 由极端构造取到",
          feasible(C, m, eps) and abs(max(eps) - bound) < 1e-12,
          f"bound={bound:.6f} max(eps)={max(eps):.6f}")

    e = [1.0, -1.0, 0.0, 0.0]
    check("可行性判定:Σε=0 但 Σε² 不匹配时被拒",
          not feasible(2.0, 4, e) and feasible(0.5, 4, e),
          f"(1/4)Σε²={sum(x*x for x in e)/4:.4f}")

    sw = StochasticGridWorld(3, 4, 0.95, random.Random(0))
    s2, r, done = sw.step(sw.goal, 0)
    check("网格世界:终点是吸收态且终止奖励为 +5", done and abs(r - 5.0) < 1e-12,
          f"s2={s2} r={r} done={done}")

    rng = random.Random(3)
    sw2 = StochasticGridWorld(3, 4, 0.95, rng)
    seen = set()
    for _ in range(4000):
        _, rr, dd = sw2.step(0, 2)
        seen.add(rr)
        if dd:
            seen.add("done")
    check("网格世界:非终止步奖励只取 {−12, +10}",
          seen <= {-12.0, 10.0, "done"}, f"观测到的取值={sorted(seen, key=str)}")

    truth = sw.true_start_value()
    gamma = 0.95
    formula = 5 * gamma ** 4 - sum(gamma ** k for k in range(4))
    check("真值核对:V*(S)=5γ⁴−Σγ^k≈0.36",
          abs(truth - formula) < 1e-9 and abs(truth - 0.3626) < 1e-3,
          f"值迭代={truth:.6f} 公式={formula:.6f}")

    rng = random.Random(4)
    ag = DoubleQLearning(4, 3, 0.9, alpha_linear, rng)
    ag.update(0, 1, 1.0, 2, False)
    only_a = all(c == 0 for c in ag.nB[0])
    ag.update(0, 1, 1.0, 2, False)
    for _ in range(20):
        ag.update(0, 1, 1.0, 2, False)
    both = sum(ag.nA[0]) > 0 and sum(ag.nB[0]) > 0
    check("Algorithm 1:每次更新只动 QA 或 QB 之一(两组各累计到更新)",
          only_a and both, f"首次仅 A={only_a} 两组都有={both}")


# ==========================================================================
# main
# ==========================================================================


def main():
    test_structures()
    single, double, exact = experiment_1()
    gauss_rows = experiment_1b()
    thm, thm_res = experiment_2()
    grid, truth = experiment_3()
    lem = experiment_4()

    worst_exact = max(abs(single[m] - exact[m]) for m in M_LIST)
    check("E1 单估计量 MC 偏差与闭式 σ(m−1)/(m+1) 一致(误差 < 0.02)",
          worst_exact < 0.02, f"max_err={worst_exact:.4f}")
    check("E1 单估计量偏差随 m 单调递增",
          all(single[M_LIST[i]] < single[M_LIST[i + 1]] for i in range(len(M_LIST) - 1)),
          " < ".join(f"{single[m]:.3f}" for m in M_LIST))
    check("E1 双估计量偏差在所有 m 上都接近 0(|bias| < 0.02)",
          max(abs(double[m]) for m in M_LIST) < 0.02,
          "max|bias|=" + "%.4f" % max(abs(double[m]) for m in M_LIST))
    check("E1 m=200 时单估计量的偏差超过 0.9",
          single[200] > 0.9, f"single[200]={single[200]:.4f}")

    worst_g = max(abs(r[1] - r[2]) for r in gauss_rows)
    check("E1b 高斯噪声下单估计量偏差与数值积分一致(误差 < 0.03)",
          worst_g < 0.03, f"max_err={worst_g:.4f}")

    check("E2 随机可行点的 max ε 从不低于下界(4000 点 × 4 个 m)",
          all(worst >= bound - 1e-12 for _, bound, _, worst in thm),
          "min over samples=" + ", ".join(f"{w:.4f}" for _, _, _, w in thm))
    check("E2 代数分解 Σδ=mt 与 Σδ²=mC+mt² 在全部随机可行点上成立,且 mC+mt² ≤ m²t²",
          thm_res < 1e-9, f"max_residual={thm_res:.2e}")
    check("E2 极端构造恰好取到等号(下界是紧的)",
          all(abs(max_e - bound) < 1e-12 for _, bound, max_e, _ in thm))

    ar_q, mq_q = grid[("poly", "q")]
    ar_d, mq_d = grid[("poly", "double")]
    check("E3 Q-learning 把起点 max Q 高估到真值之上(两种学习率下高估都 > 1)",
          all(grid[(am, "q")][1] > truth + 1.0 for am in ("poly", "linear")),
          ", ".join(f"α={am}: {grid[(am, 'q')][1]:+.2f}" for am in ("poly", "linear"))
          + f" vs 真值 {truth:.4f}")
    check("E3 Double Q-learning 的高估小于 Q-learning(α=1/n^0.8)",
          (mq_d - truth) < (mq_q - truth), f"double={mq_d - truth:+.4f} q={mq_q - truth:+.4f}")
    check("E3 Double Q-learning 的平均每步回报显著优于 Q-learning",
          ar_d >= ar_q + 0.5, f"double={ar_d:+.4f} q={ar_q:+.4f}")
    check("E3 Double Q-learning 的平均每步回报接近最优的 +0.2",
          abs(ar_d - 0.2) < 0.06, f"avg_reward={ar_d:+.4f}")

    strict = [(val < DELTA - 0.01) for _, val, _ in lem]
    missed_pos = [(missed > 0.05) for _, _, missed in lem]
    check("E4 Lemma 1 的充要条件:严格低估 ⟺ P(a*∉M) > 0(两者逐行一致)",
          strict == missed_pos,
          "低估=" + str(strict) + " 选错=" + [f"{m:.3f}" for _, _, m in lem].__str__())
    check("E4 低估幅度随噪声减小而收缩(σ=0.2 时几乎无偏)",
          lem[0][1] > lem[-1][1] and abs(lem[0][1] - DELTA) < 0.01,
          f"σ=0.2 -> {lem[0][1]:+.4f}, σ=2.0 -> {lem[-1][1]:+.4f}")
    check("E4 选错最优动作的比例随噪声单调递增",
          all(lem[i][2] < lem[i + 1][2] for i in range(len(lem) - 1)),
          "missed=" + ", ".join(f"{x:.3f}" for _, _, x in lem))

    print(f"\n断言汇总: PASS={len(PASSED)}  FAIL={len(FAILED)}")
    if FAILED:
        print("失败项: " + "; ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
