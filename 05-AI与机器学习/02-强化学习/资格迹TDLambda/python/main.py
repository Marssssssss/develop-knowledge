"""TD(λ) 资格迹:前向视图 / 后向视图 / n 步回报 / 累积迹与替换迹。

教材依据:Sutton & Barto《Reinforcement Learning: An Introduction》1st ed. 在线版
第 7 章(node72~node84),本 demo 的实验对应其 Example 7.1 / 7.2 / 7.5 与 §7.4。

实验:
  E1 n 步 TD 回报(n=1,2,4,8,16,∞)在同一 19 状态随机游走上的 RMS 误差曲线
  E2 前向视图的 λ-回报算法 与 后向视图的 TD(λ) 在**离线更新**下逐步等价(§7.4)
  E3 RMS 误差随 λ 的变化(复现 §7.2 图 7.6 的"中间 λ 最好"形状)
  E4 累积迹 vs 替换迹:S&B Example 7.5 那类"错误动作反复被选"的任务上替换迹更快
  E5 TD(1) == 每次访问蒙特卡洛(offline)
"""

import math
import random
import sys

from td_lambda_core import (
    RandomWalk, forward_lambda_increment, lambda_returns, lambda_returns_direct,
    mc_update, n_step_return, n_step_returns, rms_error, td_lambda_offline,
    td_zero_update, train_td_lambda,
)

PASSED, FAILED = [], []


