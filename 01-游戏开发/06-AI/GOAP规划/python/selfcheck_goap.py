"""GOAP 反向规划自检：把 ReGoap 的判定逐条变成断言。

运行：``python selfcheck_goap.py``（当前目录 = python/）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    Action, Cond, Goal, GoapNode, State, is_match,
)
from planner import Planner  # noqa: E402

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1


# ---------- 基础：条件匹配 ----------
ok(is_match(True, True) and not is_match(True, False), "E0-1 普通值走等值")
ok(is_match(Cond.ge(2), 3) and not is_match(Cond.ge(2), 1), "E0-2 条件是 required 时用条件判定")
ok(is_match(3, Cond.ge(2)) and not is_match(1, Cond.ge(2)), "E0-3 条件是 candidate 时反向判定")

# ---------- 场景 1：默认设置下的最优计划 ----------
world = {}
actions = [
    Action("GetAxe", {}, {"hasAxe": True}, 1),
    Action("ChopWood", {"hasAxe": True}, {"hasWood": True}, 2),
    Action("BuyWood", {}, {"hasWood": True}, 5),
]
planner = Planner(world, actions)
goal_wood = Goal("haveWood", {"hasWood": True}, priority=1)

root = GoapNode(planner, goal_wood.goal_state)
ok(root.h == 1 and root.cost == 1.0, "E1-1 根结点 h = 剩余目标条件数 = 1，f = 0 + 1")
ok(not root.is_goal(), "E1-2 世界状态不满足目标 → 根不是终点")
ok(GoapNode(planner, State({"hasOre": True})).cost == 1.0, "E1-3 单条件目标 cost=1")

kid = GoapNode(planner, goal_wood.goal_state, root, actions[1])
ok(kid.g == 2.0, "E2-1 g 累加动作代价")
ok(kid.goal.values == {"hasAxe": True}, "E2-2 目标的 hasWood 被效果消掉，换成动作的 hasAxe 前提")
ok(kid.h == 1 and kid.cost == 3.0, "E2-3 h 变成 1，f = 2 + 1")

names = [n.action.name for n in root.expand()]
ok(names == ["BuyWood", "ChopWood"], "E3-1 根节点只展开「效果命中目标」的动作（倒序遍历动作表）")
ok("GetAxe" not in names, "E3-2 GetAxe 的效果不碰目标 → 被 hasAny 挡掉")

chosen = planner.plan([goal_wood])
ok(chosen is not None and chosen.plan == ["GetAxe", "ChopWood"], "E4-1 默认设置给出最便宜的计划")
# 注意：子节点收的是「父节点的 Goal」而不是原始目标（Expand 里 newGoal = Goal）
ok(GoapNode(planner, kid.goal, kid, actions[0]).is_goal(), "E4-2 走完 GetAxe 后 goalMergedWithWorld 为空 → 命中")
ok(GoapNode(planner, kid.goal, kid, actions[0]).cost == 3.0, "E4-2b 该叶子的 f = g(3) + h(0)")
ok(chosen.plan == ["GetAxe", "ChopWood"] and chosen.plan[0] == "GetAxe",
   "E4-3 反向规划 ⇒ 从叶子回溯到根的顺序就是执行顺序")

# ---------- 场景 2：PlanningEarlyExit 打开后会返回更贵的计划 ----------
p2 = Planner(world, actions, {"max_iterations": 1000, "max_nodes_to_expand": 10000,
                              "planning_early_exit": True, "using_dynamic_actions": False,
                              "use_weighted_random": False, "weighted_random_priority_power": 1.0,
                              "weighted_random_min_weight": 0.001})
g2 = Goal("haveWood", {"hasWood": True}, priority=1)
chosen2 = p2.plan([g2])
ok(chosen2.plan == ["BuyWood"], "E5-1 early exit 返回「展开时第一个命中」的孩子（动作表倒序第一项）")
ok(len(chosen2.plan) == 1 and chosen.plan != chosen2.plan, "E5-2 early exit 换来的短计划比默认解更贵（5 > 3）")

# ---------- 场景 3：目标已被满足 → 空计划被当成失败 ----------
p3 = Planner({"hasWood": True}, actions)
ok(p3.plan([Goal("haveWood", {"hasWood": True})]) is None, "E6 目标已被世界满足 → 空计划 → 视为无计划")

# ---------- 场景 4：冲突过滤 ----------
act_trade = Action("TradeAxe", {}, {"hasAxe": False, "hasWood": True}, 1)
p4 = Planner(world, [Action("GetAxe", {}, {"hasAxe": True}, 1), act_trade])
root4 = GoapNode(p4, State({"hasAxe": True, "hasWood": True}))
names4 = [n.action.name for n in root4.expand()]
ok("TradeAxe" not in names4, "E7-1 效果与已有目标条件冲突的动作被 HasAnyConflict 挡掉")
ok("GetAxe" in names4, "E7-1b 只碰目标一个条件的动作仍然放行")

act_replan = Action("Replan", {"hasAxe": False}, {"hasAxe": True}, 1)
p5 = Planner(world, [act_replan])
root5 = GoapNode(p5, State({"hasAxe": True}))
ok([n.action.name for n in root5.expand()] == ["Replan"],
   "E7-2 宽松版冲突检查：前提与目标冲突但效果能修好 → 放行")

# ---------- 场景 5：A* 里的 break（不是 continue） ----------
def build(actions_list):
    pl = Planner({}, actions_list, {"max_iterations": 1000, "max_nodes_to_expand": 10000,
                                    "planning_early_exit": False, "using_dynamic_actions": False,
                                    "use_weighted_random": False, "weighted_random_priority_power": 1.0,
                                    "weighted_random_min_weight": 0.001})
    return pl


a1 = Action("A1", {}, {"z": 1}, 1)      # 状态 S1，最便宜
a2 = Action("A2", {}, {"z": 1}, 9)      # 状态 S1，最贵
a3 = Action("A3", {}, {"z": 1, "w": 1}, 2)   # 状态 S2

pl_a = build([a3, a2, a1])   # 倒序展开：A1, A2, A3
pl_a.plan([Goal("z", {"z": 1})])
ok(pl_a.astar.enqueued_count == 1, "E8-1 便宜的重复状态触发 break，A3 连入队机会都没有")

pl_b = build([a3, a1, a2])   # 倒序展开：A2, A1, A3
pl_b.plan([Goal("z", {"z": 1})])
ok(pl_b.astar.enqueued_count == 3, "E8-2 先贵后便宜时走 Remove 分支，三个孩子都入队")

# ---------- 场景 6：迭代上限 ----------
pl_c = build(actions)
pl_c.settings["max_iterations"] = 1
ok(pl_c.plan([Goal("haveWood", {"hasWood": True})]) is None, "E9 迭代上限耗尽 → A* 返回 null")

# ---------- 场景 7：目标选择 ----------
low = Goal("low", {"hasWood": True}, priority=1)
high = Goal("high", {"hasWood": True}, priority=5)
pl_d = build(actions)
ok(pl_d.select_next_goal([low, high]).name == "high", "E10 默认按优先级升序排序后取最后一个（最高优先级）")

pl_e = build(actions)
pl_e.settings["use_weighted_random"] = True
ok(pl_e.select_next_goal([low, high], rng=lambda: 0.0).name == "low",
   "E11-1 加权随机且 roll=0 → 命中升序第一个（最低优先级）")
zero = Goal("zero", {"hasWood": True}, priority=0.0)
# 权重：zero = clamp(0^1 → 0.001)，high = 5，total = 5.001 ⇒ 选中 zero 需要 roll ≤ 0.001
ok(pl_e.select_next_goal([zero, high], rng=lambda: 0.0001).name == "zero",
   "E11-2 优先级 0 被 minWeight(0.001) 兜底，仍有约 0.02% 概率被选中")
ok(pl_e.select_next_goal([zero, high], rng=lambda: 0.0005).name == "high",
   "E11-3 同一个 minWeight 在 roll 稍大时就让位给高优先级（概率 0.001/5.001）")

# ---------- 场景 8：不可达目标的廉价预检 ----------
pl_f = build(actions)
ok(pl_f.plan([Goal("ore", {"hasOre": True})]) is None, "E12 预检发现没有动作能产出 hasOre → 直接放弃该目标")

print("PASS =", PASS)
