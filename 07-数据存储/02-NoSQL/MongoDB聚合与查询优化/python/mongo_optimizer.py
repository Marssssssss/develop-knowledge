"""
mongo_optimizer.py — 聚合管道优化器:序列重排(R1~R4)+ 合并(R5~R9)

权威来源:
  - MongoDB manual: Aggregation Pipeline Optimization
    (https://www.mongodb.com/docs/manual/core/aggregation-pipeline-optimization/)
    官方原文要点:
      * "The aggregation pipeline can determine if it requires only a subset of the
         fields ... reducing the amount of data passing through the pipeline."
      * "MongoDB moves any filters in the $match stage that do not require values
         computed in the projection stage to a new $match stage before the projection."
      * "When possible, the optimization phase coalesces a pipeline stage into its
         predecessor. Generally, coalescence occurs after any sequence reordering
         optimization."
      * "Optimizations are subject to change between releases."
  - 官方示例(投影下推、$sort+$limit 合并、$lookup+$unwind 合并)在下方以注释标注。

运行自检: python3 mongo_pipeline_check.py
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from mongo_pipeline import (
    Stage,
    field_effect,
    filter_fields,
    split_match_filters,
)
from mongo_optimizer_coalesce import coalesce

# ---------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------


def _merge_filters(filters: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把若干单键过滤器合并成一个 $match 文档。

    官方示例:`{maxTime: {$lt:20}, minTime: {$gt:5}}` 两个过滤器被合成一个文档,
    键序保持原顺序。若出现同名字段(顶层的 `$and` 被拆开后可能出现),退化为
    `$and: [...]`,避免后写覆盖先写而改变语义。
    """
    merged: Dict[str, Any] = {}
    seen = set()
    dup = False
    for f in filters:
        for k, v in f.items():
            if k in seen:
                dup = True
            seen.add(k)
            merged[k] = v
    if dup:
        return {"$and": list(filters)}
    return merged


def _dump_list(stages: List[Stage]) -> List[str]:
    return [s.dump() for s in stages]


# ---------------------------------------------------------------------
# 阶段一:序列重排(R1~R4)
# ---------------------------------------------------------------------


def r1_projection_pushdown(stages: List[Stage]) -> Tuple[List[Stage], int]:
    """R1:投影阶段($addFields/$project/$set/$unset)后接 $match。

    官方原文:"MongoDB moves any filters in the $match stage that do not require
    values computed in the projection stage to a new $match stage before the
    projection." 且"performs this optimization for each $match stage, moving each
    $match filter before all projection stages that the filter does not depend on."

    实现:先把 $match 拆成"每个键一个过滤器",再对每个过滤器向左穿过紧邻的连续投影
    阶段;只允许穿过"不影响该过滤器字段"的投影阶段,遇到第一个有影响的投影即停。
    """
    applied = 0
    i = 0
    while i < len(stages):
        if stages[i].name == "$match":
            run_start = i
            while run_start - 1 >= 0 and stages[run_start - 1].is_projection():
                run_start -= 1
            if run_start < i:
                effs = {idx: field_effect(stages[idx]) for idx in range(run_start, i)}
                groups: Dict[int, List[Dict[str, Any]]] = {}
                stay: List[Dict[str, Any]] = []
                for _label, fspec in split_match_filters(stages[i].spec):
                    fset = filter_fields(fspec)
                    pos = i
                    for idx in range(i - 1, run_start - 1, -1):
                        if any(effs[idx].affects(f) for f in fset):
                            break
                        pos = idx
                    if pos < i:
                        groups.setdefault(pos, []).append(fspec)
                    else:
                        stay.append(fspec)
                if groups:
                    new: List[Stage] = list(stages[:run_start])
                    for idx in range(run_start, i):
                        if idx in groups:
                            new.append(Stage("$match", _merge_filters(groups[idx])))
                        new.append(stages[idx])
                    if stay:
                        new.append(Stage("$match", _merge_filters(stay)))
                    new.extend(stages[i + 1:])
                    stages = new
                    applied += 1
                    i = 0
                    continue
        i += 1
    return stages, applied


def r2_sort_match(stages: List[Stage]) -> Tuple[List[Stage], int]:
    """R2:官方原文 "the $match moves before the $sort to minimize the number of
    objects to sort"。仅对紧邻的 $sort → $match 生效。"""
    applied = 0
    i = 0
    while i + 1 < len(stages):
        if stages[i].name == "$sort" and stages[i + 1].name == "$match":
            stages = stages[:i] + [stages[i + 1], stages[i]] + stages[i + 2:]
            applied += 1
            continue
        i += 1
    return stages, applied


