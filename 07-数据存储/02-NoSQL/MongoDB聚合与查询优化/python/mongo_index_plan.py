"""
mongo_index_plan.py — 复合索引选择:ESR 规则 + 覆盖查询 + DISTINCT_SCAN

权威来源:
  - MongoDB manual: The ESR (Equality, Sort, Range) Guideline
    (https://www.mongodb.com/docs/manual/tutorial/equality-sort-range-guideline/)
    官方原文:
      * "Equality fields must come first." / "An index can have multiple equality
         keys. They can appear in any order relative to each other, but all equality
         keys must precede any sort or range fields."
      * "To avoid in-memory sorts, put sort fields before range in the index."
      * "An index supports sort operations on a subset of its keys only when the
         query includes equality conditions on all prefix keys that precede the
         sort keys."
      * "Inequality operators such as $ne or $nin are range operators, not equality
         operators." / "$regex is a range operator."
      * "$in ... fewer than 201 array elements ... similar to an equality predicate
         with ESR"; "201 elements or more ... similar to a range predicate with ESR."
  - MongoDB manual: Aggregation Pipeline Optimization(索引可被哪些阶段利用)
    * "$match: the server can use an index if $match is the first stage"
    * "$sort: ... if the stage is not preceded by a $project, $unwind, or $group"
    * "$group: ... use an index to quickly find the $first or $last document in each
       group" 且管道"sort and groups by the same field"
    * "look for IXSCAN or DISTINCT_SCAN plans" / "DISTINCT_SCAN index plan that
       returns one document per index key value"

运行自检: python3 mongo_pipeline_check.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

IN_EQUALITY_THRESHOLD = 201   # 官方:$in 数组元素 < 201 按等值,>= 201 按范围

EQUALITY_ONLY_OPS = {"$eq"}
RANGE_OPS = {"$gt", "$gte", "$lt", "$lte", "$ne", "$nin", "$regex", "$in"}


@dataclass
class Index:
    name: str
    fields: List[Tuple[str, int]]      # [(field, 1|-1)] 顺序即索引键序

    def field_names(self) -> List[str]:
        return [f for f, _ in self.fields]


@dataclass
class Plan:
    index: Optional[str]              # None = COLLSCAN
    bounds: List[str] = field(default_factory=list)
    range_field: Optional[str] = None
    sort_by_index: bool = False
    covered: bool = False
    stage: str = "COLLSCAN"
    keys_examined: float = 0.0
    note: str = ""

    def key(self) -> Tuple[int, int, float, str]:
        """排序键:先看是否免内存排序,再看是否覆盖,再看扫描键数,最后按名字。"""
        return (
            0 if self.sort_by_index else 1,
            0 if self.covered else 1,
            self.keys_examined,
            self.index or "",
        )


def classify(value: Any) -> str:
    """把一个查询谓词分类为 equality / range(官方 ESR 口径)。"""
    if not isinstance(value, dict):
        return "equality"                     # {f: v} 精确匹配
    ops = set(value.keys())
    if ops <= EQUALITY_ONLY_OPS:
        return "equality"                     # {f: {$eq: v}}
    if ops == {"$in"}:
        arr = value["$in"]
        if isinstance(arr, list) and len(arr) < IN_EQUALITY_THRESHOLD:
            return "equality"
        return "range"                        # 官方:>=201 个元素退化为范围语义
    if ops & RANGE_OPS:
        return "range"
    return "range"


def analyze(index: Index, query: Dict[str, Any], sort: Optional[Dict[str, Any]],
            needed: Sequence[str], total_docs: float,
            distinct: Dict[str, float]) -> Plan:
    """按 ESR 规则推断该索引能提供什么。"""
    names = index.field_names()
    kinds = {f: classify(query[f]) for f in names if f in query}

    # (1) 等值前缀:索引键序上"连续"且查询为其等值条件的字段。官方:"all equality
    #     keys must precede any sort or range fields"(等值键彼此之间顺序任意)。
    eq_fields: List[str] = []
    pos = 0
    while pos < len(names) and kinds.get(names[pos]) == "equality":
        eq_fields.append(names[pos])
        pos += 1
    rest = names[pos:]

    # (2) 排序:sort 字段须紧跟等值前缀、顺序与方向一致(方向全反则索引可反向遍历)。
    sort_fields: List[str] = list((sort or {}).keys())
    sort_by_index = False
    if not sort_fields:
        sort_by_index = True
    elif len(rest) >= len(sort_fields) and rest[:len(sort_fields)] == sort_fields:
        idx_dir = dict(index.fields)
        same = all(idx_dir[f] == sort[f] for f in sort_fields)
        rev = all(idx_dir[f] == -sort[f] for f in sort_fields)
        sort_by_index = same or rev

    # (3) 范围:等值前缀之后、且不承担排序职责的字段可作为范围界。官方 ESR 示例的
    #     最优索引 {directors:1, year:1, runtime:1} 正是"等值 → 排序 → 范围"排布。
    range_field = None
    for f in rest:
        if kinds.get(f) == "range" and f not in sort_fields:
            range_field = f
            break
    if range_field is not None and range_field in sort_fields:
        sort_by_index = False

    covered = set(needed) <= (set(names) | {"_id"})
    bounds = list(eq_fields) + ([range_field] if range_field else [])

    if bounds:
        sel = 1.0
        for f in eq_fields:
            sel *= 1.0 / max(distinct.get(f, 10.0), 1.0)
        if range_field:
            sel *= 0.33                    # 范围选择率自定(官方不给数值)
        keys = max(1.0, total_docs * sel)
        stage = "IXSCAN"
    else:
        keys = total_docs
        stage = "COLLSCAN"
    plan = Plan(index.name if bounds else None, bounds, range_field,
                sort_by_index, covered, stage, keys, "")
    if not bounds:
        plan.note = "无可用索引界 → 集合扫描"
    elif range_field is not None:
        plan.note = "范围字段 %s 之后的索引键不能再提供界" % range_field
    return plan


def choose(indexes: Sequence[Index], query: Dict[str, Any],
           sort: Optional[Dict[str, Any]] = None,
           needed: Sequence[str] = (), total_docs: float = 1000.0,
           distinct: Optional[Dict[str, float]] = None) -> Plan:
    """在所有候选索引里选一个:免内存排序 > 覆盖查询 > 扫描键数 > 名字。"""
    d = distinct or {}
    plans = [analyze(ix, query, sort, needed, total_docs, d) for ix in indexes]
    usable = [p for p in plans if p.index]
    if not usable:
        return Plan(None, [], None, False, set(needed) <= {"_id"}, "COLLSCAN",
                    total_docs, "全部候选索引都无可用界")
    return sorted(usable, key=lambda p: p.key())[0]


# ---------------------------------------------------------------------
# 阶段与索引的关系(官方 Aggregation Pipeline Optimization 的"利用索引"小节)
# ---------------------------------------------------------------------
IN_MEMORY_SORT_BLOCKERS = {"$project", "$unwind", "$group"}


def sort_usable_by_index(pipeline_names: Sequence[str]) -> bool:
    """官方:"the server can use an index if the stage is not preceded by a
    $project, $unwind, or $group stage" —— 只看 $sort 之前是否出现过这三者。"""
    for n in pipeline_names:
        if n == "$sort":
            return True
        if n in IN_MEMORY_SORT_BLOCKERS:
            return False
    return False


