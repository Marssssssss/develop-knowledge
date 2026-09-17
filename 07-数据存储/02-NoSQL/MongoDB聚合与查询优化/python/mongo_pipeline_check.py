"""
mongo_pipeline_check.py — 自检:优化器重排/合并规则 + ESR 索引选择 + 官方硬限制

所有断言逐条对应官方文档给出的示例或明文规则(见 mongo_optimizer.py 与
mongo_index_plan.py 文件头的原文引用)。

运行: python3 mongo_pipeline_check.py
"""
from __future__ import annotations

import sys

from mongo_pipeline import (
    BLOCKING_STAGES, MAX_RESULT_DOC_BYTES, MAX_STAGES, Stage,
    blocking_stages, needs_disk_use, pipeline_dump, result_doc_ok, validate,
)
from mongo_optimizer import optimize
from mongo_index_plan import (
    Index, IN_EQUALITY_THRESHOLD, analyze, choose, classify, distinct_scan_ok,
    match_uses_index, sort_usable_by_index,
)

PASS = 0
FAIL: list = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL.append("%s %s" % (label, detail))
        print("  FAIL  %s   %s" % (label, detail))


def dump(*args) -> list:
    return pipeline_dump(list(args))


def stage(name, spec):
    return Stage(name, spec)


# =====================================================================
# 1. R1 投影下推 —— 官方示例逐字节还原
# =====================================================================
print("\n[1] R1 投影下推(官方示例)")
src = [
    stage("$addFields", {"maxTime": {"$max": "$times"}, "minTime": {"$min": "$times"}}),
    stage("$project", {"_id": 1, "name": 1, "times": 1, "maxTime": 1, "minTime": 1,
                       "avgTime": {"$avg": ["$maxTime", "$minTime"]}}),
    stage("$match", {"name": "Joe Schmoe", "maxTime": {"$lt": 20},
                     "minTime": {"$gt": 5}, "avgTime": {"$gt": 7}}),
]
out, trace = optimize(src)
want = dump(
    stage("$match", {"name": "Joe Schmoe"}),
    stage("$addFields", {"maxTime": {"$max": "$times"}, "minTime": {"$min": "$times"}}),
    stage("$match", {"maxTime": {"$lt": 20}, "minTime": {"$gt": 5}}),
    stage("$project", {"_id": 1, "name": 1, "times": 1, "maxTime": 1, "minTime": 1,
                       "avgTime": {"$avg": ["$maxTime", "$minTime"]}}),
    stage("$match", {"avgTime": {"$gt": 7}}),
)
check("R1 官方示例优化后管道完全一致", dump(*out) == want,
      "\n    got=%s" % dump(*out))
check("R1 官方示例:依赖投影计算值的 avgTime 过滤器留在原地",
      out[4].spec == {"avgTime": {"$gt": 7}})
check("R1 优化后首阶段为 $match → 可用索引", match_uses_index([s.name for s in out]))
check("R1 规则被记录在 appliedRules", any(t.startswith("R1") for t in trace), str(trace))

print("\n[2] R1 边界:跨 $unset 的过滤器不得前移(保守口径)")
src2 = [stage("$unset", ["x"]), stage("$match", {"x": 5})]
out2, _ = optimize(src2)
check("R1 不跨越 $unset 删除的字段", dump(*out2) == dump(*src2), str(dump(*out2)))

src3 = [stage("$addFields", {"y": {"$add": [1, 2]}}), stage("$match", {"y": 5})]
out3, _ = optimize(src3)
check("R1 不跨越计算字段 y", dump(*out3) == dump(*src3), str(dump(*out3)))

# =====================================================================
# 2. R2 / R4 序列重排
# =====================================================================
print("\n[3] R2 $sort + $match")
out4, _ = optimize([stage("$sort", {"age": -1}), stage("$match", {"status": "A"})])
check("R2 $match 移到 $sort 之前",
      dump(*out4) == dump(stage("$match", {"status": "A"}), stage("$sort", {"age": -1})),
      str(dump(*out4)))

print("\n[4] R4 $project/$unset + $skip")
out5, _ = optimize([stage("$sort", {"age": -1}),
                    stage("$project", {"status": 1, "name": 1}), stage("$skip", 5)])
