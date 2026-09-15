#!/usr/bin/env python3
"""确定性帧同步 (deterministic lockstep) 最小实现与自检.

权威依据(AoE GDC 2001「1500 Archers on a 28.8」+ 帧同步定义, 见 README):
  1. 不传单位状态, 而是让每台机器跑**完全相同的模拟**, 只传玩家指令;
  2. 通信回合(communication turn)与渲染帧解耦, 典型 200 ms 一回合;
  3. 指令「预约 2 个回合之后再执行」—— 发出后还有 2 个回合的窗口
     用于接收/确认/重传, 因此链路延迟 <= 2 个回合时不会卡顿;
  4. UDP 之上自己实现定序、丢包检测、重传: 「When in doubt, assume it dropped」;
  5. 每回合做校验和(checksum)对比, 不一致即 out-of-sync(失同步);
  6. 失同步的根因常常是**极小差异的累积**: 「a deer slightly out of alignment」;
     因此「同一段代码在每台机器上必须做出逐位相同的结果」是硬要求 ——
     这就是定点数(fixed-point)替代浮点数的原因;
  7. 随机数必须同步, 且**每条指令消耗的随机次数必须一致**。

本文件分三部分:
  A. 通信回合 / 2 回合前瞻 / 丢包重传 的定量模型;
  B. 三组 peer 跑模拟并逐回合比校验和, 找首次失同步回合;
  C. 浮点 vs 定点: 为什么「换一下求和顺序」就能让两台机器彻底走散。
"""

from __future__ import annotations

import struct
from collections import defaultdict

# ------------------------------------------------------------------ 常量
TURN_MS = 200                 # 通信回合长度(GDC 2001 原文: typically 200 msec)
LOOKAHEAD = 2                 # 指令预约 2 回合后执行
N_UNITS = 8
SCALE = 1 << 16               # Q16.16 定点
MASK64 = (1 << 64) - 1
FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3


