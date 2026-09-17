"""
cassandra_modeling.py — Cassandra 查询驱动建模:主键解析、分区规模、反规范化

权威来源(全部实际读过,取自 apache/cassandra 仓库 doc/modules/cassandra/pages
下的 asciidoc 源文件,与官网最新版同源):
  - developing/data-modeling/intro.adoc
    https://cassandra.apache.org/doc/latest/cassandra/developing/data-modeling/intro.html
    原文要点:
      * "In Cassandra, data modeling is query-driven. The data access patterns and
         application queries determine the structure and organization of data."
      * "A partition key is generated from the first field of a primary key."
      * "the first field or component of a primary key is hashed to generate the
         partition key and the remaining fields or components are the clustering keys
         that are used to sort data within a partition."
      * "Keeping the number of partitions read for a query to a minimum is also
         important because different partitions could be located on different nodes"
      * 分区规模指引:"%below 100,000" 个值、磁盘 "%below 100MB"
      * "LWT transactions (compare-and-set, conditional update) could affect
         performance and queries using LWT should be kept to the minimum."
      * "Materialized views (MVs) are experimental as of the 4.0 release."
  - managing/operating/compaction/overview.adoc(压缩的原理与类型)

运行自检: python3 cassandra_check.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# 官方分区规模指引(出自 intro.adoc 的 Data Model Analysis 小节)
MAX_PARTITION_VALUES = 100_000
MAX_PARTITION_BYTES = 100 * 1024 * 1024


# ---------------------------------------------------------------------
# 主键解析
# ---------------------------------------------------------------------
@dataclass
class Schema:
    name: str
    partition_key: List[str] = field(default_factory=list)
    clustering: List[str] = field(default_factory=list)
    clustering_order: Dict[str, str] = field(default_factory=dict)
    mvs: List[str] = field(default_factory=list)      # 官方:MV 仍是试验特性
    lwt_ops: int = 0                                  # 条件更新/比较并设置的使用次数

    def primary_key_text(self) -> str:
        pk = ",".join(self.partition_key)
        if len(self.partition_key) > 1:
            pk = "(%s)" % pk
        parts = [pk] + list(self.clustering)
        return "PRIMARY KEY (%s)" % ",".join(parts)

    def needs_filtering(self, where_eq: Sequence[str]) -> bool:
        """分区键未全部以等值给出 → 该查询无法定位到单个分区。

        CQL 层面对应的就是需要 ALLOW FILTERING 的查询(本模型的映射口径,官方
        intro 页只强调"把查询涉及的分区数压到最小",未出现 ALLOW FILTERING 字样)。
        """
        return not set(self.partition_key) <= set(where_eq)

    def partitions_touched(self, where_eq: Sequence[str], n_partition_keys: int = 1) -> int:
        """命中分区数以官方口径估算:分区键全部等值给出 → 1 个分区;否则全表扫描。"""
        if self.needs_filtering(where_eq):
            return max(1, n_partition_keys)
        return 1

    def single_partition(self, where_eq: Sequence[str]) -> bool:
        return not self.needs_filtering(where_eq)


def parse_primary_key(expr: str) -> Tuple[List[str], List[str]]:
    """解析 `PRIMARY KEY (...)` 的内容,返回(分区键字段, 聚簇键字段)。

    官方语义:第一个分量(可含括号把多个字段合成一个分量)经哈希生成分区键,
    其余分量是聚簇键,用于分区内排序。

    >>> parse_primary_key("id")
    (['id'], [])
    >>> parse_primary_key("id,c")
    (['id'], ['c'])
    >>> parse_primary_key("(id1,id2),c1,c2")
    (['id1', 'id2'], ['c1', 'c2'])
    """
    text = expr.strip()
    pk: List[str] = []
    rest = text
    if text.startswith("("):
        depth = 0
        for i, ch in enumerate(text):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    pk = [f.strip() for f in text[1:i].split(",") if f.strip()]
                    rest = text[i + 1:].lstrip(",")
                    break
    else:
        first, _, tail = text.partition(",")
        pk = [first.strip()]
        rest = tail
    clustering = [f.strip() for f in rest.split(",") if f.strip()]
    return (pk, clustering)


def make_schema(name: str, primary_key_expr: str,
                clustering_order: Optional[Dict[str, str]] = None) -> Schema:
    pk, ck = parse_primary_key(primary_key_expr)
    return Schema(name=name, partition_key=pk, clustering=ck,
                  clustering_order=clustering_order or {})


# ---------------------------------------------------------------------
# 分区规模检查
# ---------------------------------------------------------------------
@dataclass
class PartitionStats:
    values: int = 0
    disk_bytes: int = 0

    def warnings(self) -> List[str]:
        """官方两把尺子:分区内值个数 与 分区磁盘大小。"""
        out = []
        if self.values > MAX_PARTITION_VALUES:
            out.append("partition_values_over_100k: %d" % self.values)
        if self.disk_bytes > MAX_PARTITION_BYTES:
            out.append("partition_disk_over_100mb: %d" % self.disk_bytes)
        return out


# ---------------------------------------------------------------------
# 查询驱动建模
# ---------------------------------------------------------------------
@dataclass
class AccessPattern:
    name: str
    entity: str
    where_eq: List[str] = field(default_factory=list)
    where_range: List[str] = field(default_factory=list)
    order_by: List[str] = field(default_factory=list)

    def sortable(self, schema: Schema) -> bool:
        """排序只能在聚簇键前缀上做(聚簇键决定分区内顺序)。"""
        if not self.order_by:
            return True
        prefix = schema.clustering[:len(self.order_by)]
        return prefix == self.order_by


def design_tables(entity: str, patterns: Sequence[AccessPattern]) -> List[Schema]:
    """查询驱动建模:每个查询(或同一批查询)对应一张按它的 WHERE 设计主键的表。

    官方原文:"Data is modeled around specific queries ... a single entity may be
    included in multiple tables." —— 于是同一实体会被复制进多张表(反规范化)。
    """
    tables: List[Schema] = []
    for p in patterns:
        pk_fields = p.where_eq[:1]           # 第一个等值字段做分区键
        clustering = list(p.where_eq[1:]) + list(p.order_by)
        expr = pk_fields[0] if pk_fields else "id"
        if clustering:
            expr = "%s,%s" % (expr, ",".join(clustering))
        schema = make_schema("%s_by_%s" % (entity.lower(), "_".join(p.where_eq)), expr)
        tables.append(schema)
    return tables


def denormalization_cost(tables: Sequence[Schema], entity_bytes: int,
                         rows_per_entity: int = 1) -> Dict[str, int]:
    """反规范化的写放大:同一实体写进 N 张表 = N 份副本。"""
    return {"copies": len(tables), "bytes_written": entity_bytes * rows_per_entity * len(tables)}


def lwt_budget(schemas: Sequence[Schema]) -> str:
    """官方:条件更新(LWT/cas)影响性能,应压到最少。"""
    total = sum(s.lwt_ops for s in schemas)
    if total == 0:
        return "no_lwt"
    if total <= 2:
        return "acceptable: %d" % total
    return "minimize: %d lwt_ops across %d tables" % (total, len(schemas))


def mv_risk(schema: Schema) -> str:
    """官方:intro.adoc 仍写明 "Materialized views (MVs) are experimental as of the
    4.0 release." —— 用 MV 顶替反规范化表要自行评估风险。"""
    return "experimental" if schema.mvs else "none"