check("R4 $skip 移到 $project 之前",
      dump(*out5) == dump(stage("$sort", {"age": -1}), stage("$skip", 5),
                          stage("$project", {"status": 1, "name": 1})),
      str(dump(*out5)))

# =====================================================================
# 3. R3 $redact + $match(官方示例)
# =====================================================================
print("\n[5] R3 $redact + $match(官方示例)")
red = stage("$redact", {"$cond": {"if": {"$eq": ["$level", 5]}, "then": "$$PRUNE",
                                  "else": "$$DESCEND"}})
m = stage("$match", {"year": 2014, "category": {"$ne": "Z"}})
out6, trace6 = optimize([red, m])
check("R3 官方示例:等值 year 前移、$ne 留在原地",
      dump(*out6) == dump(stage("$match", {"year": 2014}), red, m), str(dump(*out6)))
check("R3 不重复触发(第二次优化无变化)",
      dump(*optimize(out6)[0]) == dump(*out6), str(dump(*optimize(out6)[0])))

# =====================================================================
# 4. R5~R8 合并
# =====================================================================
print("\n[6] R5 $sort + $limit 合并")
out7, _ = optimize([stage("$sort", {"age": -1}),
                    stage("$project", {"age": 1, "status": 1, "name": 1}),
                    stage("$limit", 5)])
check("R5 中间夹 $project 仍合并,limit 并入 $sort",
      dump(*out7) == dump(stage("$sort", {"sortKey": {"age": -1}, "limit": 5}),
                          stage("$project", {"age": 1, "status": 1, "name": 1})),
      str(dump(*out7)))

out8, _ = optimize([stage("$sort", {"age": -1}), stage("$skip", 10), stage("$limit", 5)])
check("R5 $skip 夹在中间:limit 增加 skip 量 5+10=15",
      dump(*out8) == dump(stage("$sort", {"sortKey": {"age": -1}, "limit": 15}),
                          stage("$skip", 10)),
      str(dump(*out8)))

out9, _ = optimize([stage("$sort", {"age": -1}), stage("$group", {"_id": "$x"}),
                    stage("$limit", 5)])
check("R5 中间有 $group(改变文档数)则不合并", dump(*out9) == dump(
    stage("$sort", {"age": -1}), stage("$group", {"_id": "$x"}), stage("$limit", 5)),
    str(dump(*out9)))

print("\n[7] R6/R7/R8 相邻合并")
out10, _ = optimize([stage("$limit", 100), stage("$limit", 10)])
check("R6 相邻 $limit 取较小值 10", dump(*out10) == dump(stage("$limit", 10)),
      str(dump(*out10)))
out11, _ = optimize([stage("$skip", 5), stage("$skip", 2)])
check("R7 相邻 $skip 取和 7", dump(*out11) == dump(stage("$skip", 7)), str(dump(*out11)))
out12, _ = optimize([stage("$match", {"year": 2014}), stage("$match", {"status": "A"})])
check("R8 相邻 $match 用 $and 合并",
      dump(*out12) == dump(stage("$match", {"$and": [{"year": 2014}, {"status": "A"}]})),
      str(dump(*out12)))

# =====================================================================
# 5. R9 $lookup + $unwind (+$match)
# =====================================================================
print("\n[8] R9 $lookup + $unwind + $match(官方示例)")
lk = stage("$lookup", {"from": "otherCollection", "as": "resultingArray",
                       "localField": "x", "foreignField": "y"})
out13, _ = optimize([lk, stage("$unwind", "$resultingArray"),
                     stage("$match", {"resultingArray.foo": "bar"})])
merged = out13[0].spec
check("R9 $lookup 内嵌 pipeline 含 foo 的 $eq 条件",
      merged.get("pipeline") == [{"$match": {"foo": {"$eq": "bar"}}}], str(merged))
check("R9 unwinding.preserveNullAndEmptyArrays 默认 false",
      merged.get("unwinding") == {"preserveNullAndEmptyArrays": False}, str(merged))
check("R9 合并后管道只剩一个 $lookup", len(out13) == 1, str(dump(*out13)))

out14, _ = optimize([lk, stage("$unwind", "$other")])
check("R9 path 与 as 不一致时不合并", len(out14) == 2, str(dump(*out14)))

