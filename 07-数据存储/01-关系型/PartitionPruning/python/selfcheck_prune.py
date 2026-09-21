# -*- coding: utf-8 -*-
"""自检: 分区边界语义 / 三种裁剪阶段 / HASH-LIST-RANGE 差异。"""

from bounds import (RangePartition, ListPartition, HashPartition, prune,
                    combine_and, combine_or, detect_contradiction)
from execmodel import PrunePlan

PASS = [0]
FAIL = []


def ok(cond, label):
    if cond:
        PASS[0] += 1
    else:
        FAIL.append(label)


# --- 1. RANGE: 含下界不含上界(文档原句: 10 属于第二个分区) ----------------------
r1 = RangePartition("p1_10", 1, 10)
r2 = RangePartition("p10_20", 10, 20)
RS = [r1, r2]
ok(r1.match_value(1) and not r2.match_value(1), "1 属于 [1,10)")
ok(r1.match_value(9) and not r2.match_value(9), "9 属于 [1,10)")
ok((not r1.match_value(10)) and r2.match_value(10), "10 属于 [10,20) —— 文档原例")
ok(not r2.match_value(20), "20 不属于 [10,20)")
ok(r1.match_interval(10, 20) is False, "查询区间 [10,20) 与 [1,10) 无交集")
ok(r2.match_interval(10, 20) is True, "查询区间 [10,20) 命中 [10,20)")
ok(r1.match_interval(1, 20) and r2.match_interval(1, 20), "[1,20) 命中两个")
ok(not r1.match_interval(5, 8) is False, "[5,8) 命中 [1,10)(负控写法检查)")
ok(r1.match_interval(5, 8), "[5,8) 命中第一个")
ok(not r2.match_interval(5, 8), "[5,8) 不命中第二个")

# --- 2. LIST 与 DEFAULT ---------------------------------------------------------
l1 = ListPartition("east", {"BJ", "SH"})
l2 = ListPartition("west", {"CD"})
ld = ListPartition("other", set(), is_default=True)
LS = [l1, l2, ld]
ok(prune(LS, "=", "BJ")[0] == ["east", "other"], "BJ -> east + DEFAULT(DEFAULT 排除不掉)")
ok(prune(LS, "=", "CD")[0] == ["west", "other"], "CD -> west + DEFAULT")
ok(prune(LS, "=", "GZ")[0] == ["other"], "未列出的值只能落到 DEFAULT")
ok("other" in prune(LS, "=", "BJ")[0], "DEFAULT 分区无法被等值推理排除(关键!)")

# --- 3. HASH: modulus / remainder ------------------------------------------------
HS = [HashPartition("h%d" % i, 4, i) for i in range(4)]
for v in ("a", "b", "zzz", 1, 2, 3, 4, 5):
    hits = [p.name for p in HS if p.match_value(v)]
    ok(len(hits) == 1, "HASH 每个值恰好落一个分区 (v=%r -> %s)" % (v, hits))
ok(all(p.match_interval(1, 100) for p in HS), "HASH 无法按区间裁剪: 区间横跨所有余数")
ok(prune(HS, "BETWEEN", (1, 100))[0] == ["h0", "h1", "h2", "h3"], "BETWEEN 对 HASH 分区无效")

# --- 4. 算子裁剪 -------------------------------------------------------------------
ok(prune(RS, "=", 10)[0] == ["p10_20"], "= 10 -> 第二个")
ok(prune(RS, "<", 10)[0] == ["p1_10"], "< 10 -> 第一个")
ok(prune(RS, "<=", 10)[0] == ["p1_10", "p10_20"], "<= 10 跨两个(闭区间转半开)")
ok(prune(RS, ">=", 10)[0] == ["p10_20"], ">= 10 -> 第二个")
ok(prune(RS, ">", 10)[0] == ["p10_20"], "> 10 -> 第二个")
ok(prune(RS, "BETWEEN", (5, 15))[0] == ["p1_10", "p10_20"], "BETWEEN 5 AND 15 跨两个")
ok(prune(RS, "BETWEEN", (1, 10))[0] == ["p1_10", "p10_20"],
   "BETWEEN 1 AND 10 含 10, 而 10 属第二个分区 -> 两个都留")
