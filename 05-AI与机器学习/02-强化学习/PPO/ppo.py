#!/usr/bin/env python3
"""PPO 演示脚本(5 个 demo):批内多 epoch 复用、比率裁剪、优势估计、价值函数拟合。

核心实现(GridWorld / softmax 策略 / ppo_update / ppo_train)在 ppo_core.py,
以便单文件守 300 行上限(OPTIMIZATION.md §1.1)。

运行: python ppo.py
"""

import math
import random

from ppo_core import (
    GOAL, GRID, HOLES, N_ACTIONS, N_STATES, START,
    collect_batch, log_prob, moving_average, policy_to_grid, ppo_train, ppo_update,
)

# ============================================================
# Demos
# ============================================================
def demo1_basic():
    print("=" * 60)
    print("Demo 1: PPO-Clip Training (100 iterations, eps=0.2)")
    print("=" * 60)
    theta, rewards, stats, V = ppo_train(iterations=100, clip_eps=0.2)
    ma = moving_average(rewards, 10)
    success = sum(1 for r in rewards[-20:] if r > 0) / 20
    print(f"  Success rate (last 20 batches): {success:.1%}")
    print(f"  Avg return (10-batch window): first={ma[9]:.3f}, last={ma[-1]:.3f}")
    print(f"\n  Final greedy policy:")
    print(policy_to_grid(theta))
    print(f"\n  Final value function:")
    for r in range(GRID):
        vals = [f"{V[r * GRID + c]:.3f}" for c in range(GRID)]
        print(f"    {' '.join(vals)}")


def demo2_clip_effect():
    print("\n" + "=" * 60)
    print("Demo 2: Clip Parameter Comparison")
    print("=" * 60)
    for eps in [0.1, 0.2, 0.3]:
        theta, rewards, stats, _ = ppo_train(iterations=100, clip_eps=eps)
        ma = moving_average(rewards, 10)
        success = sum(1 for r in rewards[-20:] if r > 0) / 20
        avg_clip = sum(s['clip_fraction'] for s in stats[-10:]) / 10
        avg_ratio = sum(s['mean_ratio'] for s in stats[-10:]) / 10
        print(f"  eps={eps:.1f}: success={success:.1%}, "
              f"avg_return={ma[-1]:.3f}, "
              f"clip_frac={avg_clip:.2%}, "
              f"mean_ratio={avg_ratio:.3f}")


def demo3_ratio_distribution():
    print("\n" + "=" * 60)
    print("Demo 3: Policy Ratio Distribution During Training")
    print("=" * 60)
    # 必须先让策略离开均匀分布:均匀策略下同一批数据里几乎所有轨迹都落进洞
    # (奖励全 0 → 优势全 0 → 梯度为 0),比率会永远精确停在 1.000,演示退化成空转。
    random.seed(42)
    theta, _, _, V = ppo_train(iterations=20, clip_eps=0.2)

    # 用「已训练过的策略」采一批数据,记录其 old_log_prob 作为比率分母
    batch = collect_batch(theta, 0.99, batch_size=20)

    # Track ratios across epochs
    for epoch in range(4):
        ratios = []
        for item in batch:
            s, a = item['state'], item['action']
            old_lp = item['old_log_prob']
            new_lp = log_prob(theta, s, a)
            ratio = math.exp(new_lp - old_lp)
            ratios.append(ratio)

        avg_r = sum(ratios) / len(ratios)
        min_r = min(ratios)
        max_r = max(ratios)
        clipped = sum(1 for r in ratios if r > 1.2 or r < 0.8)

        print(f"  Epoch {epoch}: ratio avg={avg_r:.4f}, "
              f"min={min_r:.4f}, max={max_r:.4f}, "
              f"clipped={clipped}/{len(ratios)}")

        # Do one PPO update epoch
        ppo_update(theta, V, batch, lr_policy=0.1, lr_value=0.05,
                   clip_eps=0.2, epochs=1)


def demo4_advantage_estimation():
    print("\n" + "=" * 60)
    print("Demo 4: Advantage Estimation (Returns vs Value Baseline)")
    print("=" * 60)
    random.seed(42)
    theta = [[0.0] * N_ACTIONS for _ in range(N_STATES)]
    # Train briefly to get non-trivial value function
    # ppo_train 返回 (theta, batch_rewards, stats_history, V) —— 四个值
    theta, _, _, V = ppo_train(iterations=20, clip_eps=0.2)

    batch = collect_batch(theta, 0.99, batch_size=20)
    print(f"  {'State':>5} {'Action':>6} {'Return':>8} {'V(s)':>8} {'Advantage':>10}")
    print(f"  {'-'*5} {'-'*6} {'-'*8} {'-'*8} {'-'*10}")
    for item in batch[:15]:
        s, a, ret = item['state'], item['action'], item['return']
        v = V[s]
        adv = ret - v
        print(f"  {s:>5} {a:>6} {ret:>8.3f} {v:>8.3f} {adv:>10.3f}")


def demo5_value_loss_tracking():
    print("\n" + "=" * 60)
    print("Demo 5: Value Function Loss Tracking")
    print("=" * 60)
    theta, rewards, stats, _ = ppo_train(iterations=100, clip_eps=0.2)

    indices = [i * (len(stats) - 1) // 10 for i in range(11)]
    print("  Iter | Policy Loss | Value Loss | Clip Frac | Mean Ratio")
    print("  -----|-------------|------------|-----------|-----------")
    for idx in indices:
        s = stats[idx]
        print(f"  {s['iteration']:>4} | {s['policy_loss']:>11.4f} | "
              f"{s['value_loss']:>10.4f} | {s['clip_fraction']:>9.2%} | "
              f"{s['mean_ratio']:>10.3f}")


if __name__ == "__main__":
    demo1_basic()
    demo2_clip_effect()
    demo3_ratio_distribution()
    demo4_advantage_estimation()
    demo5_value_loss_tracking()