def check(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  |  {detail}" if detail else ""))


def main():
    rw = RandomWalk(n=19, rng=random.Random(1))
    true_v = rw.true_values()

    # ---- A1 真实值函数 ----
    check("19 状态随机游走的真实值函数为 s/20", abs(true_v[19] - 0.95) < 1e-12
          and abs(true_v[1] - 0.05) < 1e-12, f"V(1)={true_v[1]:.3f} V(19)={true_v[19]:.3f}")

    # ---- E1 n 步 TD ----
    print("\n=== E1 n 步 TD 回报(19 状态随机游走,100 episodes × 20 次重复的中位 RMS) ===")
    n_list = [1, 2, 4, 8, 16, 1000]
    e1 = {}
    for nstep in n_list:
        errs = []
        for rep in range(20):
            V, _ = train_td_lambda(episodes=100, alpha=0.05, predictor="n_step",
                                   n_step=nstep, seed=100 + rep)
            errs.append(rms_error(V, true_v))
        errs.sort()
        e1[nstep] = errs[len(errs) // 2]
        print(f"  n={nstep:<4d}  RMS={e1[nstep]:.4f}")

    # ---- 先自检 λ-回报的实现:后向递推 == 定义式(7.3) ----
    Vchk = {s: 0.5 for s in range(1, 20)}
    rwchk = RandomWalk(n=19, rng=random.Random(5))
    st, rw_ = rwchk.episode()
    st, rw_ = st[:40], rw_[:40]          # 截一段短轨迹,便于按定义式展开
    worst_define = 0.0
    for lam in (0.1, 0.3, 0.7, 0.9):
        a = lambda_returns(rw_, Vchk, st, 1.0, lam)
        b = lambda_returns_direct(rw_, Vchk, st, 1.0, lam)
        worst_define = max(worst_define, max(abs(x - y) for x, y in zip(a, b)))
    print(f"\n  λ-回报实现自检:递推 vs 定义式最大差 = {worst_define:.2e}")

    # ---- E2 前向 / 后向视图等价 ----
    print("\n=== E2 前向视图 vs 后向视图(离线更新,逐状态比较增量) ===")
    e2 = {}
    for lam in (0.0, 0.3, 0.7, 0.9, 1.0):
        V = {s: 0.5 for s in range(1, 20)}
        rw2 = RandomWalk(n=19, rng=random.Random(7))
        states, rewards = rw2.episode()
        fwd = forward_lambda_increment(V, states, rewards, 0.05, 1.0, lam)
        bwd, _ = td_lambda_offline(V, states, rewards, 0.05, 1.0, lam)
        keys = set(fwd) | set(bwd)
        worst = max(abs(fwd.get(k, 0.0) - bwd.get(k, 0.0)) for k in keys)
        e2[lam] = worst
        print(f"  λ={lam:<4}  最大逐状态增量差 = {worst:.2e}")

    # ---- 专项对照:λ=0 应等于 TD(0),λ=1 应等于每次访问 MC ----
    V = {s: 0.5 for s in range(1, 20)}
    rw3 = RandomWalk(n=19, rng=random.Random(11))
    states, rewards = rw3.episode()
    b0, _ = td_lambda_offline(V, states, rewards, 0.05, 1.0, 0.0)
    t0 = td_zero_update(V, states, rewards, 0.05, 1.0)
    worst0 = max(abs(b0.get(k, 0.0) - t0.get(k, 0.0)) for k in set(b0) | set(t0))
    b1, _ = td_lambda_offline(V, states, rewards, 0.05, 1.0, 1.0)
    mc = mc_update(V, states, rewards, 0.05, 1.0)
    worst1 = max(abs(b1.get(k, 0.0) - mc.get(k, 0.0)) for k in set(b1) | set(mc))

    # ---- E3 RMS 随 λ ----
    print("\n=== E3 RMS 误差随 λ 变化(100 episodes × 20 次重复的中位) ===")
    e3 = {}
    for lam in (0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 1.0):
        errs = []
        for rep in range(20):
            V2, _ = train_td_lambda(episodes=100, alpha=0.05, lam=lam, seed=200 + rep)
            errs.append(rms_error(V2, true_v))
        errs.sort()
        e3[lam] = errs[len(errs) // 2]
        print(f"  λ={lam:<5}  RMS={e3[lam]:.4f}")
    best_lam = min(e3, key=lambda k: e3[k])

    # ---- E4 累积迹 vs 替换迹 ----
    print("\n=== E4 累积迹 vs 替换迹(λ=0.9,100 episodes) ===")
    res4 = {}
    for name in ("accumulating", "replacing"):
        errs = []
        for rep in range(20):
            V3, mt = train_td_lambda(episodes=100, alpha=0.05, lam=0.9, trace=name,
                                     seed=300 + rep)
            errs.append(rms_error(V3, true_v))
        errs.sort()
        res4[name] = (errs[len(errs) // 2], mt)
        print(f"  {name:>12s}  RMS={res4[name][0]:.4f}  trace 上界={res4[name][1]:.3f}")

    # 替换迹的构造性性质:重复访问同一状态时迹恒 ≤1
    Vp = {s: 0.5 for s in range(1, 20)}
    seq_states = [5, 6, 5, 6, 5]
    seq_rewards = [0.0] * 4 + [1.0]
    _, mt_acc = td_lambda_offline(Vp, seq_states, seq_rewards, 0.05, 1.0, 0.9, "accumulating")
    _, mt_rep = td_lambda_offline(Vp, seq_states, seq_rewards, 0.05, 1.0, 0.9, "replacing")

    # ---- 断言 ----
    print()
    check("λ-回报的后向递推实现 == (7.3) 定义式", worst_define < 1e-9,
          f"max={worst_define:.2e}")
    check("E2 前向/后向视图在所有 λ 上等价(离线更新,误差 < 1e-9)",
          max(e2.values()) < 1e-9, f"max={max(e2.values()):.2e}")
    check("λ=0 的后向视图 == TD(0)", worst0 < 1e-12, f"max_diff={worst0:.2e}")
    check("λ=1 的后向视图 == 每次访问蒙特卡洛", worst1 < 1e-12, f"max_diff={worst1:.2e}")
    check("E1 中间步长优于两个极端(1 < n=4 < n=∞)",
          e1[4] < e1[1] and e1[4] < e1[1000],
          f"n=1:{e1[1]:.4f} n=4:{e1[4]:.4f} n=∞:{e1[1000]:.4f}")
    check("E3 最优 λ 落在 (0,1) 内部", 0.0 < best_lam < 1.0,
          f"best λ={best_lam} RMS={e3[best_lam]:.4f}")
    check("累积迹可超过 1(重复访问累加)", mt_acc > 1.0, f"max_trace={mt_acc:.3f}")
    check("替换迹恒不超过 1", mt_rep <= 1.0 + 1e-12, f"max_trace={mt_rep:.3f}")
    check("E4 替换迹优于累积迹", res4["replacing"][0] < res4["accumulating"][0],
          f"replacing={res4['replacing'][0]:.4f} < accumulating={res4['accumulating'][0]:.4f}")

    print(f"\n断言汇总: PASS={len(PASSED)}  FAIL={len(FAILED)}")
    if FAILED:
        print("失败项: " + "; ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
