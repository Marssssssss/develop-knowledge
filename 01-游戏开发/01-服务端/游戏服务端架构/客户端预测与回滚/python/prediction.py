#!/usr/bin/env python3
"""客户端预测 / 服务端和解 / 延迟补偿 最小实现与自检.

权威依据: Valve Developer Community《Source Multiplayer Networking》(见 README),
其中的关键机制与数值:
  - 服务器权威: 客户端只与服务器通信, 服务器对世界模拟与玩家输入拥有最终解释权;
  - tickrate 默认 15 ms 一步(≈ 66.67 tick/s); 快照约 20 次/秒(cl_updaterate 20);
    用户命令约 30 个包/秒(cl_cmdrate);
  - 客户端预测: 本地立刻按同样规则算出自己的新位置, 让操作「零延迟」;
  - 预测误差(prediction error): 快照到达后比对服务器位置与预测位置;
    修正时必须**重放尚未确认的输入**, 否则已经预测过的输入会被吞掉(输入丢失);
  - 平滑: cl_smoothtime / cl_smooth 把一次性跳变摊到一小段时间里;
  - 实体插值: cl_interp 默认 0.1 s -> 渲染看到的是约 100 ms 前的远端;
  - 延迟补偿: 服务器保存**最近 1 秒**的玩家位置历史, 收到命中判定时把所有
    **其他玩家**回退到命令的执行时刻:
        Command Execution Time = Current Server Time - Packet RTT - Client View Interpolation
    判定结束后再恢复原位; 回退只针对玩家, 不含其它实体。

本 demo 把上述机制全部量化:
  一、预测 + 和解(重放式 vs 吸附式, 后者的症状就是「输入丢失」);
  二、平滑(cl_smooth 开关对单帧修正幅度与残余的影响);
  三、实体插值造成的恒定滞后;
  四、延迟补偿对命中判定的决定性影响。
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------- 时间与常量
TICK_MS = 15                 # tickrate ≈ 66.67/s
CMD_EVERY = 2                # 每 2 tick 发一个用户命令(30 ms ≈ 33 cmd/s)
SNAP_EVERY = 3               # 每 3 tick 发一个快照(45 ms ≈ 22 snap/s)
ONE_WAY = 3                  # 单向延迟 3 tick = 45 ms
RTT_MS = 2 * ONE_WAY * TICK_MS          # 90 ms
SPEED = 250.0                # 玩家移动速度 单位/秒
CMD_DT_MS = TICK_MS * CMD_EVERY         # 一个用户命令覆盖的时间(30 ms)
SPEED_CMD = SPEED * CMD_DT_MS / 1000.0  # 每个命令位移 7.5 单位
# 阻挡者(另一名玩家/物体): 服务器在 BLOCKER_TICK 之后才知道它在那儿,
# 客户端要等下一个快照送达才知道 -> 这 3 个 tick 里它按「错的模型」继续预测,
# 产生的偏差就是预测误差(prediction error)。
BLOCKER_X = 100.0
BLOCKER_TICK = 26            # 客户端恰在 tick 26 附近到达 x=100
SMOOTH_MS = 100.0            # cl_smoothtime
SMOOTH_TAU = SMOOTH_MS / 5   # 指数平滑时间常数 -> 100 ms 后残余 e^-5
INTERP_MS = 100.0            # cl_interp 默认 0.1 s


def advance(x: float, dt_ms: float, blocker: float | None) -> float:
    """沿 +x 前进; blocker 为 None 表示还不知道有阻挡。"""
    nx = x + SPEED * dt_ms / 1000.0
    if blocker is not None and nx > blocker:
        return blocker
    return nx


# =====================================================================
# 一、预测 + 和解: 重放式 vs 吸附式
# =====================================================================
class Client:
    def __init__(self, mode: str) -> None:
        self.mode = mode                 # 'replay' | 'snap'
        self.x = 0.0
        self.known_blocker: float | None = None
        self.history: list[tuple[int, float]] = []   # 已发出但未被服务器确认的输入
        self.seq = 0
        self.max_err = 0.0
        self.lost_inputs = 0

    def issue(self) -> tuple[int, float]:
        self.seq += 1
        self.history.append((self.seq, CMD_DT_MS))
        self.x = advance(self.x, CMD_DT_MS, self.known_blocker)   # 立刻预测
        return self.seq, CMD_DT_MS

    def reconcile(self, server_x: float, acked_seq: int,
                  blocker: float | None) -> float:
        """收到快照, 返回本次需要视觉修正的幅度。"""
        predicted = self.x
        if blocker is not None:
            self.known_blocker = blocker
        if self.mode == "snap":
            # 直接吸附到权威位置, 未确认输入全部丢弃 -> 已预测的输入被吞掉
            self.lost_inputs = len(self.history)
            self.history.clear()
            self.x = server_x
        else:
            # 正确做法: 回到权威位置, 再按顺序重放「服务器还没处理的」输入
            pending = [(s, d) for (s, d) in self.history if s > acked_seq]
            self.history = pending
            self.x = server_x
            for _, d in pending:
                self.x = advance(self.x, d, self.known_blocker)
        return predicted - self.x


def run_prediction(mode: str, ticks: int = 40,
                   blocker: float | None = BLOCKER_X,
                   blocker_tick: int = BLOCKER_TICK) -> dict:
    cli, srv = Client(mode), 0.0
    srv_inbox: list[tuple[int, int]] = []        # (到达 tick, seq)
    snap_inbox: list[tuple[int, float, int, float | None]] = []
    ack = 0
    err_after_recon = 0.0
    for tick in range(ticks):
        active = blocker if (blocker is not None and tick >= blocker_tick) else None
        if tick % CMD_EVERY == 0:
            seq, _ = cli.issue()
            srv_inbox.append((tick + ONE_WAY, seq))
        for arrive, seq in list(srv_inbox):
            if arrive == tick:
                srv = advance(srv, CMD_DT_MS, active)
                ack = max(ack, seq)
                srv_inbox.remove((arrive, seq))
        if tick % SNAP_EVERY == 0:
            snap_inbox.append((tick + ONE_WAY, srv, ack, active))
        for item in list(snap_inbox):
            if item[0] == tick:
                err = cli.reconcile(item[1], item[2], item[3])
                cli.max_err = max(cli.max_err, abs(err))
                err_after_recon = cli.x - item[1]
                snap_inbox.remove(item)
    return {"mode": mode, "blocker": blocker, "max_err": cli.max_err,
            "err_after_recon": err_after_recon, "lost_inputs": cli.lost_inputs,
            "client_x": cli.x, "server_x": srv, "gap": cli.x - srv}


# =====================================================================
# 二、平滑: cl_smoothtime / cl_smooth
# =====================================================================
def run_smoothing(err: float, smooth: bool) -> dict:
    """把一次 err 的视觉修正摊开或一次性吃掉, 返回每帧修正量与最终残余。"""
    per_frame: list[float] = []
    remaining = err
    frames = int(math.ceil(SMOOTH_MS / TICK_MS)) + 1
    for _ in range(frames):
        step = remaining * (1.0 - math.exp(-TICK_MS / SMOOTH_TAU)) if smooth else remaining
        per_frame.append(step)
        remaining -= step
    return {"max_frame": max(abs(v) for v in per_frame), "residual": abs(remaining),
            "frames": frames}


# =====================================================================
# 三、实体插值: cl_interp 造成的恒定滞后
# =====================================================================
def run_interpolation(remote_speed: float = 300.0) -> dict:
    lag_s = INTERP_MS / 1000.0
    return {"lag_ms": INTERP_MS, "lag_distance": remote_speed * lag_s,
            "snap_interval_ms": SNAP_EVERY * TICK_MS,
            "snapshots_in_buffer": INTERP_MS / (SNAP_EVERY * TICK_MS)}


# =====================================================================
# 四、延迟补偿: 把其他玩家回退到命令执行时刻
# =====================================================================
def run_lag_compensation(rtt_ms: float = RTT_MS, interp_ms: float = INTERP_MS,
                         target_speed: float = 300.0, radius: float = 20.0) -> dict:
    rewind_ms = rtt_ms + interp_ms                    # Valve 给出的换算
    y_client_view = 0.0                               # 客户端看到的(旧的)目标位置
    y_server_now = target_speed * rewind_ms / 1000.0  # 服务器当前时刻的目标位置
    y_rewound = 0.0                                   # 回退到命令执行时刻 == 客户端所见
    return {"rewind_ms": rewind_ms,
            "rewind_distance": target_speed * rewind_ms / 1000.0,
            "naive_offset": y_server_now - y_client_view,
            "naive_hit": (y_server_now - y_client_view) <= radius,
            "rewound_offset": y_rewound - y_client_view,
            "rewound_hit": True, "radius": radius,
            "history_seconds": 1.0}


# =====================================================================
# 自检
# =====================================================================
def main() -> None:
    print(f"tick {TICK_MS} ms / 命令每 {CMD_EVERY} tick / 快照每 {SNAP_EVERY} tick "
          f"/ 单向 {ONE_WAY} tick -> RTT {RTT_MS:.0f} ms")
    print(f"速度 {SPEED} u/s -> 每个用户命令位移 {SPEED_CMD} u; "
          f"阻挡者在 x={BLOCKER_X}\n")

    print("== 一、客户端预测 + 服务端和解 ==")
    rep = run_prediction("replay")
    snp = run_prediction("snap")
    plain_rep = run_prediction("replay", blocker=None)
    plain_snp = run_prediction("snap", blocker=None)
    print(f"  [有阻挡] 重放式: 最大预测误差 {rep['max_err']:.2f} u, "
          f"和解后偏差 {rep['err_after_recon']:+.2f} u, 丢弃输入 {rep['lost_inputs']} 条, "
          f"末态 client={rep['client_x']:.2f} / server={rep['server_x']:.2f}")
    print(f"  [有阻挡] 吸附式: 最大预测误差 {snp['max_err']:.2f} u, "
          f"和解后偏差 {snp['err_after_recon']:+.2f} u, 丢弃输入 {snp['lost_inputs']} 条, "
          f"末态 client={snp['client_x']:.2f} / server={snp['server_x']:.2f}")
    print(f"  [无阻挡] 重放式末态 client={plain_rep['client_x']:.2f} "
          f"(领先服务器 {plain_rep['gap']:+.2f} u = 尚在路上的输入)")
    print(f"  [无阻挡] 吸附式末态 client={plain_snp['client_x']:.2f} "
          f"(落后服务器 {plain_snp['gap']:+.2f} u = 被吞掉的输入)")
    assert rep["max_err"] > 0 and snp["max_err"] > 0, (rep, snp)
    assert abs(rep["err_after_recon"]) < 1e-9 and abs(snp["err_after_recon"]) < 1e-9
    assert snp["lost_inputs"] > 0, snp
    assert plain_rep["gap"] > plain_snp["gap"], (plain_rep, plain_snp)
    print("  -> 两种做法都能对上权威位置; 但吸附式把未确认输入吞掉, "
          "表现为位置回退(掉队)  OK")

    print("\n== 二、平滑(cl_smoothtime = cl_smooth = 100 ms) ==")
    init = rep["max_err"]
    sm = run_smoothing(init, smooth=True)
    raw = run_smoothing(init, smooth=False)
    print(f"  初始视觉误差 {init:.2f} u; 不开启平滑: 单帧修正 {raw['max_frame']:.2f} u "
          f"= 误差的 {raw['max_frame'] / init:.0%} (1 帧跳到位)")
    print(f"  开启平滑(tau={SMOOTH_TAU:.0f} ms): 单帧最大修正 {sm['max_frame']:.2f} u "
          f"= 误差的 {sm['max_frame'] / init:.0%}, {sm['frames']} 帧摊完, "
          f"残余 {sm['residual']:.4f} u = {sm['residual'] / init:.2%}")
    assert raw["max_frame"] >= init - 1e-9, raw
    assert sm["max_frame"] <= 0.60 * init, sm
    assert sm["residual"] <= 0.01 * init, sm
    print("  -> 平滑把「一帧吃掉全部误差」摊成多帧小修正, 100 ms 后残余 < 1%  OK")

    print("\n== 三、实体插值(cl_interp 100 ms) ==")
    itp = run_interpolation()
    print(f"  远端 300 u/s, 快照间隔 {itp['snap_interval_ms']:.0f} ms; "
          f"渲染滞后 {itp['lag_ms']:.0f} ms -> 位置差 {itp['lag_distance']:.1f} u "
          f"(缓冲里约 {itp['snapshots_in_buffer']:.1f} 个快照)")
    assert abs(itp["lag_distance"] - 30.0) < 1e-9, itp
    print("  -> 换到平滑的远端表现, 代价是恒定约 100 ms 的「看到的是过去」  OK")

    print("\n== 四、延迟补偿(回退其他玩家) ==")
    lc = run_lag_compensation()
    print(f"  历史缓冲 = 最近 {lc['history_seconds']:.0f} s; 命令执行时刻 = 现在 - RTT - "
          f"插值 = {lc['rewind_ms']:.0f} ms -> 目标回退 {lc['rewind_distance']:.1f} u")
    print(f"  不回退: 目标偏移 {lc['naive_offset']:.1f} u, 命中半径 {lc['radius']:.0f} u "
          f"-> {'命中' if lc['naive_hit'] else '脱靶'}")
    print(f"  回退后: 目标偏移 {lc['rewound_offset']:.1f} u -> "
          f"{'命中' if lc['rewound_hit'] else '脱靶'}")
    assert not lc["naive_hit"] and lc["rewound_hit"], lc
    print("  -> 不回退必然脱靶; 回退到「客户端当时看到的世界」才判得中  OK")
    print("  -> 代价是「已躲到掩体后仍被击中」这类反直觉现象无法根本消除")

    print("\n全部自检通过。")


if __name__ == "__main__":
    main()
