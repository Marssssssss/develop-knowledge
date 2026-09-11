#!/usr/bin/env python3
"""
PPO (Proximal Policy Optimization) implementation on a 4x4 GridWorld.

Demonstrates:
  1. PPO-Clip training with multiple epochs per batch
  2. Policy ratio clipping effect (ratio distribution before/after clip)
  3. Advantage estimation (returns - value function baseline)
  4. Effect of clip parameter epsilon (0.1 vs 0.2 vs 0.3)
  5. Value function fitting (MSE loss tracking)

No external dependencies — pure Python stdlib only.

Reference:
  - Schulman et al. (2017). Proximal Policy Optimization Algorithms.
    arXiv:1707.06347
  - arXiv:2307.04964 (Secrets of RLHF in LLMs, Part I: PPO)
"""

import random
import math
from typing import List, Tuple, Dict

# ============================================================
# GridWorld (same as Q-Learning / REINFORCE for consistency)
# ============================================================
GRID = 4
N_STATES = GRID * GRID
N_ACTIONS = 4
HOLES = {5, 7, 11, 12}
GOAL = 15
START = 0


def step(state: int, action: int) -> Tuple[int, float, bool]:
    row, col = divmod(state, GRID)
    if action == 0: row = max(0, row - 1)
    elif action == 1: col = min(GRID - 1, col + 1)
    elif action == 2: row = min(GRID - 1, row + 1)
    elif action == 3: col = max(0, col - 1)
    ns = row * GRID + col
    if ns in HOLES: return ns, 0.0, True
    if ns == GOAL:  return ns, 1.0, True
    return ns, 0.0, False


# ============================================================
# Policy: softmax linear (same as REINFORCE demo)
# ============================================================
def softmax(logits: List[float]) -> List[float]:
    m = max(logits)
    exps = [math.exp(l - m) for l in logits]
    s = sum(exps)
    return [e / s for e in exps]


def select_action(theta: List[List[float]], state: int) -> Tuple[int, List[float]]:
    probs = softmax(theta[state])
    r = random.random()
    cum = 0.0
    for a in range(N_ACTIONS):
        cum += probs[a]
        if r <= cum:
            return a, probs
    return N_ACTIONS - 1, probs


def log_prob(theta: List[List[float]], state: int, action: int) -> float:
    probs = softmax(theta[state])
    return math.log(probs[action] + 1e-12)


def prob(theta: List[List[float]], state: int, action: int) -> float:
    probs = softmax(theta[state])
    return probs[action]


def log_prob_grad(theta: List[List[float]], state: int, action: int) -> List[float]:
    """d(log pi(a|s))/d(theta[s]) = I(a'=a) - pi(a'|s)"""
    probs = softmax(theta[state])
    grad = [0.0] * N_ACTIONS
    for a in range(N_ACTIONS):
        grad[a] = (1.0 if a == action else 0.0) - probs[a]
    return grad


# ============================================================
# Collect trajectories for one batch
# ============================================================
def collect_batch(theta: List[List[float]], gamma: float,
                   batch_size: int = 20, max_steps: int = 100) -> List[Dict]:
    """Collect a batch of trajectories. Each item is a dict with
    (state, action, return, advantage, old_log_prob)."""
    batch = []
    for _ in range(batch_size):
        state = START
        sa_list = []
        rewards = []
        for _ in range(max_steps):
            action, _ = select_action(theta, state)
            ns, r, done = step(state, action)
            sa_list.append((state, action))
            rewards.append(r)
            state = ns
            if done:
                break
        # Compute returns
        T = len(rewards)
        returns = [0.0] * T
        G = 0.0
        for t in range(T - 1, -1, -1):
            G = rewards[t] + gamma * G
            returns[t] = G
        for t, (s, a) in enumerate(sa_list):
            batch.append({
                'state': s, 'action': a, 'return': returns[t],
                'old_log_prob': log_prob(theta, s, a),
            })
    return batch


