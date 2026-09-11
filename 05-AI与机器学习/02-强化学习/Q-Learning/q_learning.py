#!/usr/bin/env python3
"""
Q-Learning tabular implementation on a 4x4 GridWorld.

Demonstrates:
  1. Basic Q-learning convergence (epsilon=0.9, gamma=0.95)
  2. Effect of exploration rate (epsilon=0.1 vs 0.9)
  3. Effect of discount factor (gamma=0.0 vs 0.5 vs 0.95)
  4. "Self-correcting" property: overestimated Q gets corrected by experience

No external dependencies — pure Python stdlib only.

Reference: d2l.ai §17.3 Q-Learning (Watkins & Dayan, 1992)
"""

import random
import math
from typing import List, Tuple

# ============================================================
# GridWorld Environment (FrozenLake-style 4x4)
# ============================================================
#  S  F  F  F
#  F  H  F  H
#  F  F  F  H
#  H  F  F  G
#
# S=start(0), G=goal(15), H=hole(5,7,11,12)
# Reward: +1 at G, 0 elsewhere
# Actions: 0=up, 1=right, 2=down, 3=left

GRID = 4
N_STATES = GRID * GRID  # 16
N_ACTIONS = 4
HOLES = {5, 7, 11, 12}
GOAL = 15
START = 0

# Transition: deterministic (slippery version omitted for clarity)
def step(state: int, action: int) -> Tuple[int, float, bool]:
    """Take one step in the environment. Returns (next_state, reward, done)."""
    row, col = divmod(state, GRID)
    if action == 0:  # up
        row = max(0, row - 1)
    elif action == 1:  # right
        col = min(GRID - 1, col + 1)
    elif action == 2:  # down
        row = min(GRID - 1, row + 1)
    elif action == 3:  # left
        col = max(0, col - 1)
    next_state = row * GRID + col
    if next_state in HOLES:
        return next_state, 0.0, True
    if next_state == GOAL:
        return next_state, 1.0, True
    return next_state, 0.0, False


def epsilon_greedy(Q: List[List[float]], state: int, epsilon: float) -> int:
    """Select action using epsilon-greedy policy."""
    if random.random() < epsilon:
        return random.randint(0, N_ACTIONS - 1)
    # break ties randomly
    max_q = max(Q[state])
    best = [a for a in range(N_ACTIONS) if Q[state][a] == max_q]
    return random.choice(best)


def q_learning(
    episodes: int = 500,
    alpha: float = 0.1,
    gamma: float = 0.95,
    epsilon: float = 0.1,
    max_steps: int = 100,
    seed: int = 42,
) -> Tuple[List[List[float]], List[float]]:
    """Run Q-learning. Returns (Q_table, rewards_per_episode)."""
    random.seed(seed)
    Q = [[0.0] * N_ACTIONS for _ in range(N_STATES)]
    ep_rewards = []

    for ep in range(episodes):
        state = START
        total_r = 0.0
        for _ in range(max_steps):
            action = epsilon_greedy(Q, state, epsilon)
            next_state, reward, done = step(state, action)
            # Q-update: Q(s,a) <- Q(s,a) + alpha * [r + gamma * max_a' Q(s',a') - Q(s,a)]
            if done:
                target = reward
            else:
                target = reward + gamma * max(Q[next_state])
            Q[state][action] += alpha * (target - Q[state][action])
            state = next_state
            total_r += reward
            if done:
                break
        ep_rewards.append(total_r)

    return Q, ep_rewards


def greedy_policy(Q: List[List[float]]) -> List[int]:
    """Extract greedy policy from Q-table."""
    return [max(range(N_ACTIONS), key=lambda a: Q[s][a]) for s in range(N_STATES)]


def policy_to_grid(policy: List[int]) -> str:
    """Render policy as a grid with arrows."""
    arrows = {0: "^", 1: ">", 2: "v", 3: "<"}
    lines = []
    for r in range(GRID):
        row = []
        for c in range(GRID):
            s = r * GRID + c
            if s in HOLES:
                row.append("H")
            elif s == GOAL:
                row.append("G")
            elif s == START:
                row.append("S")
            else:
                row.append(arrows[policy[s]])
        lines.append(" ".join(row))
    return "\n".join(lines)


def moving_average(data: List[float], window: int = 20) -> List[float]:
    """Compute simple moving average."""
    result = []
    for i in range(len(data)):
        start = max(0, i - window + 1)
        result.append(sum(data[start:i+1]) / (i - start + 1))
    return result