ok(prune(RS, "~", 1)[1] == "UNSUPPORTED", "不支持的算子 -> UNSUPPORTED")

# --- 5. AND / OR / 矛盾 --------------------------------------------------------------
and_hits = combine_and([prune(RS, ">=", 10)[0], prune(RS, "<", 20)[0]])
ok(and_hits == ["p10_20"], "AND: x>=10 AND x<20 -> 只第二个")
or_hits = combine_or([prune(RS, "<", 5)[0], prune(RS, ">", 15)[0]])
ok(set(or_hits) == {"p1_10", "p10_20"}, "OR: x<5 OR x>15 -> 两个都在(OR 不裁剪)")
empty, status = detect_contradiction(RS, [(">", 15), ("<", 5)])
ok(empty == [] and status == "MATCH_CONTRADICT", "x>15 AND x<5 -> 矛盾, 全裁")
kept, status2 = detect_contradiction(RS, [(">", 5), ("<", 15)])
ok(status2 == "MATCH_STEPS" and len(kept) > 0, "x>5 AND x<15 -> 生成裁剪步骤")

# --- 6. IS NULL ---------------------------------------------------------------------
ok(prune(LS, "IS NULL", None)[0] == ["other"], "LIST: NULL 只能落 DEFAULT")
ok(prune(LS, "IS NULL", None)[1] == "MATCH_NULLNESS", "IS NULL 走 MATCH_NULLNESS 分支")
unbounded = [RangePartition("low", None, 10), RangePartition("high", 10, None)]
ok(prune(unbounded, "IS NULL", None)[0] == ["low"], "无下界的 range 分区可容纳 NULL")
ok("high" not in prune(unbounded, "IS NULL", None)[0], "有下界的 range 分区被排除")

# --- 7. 三阶段裁剪 ----------------------------------------------------------------------
plan = PrunePlan(RS)
surv, removed = plan.planner_prune([("=", 5)])
ok(surv == ["p1_10"] and removed == 1, "计划期: 字面量裁掉 1 个")
ok("Subplans Removed" in plan.render(surv, removed), "渲染含 Subplans Removed")
surv_all, removed_all = plan.planner_prune([])
ok(surv_all == ["p1_10", "p10_20"] and removed_all == 0, "无条件 -> 不裁剪(负控)")

init_surv, subplans_removed, locked = plan.initial_prune([("=", 15)])
ok(subplans_removed == 1, "初始化期: Subplans Removed: 1")
ok(locked == ["p1_10", "p10_20"], "但被裁掉的分区仍然会被加锁(文档原话)")

loops, never = plan.exec_prune([2, 5, 12, 18], lambda v: [("=", v)])
ok(loops["p1_10"] == 2 and loops["p10_20"] == 2, "执行期逐次: 四个外值 -> 2/2")
ok(never == [], "这次没有 never executed 的分区")
loops2, never2 = plan.exec_prune([2, 5, 7], lambda v: [("=", v)])
ok(loops2["p10_20"] == 0 and never2 == ["p10_20"], "外值全在第一个分区 -> 第二个 (never executed)")
ok(loops2["p1_10"] == 3, "第一个分区 loops=3")

# --- 8. 关掉 enable_partition_pruning -----------------------------------------------------
off = PrunePlan(RS, enable_pruning=False)
ok(off.planner_prune([("=", 5)])[0] == ["p1_10", "p10_20"], "关闭后不裁剪, 全扫")
ok(off.initial_prune([("=", 5)])[1] == 0, "关闭后 Subplans Removed = 0")
loops3, never3 = off.exec_prune([2, 5], lambda v: [("=", v)])
ok(loops3 == {"p1_10": 2, "p10_20": 2} and never3 == [], "关闭后每个分区都跑满 loops")

if __name__ == "__main__":
    print("PASS=%d FAIL=%d" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