out15, _ = optimize([lk, stage("$unwind", {"path": "$resultingArray",
                                           "preserveNullAndEmptyArrays": True})])
check("R9 preserveNullAndEmptyArrays 被如实带出",
      out15[0].spec.get("unwinding") == {"preserveNullAndEmptyArrays": True},
      str(out15[0].spec))

# =====================================================================
# 6. 优化幂等 + 结构校验 + 内存限制
# =====================================================================
print("\n[9] 幂等 / 结构校验 / 硬限制")
check("优化是幂等的", dump(*optimize(out)[0]) == dump(*out))
check("$out 非末位 → 报错",
      "not_last_stage: $out at index 0" in validate([stage("$out", "t"), stage("$match", {})]))
check("$merge 放末位 → 通过",
      validate([stage("$match", {}), stage("$merge", {"into": "t"})]) == [])
check("$geoNear 非首位 → 报错",
      any("not_first_stage" in p for p in validate([stage("$match", {}), stage("$geoNear", {})])))
check("阶段数 > 1000 → 报错",
      any("too_many_stages" in p for p in validate([stage("$match", {})] * (MAX_STAGES + 1))))
check("阶段数 = 1000 → 通过",
      validate([stage("$match", {})] * MAX_STAGES) == [])
check("阻塞型阶段集合与官方列举一致",
      BLOCKING_STAGES == {"$bucket", "$bucketAuto", "$group", "$setWindowFields",
                          "$sort", "$sortByCount"}, str(BLOCKING_STAGES))
check("$group/$sort 被识别为阻塞型阶段",
      blocking_stages([stage("$match", {}), stage("$group", {}), stage("$sort", {})]) == [1, 2])
check("无阻塞阶段 → 不涉及落盘",
      needs_disk_use([stage("$match", {})], True) == "no_blocking_stage")
check("6.0 allowDiskUseByDefault=true → 超 100MB 默认落盘",
      needs_disk_use([stage("$group", {})], True) == "spill_to_disk")
check("6.0 allowDiskUseByDefault=false → 超 100MB 默认报错",
      needs_disk_use([stage("$group", {})], False) == "error_on_exceed")
check("返回值文档 16 MiB 内合法", result_doc_ok(MAX_RESULT_DOC_BYTES))
check("返回值文档超 16 MiB 非法", not result_doc_ok(MAX_RESULT_DOC_BYTES + 1))

# =====================================================================
# 7. 谓词分类:$in 的 201 分界
# =====================================================================
print("\n[10] ESR 谓词分类")
check("精确值 → equality", classify("David Lynch") == "equality")
check("$eq → equality", classify({"$eq": 1}) == "equality")
check("$in 200 个元素 → equality",
      classify({"$in": list(range(IN_EQUALITY_THRESHOLD - 1))}) == "equality")
check("$in 201 个元素 → range",
      classify({"$in": list(range(IN_EQUALITY_THRESHOLD))}) == "range")
for op in ("$gt", "$gte", "$lt", "$lte", "$ne", "$nin", "$regex"):
    check("%s → range" % op, classify({op: 1}) == "range")

# =====================================================================
# 8. ESR 索引选择(官方 movies 示例)
# =====================================================================
print("\n[11] ESR 索引选择(官方示例)")
IDX = [Index("directors_1_runtime_1_year_1", [("directors", 1), ("runtime", 1), ("year", 1)]),
       Index("directors_1_year_1_runtime_1", [("directors", 1), ("year", 1), ("runtime", 1)]),
       Index("year_1", [("year", 1)])]
Q = {"directors": "David Lynch", "runtime": {"$lt": 130}}
SORT = {"year": 1}
NEED = ["title", "year", "runtime"]
DIST = {"directors": 50.0, "year": 100.0, "runtime": 200.0}

chosen = choose(IDX, Q, SORT, NEED, total_docs=1000.0, distinct=DIST)
plan_by_name = {p.index: p for p in
                [analyze(i, Q, SORT, NEED, 1000.0, DIST) for i in IDX] if p.index}
check("官方示例选中 ESR 索引 directors_1_year_1_runtime_1",
      chosen.index == "directors_1_year_1_runtime_1", str(chosen))
check("ESR 索引的界为 [directors(等值), runtime(范围)]",
      chosen.bounds == ["directors", "runtime"], str(chosen.bounds))
