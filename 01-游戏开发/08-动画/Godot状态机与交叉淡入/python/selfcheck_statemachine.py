"""Godot 状态机自检：期望值逐条手算过。"""

import math

from statemachine import (
    Playback, StateMachine, Transition, CMP_EPSILON,
    IMMEDIATE, SYNC, AT_END, ADVANCE_AUTO, ADVANCE_DISABLED,
)

COUNT = 0
FAIL = []


def ok(cond, msg):
    global COUNT
    COUNT += 1
    if not cond:
        FAIL.append(msg)
        print("FAIL:", msg)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def raises(fn, label):
    try:
        fn()
    except ValueError:
        return True
    except Exception as e:  # noqa: BLE001
        print("  (", label, "抛了非 ValueError:", type(e).__name__, ")")
        return False
    print("  (", label, "没有抛异常 )")
    return False


# ---------------------------------------------------------------- 交叉淡入推进
sm = StateMachine([Transition("Idle", "Walk", xfade_time=0.5, advance_condition="move")])
pb = Playback(sm, "Idle")
sm.set_condition("move", True)
w = pb.process(0.1)
ok(pb.current == "Walk", "条件成立后切到 Walk")
ok(pb.fading_from == "Idle", "xfade_time > 0 时记录 fading_from")
ok(close(w["Walk"], 0.2), f"0.1/0.5 = 0.2, 实得 {w['Walk']}")
ok(close(w["Idle"], 0.8), f"旧状态权重 1-0.2 = 0.8, 实得 {w['Idle']}")
ok(close(sum(w.values()), 1.0), "两侧权重和为 1")

w = pb.process(0.2)
ok(close(w["Walk"], 0.6), f"0.3/0.5 = 0.6, 实得 {w['Walk']}")
w = pb.process(0.2)
ok(close(w["Walk"], 1.0), f"0.5/0.5 = 1.0（MIN 封顶）, 实得 {w['Walk']}")
ok(close(w["Idle"], CMP_EPSILON),
   f"淡入结束时旧状态仍保留 CMP_EPSILON 权重（源码注释：为了处理 discrete 关键帧）, 实得 {w['Idle']}")
w = pb.process(0.5)
ok(close(w["Walk"], 1.0), "继续推进也不会超过 1")

# 负 delta 取绝对值
sm2 = StateMachine([Transition("A", "B", xfade_time=1.0, advance_condition="go")])
pb2 = Playback(sm2, "A")
sm2.set_condition("go", True)
pb2.process(-0.25)
w = pb2.process(-0.25)
ok(close(w["B"], 0.5), f"负 delta 取 abs 后仍推进：0.5, 实得 {w['B']}")

# seek 不推进淡入
sm3 = StateMachine([Transition("A", "B", xfade_time=1.0, advance_condition="go")])
pb3 = Playback(sm3, "A")
sm3.set_condition("go", True)
pb3.process(0.3, seek=True)
w = pb3.process(0.3, seek=True)
ok(close(w["B"], 0.0 + CMP_EPSILON),
   f"seek 时 fading_pos 不累加 → fade 保持 0（下限 CMP_EPSILON）, 实得 {w['B']}")

# 无淡入
sm4 = StateMachine([Transition("A", "B", xfade_time=0.0, advance_condition="go")])
pb4 = Playback(sm4, "A")
sm4.set_condition("go", True)
w = pb4.process(0.1)
ok(pb4.fading_from is None, "xfade_time == 0 时不产生淡入")
ok(close(w["B"], 1.0) and "A" not in w, "无淡入时只有新状态有权重")

# xfade_curve 采样
sm5 = StateMachine([Transition("A", "B", xfade_time=1.0, xfade_curve=lambda x: x * x,
                               advance_condition="go")])
pb5 = Playback(sm5, "A")
sm5.set_condition("go", True)
w = pb5.process(0.5)
ok(close(w["B"], 0.25), f"曲线采样：0.5² = 0.25, 实得 {w['B']}")
ok(close(w["A"], 0.75), f"另一侧 1-0.25 = 0.75, 实得 {w['A']}")

# ---------------------------------------------------------------- 优先级
smp = StateMachine([
    Transition("A", "B", priority=5, advance_condition="go"),
    Transition("A", "C", priority=1, advance_condition="go"),
])
pbp = Playback(smp, "A")
smp.set_condition("go", True)
pbp.process(0.1)
ok(pbp.current == "C", f"优先级更小者优先（1 < 5）, 实得 {pbp.current}")

smt = StateMachine([
    Transition("A", "B", priority=1, advance_condition="go"),
    Transition("A", "C", priority=1, advance_condition="go"),
])
pbt = Playback(smt, "A")
smt.set_condition("go", True)
pbt.process(0.1)
ok(pbt.current == "C", f"优先级相同时下标更大者胜（<= 判定）, 实得 {pbt.current}")

# 条件不成立 / 非 AUTO / DISABLED 都不会推进
smc = StateMachine([Transition("A", "B", advance_condition="go")])
pbc = Playback(smc, "A")
smc.set_condition("go", False)
pbc.process(0.1)
ok(pbc.current == "A", "条件未成立时不推进")
ok(Playback(smc, "A")._check_advance_condition(
    Transition("A", "B", advance_mode=ADVANCE_DISABLED, advance_condition="go")) is False,
   "advance_mode != AUTO 时 _check_advance_condition 恒 false")

smd = StateMachine([Transition("A", "B", advance_mode=ADVANCE_DISABLED, advance_condition="go")])
pbd = Playback(smd, "A")
smd.set_condition("go", True)
pbd.process(0.1)
ok(pbd.current == "A", "ADVANCE_MODE_DISABLED 的过渡被跳过（源码 continue）")

