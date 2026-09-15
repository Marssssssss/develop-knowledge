#!/usr/bin/env python3
"""匹配系统 (Matchmaking) 最小实现与自检 —— Elo / TrueSkill / 匹配算法.

权威依据(见 README 参考资料):
  * Elo: 单一评分 + 400 分标尺 + K 因子; 期望胜率 E = 1/(1+10^((Rb-Ra)/400))。
  * TrueSkill(微软研究院): 技能用**两个**数刻画 —— 均值 mu 与不确定性 sigma;
    新玩家 mu=25, sigma=8.333, 展示分 = mu - 3*sigma = 0("技能可能在 0~50 之间");
    每局比赛提供信息 -> sigma 通常下降, 但赛前会微增, 因此 sigma **永不归零**;
    匹配质量 = (虚拟)平局概率, 取值 0~1; "即使 mu 相同, sigma 大的一方也会让质量明显小于 1"。

本 demo 量化四件事:
  一、Elo 的 400 分标尺与 K 因子;
  二、TrueSkill 的 (mu, sigma) 更新、保守估计与收敛场次;
  三、匹配质量: 为什么"展示分差"不等于"实际匹配好坏";
  四、匹配算法: 窗口扩张(等待 vs 质量权衡) + 队伍平衡(贪心 vs 局部搜索 vs 精确最优)。
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------- 常量

from rating import (BETA, ELO_K, K_DISPLAY, MU0, SIGMA0, TAU, Phi, Rng,
                    display, elo_expect, elo_update, match_quality, ts_1v1)

# =====================================================================
# 四、匹配算法
# =====================================================================
class Player:
    __slots__ = ("pid", "arrive", "skill", "mu", "sigma")

    def __init__(self, pid: int, arrive: int, skill: float) -> None:
        self.pid, self.arrive, self.skill = pid, arrive, skill
        self.mu, self.sigma = MU0, SIGMA0


def simulate(n_players: int, ticks: int, base: float, rate: float, by_quality: bool,
             seed: int = 12345, cooldown: int = 5):
    """按 tick 运行的匹配循环: 排队 -> 配对 -> 对战 -> 更新 -> 冷却后重新排队。

    每个 tick 放 2 名冷却结束的玩家入队(制造队列积压);
    window(x) = base + rate * wait:等待越久窗口越宽, 防止高/低分玩家饿死。
    by_quality=True 时在窗口内挑匹配质量最大的对手; False 时按 FIFO 先到先配。
    """
    rng = Rng(seed)
    ps = [Player(i, 0, MU0 + 6.0 * rng.normal()) for i in range(n_players)]
    idle_after = [0] * n_players
    queue: list[Player] = []
    in_queue: set[int] = set()
    waits, gaps, tgs, quals, mt = [], [], [], [], []
    for t in range(ticks):
        for _ in range(2):
            for _try in range(8):
                i = rng.u32() % n_players
                if idle_after[i] <= t and i not in in_queue:
                    ps[i].arrive = t
                    queue.append(ps[i])
                    in_queue.add(i)
                    break
        matched: set[int] = set()
        for p in queue:
            if p.pid in matched:
                continue
            wait = t - p.arrive
            best, best_q = None, None
            if by_quality:
                window = base + rate * wait
                for q in queue:
                    if q.pid == p.pid or q.pid in matched:
                        continue
                    if abs(display(q.mu, q.sigma) - display(p.mu, p.sigma)) > window:
                        continue
                    qq = match_quality((p.mu, p.sigma), (q.mu, q.sigma))
                    if best_q is None or qq > best_q:
                        best, best_q = q, qq
            else:
                for q in queue:
                    if q.pid != p.pid and q.pid not in matched:
                        best = q
                        break
            if best is None:
                continue
            matched.add(p.pid)
            matched.add(best.pid)
            waits.append(wait)
            waits.append(t - best.arrive)
            gaps.append(abs(p.mu - best.mu))
            tgs.append(abs(p.skill - best.skill))
            quals.append(match_quality((p.mu, p.sigma), (best.mu, best.sigma)))
            mt.append(t)
            pa = Phi((p.skill - best.skill) / math.sqrt(2.0 * BETA * BETA))
            w, l = (p, best) if rng.u() < pa else (best, p)
            (w.mu, w.sigma), (l.mu, l.sigma) = ts_1v1((w.mu, w.sigma), (l.mu, l.sigma))
            idle_after[p.pid] = idle_after[best.pid] = t + cooldown
        queue = [p for p in queue if p.pid not in matched]
        in_queue = {p.pid for p in queue}
    m = max(1, len(quals))
    half = len(quals) // 2
    return {"matches": len(quals), "wait": sum(waits) / (2 * m),
            "gap": sum(gaps) / m, "true_gap": sum(tgs) / m,
            "quality": sum(quals) / m,
            "tg_early": sum(tgs[:half]) / max(1, half),
            "tg_late": sum(tgs[half:]) / max(1, len(tgs) - half),
            "left": len(queue), "sigma": sum(p.sigma for p in ps) / n_players}


def team_split(ratings):
    """把 2n 个评分分成两队, 使两队总分差最小 —— 划分问题(NP-hard)。

    返回 (贪心差值, 局部搜索差值, 精确最优差值)。
    """
    ga, gb = greedy_split(ratings)
    la, lb = local_search_split(ratings, ga, gb)
    ea, eb = exact_split(ratings)
    return (abs(sum(ga) - sum(gb)), abs(sum(la) - sum(lb)), abs(sum(ea) - sum(eb)))


def greedy_split(ratings):
    """贪心: 从高到低依次丢给"当前总分较低"的队。"""
    a, b = [], []
    for r in sorted(ratings, reverse=True):
        (a if sum(a) <= sum(b) else b).append(r)
    return a, b


def local_search_split(ratings, a, b):
    """局部搜索: 反复交换两队中的一名队员, 直到无法改进。"""
    a, b = list(a), list(b)
    improved = True
    while improved:
        improved = False
        for i in range(len(a)):
            for j in range(len(b)):
                cur = abs(sum(a) - sum(b))
                a[i], b[j] = b[j], a[i]
                if abs(sum(a) - sum(b)) < cur:
                    improved = True
                else:
                    a[i], b[j] = b[j], a[i]
    return a, b


def exact_split(ratings):
    """精确最优: 2^n 枚举(仅用于小规模参照, n<=16)。"""
    n = len(ratings)
    best, best_mask = None, 0
    for mask in range(1 << n):
        if bin(mask).count("1") != n // 2:
            continue
        sa = sum(ratings[i] for i in range(n) if mask >> i & 1)
        sb = sum(ratings[i] for i in range(n) if not mask >> i & 1)
        if best is None or abs(sa - sb) < best:
            best, best_mask = abs(sa - sb), mask
    a = [ratings[i] for i in range(n) if best_mask >> i & 1]
    b = [ratings[i] for i in range(n) if not best_mask >> i & 1]
    return a, b


# =====================================================================
# 自检
# =====================================================================
def main() -> None:
    print("== 一、Elo: 400 分标尺 + K 因子 ==")
    assert abs(elo_expect(1500, 1500) - 0.5) < 1e-12
    e400, e400neg = elo_expect(1900, 1500), elo_expect(1100, 1500)
    print(f"  同分 -> 期望 {elo_expect(1500, 1500):.3f}; 高 400 分 -> {e400:.3f}; "
          f"低 400 分 -> {e400neg:.3f}")
    assert abs(e400 - 0.909) < 0.001 and abs(e400neg - 0.091) < 0.001
    w, l = elo_update(1500, 1500, 1.0)
    print(f"  K={ELO_K:.0f}, 1500 vs 1500 胜: {1500:.0f} -> {w:.0f}; 负方 -> {l:.0f} (-16)")
    assert w == 1516.0 and l == 1484.0
    w2, l2 = elo_update(1500, 1900, 1.0)
    print(f"  弱胜强(1500 赢 1900): +{w2 - 1500:.2f} / {l2 - 1900:.2f} —— 期望仅 {elo_expect(1500, 1900):.3f},"
          f" 故单局收益很大")
    assert w2 - 1500 > 25.0 and l2 - 1900 < -25.0
    print("  -> 400 分差只是'约 91% 胜率', 不是必胜; 强方赢球几乎不加分, 输球重罚  OK")

    print("\n== 二、TrueSkill: (mu, sigma) 与保守估计 ==")
    print(f"  新玩家 mu={MU0:.0f}, sigma={SIGMA0:.3f} -> 展示分 {display(MU0, SIGMA0):.1f};"
          f" 区间 [mu-3sigma, mu+3sigma] = [{display(MU0, SIGMA0):.0f}, {MU0 + 3 * SIGMA0:.0f}]")
    assert abs(display(MU0, SIGMA0)) < 1e-9
    a, b = (MU0, SIGMA0), (MU0, SIGMA0)
    (wa, sa), (lb, sb) = ts_1v1(a, b)
    print(f"  新手 vs 新手(一方赢): 胜者 mu {MU0:.2f}->{wa:.2f}, sigma {SIGMA0:.2f}->{sa:.2f},"
          f" 展示分 0.0 -> {display(wa, sa):.2f}")
    print(f"                        负者 mu {MU0:.2f}->{lb:.2f}, sigma {SIGMA0:.2f}->{sb:.2f},"
          f" 展示分 0.0 -> {display(lb, sb):.2f}")
    assert wa > MU0 and sa < SIGMA0 and lb < MU0 and sb < SIGMA0
    assert abs(display(wa, sa) + display(lb, sb)) > 1e-9
    print(f"  两者展示分之和 = {display(wa, sa) + display(lb, sb):.2f} != 0 —— TrueSkill **不是零和**"
          f"(不确定性下降本身就是收益)")
    # sigma 小的一方: 同样胜负, 变化幅度远小于 sigma 大的一方
    (nw_, ns_), (vl_, vs_) = ts_1v1((MU0, SIGMA0), (30.0, 2.0))   # 新手爆冷击败老手
    print(f"  新手爆冷击败老手(mu=30, sigma=2.0): 新手 mu {MU0:.2f}->{nw_:.2f} (+{nw_ - MU0:.2f}),"
          f" sigma {SIGMA0:.2f}->{ns_:.2f}")
    print(f"                                   老手 mu 30.00->{vl_:.2f} ({vl_ - 30.0:+.2f}),"
          f" sigma 2.00->{vs_:.2f}")
    assert (nw_ - MU0) > (30.0 - vl_)
    print(f"  -> 更新权重 ~ sigma^2/(2beta^2+sigma_w^2+sigma_l^2): 同样的胜负,"
          f" 新手动 {nw_ - MU0:.2f} 分, 老手只动 {30.0 - vl_:.2f} 分  OK")
    # 收敛: 反复对局后 sigma 收缩
    cur = (MU0, SIGMA0)
    for i in range(12):
        (cur, _) = ts_1v1(cur, (MU0, SIGMA0))
    print(f"  连续胜 12 局(对手始终是新手): mu {MU0:.2f}->{cur[0]:.2f},"
          f" sigma {SIGMA0:.3f}->{cur[1]:.3f} (展示分 {display(cur[0], cur[1]):.2f})")
    assert cur[1] < SIGMA0 * 0.75
    print(f"  注: sigma 每次赛前 +tau={TAU:.4f} 的'动量', 因此**永不归零**(技能会随时间变化)  OK")
    print(f"  对照 MSR 页面给出的收敛场次: 2 人 12 局、2v2*2 队 10 局、4v4 46 局 —— 人越多/队越大,"
          f" 单次结果信息量越少")

    print("\n== 三、匹配质量: 展示分差 != 匹配好坏 ==")
    cases = [
        ("新手(展示 0) vs 老手(展示 25, sigma=2)", (MU0, SIGMA0), (31.0, 2.0)),
        ("新手(展示 0) vs 差手(展示 1, sigma=0.5)", (MU0, SIGMA0), (2.5, 0.5)),
        ("两个新手(展示 0) vs (0)", (MU0, SIGMA0), (MU0, SIGMA0)),
        ("两个老手(展示 25) vs (25), sigma=2", (31.0, 2.0), (31.0, 2.0)),
    ]
    qs = []
    for label, pa, pb in cases:
        q = match_quality(pa, pb)
        gap = abs(display(pa[0], pa[1]) - display(pb[0], pb[1]))
        qs.append(q)
        print(f"  {label:<34} 展示分差 {gap:5.1f} -> 质量 {q:.3f}")
    assert qs[0] > qs[1], qs
    print(f"  -> 反直觉但正确: 展示分差大(25 级)的一方质量 {qs[0]:.3f}, 反而高于"
          f"展示分差仅 1 级的 {qs[1]:.3f}")
    print(f"     原因: 差手已高度确定(sigma=0.5, 真实 mu 只有 2.5), 新手对他毫无可学之物  OK")
    q_new, q_old = qs[2], qs[3]
    assert q_old > q_new
    print(f"  -> mu 完全相同(mu=25)时: 两个新手质量仅 {q_new:.3f}, 两个老手 {q_old:.3f};"
          f" sigma 大 => 质量明显小于 1  OK")
    print("  -> MSR 页面例子同此规律: 新手 vs 老手 展示分差 23 级仍有 57.6% 质量,")
    print("     而新手 vs '摆烂老手' 展示分差仅 1 级、质量只有 5.7% (不确定性主导) ")

    print("\n== 四、匹配算法: 窗口扩张(等待 vs 公平) ==")
    print(f"  {'策略':<26}{'对局':>6}{'平均等待':>9}{'|d展示分|':>10}{'|d真技能|':>10}"
          f"{'平均质量':>9}{'余留':>6}{'均sigma':>8}")
    res = {}
    for label, bq, base, rate in (
        ("FIFO(先到先配)", False, 0.0, 0.0),
        ("窗口 8+0.5*t 最大质量", True, 8.0, 0.5),
        ("窗口 3+0.2*t 最大质量", True, 3.0, 0.2),
    ):
        r = simulate(120, 1500, base, rate, bq)
        res[label] = r
        print(f"  {label:<26}{r['matches']:>6}{r['wait']:>9.2f}{r['gap']:>10.2f}"
              f"{r['true_gap']:>10.2f}{r['quality']:>9.3f}{r['left']:>6}{r['sigma']:>8.3f}")
    fifo = res["FIFO(先到先配)"]
    w8, w3 = res["窗口 8+0.5*t 最大质量"], res["窗口 3+0.2*t 最大质量"]
    assert w3["true_gap"] < w8["true_gap"] < fifo["true_gap"], (w3, w8, fifo)
    assert w3["wait"] > fifo["wait"] and w3["quality"] > fifo["quality"]
    print(f"  -> 窗口越紧: 真实技能差 {fifo['true_gap']:.2f} -> {w8['true_gap']:.2f} -> "
          f"{w3['true_gap']:.2f}(越公平), 代价是等待 {fifo['wait']:.2f} -> {w3['wait']:.2f} tick")
    print(f"  -> sigma 从 {SIGMA0:.2f} 收敛到 {w3['sigma']:.2f}(120 名玩家 / 1500 tick),"
          f" 估计越准匹配越准:")
    print(f"     窗口 3 策略前半程 |d真技能| {w3['tg_early']:.2f} vs 后半程 {w3['tg_late']:.2f}  OK")
    assert w3["tg_late"] < w3["tg_early"], (w3["tg_early"], w3["tg_late"])
    print("  注: FIFO 的 |d展示分| 与 |d真技能| 都最大 —— 先到先配等价于随机配,"
          " 排名系统再好也白搭")

    print("\n== 五、队伍平衡: 贪心 vs 局部搜索 vs 精确最优 ==")
    rng = Rng(999)
    for n in (10, 12, 14):
        ratings = [round(1000 + 400 * rng.normal(), 1) for _ in range(n)]
        g, ls, ex = team_split(ratings)
        print(f"  {n} 人分两队(评分 1000+-400): 贪心差 {g:7.1f} | 局部搜索差 {ls:7.1f} | "
              f"精确最优 {ex:7.1f}")
        assert ex <= ls + 1e-6, (ex, ls)
        assert ls <= g + 1e-9, (ls, g)
    print("  -> 贪心(排序后交替分发)偏差最大; 一个交换式局部搜索即可逼近 2^n 枚举的最优  OK")

    print("\n全部自检通过。")


if __name__ == "__main__":
    main()

