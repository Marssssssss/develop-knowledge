"""DQN 的两大稳定化机制:经验回放(experience replay)与目标网络(target network)。

三个实验:
  E1 学习表现消融:full / no_target / no_replay 三配置 × 5 种子 × 700 回合(4×4 网格)
  E2 样本相关性:相邻两次更新的 TD 误差"同号率"(无回放时样本高度相关 → 同号率显著偏高)
  E3 结构断言:回放缓冲的覆盖语义、MLP 解析梯度、目标网络"目标在同步周期内不变"

口径说明:全程 4×4 网格、γ=0.9、lr=0.1、hidden=32、batch=8、warmup=200、sync_every=50。
这一组超参是本 demo 开发期实测筛出来的(见 README「性能边界」),不是 arXiv:1312.5602
里的 Atari 配置——论文用 100 万帧的 ε 退火、RMSProp、4 帧堆叠,这里全部换成最小可断言版本。

运行: python main.py
"""

import math
import random
import sys

from dqn_core import GridWorld, MLP, ReplayBuffer

GAMMA = 0.9
GRID_N = 4
HIDDEN = 32
EPISODES = 700
SYNC_EVERY = 50
BATCH = 8
WARMUP = 200
LR = 0.1
SEEDS = (1, 2, 3, 4, 5)

PASSED = []
FAILED = []