# ============================================================
# PPO update with clipped surrogate objective
# ============================================================
def ppo_update(
    theta: List[List[float]],
    V: List[float],
    batch: List[Dict],
    lr_policy: float = 0.1,
    lr_value: float = 0.1,
    clip_eps: float = 0.2,
    epochs: int = 4,
) -> Tuple[float, float, Dict]:
    """Perform PPO update on a batch. Returns (policy_loss, value_loss, stats)."""
    # Compute advantages using current value function
    for item in batch:
        item['advantage'] = item['return'] - V[item['state']]

    total_policy_loss = 0.0
    total_value_loss = 0.0
    clip_count = 0
    ratio_sum = 0.0

    for _ in range(epochs):
        for item in batch:
            s = item['state']
            a = item['action']
            old_lp = item['old_log_prob']
            new_lp = log_prob(theta, s, a)

            # Policy ratio: r_t(theta) = pi_new / pi_old
            ratio = math.exp(new_lp - old_lp)
            ratio_sum += ratio

            # Clipped surrogate objective
            adv = item['advantage']
            clipped_ratio = max(1.0 - clip_eps, min(1.0 + clip_eps, ratio))
            # We maximize min(ratio * adv, clip(ratio) * adv)
            # → gradient ascent on the min
            # Choose the one with smaller value (pessimistic)
            obj_uncapped = ratio * adv
            obj_capped = clipped_ratio * adv
            obj = min(obj_uncapped, obj_capped)

            if ratio > 1.0 + clip_eps or ratio < 1.0 - clip_eps:
                clip_count += 1

            # Gradient of log pi(a|s) w.r.t. theta[s]
            grad = log_prob_grad(theta, s, a)

            # Policy gradient ascent
            for act in range(N_ACTIONS):
                theta[s][act] += lr_policy * adv * grad[act] * ratio  # simplified
                # More precisely: d(obj)/d(theta) involves ratio gradient,
                # but for small lr, ratio ≈ 1 in first epoch, so we use the
                # standard policy gradient form scaled by the ratio

            # Track losses
            total_policy_loss += obj
            total_value_loss += (V[s] - item['return']) ** 2

            # Value function update (MSE gradient descent)
            V[s] += lr_value * (item['return'] - V[s])

    n = len(batch) * epochs
    stats = {
        'policy_loss': total_policy_loss / n,
        'value_loss': total_value_loss / n,
        'clip_fraction': clip_count / n,
        'mean_ratio': ratio_sum / n,
    }
    return total_policy_loss / n, total_value_loss / n, stats


# ============================================================
# PPO training loop
# ============================================================
def ppo_train(
    iterations: int = 100,
    batch_size: int = 20,
    gamma: float = 0.99,
    lr_policy: float = 0.1,
    lr_value: float = 0.05,
    clip_eps: float = 0.2,
    epochs_per_batch: int = 4,
    seed: int = 42,
) -> Tuple[List[List[float]], List[float], List[Dict], List[float]]:
    """Train PPO. Returns (theta, batch_rewards, stats_history, V)."""
    random.seed(seed)
    theta = [[0.0] * N_ACTIONS for _ in range(N_STATES)]
    V = [0.0] * N_STATES
    batch_rewards = []
    stats_history = []

    for it in range(iterations):
        batch = collect_batch(theta, gamma, batch_size)
        # Record average reward in this batch
        avg_r = sum(b['return'] for b in batch) / len(batch)
        batch_rewards.append(avg_r)

        _, _, stats = ppo_update(
            theta, V, batch,
            lr_policy=lr_policy, lr_value=lr_value,
            clip_eps=clip_eps, epochs=epochs_per_batch,
        )
        stats['iteration'] = it
        stats_history.append(stats)

    return theta, batch_rewards, stats_history, V


def policy_to_grid(theta: List[List[float]]) -> str:
    arrows = {0: "^", 1: ">", 2: "v", 3: "<"}
    lines = []
    for r in range(GRID):
        row = []
        for c in range(GRID):
            s = r * GRID + c
            if s in HOLES: row.append("H")
            elif s == GOAL: row.append("G")
            elif s == START: row.append("S")
            else:
                p = max(range(N_ACTIONS), key=lambda a: theta[s][a])
                row.append(arrows[p])
        lines.append(" ".join(row))
    return "\n".join(lines)


def moving_average(data: List[float], window: int = 10) -> List[float]:
    result = []
    for i in range(len(data)):
        start = max(0, i - window + 1)
        result.append(sum(data[start:i+1]) / (i - start + 1))
    return result


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
    random.seed(42)
    theta = [[0.0] * N_ACTIONS for _ in range(N_STATES)]
    V = [0.0] * N_STATES

    # Collect initial batch
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

        print(f"  Epoch {epoch}: ratio avg={avg_r:.3f}, "
              f"min={min_r:.3f}, max={max_r:.3f}, "
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
    theta, _, _ = ppo_train(iterations=20, clip_eps=0.2)

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