def r4_projection_skip(stages: List[Stage]) -> Tuple[List[Stage], int]:
    """R4:官方原文 "When you have a sequence with $project or $unset followed by
    $skip, the $skip moves before $project." """
    applied = 0
    i = 0
    while i + 1 < len(stages):
        if stages[i].name in ("$project", "$unset") and stages[i + 1].name == "$skip":
            stages = stages[:i] + [stages[i + 1], stages[i]] + stages[i + 2:]
            applied += 1
            continue
        i += 1
    return stages, applied


def _redact_fields(spec: Any) -> set:
    """取 $redact 表达式里引用到的字段名(用于判断过滤器是否与它同源)。"""
    out: set = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and node.startswith("$"):
            # "$$PRUNE" / "$$DESCEND" 是系统变量,不是字段路径,须排除
            if not node.startswith("$$"):
                out.add(node[1:].split(".")[0])

    walk(spec)
    return out


def r3_redact_match(stages: List[Stage]) -> Tuple[List[Stage], int]:
    """R3:$redact 紧接 $match。官方原文只说 "can **sometimes** add a portion of the
    $match before the $redact",并未给出完整判定条件。

    口径说明(本项目自定):以官方示例的可观察行为反推 —— 示例里 `{year: 2014}`
    (等值)被前移,而 `{category: {$ne: "Z"}}`(否定/范围)留在原地。故本模型只前移
    "纯等值"且字段不与 $redact 表达式同源的过滤器,并把原 $match 原样保留(官方
    优化后输出确实是原 $match 未拆分)。等值判定:`{f: v}` 或 `{f: {$eq: v}}`。
    """
    applied = 0
    i = 0
    while i + 1 < len(stages):
        if (
            stages[i].name == "$redact"
            and stages[i + 1].name == "$match"
            and not stages[i].meta.get("r3_done")
        ):
            redact_src = _redact_fields(stages[i].spec)
            pushable: List[Dict[str, Any]] = []
            for _label, fspec in split_match_filters(stages[i + 1].spec):
                if len(fspec) != 1:
                    continue
                fname, fval = next(iter(fspec.items()))
                if fname in redact_src:
                    continue
                if isinstance(fval, dict) and set(fval.keys()) != {"$eq"}:
                    continue
                pushable.append(fspec)
            if pushable:
                stages[i].meta["r3_done"] = True
                stages = (
                    stages[:i]
                    + [Stage("$match", _merge_filters(pushable)), stages[i]]
                    + stages[i + 1:]
                )
                applied += 1
                i = 0
                continue
        i += 1
    return stages, applied


def reorder(stages: List[Stage], max_rounds: int = 40) -> Tuple[List[Stage], List[str]]:
    """重排阶段跑到不动点。返回 (管道, 每轮生效的规则序列)。"""
    trace: List[str] = []
    for _ in range(max_rounds):
        changed = False
        for name, fn in (("R1", r1_projection_pushdown), ("R2", r2_sort_match),
                         ("R3", r3_redact_match), ("R4", r4_projection_skip)):
            stages, n = fn(stages)
            if n:
                trace.append("%s x%d" % (name, n))
                changed = True
        if not changed:
            break
    return stages, trace


# ---------------------------------------------------------------------
# 入口:先重排再合并(官方:"coalescence occurs after any sequence reordering")
# ---------------------------------------------------------------------
def optimize(stages: List[Stage]) -> Tuple[List[Stage], List[str]]:
    stages, t1 = reorder(stages)
    stages, t2 = coalesce(stages)
    # 合并后可能又暴露出可重排的相邻对(官方未展开,这里跑到不动点更稳)
    for _ in range(5):
        stages, t3 = reorder(stages)
        stages, t4 = coalesce(stages)
        if not t3 and not t4:
            break
        t1 += t3
        t2 += t4
    return stages, t1 + t2


def explain(stages: List[Stage]) -> Dict[str, Any]:
    """模拟 `db.coll.aggregate(..., {explain: true})` 里优化后的管道形态。"""
    opt, trace = optimize(stages)
    return {"optimizedPipeline": _dump_list(opt), "appliedRules": trace}