# =====================================================================
# A. 通信回合 / 前瞻 / 丢包重传
# =====================================================================
def local_cmd(peer: int, turn: int) -> tuple[int, int, int]:
    """peer 在 turn 回合产生的本地指令: (目标单位, dx, dy) 单位 = 定点格。"""
    return ((peer * 3 + turn) % N_UNITS, (peer + 1) * SCALE // 64, turn % 3 - 1)


def run_lockstep(n_peers: int, latency: int, n_turns: int,
                 drops: frozenset[tuple[int, int]] = frozenset(),
                 rto: int | None = None) -> dict:
    """模拟 n_peers 个 peer 在「通信回合」模型下推进 n_turns 个游戏回合。

    - 指令在通信回合 t 发出, 预约在 t + LOOKAHEAD 执行;
    - 正常到达时刻 = t + latency;
    - 命中 drops 的包按「重传」处理, 到达时刻 = t + latency + rto;
    - 只有当某个执行回合所需**全部** peer 的指令都到齐才能推进, 否则该回合被卡住。
    """
    rto = (latency + 1) if rto is None else rto
    inflight: list[tuple[int, int, int]] = []          # (arrive, peer, exec_turn)
    ready: dict[int, set[int]] = defaultdict(set)
    stall_events = 0
    first_success = None
    # 前 LOOKAHEAD 个回合只做网络(指令预约在未来), 没有任何回合可执行
    first_exec = 1 + LOOKAHEAD
    last_exec = n_turns + LOOKAHEAD
    next_exec = first_exec
    t = 1
    guard = (n_turns + LOOKAHEAD) * 8

    while next_exec <= last_exec and t <= guard:
        for p in range(n_peers):
            penalty = rto if (p, t) in drops else 0
            inflight.append((t + latency + penalty, p, t + LOOKAHEAD))
        for item in list(inflight):
            if item[0] <= t:
                ready[item[2]].add(item[1])
                inflight.remove(item)
        if len(ready[next_exec]) == n_peers:
            if first_success is None:
                first_success = t
            next_exec += 1
        elif t >= next_exec:                 # 该回合的执行时隙已到却凑不齐指令
            stall_events += 1
        t += 1
    return {
        "latency": latency, "drops": len(drops),
        "executed": next_exec - first_exec, "comm_turns": t - 1,
        "stall_events": stall_events,
        # 首个可执行回合实际被推迟了几个通信回合 = 玩家感受到的额外延迟
        "setup_delay": max(0, (first_success or t) - first_exec),
    }


# =====================================================================
# B. 确定性模拟 + 校验和
# =====================================================================
class Rng:
    """显式可同步的 LCG: 每回合必须被调用**相同次数**。"""

    def __init__(self, seed: int = 20260915) -> None:
        self.s = seed & 0x7FFFFFFF

    def next(self) -> int:
        self.s = (self.s * 1103515245 + 12345) & 0x7FFFFFFF
        return self.s

    def clone(self) -> "Rng":
        r = Rng()
        r.s = self.s
        return r


RATE_F = [1.0 / (i + 1) for i in range(N_UNITS)]             # 浮点模型
RATE_Q = [int(round(r * SCALE)) for r in RATE_F]             # 定点模型


class World:
    """同一个世界, 三种「实现方式」:

    mode  = 'fixed'  定点池 + 整数加法   (顺序无关)
    mode  = 'float'  浮点池 + 浮点加法   (顺序敏感 -> 换顺序就分叉)
    bug   = 'extra_rng' 每回合多取一次随机数(破坏 RNG 同步)
    """

    def __init__(self, mode: str = "fixed", bug: str = "") -> None:
        self.mode, self.bug = mode, bug
        self.pool = 0 if mode == "fixed" else 0.0
        self.px = [0] * N_UNITS
        self.hp = [100] * N_UNITS
        self.rng = Rng()

    def step(self, cmds, order) -> None:
        # 1) 施加指令(按单位 id + peer 排序, 与到达顺序无关 -> 可复现)
        for _, uid, dx, dy in sorted(cmds, key=lambda c: (c[1], c[0])):
            self.px[uid] += dx
        # 2) 生产累积: 只有这一步对「遍历顺序」敏感
        acc = self.pool
        for i in order:
            acc = acc + (RATE_Q[i] if self.mode == "fixed" else RATE_F[i])
        if self.mode == "fixed":
            self.pool = acc
        else:
            self.pool = acc
        # 3) 战斗: 消耗随机数(次数必须跨 peer 一致)
        r = self.rng.next()
        if self.bug == "extra_rng":
            self.rng.next()                      # 故意多取一次
        self.hp[r % N_UNITS] -= 1

    def checksum(self) -> int:
        h = FNV_OFFSET
        if self.mode == "fixed":
            buf = struct.pack("<q", self.pool)
        else:
            buf = struct.pack("<d", self.pool)
        buf += struct.pack("<%dq" % N_UNITS, *self.px)
        buf += struct.pack("<%dq" % N_UNITS, *self.hp)
        buf += struct.pack("<q", self.rng.s)
        for b in buf:
            h ^= b
            h = (h * FNV_PRIME) & MASK64
        return h


def run_peers(mode: str, bug_peer: int | None, bug: str, n_turns: int = 20) -> dict:
    """n_peers=3, 其中 bug_peer 走「反向遍历」顺序(或附加 bug)。"""
    worlds = [World(mode, bug if p == bug_peer else "") for p in range(3)]
    asc = list(range(N_UNITS))
    desc = list(reversed(asc))
    first_div = None
    for turn in range(1, n_turns + 1):
        cmds = []
        for p in range(3):
            uid, dx, dy = local_cmd(p, turn)
            cmds.append((p, uid, dx, dy))
        for p, w in enumerate(worlds):
            w.step(cmds, desc if (p == bug_peer) else asc)
        sums = [w.checksum() for w in worlds]
        if len(set(sums)) > 1 and first_div is None:
            first_div = turn
    return {"mode": mode, "bug": bug or "-", "bug_peer": bug_peer,
            "first_desync_turn": first_div, "final_sums": sums}


# =====================================================================
# C. 浮点 vs 定点: 求和顺序的影响
# =====================================================================
def first_order_divergence(vals, turns: int = 200) -> tuple[int | None, float, float]:
    """累加同一个多重集合, 一次升序一次降序, 找首次不等的回合。"""
    asc = desc = 0.0
    for t in range(1, turns + 1):
        a, b = asc, desc
        for v in vals:
            a += v
        for v in reversed(vals):
            b += v
        asc, desc = a, b
        if asc != desc:
            return t, asc, desc
    return None, asc, desc


def mul_trunc(x: int, y: int) -> int:
    """Q16.16 乘法: 向零截断。"""
    p = x * y
    return p >> 16


def mul_floor(x: int, y: int) -> int:
    """Q16.16 乘法: 向下取整。负数时会与截断分叉。"""
    p = x * y
    return p >> 16 if p >= 0 else -((-p) >> 16)


# =====================================================================
# 自检
# =====================================================================
def main() -> None:
    print("== A. 通信回合 / 2 回合前瞻 / 丢包重传 ==")
    rows = []
    for lat in (1, 2, 3):
        rows.append(run_lockstep(3, lat, 12))
    for r in rows:
        print(f"  链路延迟 {r['latency']} 回合: 执行 {r['executed']}/12 回合, "
              f"起播延迟 {r['setup_delay']} 回合, 卡顿 {r['stall_events']} 次")
    assert rows[0]["setup_delay"] == 0 and rows[1]["setup_delay"] == 0, rows
    assert rows[2]["setup_delay"] == rows[2]["latency"] - LOOKAHEAD, rows
    print("  -> 延迟 <= 2 回合时 2 回合前瞻全额吸收(起播延迟 0); "
          "延迟 3 回合起每多 1 回合就多 1 回合起播延迟  OK")

    dropped = frozenset({(0, 3), (1, 3), (2, 3)})
    d = run_lockstep(3, 1, 12, drops=dropped)
    clean = run_lockstep(3, 1, 12)
    print(f"  通信回合 3 上 3 个包全丢(按 RTO 重传): 卡顿 {d['stall_events']} 次 vs "
          f"无丢包 {clean['stall_events']} 次, 通信回合 {d['comm_turns']} vs "
          f"{clean['comm_turns']} (+{d['comm_turns'] - clean['comm_turns']} 回合)")
    assert d["stall_events"] > clean["stall_events"], (d, clean)
    assert d["comm_turns"] > clean["comm_turns"], (d, clean)
    print("  -> 丢包被 UDP 之上的重传兜住, 代价是通信回合被拉长(= AoE 的 speed control)  OK")

    print("\n== B. 校验和跨 peer 对比(找首次失同步回合) ==")
    ok = run_peers("fixed", None, "")
    order = run_peers("fixed", 2, "")
    flt = run_peers("float", 2, "")
    rng = run_peers("fixed", 2, "extra_rng")
    cases = [
        ("定点 + 全部同序", ok),
        ("定点 + peer2 反向遍历", order),
        ("浮点 + peer2 反向遍历", flt),
        ("定点 + peer2 多取一次随机", rng),
    ]
    for name, r in cases:
        t = r["first_desync_turn"]
        print(f"  {name:26s} 首次失同步回合 = {t if t else '未失同步'}  "
              f"(3 个 peer 校验和{'一致' if t is None else '不一致'})")
    assert ok["first_desync_turn"] is None
    assert order["first_desync_turn"] is None      # 整数加法与顺序无关
    assert flt["first_desync_turn"] == 2           # 浮点第 2 回合即分叉
    assert rng["first_desync_turn"] == 1           # 随机数没同步, 立刻崩
    print("  -> 定点对「求和顺序」免疫; 浮点第 2 回合就分叉; "
          "随机次数不一致第 1 回合就分叉  OK")

    print("\n== C. 浮点 vs 定点 ==")
    t, a, b = first_order_divergence(RATE_F)
    print(f"  浮点累加 1/(i+1) (i=0..7): 首次分叉于第 {t} 回合, "
          f"升序 {a!r} vs 降序 {b!r}, 差 {a - b:.3e}")
    ia = ib = 0
    for v in RATE_Q:
        ia += v
    for v in reversed(RATE_Q):
        ib += v
    print(f"  定点累加同一序列: 升序 {ia} vs 降序 {ib} -> {'一致' if ia == ib else '不一致'}")
    assert t == 2 and ia == ib
    print(f"  0.1 + 0.2 - 0.3 = {0.1 + 0.2 - 0.3!r}  (不等于 0)")
    assert 0.1 + 0.2 - 0.3 != 0.0
    x, y = -(3 * SCALE // 2), SCALE // 3
    print(f"  Q16.16 乘法舍入方向: 向零截断 {mul_trunc(x, y)} vs 向下取整 "
          f"{mul_floor(x, y)}  (负数结果分叉)")
    assert mul_trunc(x, y) != mul_floor(x, y)
    print("  -> 即使改用定点, 也必须跨 peer 约定**同一个舍入方向**  OK")

    print("\n全部自检通过。")


if __name__ == "__main__":
    main()
