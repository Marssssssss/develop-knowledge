#!/usr/bin/env python3
"""
REINFORCE (Policy Gradient) implementation on a 4x4 GridWorld.

Demonstrates:
  1. Basic REINFORCE without baseline (high variance)
  2. REINFORCE with baseline (variance reduction)
  3. Effect of discount factor on returns
  4. Learning curve and policy evolution
  5. Variance comparison: with vs without baseline

No external dependencies — pure Python stdlib only.

Reference:
  - OpenAI Spinning Up: Vanilla Policy Gradient
  - Sutton & Barto, RL: An Introduction, Ch. 13 (Policy Gradient Methods)
  - Williams, R.J. (1992). Simple statistical gradient-following
    algorithms for connectionist reinforcement learning. Machine Learning, 8, 229-256.
"""

import random
import math
from typing import List, Tuple

# ============================================================
# GridWorld (same as Q-Learning demo for consistency)
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
# Policy: softmax linear policy theta[s] -> pi(a|s) = softmax(theta[s])
# ============================================================
def softmax(logits: List[float]) -> List[float]:
    """Numerically stable softmax."""
    m = max(logits)
    exps = [math.exp(l - m) for l in logits]
    s = sum(exps)
    return [e / s for e in exps]


def select_action(theta: List[List[float]], state: int) -> Tuple[int, List[float]]:
    """Sample action from softmax policy. Returns (action, prob_vector)."""
    probs = softmax(theta[state])
    r = random.random()
    cum = 0.0
    for a in range(N_ACTIONS):
        cum += probs[a]
        if r <= cum:
            return a, probs
    return N_ACTIONS - 1, probs


def log_prob_grad(theta: List[List[float]], state: int, action: int) -> Tuple[List[float], float]:
    """Compute d(log pi(a|s))/d(theta[s]) and log pi(a|s).
    
    For softmax: d(log pi(a|s))/d(theta[s][a']) = I(a'=a) - pi(a'|s)
    """
    probs = softmax(theta[state])
    grad = [0.0] * N_ACTIONS
    for a in range(N_ACTIONS):
        grad[a] = (1.0 if a == action else 0.0) - probs[a]
    lp = math.log(probs[action] + 1e-12)
    return grad, lp


# ============================================================
# Collect a trajectory
# ============================================================
def collect_trajectory(theta: List[List[float]], gamma: float,
                        max_steps: int = 100) -> Tuple[List[Tuple[int,int]], List[float], float]:
    """Run one episode. Returns (state_actions, returns, total_reward)."""
    states_actions = []
    rewards = []
    state = START
    total_r = 0.0

    for _ in range(max_steps):
        action, _ = select_action(theta, state)
        next_state, reward, done = step(state, action)
        states_actions.append((state, action))
        rewards.append(reward)
        total_r += reward
        state = next_state
        if done:
            break

    # Compute returns: G_t = sum_{k=t}^{T-1} gamma^(k-t) * r_k
    T = len(rewards)
    returns = [0.0] * T
    G = 0.0
    for t in range(T - 1, -1, -1):
        G = rewards[t] + gamma * G
        returns[t] = G

    return states_actions, returns, total_r


# ============================================================
# REINFORCE algorithm
# ============================================================
def reinforce(
    episodes: int = 2000,
    alpha: float = 0.1,
    gamma: float = 0.99,
    use_baseline: bool = False,
    seed: int = 42,
) -> Tuple[List[List[float]], List[float], List[float]]:
    """Run REINFORCE. Returns (theta, rewards, grad_norms)."""
    random.seed(seed)
    theta = [[0.0] * N_ACTIONS for _ in range(N_STATES)]
    V = [0.0] * N_STATES  # baseline value function (simple moving average)
    ep_rewards = []
    grad_norms = []

    for ep in range(episodes):
        sa_list, returns, total_r = collect_trajectory(theta, gamma)
        ep_rewards.append(total_r)

        # Compute gradient and update
        grad_norm = 0.0
        for t, (s, a) in enumerate(sa_list):
            grad, _ = log_prob_grad(theta, s, a)
            if use_baseline:
                advantage = returns[t] - V[s]
            else:
                advantage = returns[t]

            for act in range(N_ACTIONS):
                theta[s][act] += alpha * advantage * grad[act]
                grad_norm += (alpha * advantage * grad[act]) ** 2

            # Update baseline (simple moving average of returns)
            if use_baseline:
                V[s] += 0.01 * (returns[t] - V[s])

        grad_norms.append(math.sqrt(grad_norm))

    return theta, ep_rewards, grad_norms


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
                policy = max(range(N_ACTIONS), key=lambda a: theta[s][a])
                row.append(arrows[policy])
        lines.append(" ".join(row))
    return "\n".join(lines)