# ============================================================
# Demo 1: Basic Q-learning convergence
# ============================================================
def demo1_basic():
    print("=" * 60)
    print("Demo 1: Basic Q-Learning (epsilon=0.1, gamma=0.95, alpha=0.1)")
    print("=" * 60)
    Q, rewards = q_learning(episodes=1000, alpha=0.1, gamma=0.95, epsilon=0.1)
    policy = greedy_policy(Q)
    ma = moving_average(rewards, 50)

    print(f"\nEpisodes: 1000")
    print(f"First 50-ep moving avg reward: {ma[49]:.3f}")
    print(f"Last  50-ep moving avg reward: {ma[-1]:.3f}")
    success_rate = sum(1 for r in rewards[-100:] if r > 0) / 100
    print(f"Success rate (last 100 eps): {success_rate:.1%}")
    print(f"\nGreedy policy:")
    print(policy_to_grid(policy))
    print(f"\nQ-values for state 0 (start): {[f'{v:.3f}' for v in Q[0]]}")
    print(f"Q-values for state 10:       {[f'{v:.3f}' for v in Q[10]]}")


# ============================================================
# Demo 2: Effect of exploration rate
# ============================================================
def demo2_exploration():
    print("\n" + "=" * 60)
    print("Demo 2: Exploration Rate Comparison")
    print("=" * 60)
    for eps in [0.0, 0.1, 0.3, 0.9]:
        Q, rewards = q_learning(episodes=500, epsilon=eps, gamma=0.95)
        ma = moving_average(rewards, 50)
        success = sum(1 for r in rewards[-100:] if r > 0) / 100
        print(f"  epsilon={eps:.1f}: last-100 success={success:.1%}, "
              f"avg_reward(50ep)={ma[-1]:.3f}")


# ============================================================
# Demo 3: Effect of discount factor
# ============================================================
def demo3_discount():
    print("\n" + "=" * 60)
    print("Demo 3: Discount Factor Comparison")
    print("=" * 60)
    for g in [0.0, 0.5, 0.95]:
        Q, rewards = q_learning(episodes=500, gamma=g, epsilon=0.1)
        success = sum(1 for r in rewards[-100:] if r > 0) / 100
        policy = greedy_policy(Q)
        print(f"\n  gamma={g:.2f}: success={success:.1%}")
        print(f"  Q[state=6] = {[f'{v:.3f}' for v in Q[6]]}")
        # With gamma=0, agent only cares about immediate reward
        # → can't propagate goal reward backward


# ============================================================
# Demo 4: Self-correcting property
# ============================================================
def demo4_self_correcting():
    print("\n" + "=" * 60)
    print("Demo 4: Self-Correcting Property")
    print("=" * 60)
    # Manually inflate Q[0][2] (down from start) to 5.0
    # Q-learning should correct this overestimation
    random.seed(42)
    Q = [[0.0] * N_ACTIONS for _ in range(N_STATES)]
    Q[0][2] = 5.0  # artificially inflate "down from start"
    print(f"  Before learning: Q[0] = {[f'{v:.3f}' for v in Q[0]]}")

    alpha, gamma, epsilon = 0.1, 0.95, 0.1
    for ep in range(200):
        state = START
        for _ in range(100):
            action = epsilon_greedy(Q, state, epsilon)
            next_state, reward, done = step(state, action)
            target = reward if done else reward + gamma * max(Q[next_state])
            Q[state][action] += alpha * (target - Q[state][action])
            state = next_state
            if done:
                break

    print(f"  After  200 eps: Q[0] = {[f'{v:.3f}' for v in Q[0]]}")
    print(f"  Q[0][2] dropped from 5.000 to {Q[0][2]:.3f}")
    print(f"  → Overestimated action was explored and corrected")


# ============================================================
# Demo 5: Learning curve visualization (text)
# ============================================================
def demo5_learning_curve():
    print("\n" + "=" * 60)
    print("Demo 5: Learning Curve (text visualization)")
    print("=" * 60)
    Q, rewards = q_learning(episodes=1000, alpha=0.1, gamma=0.95, epsilon=0.1)
    ma = moving_average(rewards, 50)
    # Sample at 10 points
    indices = [i * (len(ma) - 1) // 10 for i in range(11)]
    print("  Episode | Avg Reward (50-ep window)")
    print("  --------|--------------------------")
    for idx in indices:
        bar_len = int(ma[idx] * 30)
        print(f"  {idx+1:>7} | {'#' * bar_len} {ma[idx]:.3f}")


if __name__ == "__main__":
    demo1_basic()
    demo2_exploration()
    demo3_discount()
    demo4_self_correcting()
    demo5_learning_curve()