def match_uses_index(pipeline_names: Sequence[str]) -> bool:
    """官方:"the server can use an index if $match is the first stage in the
    pipeline, after any optimizations from the query planner."""
    for n in pipeline_names:
        return n == "$match"
    return False


def distinct_scan_ok(group_spec: Optional[Dict[str, Any]], sort_spec: Optional[Dict[str, Any]],
                     indexes: Sequence[Index]) -> Optional[str]:
    """官方:$group 可借索引快速取每组 $first/$last,条件是"管道按同一字段排序并分组"
    且"只用 $first 或 $last 累加器"。此时计划器可能用 DISTINCT_SCAN(每个索引键值
    只返回一个文档),官方称其"executes faster than IXSCAN if there are multiple
    documents per key value"。
    """
    if not group_spec or not sort_spec:
        return None
    gid = group_spec.get("_id")
    if not isinstance(gid, str) or not gid.startswith("$"):
        return None
    gid = gid[1:]
    accs = [k for k in group_spec if k != "_id"]
    if not accs:
        return None
    for a in accs:
        spec = group_spec[a]
        op = next(iter(spec)) if isinstance(spec, dict) and spec else None
        if op not in ("$first", "$last"):
            return None
    if list(sort_spec.keys()) != [gid]:
        return None
    for ix in indexes:
        if ix.field_names()[:1] == [gid]:
            return ix.name
    return None
