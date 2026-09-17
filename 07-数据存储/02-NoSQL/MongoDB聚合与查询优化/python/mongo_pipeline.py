"""
mongo_pipeline.py — MongoDB 聚合管道阶段模型与结构约束

权威来源:
  - MongoDB manual: Aggregation Pipeline
    (https://www.mongodb.com/docs/manual/core/aggregation-pipeline/)
  - MongoDB manual: Aggregation Pipeline Limits
    (https://www.mongodb.com/docs/manual/core/aggregation-pipeline-limits/)
  - MongoDB manual: Aggregation Pipeline Optimization
    (https://www.mongodb.com/docs/manual/core/aggregation-pipeline-optimization/)

运行自检: python3 mongo_pipeline_check.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------
# 官方硬限制(单位:官方口径 1 MB = 1024 KB,1 MiB = 1024^2 B)
# ---------------------------------------------------------------------
MAX_STAGES = 1000                 # 单条管道最多 1000 个阶段(解析前后都查)
STAGE_MEMORY_LIMIT_MB = 100       # 阻塞型阶段超过 100 MB 需落盘或报错
MAX_RESULT_DOC_BYTES = 16 * 1024 * 1024   # 单文档 BSON 上限 16 MiB

# 阻塞型阶段:必须读完所有输入文档才能产出第一条输出 → 内存中留有全套中间结果
# 官方列举的必须落盘候选:$bucket / $bucketAuto / $group / $setWindowFields /
# $sort(未被索引满足时)/ $sortByCount
BLOCKING_STAGES: Set[str] = {
    "$bucket",
    "$bucketAuto",
    "$group",
    "$setWindowFields",
    "$sort",
    "$sortByCount",
}

# 改变文档数量的阶段:决定 $sort + $limit 能否合并(官方原文用 $unwind/$group 举例)
COUNT_CHANGING_STAGES: Set[str] = {"$unwind", "$group", "$bucket", "$bucketAuto"}

# 投影类阶段:官方"投影优化"针对的四个阶段
PROJECTION_STAGES: Set[str] = {"$addFields", "$project", "$set", "$unset"}

# 只能出现在管道末端的阶段(官方:同一阶段可重复出现,除了这三个)
LAST_ONLY_STAGES: Set[str] = {"$out", "$merge"}

# 只能出现在管道开头的阶段
FIRST_ONLY_STAGES: Set[str] = {"$geoNear"}


@dataclass
class Stage:
    """一个管道阶段。name 形如 '$match';spec 为该阶段的参数文档。"""

    name: str
    spec: Any = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict, compare=False)  # 优化器内部标记

    def __post_init__(self) -> None:
        """归一化官方简写形式:$skip: 5 / $limit: 5 / $unwind: "$path" / $out: "coll"。"""
        if self.name in ("$skip", "$limit") and isinstance(self.spec, int):
            self.spec = {self.name[1:]: self.spec}
        elif self.name == "$unwind":
            if isinstance(self.spec, str):
                self.spec = {"path": self.spec}
        elif self.name in ("$out", "$merge") and isinstance(self.spec, str):
            self.spec = {"into": self.spec}

    def dump(self) -> str:
        """规范化字符串表示,用于断言与 explain 对比。"""
        return "%s %s" % (self.name, _canon(self.spec))

    def is_projection(self) -> bool:
        return self.name in PROJECTION_STAGES


def _canon(obj: Any) -> str:
    """把 spec 渲染成稳定的 JSON 文本:键按插入序保留(语义敏感)。"""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def pipeline_dump(stages: List[Stage]) -> List[str]:
    return [s.dump() for s in stages]


# ---------------------------------------------------------------------
# 投影阶段对字段的影响:用于 R1 判断"过滤器是否依赖投影计算结果"
# ---------------------------------------------------------------------
@dataclass
class FieldEffect:
    """一个投影阶段对各字段的影响。"""

    computed: Set[str] = field(default_factory=set)   # 值由表达式算出 → 依赖该阶段
    removed: Set[str] = field(default_factory=set)    # 被排除/删除 → 依赖该阶段

    def affects(self, fieldname: str) -> bool:
        return fieldname in self.computed or fieldname in self.removed


def field_effect(stage: Stage) -> FieldEffect:
    """计算投影阶段对顶层字段的影响。

    口径说明(本项目自定,非官方):
      - $addFields / $set:spec 的每个键都是计算字段;
      - $project:值为 1/true/0/false 属于"选入/排除",其余(表达式、字段路径
        重命名)算计算字段;值为 0/false 的字段记为 removed;
      - $unset:官方文档把 $unset 也列入投影阶段,但它**删除**字段。若把
        `{$match: {x: 5}}` 前移到 `$unset: [x]` 之前,语义会从"匹配不到任何
        文档"变成"匹配 x==5 的文档",结果集改变。故本模型把所有被 $unset
        删掉的字段都视为"受影响",即不允许过滤器跨越 $unset。这是保守实现,
        官方文档未给出 $unset 的判定细节。
    """
    eff = FieldEffect()
    if stage.name in ("$addFields", "$set"):
        eff.computed.update(stage.spec.keys())
    elif stage.name == "$unset":
        keys = stage.spec if isinstance(stage.spec, list) else list(stage.spec)
        eff.removed.update(k for k in keys if isinstance(k, str))
    elif stage.name == "$project":
        for k, v in stage.spec.items():
            if isinstance(v, bool) or v in (0, 1):
                if v in (0, False):
                    eff.removed.add(k)
            else:
                eff.computed.add(k)
    return eff


def filter_fields(spec: Dict[str, Any]) -> Set[str]:
    """取一个过滤器文档里引用到的顶层字段名。

    入口先展开顶层 `$and`(官方优化器也会把 $match 拆成"每个键一个过滤器",
    $and 的情形同理可拆)。嵌套文档里的字段算作其顶层的子路径归属。
    """
    out: Set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$and" and isinstance(v, list):
                    for sub in v:
                        walk(sub)
                elif k.startswith("$"):
                    # 顶层裸逻辑算子($or/$nor/$expr...)一并取其子条件字段
                    if isinstance(v, list):
                        for sub in v:
                            walk(sub)
                    else:
                        walk(v)
                else:
                    out.add(k)

    if isinstance(spec, dict):
        walk(spec)
    return out


def split_match_filters(spec: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """按官方描述把 $match 拆成"每个键一个过滤器",并给出用于依赖判断的字段集。"""
    filters: List[Tuple[str, Dict[str, Any]]] = []
    for k, v in spec.items():
        if k == "$and" and isinstance(v, list):
            for i, sub in enumerate(v):
                filters.append(("%s[%d]" % (k, i), sub if isinstance(sub, dict) else {k: sub}))
        else:
            filters.append((k, {k: v}))
    return filters


# ---------------------------------------------------------------------
# 结构校验:官方硬限制
# ---------------------------------------------------------------------
def validate(stages: List[Stage]) -> List[str]:
    """返回违规项列表(空列表 = 通过)。"""
    problems: List[str] = []
    if len(stages) > MAX_STAGES:
        problems.append("too_many_stages: %d > %d" % (len(stages), MAX_STAGES))
    for i, st in enumerate(stages):
        if st.name in LAST_ONLY_STAGES and i != len(stages) - 1:
            problems.append("not_last_stage: %s at index %d" % (st.name, i))
        if st.name in FIRST_ONLY_STAGES and i != 0:
            problems.append("not_first_stage: %s at index %d" % (st.name, i))
    return problems


def blocking_stages(stages: List[Stage]) -> List[int]:
    """返回可能超过 100 MB 内存的阶段下标。

    $sort 只有在"未被索引满足"时才算阻塞型;本函数按保守口径把 $sort 一律计入,
    索引满足的判定在 mongo_index_plan.py 里做(`sort_by_index`)后剔除。
    """
    return [i for i, s in enumerate(stages) if s.name in BLOCKING_STAGES]


def needs_disk_use(stages: List[Stage], allow_disk_use_by_default: bool) -> str:
    """官方 allowDiskUseByDefault(6.0 起)语义:
      - true  → 超过 100 MB 的阻塞型阶段默认写临时文件(可用 {allowDiskUse:false} 关闭)
      - false → 默认直接报错(可用 {allowDiskUse:true} 打开)
    返回该管道的默认行为。
    """
    if not blocking_stages(stages):
        return "no_blocking_stage"
    return "spill_to_disk" if allow_disk_use_by_default else "error_on_exceed"


def result_doc_ok(doc_bytes: int) -> bool:
    """官方:返回文档受 16 MiB BSON 上限约束;管道处理过程中的中间文档可超过该值。"""
    return doc_bytes <= MAX_RESULT_DOC_BYTES
