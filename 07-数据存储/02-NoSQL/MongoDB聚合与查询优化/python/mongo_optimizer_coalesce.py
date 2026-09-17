"""
mongo_optimizer_coalesce.py — 管道合并优化 R5~R9(官方"coalescence"阶段)

权威来源:MongoDB manual: Aggregation Pipeline Optimization
  (https://www.mongodb.com/docs/manual/core/aggregation-pipeline-optimization/)
官方原文:"When possible, the optimization phase coalesces a pipeline stage into its
predecessor. Generally, coalescence occurs *after* any sequence reordering
optimization." —— 故本模块由 mongo_optimizer.optimize() 在重排之后调用。

运行自检: python3 mongo_pipeline_check.py
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from mongo_pipeline import COUNT_CHANGING_STAGES, Stage
# ---------------------------------------------------------------------
# 阶段二:合并(R5~R9)
# ---------------------------------------------------------------------


def r5_sort_limit(stages: List[Stage]) -> Tuple[List[Stage], int]:
    """R5:$sort + $limit → 把 $limit 合并进 $sort。

    官方原文:"When a $sort precedes a $limit, the optimizer can coalesce the $limit
    into the $sort if no intervening stages modify the number of documents (for
    example, $unwind or $group)." 且 "If a $skip stage sits between the $sort and
    the $limit stages, MongoDB coalesces the $limit into the $sort and increases the
    $limit value by the $skip amount." 中间夹 $project 等中性阶段不影响合并(官方
    示例即含 $project)。合并后 $sort 位置不变,输出形如
    `{$sort: {sortKey: {...}, limit: N}}`。
    """
    applied = 0
    i = 0
    while i < len(stages):
        if stages[i].name != "$sort":
            i += 1
            continue
        st = stages[i]
        if st.spec.get("limit") is not None:
            i += 1          # 已合并过,不再二次合并(避免跨中性阶段叠加语义歧义)
            continue
        skips = 0
        found = -1
        for j in range(i + 1, len(stages)):
            nxt = stages[j].name
            if nxt == "$limit":
                found = j
                break
            if nxt == "$skip":
                skips += int(stages[j].spec.get("skip", 0))
                continue
            if nxt in COUNT_CHANGING_STAGES or nxt in ("$out", "$merge", "$facet", "$unionWith"):
                break
        if found < 0:
            i += 1
            continue
        n = int(stages[found].spec.get("limit", 0))
        st.spec = {"sortKey": dict(st.spec), "limit": n + skips}
        stages = stages[:found] + stages[found + 1:]
        applied += 1
    return stages, applied


def r6_limit_limit(stages: List[Stage]) -> Tuple[List[Stage], int]:
    """R6:相邻两个 $limit → 取较小值。"""
    applied = 0
    i = 0
    while i + 1 < len(stages):
        if stages[i].name == "$limit" and stages[i + 1].name == "$limit":
            a = int(stages[i].spec["limit"])
            b = int(stages[i + 1].spec["limit"])
            stages = stages[:i] + [Stage("$limit", {"limit": min(a, b)})] + stages[i + 2:]
            applied += 1
            continue
        i += 1
    return stages, applied


def r7_skip_skip(stages: List[Stage]) -> Tuple[List[Stage], int]:
    """R7:相邻两个 $skip → 取和。"""
    applied = 0
    i = 0
    while i + 1 < len(stages):
        if stages[i].name == "$skip" and stages[i + 1].name == "$skip":
            a = int(stages[i].spec["skip"])
            b = int(stages[i + 1].spec["skip"])
            stages = stages[:i] + [Stage("$skip", {"skip": a + b})] + stages[i + 2:]
            applied += 1
            continue
        i += 1
    return stages, applied


def r8_match_match(stages: List[Stage]) -> Tuple[List[Stage], int]:
    """R8:相邻两个 $match → 用 $and 合并为一个。"""
    applied = 0
    i = 0
    while i + 1 < len(stages):
        if stages[i].name == "$match" and stages[i + 1].name == "$match":
            merged = {"$and": [stages[i].spec, stages[i + 1].spec]}
            stages = stages[:i] + [Stage("$match", merged)] + stages[i + 2:]
            applied += 1
            continue
        i += 1
    return stages, applied


def r9_lookup_unwind(stages: List[Stage]) -> Tuple[List[Stage], int]:
    """R9:$lookup + $unwind(可再跟一个 $match)→ 合并进 $lookup。

    官方原文:"When $unwind immediately follows $lookup, and the $unwind operates on
    the `as` field of the $lookup, the optimizer coalesces the $unwind into the
    $lookup stage." 且 "if $unwind is followed by a $match on any `as` subfield of
    the $lookup, the optimizer also coalesces the $match." 合并结果在 $lookup 上加
    `pipeline` 与 `unwinding` 两个内部字段(官方 explain 输出形态)。
    """
    applied = 0
    i = 0
    while i < len(stages):
        if i + 1 < len(stages) and stages[i].name == "$lookup" and stages[i + 1].name == "$unwind":
            lk = stages[i]
            path = stages[i + 1].spec.get("path", "")
            as_field = lk.spec.get("as")
            if isinstance(path, str) and path == "$" + str(as_field):
                preserve = bool(stages[i + 1].spec.get("preserveNullAndEmptyArrays", False))
                inner: List[Dict[str, Any]] = []
                consumed = 2
                if i + 2 < len(stages) and stages[i + 2].name == "$match":
                    sub: Dict[str, Any] = {}
                    for k, v in stages[i + 2].spec.items():
                        prefix = str(as_field) + "."
                        if k.startswith(prefix):
                            sub[k[len(prefix):]] = v if isinstance(v, dict) else {"$eq": v}
                    if sub:
                        inner.append({"$match": sub})
                        consumed = 3
                new_spec = dict(lk.spec)
                if inner:
                    new_spec["pipeline"] = inner
                new_spec["unwinding"] = {"preserveNullAndEmptyArrays": preserve}
                stages = (
                    stages[:i]
                    + [Stage("$lookup", new_spec, dict(lk.meta))]
                    + stages[i + consumed:]
                )
                applied += 1
                continue
        i += 1
    return stages, applied


def coalesce(stages: List[Stage], max_rounds: int = 40) -> Tuple[List[Stage], List[str]]:
    trace: List[str] = []
    for _ in range(max_rounds):
        changed = False
        for name, fn in (("R5", r5_sort_limit), ("R6", r6_limit_limit),
                         ("R7", r7_skip_skip), ("R8", r8_match_match),
                         ("R9", r9_lookup_unwind)):
            stages, n = fn(stages)
            if n:
                trace.append("%s x%d" % (name, n))
                changed = True
        if not changed:
            break
    return stages, trace


