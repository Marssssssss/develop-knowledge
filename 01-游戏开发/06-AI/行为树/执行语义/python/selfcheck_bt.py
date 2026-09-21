"""行为树执行语义自检：把 BT.CPP 的判定逐条变成断言。

运行：``python selfcheck_bt.py`` （当前目录 = python/）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    FAILURE, IDLE, RUNNING, SKIPPED, SUCCESS,
    FallbackNode, InverterNode, Leaf, LogicError, ParallelNode,
    ReactiveSequence, RepeatNode, SequenceNode, SkippedLeaf,
    REACTIVE_THROW_IF_MULTIPLE_RUNNING,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1


def raises(fn, label):
    global PASS
    try:
        fn()
    except LogicError:
        PASS += 1
        return
    raise AssertionError("FAIL(should raise LogicError): " + label)


# ---------- E1..E3 Sequence 的「记忆」与「重置」 ----------
a = Leaf("a", [SUCCESS])
b = Leaf("b", [RUNNING])
c = Leaf("c", [SUCCESS])
seq = SequenceNode("seq", [a, b, c])
ok(seq.execute_tick() == RUNNING, "E1-1 seq 首 tick RUNNING")
ok(a.tick_count == 1 and b.tick_count == 1 and c.tick_count == 0, "E1-2 c 未被 tick")
ok(seq.execute_tick() == RUNNING, "E1-3 第二 tick 仍 RUNNING")
ok(a.tick_count == 1, "E1-4 前半段不会再被 tick（Sequence 不回头）")
ok(b.tick_count == 2, "E1-5 RUNNING 的孩子被重复 tick")

a2 = Leaf("a2", [SUCCESS])
b2 = Leaf("b2", [SUCCESS])
seq2 = SequenceNode("seq2", [a2, b2])
ok(seq2.execute_tick() == SUCCESS, "E2-1 全部 SUCCESS → SUCCESS")
ok(seq2.execute_tick() == SUCCESS and a2.tick_count == 2, "E2-2 走完后复位，下一 tick 从头开始")

a3 = Leaf("a3", [SUCCESS])
b3 = Leaf("b3", [FAILURE, SUCCESS])
c3 = Leaf("c3", [SUCCESS])
seq3 = SequenceNode("seq3", [a3, b3, c3])
ok(seq3.execute_tick() == FAILURE, "E3-1 子 FAILURE → 立即 FAILURE")
ok(c3.tick_count == 0, "E3-2 FAILURE 后不再继续 tick 后续兄弟")
ok(a3.status == IDLE and b3.status == IDLE, "E3-3 resetChildren 把孩子状态清回 IDLE")
ok(seq3.execute_tick() == SUCCESS and c3.tick_count == 1, "E3-4 失败复位后下一 tick 重新走完整条")

# ---------- E4..E5 Sequence 的 SKIPPED 与 IDLE ----------
ok(SequenceNode("s", [SkippedLeaf(), SkippedLeaf()]).execute_tick() == SKIPPED,
   "E4-1 全部 SKIPPED → SKIPPED")
ok(SequenceNode("s", [SkippedLeaf(), Leaf("x", [SUCCESS])]).execute_tick() == SUCCESS,
   "E4-2 部分 SKIPPED 不算全跳过")
raises(lambda: SequenceNode("s", [Leaf("bad", [])]).execute_tick(),
       "E5 子返回 IDLE → LogicError")

# ---------- E6..E8 Fallback ----------
fa = Leaf("fa", [FAILURE])
fb = Leaf("fb", [RUNNING])
fbk = FallbackNode("fbk", [fa, fb])
ok(fbk.execute_tick() == RUNNING, "E6-1 Fallback 命中 RUNNING")
ok(fbk.execute_tick() == RUNNING and fa.tick_count == 1, "E6-2 前面失败的孩子不再重试")
fc = Leaf("fc", [SUCCESS])
fbk2 = FallbackNode("fbk2", [Leaf("f1", [FAILURE]), fc])
ok(fbk2.execute_tick() == SUCCESS, "E7-1 命中 SUCCESS → SUCCESS")
ok(fbk2.execute_tick() == SUCCESS and fbk2.current_child_idx_ == 0, "E7-2 成功后复位")
ok(FallbackNode("f", [Leaf("f", [FAILURE]), Leaf("f", [FAILURE])]).execute_tick() == FAILURE,
   "E7-3 全失败 → FAILURE")
ok(FallbackNode("f", [SkippedLeaf(), SkippedLeaf()]).execute_tick() == SKIPPED,
   "E8 全 SKIPPED → SKIPPED")

# ---------- E9..E11 ReactiveSequence ----------
ra = Leaf("ra", [SUCCESS])
rb = Leaf("rb", [RUNNING])
rs = ReactiveSequence("rs", [ra, rb])
ok(rs.execute_tick() == RUNNING, "E9-1 首 tick RUNNING")
ok(rs.execute_tick() == RUNNING, "E9-2 仍 RUNNING")
ok(ra.tick_count == 2, "E9-3 响应式每个 tick 都从头扫（与 Sequence 的关键差异）")
ok(ra.status == IDLE, "E9-4 命中 RUNNING 时把其余兄弟重置为 IDLE")
ok(ReactiveSequence("r", [SkippedLeaf(), SkippedLeaf()]).execute_tick() == SKIPPED,
   "E10 全 SKIPPED → SKIPPED")
rc = Leaf("rc", [SUCCESS, FAILURE])
rs2 = ReactiveSequence("rs2", [rc])
ok(rs2.execute_tick() == SUCCESS, "E11-1 全成功 → SUCCESS")
ok(rs2.execute_tick() == FAILURE and rc.status == IDLE, "E11-2 FAILURE 后 resetChildren")

# ---------- E12..E15 Parallel ----------
p = ParallelNode("p", [Leaf("p0", [SUCCESS]), Leaf("p1", [SUCCESS]), Leaf("p2", [RUNNING])])
ok(p.success_threshold() == 3 and p.failure_threshold() == 1, "E12-1 默认阈值 -1/1 → 3/1")
ok(p.execute_tick() == RUNNING, "E12-2 两成一未完 → RUNNING")
ok(p.execute_tick() == RUNNING and p.children[0].tick_count == 1, "E12-3 completed_list_ 不重复 tick 已完成的孩子")
ok(ParallelNode("p", [Leaf("s", [SUCCESS])] * 3).execute_tick() == SUCCESS, "E12-4 全部成功 → SUCCESS")

pf = ParallelNode("pf", [Leaf("f", [FAILURE]), Leaf("s", [SUCCESS]), Leaf("s", [SUCCESS])])
ok(pf.execute_tick() == FAILURE, "E13-1 一个失败即 FAILURE（failure_threshold 默认 1）")
ok(pf.children[1].tick_count == 0, "E13-2 剩余孩子在失败判定后根本不会被 tick")

pneg = ParallelNode("pn", [Leaf("a", [SUCCESS]), Leaf("b", [SUCCESS]), Leaf("c", [RUNNING])],
                    success_threshold=-2)
ok(pneg.success_threshold() == 2, "E14-1 负阈值 -2 表示 3+(-2)+1=2")
ok(pneg.execute_tick() == SUCCESS and pneg.children[2].tick_count == 0, "E14-2 达到 2 票即 SUCCESS")

raises(lambda: ParallelNode("p", [Leaf("s", [SUCCESS])] * 3, success_threshold=4).execute_tick(),
       "E15-1 孩子数 < success 阈值 → LogicError")
raises(lambda: ParallelNode("p", [Leaf("s", [SUCCESS])] * 3, failure_threshold=4).execute_tick(),
       "E15-2 孩子数 < failure 阈值 → LogicError")
ok(ParallelNode("p", [SkippedLeaf(), SkippedLeaf()], success_threshold=1).execute_tick() == SKIPPED,
   "E15-3 正阈值下全跳过才返回 SKIPPED")
ok(ParallelNode("p", [SkippedLeaf(), SkippedLeaf()], success_threshold=-1).execute_tick() == SUCCESS,
   "E15-4 负阈值时 SKIPPED 计入成功票，故全跳过反而 SUCCESS")

# ---------- E16..E17 Inverter ----------
iv = InverterNode("iv", Leaf("s", [SUCCESS]))
ok(iv.execute_tick() == FAILURE, "E16-1 SUCCESS → FAILURE")
ok(iv.child.status == IDLE, "E16-2 翻转子节点后 resetChild")
ok(InverterNode("iv", Leaf("f", [FAILURE])).execute_tick() == SUCCESS, "E16-3 FAILURE → SUCCESS")
ok(InverterNode("iv", Leaf("r", [RUNNING])).execute_tick() == RUNNING, "E16-4 RUNNING 原样透传")
ok(InverterNode("iv", SkippedLeaf()).execute_tick() == SKIPPED, "E17 SKIPPED 原样透传")

# ---------- E18..E22 Repeat ----------
rp = RepeatNode("rp", Leaf("r", [SUCCESS]), 3)
ok(rp.execute_tick() == SUCCESS and rp.children[0].tick_count == 3, "E18 N=3 需要 3 次成功")
ok(rp.repeat_count_ == 0, "E18-2 成功后计数清零")

rp2 = RepeatNode("rp2", Leaf("r2", [SUCCESS, FAILURE]), 3)
ok(rp2.execute_tick() == FAILURE and rp2.children[0].tick_count == 2, "E19 中途 FAILURE 立即返回")
ok(rp2.repeat_count_ == 0, "E19-2 失败时计数清零")

rp3 = RepeatNode("rp3", Leaf("r3", [RUNNING, SUCCESS]), 2)
ok(rp3.execute_tick() == RUNNING and rp3.repeat_count_ == 0, "E20 RUNNING 直接返回且计数不变")

rp4 = RepeatNode("rp4", Leaf("r4", [SUCCESS, SUCCESS, FAILURE]), -1)
ok(rp4.execute_tick() == FAILURE and rp4.children[0].tick_count == 3, "E21 N=-1 无限重复直到失败")
ok(RepeatNode("rp5", SkippedLeaf(), 3).execute_tick() == SKIPPED, "E22 SKIPPED 透传")

# ---------- E23 组合：ReactiveSequence 套 Parallel 的抢占 ----------
inner = ParallelNode("inner", [Leaf("i0", [RUNNING])], success_threshold=1, failure_threshold=1)
guard = Leaf("guard", [SUCCESS])
outer = ReactiveSequence("outer", [guard, inner])
ok(outer.execute_tick() == RUNNING, "E23-1 外层命中 RUNNING")
ok(guard.status == IDLE, "E23-2 guard 每 tick 被重置，下一 tick 会重新求值（条件可被抢占）")

ok(REACTIVE_THROW_IF_MULTIPLE_RUNNING is False, "E24 官方 throw_if_multiple_running 默认关闭")

print("PASS =", PASS)