# ---------------------------------------------------------------- travel 优先于条件与优先级
smv = StateMachine([
    Transition("A", "B", priority=9, advance_condition="never"),
    Transition("B", "C", priority=9, advance_condition="never"),
    Transition("A", "D", priority=0, advance_condition="go"),
])
pbv = Playback(smv, "A")
smv.set_condition("go", True)
pbv.travel("C")
ok(pbv.path == ["B", "C"], f"travel 会规划路径, 实得 {pbv.path}")
# 注意：无淡入时 _transition_to_next_recursive 会在**同一帧**走完整条路径
pbv.process(0.1)
ok(pbv.current == "C", f"无淡入时一帧走完 B→C（路径上的过渡不看条件也不比优先级）, 实得 {pbv.current}")
ok(pbv.path == [], "路径走完后被清空")

# 带上淡入后才会「一帧一步」
smv2 = StateMachine([
    Transition("A", "B", xfade_time=0.4, priority=9, advance_condition="never"),
    Transition("B", "C", xfade_time=0.4, priority=9, advance_condition="never"),
    Transition("A", "D", priority=0, advance_condition="go"),
])
pbv2 = Playback(smv2, "A")
smv2.set_condition("go", True)
pbv2.travel("C")
pbv2.process(0.1)
ok(pbv2.current == "B", f"有淡入时第一帧只走到 B（fading 必须先处理）, 实得 {pbv2.current}")
ok(pbv2.path == ["C"], f"路径还剩 C, 实得 {pbv2.path}")
pbv2.process(0.5)
ok(pbv2.current == "C", f"淡入推进后走到 C, 实得 {pbv2.current}")

# travel 到不可达状态 → teleport（清掉淡入）
smu = StateMachine([Transition("A", "B", advance_condition="go")])
pbu = Playback(smu, "A")
pbu.travel("Z")
ok(pbu.current == "Z" and pbu.teleported, "不可达的 travel 退化为 teleport")
ok(pbu.fading_from is None and pbu.fading_time == 0.0, "teleport 会清掉淡入状态")

# start 同样是 teleport
sms = StateMachine([Transition("A", "B", xfade_time=0.4, advance_condition="go")])
pbs = Playback(sms, "B")
sms.set_condition("go", True)
pbs.process(0.1)
pbs.start("A")
ok(pbs.current == "A" and pbs.fading_from is None, "start 直接 teleport 并清掉淡入")

# ---------------------------------------------------------------- 环路检测
sml = StateMachine([
    Transition("A", "B", advance_condition="go"),
    Transition("B", "A", advance_condition="go"),
])
pbl = Playback(sml, "A")
sml.set_condition("go", True)
pbl.process(0.1)  # 不应无限递归
ok(pbl.loop_aborted, "互相指向的过渡会被 transition_path 环路检测拦下")
ok(pbl.current in ("A", "B"), f"拦下后停在某个状态, 实得 {pbl.current}")

# ---------------------------------------------------------------- switch mode
smsy = StateMachine([Transition("A", "B", switch_mode=SYNC, advance_condition="go")])
pbsy = Playback(smsy, "A")
pbsy.position = 0.75
smsy.set_condition("go", True)
pbsy.process(0.1)
ok(close(pbsy.position, 0.75), f"SWITCH_MODE_SYNC 会把新状态 seek 到旧位置, 实得 {pbsy.position}")

smim = StateMachine([Transition("A", "B", switch_mode=IMMEDIATE, advance_condition="go")])
pbim = Playback(smim, "A")
pbim.position = 0.75
smim.set_condition("go", True)
pbim.process(0.1)
ok(close(pbim.position, 0.75), "IMMEDIATE 不重置位置（is_reset 为 false）")

smrs = StateMachine([Transition("A", "B", switch_mode=IMMEDIATE, is_reset=True,
                                advance_condition="go")])
pbrs = Playback(smrs, "A")
pbrs.position = 0.75
smrs.set_condition("go", True)
pbrs.process(0.1)
ok(close(pbrs.position, 0.0), f"is_reset 为真时新状态从头播, 实得 {pbrs.position}")

# ---------------------------------------------------------------- 参数校验
ok(raises(lambda: Transition("A", "B", xfade_time=-0.1), "负 xfade"),
   "xfade_time < 0 会被 set_xfade_time 的 ERR_FAIL_COND 拒绝")
ok(Transition("A", "B", xfade_time=0.0).xfade_time == 0.0, "xfade_time = 0 合法")

# ---------------------------------------------------------------- 不变量
smi = StateMachine([Transition("A", "B", xfade_time=0.5, advance_condition="go")])
pbi = Playback(smi, "A")
smi.set_condition("go", True)
for i in range(12):
    ww = pbi.process(0.1)
    ok(close(sum(ww.values()), 1.0, 1e-12) or close(sum(ww.values()), 1.0 + CMP_EPSILON, 1e-12),
       f"任何时刻两侧权重和约为 1（第 {i} 步 {ww}）")
    ok(all(v > 0 for v in ww.values()), f"两侧权重恒 > 0（第 {i} 步 {ww}）")
    ok(ww["B"] <= 1.0 + 1e-12, f"新状态权重不超过 1（第 {i} 步）")

print(f"\n断言总数: {COUNT}, 失败: {len(FAIL)}")
if FAIL:
    for m in FAIL:
        print("  -", m)
    raise SystemExit(1)
print("全部通过")
