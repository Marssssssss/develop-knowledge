# -*- coding: utf-8 -*-
"""声明式分区与分区裁剪 —— 演示入口。"""

from bounds import (RangePartition, ListPartition, HashPartition, prune,
                    detect_contradiction)
from execmodel import PrunePlan

MONTHS = [
    RangePartition("y2006m02", 20060201, 20060301),
    RangePartition("y2006m03", 20060301, 20060401),
    RangePartition("y2007m11", 20071101, 20071201),
    RangePartition("y2007m12", 20071201, 20080101),
    RangePartition("y2008m01", 20080101, 20080201),
]


def hr(t):
    print("\n== %s ==" % t)


def scenario_range():
    hr("1. RANGE: 含下界不含上界(文档原例)")
    print("  分区 [1,10) 与 [10,20):")
    for v in (1, 9, 10, 20):
        hit = [p.name for p in (RangePartition("p1_10", 1, 10), RangePartition("p10_20", 10, 20))
               if p.match_value(v)]
        print("    值 %-3d -> %s" % (v, hit or "无(会报错)"))
    print("  logdate >= '2008-01-01' 的裁剪:")
    surv, removed = PrunePlan(MONTHS).planner_prune([(">=", 20080101)])
    print("    存活:", surv, " 裁掉:", removed)


def scenario_hash_list():
    hr("2. HASH 与 LIST 的差异")
    HS = [HashPartition("h%d" % i, 4, i) for i in range(4)]
    print("  HASH(modulus=4): 每个值落且只落一个分区")
    for v in ("user-1", "user-2", "user-3"):
        print("    %-8s -> %s" % (v, [p.name for p in HS if p.match_value(v)]))
    print("    但 BETWEEN 区间无法裁剪:", prune(HS, "BETWEEN", (1, 100))[0])
    LS = [ListPartition("east", {"BJ", "SH"}), ListPartition("west", {"CD"}),
          ListPartition("other", set(), is_default=True)]
    print("  LIST: 等值能定位, 但 DEFAULT 分区永远排除不掉")
    print("    city='BJ' ->", prune(LS, "=", "BJ")[0])


def scenario_contradiction():
    hr("3. 矛盾条件 -> 全部裁掉")
    for clauses in ([(">", 15), ("<", 5)], [(">=", 20080101), ("<", 20080101)]):
        kept, st = detect_contradiction(MONTHS, clauses)
        print("  %s -> %s, 存活 %s" % (clauses, st, kept or "无"))


def scenario_phases():
    hr("4. 三个裁剪阶段在 EXPLAIN 里的样子")
    plan = PrunePlan(MONTHS)
    surv, removed, locked = plan.initial_prune([(">=", 20080101)])
    print(plan.render(surv, removed))
    print("  ↑ 初始化期裁剪不留痕迹, 只有 Subplans Removed 说明裁掉了 %d 个" % removed)
    loops, never = plan.exec_prune([20060210, 20071205, 20080110], lambda v: [("=", v)])
    print("  执行期逐次裁剪 loops:", loops)
    print("  never executed:", never or "无")


if __name__ == "__main__":
    print("声明式分区与分区裁剪(PostgreSQL 18 官方文档 5.12)")
    scenario_range()
    scenario_hash_list()
    scenario_contradiction()
    scenario_phases()