def moving_average(data: List[float], window: int = 50) -> List[float]:
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
    print("Demo 1: REINFORCE without baseline (2000 episodes)")
    print("=" * 60)
    theta, rewards, _ = reinforce(episodes=2000, alpha=0.1, gamma=0.99, use_baseline=False)
    ma = moving_average(rewards, 100)
    success = sum(1 for r in rewards[-200:] if r > 0) / 200
    print(f"  Success rate (last 200): {success:.1%}")
    print(f"  Avg reward (100-ep window): first={ma[99]:.3f}, last={ma[-1]:.3f}")
    print(f"\n  Greedy policy:")
    print(policy_to_grid(theta))


def demo2_with_baseline():
    print("\n" + "=" * 60)
    print("Demo 2: REINFORCE with baseline (2000 episodes)")
    print("=" * 60)
    theta, rewards, _ = reinforce(episodes=2000, alpha=0.1, gamma=0.99, use_baseline=True)
    ma = moving_average(rewards, 100)
    success = sum(1 for r in rewards[-200:] if r > 0) / 200
    print(f"  Success rate (last 200): {success:.1%}")
    print(f"  Avg reward (100-ep window): first={ma[99]:.3f}, last={ma[-1]:.3f}")
    print(f"\n  Greedy policy:")
    print(policy_to_grid(theta))


def demo3_variance_comparison():
    print("\n" + "=" * 60)
    print("Demo 3: Variance Comparison (with vs without baseline)")
    print("=" * 60)
    _, r_no_bl, g_no_bl = reinforce(episodes=500, use_baseline=False)
    _, r_bl, g_bl = reinforce(episodes=500, use_baseline=True)

    # Compare gradient norm variance
    window = 50
    ma_no_bl = moving_average(g_no_bl, window)
    ma_bl = moving_average(g_bl, window)

    print(f"  {'Metric':<25} {'No Baseline':>12} {'With Baseline':>14}")
    print(f"  {'-'*25} {'-'*12} {'-'*14}")
    print(f"  {'Mean grad norm':<25} {sum(g_no_bl)/len(g_no_bl):>12.4f} {sum(g_bl)/len(g_bl):>14.4f}")
    print(f"  {'Std grad norm':<25} "
          f"{math.sqrt(sum((g-ma_no_bl[-1])**2 for g in g_no_bl)/len(g_no_bl)):>12.4f} "
          f"{math.sqrt(sum((g-ma_bl[-1])**2 for g in g_bl)/len(g_bl)):>14.4f}")
    print(f"  {'Success rate (last 100)':<25} "
          f"{sum(1 for r in r_no_bl[-100:] if r>0)/100:>12.1%} "
          f"{sum(1 for r in r_bl[-100:] if r>0)/100:>14.1%}")


def demo4_discount_effect():
    print("\n" + "=" * 60)
    print("Demo 4: Effect of Discount Factor on Returns")
    print("=" * 60)
    # Show a sample trajectory and its returns at different gamma
    random.seed(99)
    theta = [[0.0] * N_ACTIONS for _ in range(N_STATES)]
    # Make a slightly biased policy to get longer trajectories
    for s in range(N_STATES):
        theta[s][2] = 0.5  # bias toward "down"

    for gamma in [0.5, 0.9, 0.99, 1.0]:
        sa_list, returns, total_r = collect_trajectory(theta, gamma)
        print(f"\n  gamma={gamma:.2f}: trajectory length={len(sa_list)}, "
              f"total_reward={total_r:.1f}")
        if len(returns) > 0:
            print(f"    Returns: G_0={returns[0]:.3f}, "
                  f"G_mid={returns[len(returns)//2]:.3f}, "
                  f"G_last={returns[-1]:.3f}")
        else:
            print(f"    (empty trajectory)")


def demo5_learning_curve():
    print("\n" + "=" * 60)
    print("Demo 5: Learning Curve (REINFORCE with baseline, 2000 eps)")
    print("=" * 60)
    theta, rewards, _ = reinforce(episodes=2000, use_baseline=True)
    ma = moving_average(rewards, 100)
    indices = [i * (len(ma) - 1) // 10 for i in range(11)]
    print("  Episode | Avg Reward (100-ep window)")
    print("  --------|--------------------------")
    for idx in indices:
        bar_len = int(ma[idx] * 30)
        print(f"  {idx+1:>7} | {'#' * bar_len} {ma[idx]:.3f}")
    print(f"\n  Final greedy policy:")
    print(policy_to_grid(theta))


if __name__ == "__main__":
    demo1_basic()
    demo2_with_baseline()
    demo3_variance_comparison()
    demo4_discount_effect()
    demo5_learning_curve()