check("ESR 索引免内存排序", chosen.sort_by_index)

ers = plan_by_name["directors_1_runtime_1_year_1"]
check("ERS 排布(range 在 sort 前)需要内存排序", not ers.sort_by_index)
check("ERS 排布仍能拿到 directors/runtime 两个界",
      ers.bounds == ["directors", "runtime"], str(ers.bounds))
check("year_1 单键索引对 directors+runtime 谓词无可用界 → 不进入候选",
      "year_1" not in plan_by_name, str(sorted(plan_by_name)))
check("候选索引按(免排序 > 覆盖 > 扫描键数)排序取首个",
      sorted(plan_by_name.values(), key=lambda p: p.key())[0].index == chosen.index)

print("\n[12] 等值键彼此顺序任意 / 覆盖查询 / COLLSCAN")
ix_ba = Index("b_1_a_1", [("b", 1), ("a", 1)])
p_ba = choose([ix_ba], {"a": "x", "b": "y"}, None, ["a"], total_docs=100.0, distinct=DIST)
check("等值键可任意相对顺序:b,a 都成为等值前缀", p_ba.bounds == ["b", "a"], str(p_ba.bounds))

cov = choose([Index("a_1_b_1", [("a", 1), ("b", 1)])], {"a": 1}, {"b": 1},
             ["a", "b"], total_docs=100.0, distinct=DIST)
check("覆盖查询:所需字段全在索引中", cov.covered and cov.stage == "IXSCAN")
nocov = choose([Index("a_1_b_1", [("a", 1), ("b", 1)])], {"a": 1}, {"b": 1},
               ["a", "b", "c"], total_docs=100.0, distinct=DIST)
check("缺 c → 非覆盖查询", not nocov.covered)
coll = choose([Index("a_1", [("a", 1)])], {"z": 1}, None, ["z"], total_docs=100.0)
check("无可用界 → COLLSCAN", coll.stage == "COLLSCAN" and coll.index is None)

print("\n[13] $in 201 分界对排序的影响(官方原文)")
small = choose([Index("a_1_b_1", [("a", 1), ("b", 1)])],
               {"a": {"$in": list(range(200))}}, {"b": 1}, ["b"], 1000.0, DIST)
big = choose([Index("a_1_b_1", [("a", 1), ("b", 1)])],
             {"a": {"$in": list(range(201))}}, {"b": 1}, ["b"], 1000.0, DIST)
check("<200 个元素:$in 按等值 → 后续字段 b 仍可提供排序", small.sort_by_index)
check(">=201 个元素:$in 按范围 → 后续字段再不能提供排序", not big.sort_by_index)

print("\n[14] 索引可用性与阶段位置(官方明文)")
check("$sort 前有 $project → 不能用索引排序",
      not sort_usable_by_index(["$match", "$project", "$sort"]))
check("$sort 前有 $unwind/$group → 不能用索引排序",
      not sort_usable_by_index(["$unwind", "$sort"]) and
      not sort_usable_by_index(["$group", "$sort"]))
check("$sort 紧随 $match → 可用索引排序", sort_usable_by_index(["$match", "$sort"]))
check("$match 非首阶段 → 不能用索引", not match_uses_index(["$sort", "$match"]))
check("$match 为首阶段 → 可用索引", match_uses_index(["$match", "$sort"]))

print("\n[15] DISTINCT_SCAN(官方 $group 条件)")
G = {"_id": "$status", "first_x": {"$first": "$x"}}
IXS = [Index("status_1", [("status", 1)])]
check("group 与 sort 同字段 + 仅 $first → 可走 DISTINCT_SCAN",
      distinct_scan_ok(G, {"status": 1}, IXS) == "status_1")
check("sort 字段与 group 不同 → 不可",
      distinct_scan_ok(G, {"other": 1}, IXS) is None)
check("含 $sum 累加器 → 不可",
      distinct_scan_ok({"_id": "$status", "n": {"$sum": 1}}, {"status": 1}, IXS) is None)
check("没有以该字段开头的索引 → 不可",
      distinct_scan_ok(G, {"status": 1}, [Index("x_1", [("x", 1)])]) is None)

# =====================================================================
print("\n" + "=" * 68)
print("断言总数 %d,失败 %d" % (PASS + len(FAIL), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过")
