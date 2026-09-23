"""Godot 状态机演示入口：交叉淡入推进 / 优先级 / travel。"""

from statemachine import (
    Playback, StateMachine, Transition, CMP_EPSILON, SYNC, ADVANCE_DISABLED,
)

if __name__ == "__main__":
    sm = StateMachine([Transition("Idle", "Walk", xfade_time=0.5, advance_condition="move")])
    pb = Playback(sm, "Idle")
    sm.set_condition("move", True)
    print("[交叉淡入] xfade=0.5, 每帧 delta=0.1：")
    for i in range(7):
        w = pb.process(0.1)
        print(f"  第 {i + 1} 帧: {w}")

    smp = StateMachine([
        Transition("A", "B", priority=5, advance_condition="go"),
        Transition("A", "C", priority=1, advance_condition="go"),
        Transition("A", "D", priority=1, advance_condition="go"),
    ])
    pbp = Playback(smp, "A")
    smp.set_condition("go", True)
    pbp.process(0.1)
    print("[优先级] 5/1/1 三条同时成立 → 选中:", pbp.current, "（最小 1，平局取下标大者）")

    smv = StateMachine([
        Transition("A", "B", xfade_time=0.4, priority=9, advance_condition="never"),
        Transition("B", "C", xfade_time=0.4, priority=9, advance_condition="never"),
        Transition("A", "D", priority=0, advance_condition="go"),
    ])
    pbv = Playback(smv, "A")
    smv.set_condition("go", True)
    pbv.travel("C")
    print("[travel ] 规划路径:", pbv.path)
    pbv.process(0.1)
    print("  第 1 帧后 current:", pbv.current, " 剩余路径:", pbv.path)
    pbv.process(0.5)
    print("  第 2 帧后 current:", pbv.current, " 剩余路径:", pbv.path)

    smd = StateMachine([Transition("A", "B", advance_mode=ADVANCE_DISABLED,
                                   advance_condition="go")])
    pbd = Playback(smd, "A")
    smd.set_condition("go", True)
    pbd.process(0.1)
    print("[DISABLED] 条件成立也不推进，current:", pbd.current)
