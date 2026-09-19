"""timestep.py — 固定时间步 + accumulator + 插值渲染的最小模型与自检.

事实来源：Glenn Fiedler《Fix Your Timestep!》（gafferongames.com/post/fix_your_timestep/，
本机 curl 落地后全文实读）。原文给出的四种方案与本 demo 的对应关系：

  1. fixed delta time       —— dt 恒定，帧率不匹配时物理时快时慢
  2. variable delta time    —— 把上一帧耗时当 dt 喂回去，仿真结果与帧率耦合（本 demo 量化）
  3. semi-fixed timestep    —— 按 min(frameTime, dt) 切分，仍会出现「非 dt 的零头步」
  4. free the physics       —— accumulator + 固定 dt + alpha 插值（本文最终方案）

原文代码里的两个关键细节：frameTime > 0.25 时钳到 0.25 秒；alpha = accumulator / dt，
渲染状态 = current * alpha + previous * (1 - alpha)。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List

DT_MS = 10                 # 固定步长 10 ms（= 1/100 秒）
DT = DT_MS / 1000.0
MAX_FRAME_MS = 250         # 原文：`if ( frameTime > 0.25 ) frameTime = 0.25;`
K = 100.0                  # 弹簧刚度（ω = 10 rad/s）

_ASSERTIONS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _ASSERTIONS
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")
    _ASSERTIONS += 1
    print(f"ok {_ASSERTIONS:>2} {label}" + (f"  [{detail}]" if detail else ""))


@dataclass
class State:
    x: float = 1.0
    v: float = 0.0


def integrate(s: State, dt: float) -> None:
    """显式欧拉积分弹簧：a = -K·x。步长过大会发散（用来对比可变步长）。"""
    a = -K * s.x
    s.v += a * dt
    s.x += s.v * dt


def run(frame_times_ms: List[int], max_steps: int | None = None,
        clamp_ms: int | None = MAX_FRAME_MS, interp: bool = False,
        cost_per_step_ms: int = 0) -> Dict[str, object]:
    """accumulator 主循环。时间一律用整数 ms 记账，避免浮点误差污染步数。"""
    acc = 0
    t_ms = 0
    prev, cur = State(), State()
    spf: List[int] = []
    rendered: List[float] = []
    alphas: List[float] = []
    prevs: List[float] = []
    curs: List[float] = []
    pending_ms = 0
    for base in frame_times_ms:
        ft = base + pending_ms                    # 上一帧仿真耗时计入本帧帧时间
        if clamp_ms is not None:
            ft = min(ft, clamp_ms)
        acc += ft
        n = 0
        while acc >= DT_MS and (max_steps is None or n < max_steps):
            prev = replace(cur)
            integrate(cur, DT)
            acc -= DT_MS
            t_ms += DT_MS
            n += 1
        spf.append(n)
        pending_ms = n * cost_per_step_ms
        alpha = acc / DT_MS
        alphas.append(alpha)
        prevs.append(prev.x)
        curs.append(cur.x)
        rendered.append(cur.x * alpha + prev.x * (1.0 - alpha) if interp else cur.x)
    return {"t_ms": t_ms, "acc": acc, "x": cur.x, "v": cur.v, "steps": sum(spf),
            "spf": spf, "rendered": rendered, "alphas": alphas,
            "prevs": prevs, "curs": curs}


def run_variable(frame_times_ms: List[int]) -> State:
    """可变步长：直接把每帧耗时当 dt（原文方案 2）。"""
    s = State()
    for ft in frame_times_ms:
        integrate(s, ft / 1000.0)
    return s


def pacing_60(total_ms: int = 1000) -> List[int]:
    out, left = [], total_ms
    while left > 0:
        step = min(16, left)
        out.append(step)
        left -= step
    return out


def pacing_30(total_ms: int = 1000) -> List[int]:
    out, left = [], total_ms
    while left > 0:
        step = min(33, left)
        out.append(step)
        left -= step
    return out


def main() -> None:
    # 1) 固定步长：仿真时间 = floor(总时间 / dt) * dt，余数 < dt
    r = run(pacing_60(1000))
    check("1 秒内步数 = 总时间 / dt", r["steps"] == 100, f"{r['steps']}")
    check("仿真时间不超过真实时间", r["t_ms"] <= 1000, f"{r['t_ms']}")
    check("accumulator 余数恒 < dt", r["acc"] < DT_MS, f"{r['acc']}")
    check("每帧步数随余数在 1/2 之间摆动", set(r["spf"]) <= {1, 2}, f"{sorted(set(r['spf']))}")

    # 2) 决定性（deterministic lockstep 的前提）：同样总时长、不同帧率 → 完全一致
    a = run(pacing_60(1000))
    b = run(pacing_30(1000))
    check("60fps 与 30fps 跑同样总时长步数相同", a["steps"] == b["steps"] == 100,
          f"{a['steps']} vs {b['steps']}")
    check("最终状态逐位相同（同输入同输出）", a["x"] == b["x"] and a["v"] == b["v"],
          f"{a['x']!r} vs {b['x']!r}")

    # 3) 可变步长会把帧率耦合进物理结果
    va = run_variable(pacing_60(1000))
    vb = run_variable(pacing_30(1000))
    check("可变步长下不同帧率得到不同结果", abs(va.x - vb.x) > 1e-6, f"{va.x} vs {vb.x}")
    check("可变步长与固定步长也不同", abs(va.x - a["x"]) > 1e-6, f"{va.x} vs {a['x']}")

    # 4) 帧时间钳位：2 秒卡顿只计入 0.25 秒
    c = run([2000, 16])
    check("超长帧被钳到 0.25 秒", c["steps"] == 25 + 1, f"{c['steps']}")
    c2 = run([2000, 16], clamp_ms=None)
    check("不钳位则一帧内跑满 200 步", c2["steps"] == 200 + 1, f"{c2['steps']}")

    # 5) 死亡螺旋：仿真耗时 > 帧时间 → 每帧步数递增
    spiral = run([16] * 8, cost_per_step_ms=12)          # 每步 12ms > dt 10ms
    spf = spiral["spf"]                                   # type: ignore[assignment]
    check("死亡螺旋：步数逐帧递增", spf[-1] > spf[0] and spf[-1] > 3, f"{spf}")
    clamped = run([16] * 8, max_steps=3, cost_per_step_ms=12)
    check("限制每帧最大步数后不再发散", max(clamped["spf"]) == 3, f"{clamped['spf']}")  # type: ignore[arg-type]
    check("限流后 accumulator 仍在累积（仿真落后于真实时间）",
          clamped["acc"] > 0 and clamped["acc"] >= 22, f"acc={clamped['acc']}")

    # 5b) 对照组：单步耗时 < dt 时不会螺旋（步数收敛到稳态）
    steady = run([16] * 40, cost_per_step_ms=8)            # 单步 8ms < dt 10ms
    ss = steady["spf"]                                     # type: ignore[assignment]
    check("单步耗时 < dt 时步数收敛到稳态（不螺旋）",
          ss[-1] == ss[-2] == 8, f"{ss[-4:]}")

    # 5c) 插值消除「画面停顿」：统计相邻帧渲染值完全不变的帧数
    def stalls(frame_times: List[int], interp: bool) -> int:
        r = run(frame_times, interp=interp)["rendered"]     # type: ignore[assignment]
        return sum(1 for i in range(1, len(r)) if r[i] == r[i - 1])

    high_fps = [5] * 120                                   # 200fps：帧时间 < dt
    raw_stalls = stalls(high_fps, interp=False)
    interp_stalls = stalls(high_fps, interp=True)
    check("高刷下不插值会有画面停顿帧", raw_stalls > 0, f"{raw_stalls}")
    check("插值后停顿帧显著减少", interp_stalls < raw_stalls, f"{interp_stalls} < {raw_stalls}")

    # 6) alpha 插值：渲染的是 previous 与 current 之间的一点
    d = run(pacing_60(300), interp=True)
    check("alpha 恒在 [0,1)", all(0.0 <= a_ < 1.0 for a_ in d["alphas"]),  # type: ignore[union-attr]
          f"{d['alphas'][:4]}")
    rd = d["rendered"]                                     # type: ignore[assignment]
    pv, cu = d["prevs"], d["curs"]                         # type: ignore[assignment]
    check("插值结果恒落在 previous 与 current 之间（不 extrapolate）",
          all(min(p, c) - 1e-12 <= r_ <= max(p, c) + 1e-12
              for r_, p, c in zip(rd, pv, cu)),            # type: ignore[arg-type]
          f"首帧 {rd[0]} in [{pv[0]}, {cu[0]}]")
    zero = run([20], interp=True)
    check("alpha=0 时渲染的正是 previous", zero["alphas"][0] == 0.0
          and zero["rendered"][0] == zero["prevs"][0],     # type: ignore[index]
          f"alpha={zero['alphas'][0]} rendered={zero['rendered'][0]} prev={zero['prevs'][0]}")  # type: ignore[index]
    half = run([25], interp=True)
    check("alpha=1/2 时渲染取前后中点",
          abs(half["rendered"][0]                          # type: ignore[index]
              - (half["curs"][0] * 0.5 + half["prevs"][0] * 0.5)) < 1e-12,  # type: ignore[index]
          f"{half['rendered'][0]}")                        # type: ignore[index]

    # 7) 固定步长 + 插值的总代价：步数只由总时长决定，与帧率无关
    e1 = run(pacing_60(2000))
    e2 = run(pacing_30(2000))
    check("2 秒内步数与帧率无关", e1["steps"] == e2["steps"] == 200,
          f"{e1['steps']} vs {e2['steps']}")

    # 8) 半固定步长会引入「非 dt 的零头步」
    def semi_fixed(frame_times_ms: List[int]) -> List[float]:
        dts = []
        for ft in frame_times_ms:
            left = ft
            while left > 0:
                step = min(DT_MS, left)
                dts.append(step / 1000.0)
                left -= step
        return dts

    dts = semi_fixed(pacing_60(1000))
    check("半固定步长会出现小于 dt 的零头步", any(abs(d - DT) > 1e-12 for d in dts),
          f"零头步 {sum(1 for d in dts if abs(d - DT) > 1e-12)} 个")

    print(f"\n全部 {_ASSERTIONS} 条断言通过")


if __name__ == "__main__":
    main()