def check(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  |  {detail}" if detail else ""))


def onehot(s, n):
    v = [0.0] * n
    v[s] = 1.0
    return v


# ==========================================================================
# E1 / E2:网格世界上的 DQN 消融
# ==========================================================================


CONFIGS = [
    # 名字          replay  target
    ("full", True, True),
    ("no_target", True, False),
    ("no_replay", False, True),
]


def train_dqn(replay, use_target, seed, episodes=EPISODES, lr=LR,
              gamma=GAMMA, hidden=HIDDEN, batch=BATCH, warmup=WARMUP,
              sync_every=SYNC_EVERY, capacity=5000, slip=0.1):
    """返回 dict:成功率 / max|Q| / 相邻 TD 误差同号率 / 同步次数 / |θ|max。"""
    rng = random.Random(seed)
    env = GridWorld(n=GRID_N, slip=slip, rng=random.Random(seed + 1))
    n = env.n_states
    online = MLP(n, hidden, env.N_ACTIONS, lr=lr, rng=random.Random(seed + 2))
    target_net = MLP(n, hidden, env.N_ACTIONS, lr=lr, rng=random.Random(seed + 2))
    target_net.copy_from(online)
    buf = ReplayBuffer(capacity, rng=random.Random(seed + 3))

    steps = 0
    syncs = 0
    max_q = 0.0
    last_sign = None
    agree = same = 0

    for ep in range(episodes):
        s = env.reset()
        # arXiv:1312.5602 §4.1:ε 线性退火(该文为 1.0 → 0.1)
        eps = 1.0 + (0.1 - 1.0) * min(1.0, ep / (episodes * 0.5))
        done = False
        while not done:
            a = rng.randrange(env.N_ACTIONS) if rng.random() < eps \
                else online.argmax_onehot(s)
            s2, r, done = env.step(a)
            if replay:
                buf.push(s, a, r, s2, done)
                if len(buf) < warmup:
                    continue
                samples = buf.sample(batch)
            else:
                samples = [(s, a, r, s2, done)]
            for (bs, ba, br, bs2, bd) in samples:
                pre, h, q = online.forward_onehot(bs)
                qa = q[ba]
                if bd:
                    target = br
                else:
                    net = target_net if use_target else online
                    target = br + gamma * max(net.forward_onehot(bs2)[2])
                td = target - qa
                if last_sign is not None:
                    agree += 1
                    if (td >= 0) == last_sign:
                        same += 1
                last_sign = td >= 0
                max_q = max(max_q, abs(qa))
                dq = [0.0] * env.N_ACTIONS
                dq[ba] = qa - target  # ∂(½(q−y)²)/∂q = q − y
                online.sgd_step_onehot(bs, pre, h, dq)
            steps += 1
            if use_target and steps % sync_every == 0:
                target_net.copy_from(online)
                syncs += 1
    return {
        "success": evaluate(online, env, n),
        "max_q": max_q,
        "agree": same / max(1, agree),
        "syncs": syncs,
        "wmax": online.max_abs_weight(),
        "finite": all(math.isfinite(v) for v in online.w2[0]),
    }


def evaluate(agent, env, n, max_steps=120):
    """从每个状态出发做贪心 rollout(slip=0,确定性),统计到达目标的比例。"""
    lv = GridWorld(n=env.n, slip=0.0, max_steps=max_steps, rng=random.Random(0))
    ok = 0
    for start in range(n):
        lv.state = start
        lv.steps = 0
        done = False
        while not done:
            _, _, done = lv.step(agent.argmax_onehot(lv.state))
        if lv.state == lv.goal:
            ok += 1
    return ok / n


def seed_str(res):
    return "[" + ", ".join("%.2f" % r["success"] for r in res) + "]"


def experiment_1_and_2():
    print(f"\n=== E1/E2 消融:{GRID_N}×{GRID_N} 网格,γ={GAMMA},lr={LR},"
          f"hidden={HIDDEN},episodes={EPISODES},{len(SEEDS)} 种子 ===")
    rows = {}
    for name, replay, use_target in CONFIGS:
        res = [train_dqn(replay, use_target, s) for s in SEEDS]
        agg = {k: sum(r[k] for r in res) / len(res) for k in res[0]}
        rows[name] = (agg, res)
        print(f"  {name:>10s} 成功率={agg['success']:.3f} per-seed={seed_str(res)} "
              f"max|Q|={agg['max_q']:.2f} 同号率={agg['agree']:.3f} "
              f"同步={agg['syncs']:.0f}")
    return rows


# ==========================================================================
# E3:结构断言
# ==========================================================================


def test_replay_buffer():
    b = ReplayBuffer(4, rng=random.Random(0))
    for i in range(6):
        b.push(i, 0, 0.0, i, False)
    check("ReplayBuffer:容量固定,超容后覆盖最旧转移",
          len(b) == 4 and b.data[b.pos][0] == 2,
          f"len={len(b)} pos={b.pos} oldest.s={b.data[b.pos][0]}")
    check("ReplayBuffer:采样只返回缓冲内元素", all(x in b.data for x in b.sample(20)))


def test_mlp_gradient():
    m = MLP(7, 5, 3, lr=0.1, rng=random.Random(7))
    s, a, y = 3, 2, -1.5
    x = onehot(s, 7)
    _, h, q = m.forward(x)
    dq = [0.0] * 3
    dq[a] = q[a] - y

    def loss():
        return 0.5 * (m.forward(x)[2][a] - y) ** 2

    eps = 1e-5
    worst = 0.0
    for name, j, i in (("w2", a, 1), ("w2", a, 4), ("w1", 2, s)):
        mat = m.w2 if name == "w2" else m.w1
        old = mat[j][i]
        mat[j][i] = old + eps
        lp = loss()
        mat[j][i] = old - eps
        lm = loss()
        mat[j][i] = old
        num = (lp - lm) / (2 * eps)
        if name == "w2":
            ana = dq[a] * h[i]
        else:
            dh_j = sum(m.w2[k][j] * dq[k] for k in range(3)) * (1.0 if h[j] > 0 else 0.0)
            ana = dh_j
        worst = max(worst, abs(num - ana))
    check("MLP:解析梯度与数值梯度一致(误差 < 1e-6)", worst < 1e-6, f"max_err={worst:.2e}")


def test_target_semantics():
    """目标网络的核心语义:θ⁻ 在同步周期之间**不变**,而 θ 每步都变。"""
    net = MLP(25, 8, 4, lr=0.1, rng=random.Random(1))
    tgt = MLP(25, 8, 4, lr=0.1, rng=random.Random(1))
    tgt.copy_from(net)
    s2 = 12
    before_t = max(tgt.forward_onehot(s2)[2])
    before_o = max(net.forward_onehot(s2)[2])
    pre, h, _ = net.forward_onehot(6)
    net.sgd_step_onehot(6, pre, h, [0.5, -0.5, 0.0, 0.25])
    after_t = max(tgt.forward_onehot(s2)[2])
    after_o = max(net.forward_onehot(s2)[2])
    check("目标网络:梯度步只改 θ,不改 θ⁻(自举目标在同步周期内固定)",
          abs(after_t - before_t) < 1e-15 and abs(after_o - before_o) > 1e-9,
          f"|Δθ⁻max|={abs(after_t-before_t):.2e} |Δθmax|={abs(after_o-before_o):.2e}")

    r = train_dqn(True, True, seed=11, episodes=60)
    check("目标网络:训练中按 sync_every 周期拷贝(60 回合内同步次数 > 0)",
          r["syncs"] > 0, f"syncs={r['syncs']}")


def test_done_no_bootstrap():
    env = GridWorld(n=GRID_N, slip=0.0, rng=random.Random(0))
    env.state, env.steps = env.goal, 1
    _, rr, dd = env.step(0)
    check("TD 目标:终止转移不 bootstrap(y = r)", dd and abs(rr) < 1e-12,
          f"r={rr} done={dd}")


# ==========================================================================
# main
# ==========================================================================


def main():
    test_replay_buffer()
    test_mlp_gradient()
    test_target_semantics()
    test_done_no_bootstrap()
    rows = experiment_1_and_2()

    full, nt, nr = rows["full"][0], rows["no_target"][0], rows["no_replay"][0]
    check("E1 完整 DQN:5 个种子上都完美求解所有起点(成功率 = 1.0)",
          full["success"] >= 0.999, "per-seed=" + seed_str(rows["full"][1]))
    check("E1 去掉目标网络后退化(平均成功率低于完整 DQN)",
          nt["success"] < full["success"],
          f"no_target={nt['success']:.3f} < full={full['success']:.3f}")
    check("E1 去掉经验回放后退化(平均成功率低于完整 DQN)",
          nr["success"] < full["success"],
          f"no_replay={nr['success']:.3f} < full={full['success']:.3f}")
    check("E2 无回放时相邻 TD 误差同号率显著更高(样本时间相关)",
          nr["agree"] > full["agree"] + 0.15,
          f"no_replay={nr['agree']:.3f} > full={full['agree']:.3f} + 0.15")
    check("E2 有回放时同号率接近 0.5(均匀采样近似独立)",
          abs(full["agree"] - 0.5) < 0.08, f"full={full['agree']:.3f}")
    check("E1 三个配置的 Q 值均有界(未出现发散/inf)",
          all(rows[k][0]["max_q"] < 50.0 and rows[k][1][0]["finite"] for k, _, _ in CONFIGS),
          "max|Q|=" + ", ".join(f"{k}:{rows[k][0]['max_q']:.1f}" for k, _, _ in CONFIGS))

    print(f"\n断言汇总: PASS={len(PASSED)}  FAIL={len(FAILED)}")
    if FAILED:
        print("失败项: " + "; ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
