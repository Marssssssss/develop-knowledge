"""
dynamodb_design.py — DynamoDB 单表设计模型:LSI / GSI 语义、项目集合上限、分区限流

权威来源(全部实际读过):
  - AWS: Using Global Secondary Indexes in DynamoDB
    https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GSI.html
      * "Every global secondary index must have a partition key, and can have an
         optional sort key. The index key schema can be different from the base
         table schema."
      * "the key values in a global secondary index do not need to be unique."
      * "you don't have to specify the attributes for any global secondary index sort
         key ... In this case, DynamoDB does not write any data to the index for this
         particular item."
      * "The provisioned throughput settings of a global secondary index are separate
         from those of its base table."
      * "For a table write to succeed, the provisioned throughput settings for the
         table and all of its global secondary indexes must have enough write
         capacity ... Otherwise, the write to the table is throttled."
      * "Global secondary indexes support eventually consistent reads, each of which
         consume one half of a read capacity unit."
      * "global secondary index queries cannot fetch attributes from the base table."
      * "The maximum size of the results returned by a Query operation is 1 MB."
  - AWS: Using Local Secondary Indexes in DynamoDB
    https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/LSI.html
      * "For any local secondary index, you can store up to 10 GB of data per distinct
         partition key value. This figure includes all of the items in the base table,
         plus all of the items in the indexes, that have the same partition key value."
      * "You can query a local secondary index using either eventually consistent or
         strongly consistent reads."
      * 非投影属性回表:"you are charged for read capacity units for every base table
         item fetched. This charge is for reading each entire item from the table,
         not just the requested attributes."
      * "each item collection is stored in one partition. The total size of such an
         item collection is limited to the capability of that partition: 10 GB."
  - AWS Cheat Sheet:LSI 最多 5 个 / GSI 默认 20 个 / 投影属性合计最多 100 个

运行自检: python3 dynamodb_check.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from dynamodb_capacity import (
    MAX_ITEM_BYTES, PER_PARTITION_RCU, PER_PARTITION_WCU, read_units, validate_item_size,
    write_units,
)

MAX_LSI_PER_TABLE = 5
MAX_GSI_PER_TABLE = 20
MAX_PROJECTED_ATTRS = 100
LSI_ITEM_COLLECTION_LIMIT = 10 * 1024 * 1024 * 1024   # 官方:10 GB


class ValidationException(Exception):
    """参数/模型不合法(对应 DynamoDB ValidationException)。"""


class ProvisionedThroughputExceededException(Exception):
    """吞吐不足被限流。"""


class ItemCollectionSizeLimitExceededException(Exception):
    """项目集合超过 10 GB(仅存在于带 LSI 的表上)。"""


# ---------------------------------------------------------------------
# 单表设计:键构造
# ---------------------------------------------------------------------
def build_key(entity: str, **parts: str) -> Tuple[str, str]:
    """把实体与属性拼成分区键 / 排序键(单表设计的 # 分隔惯例)。

    例:build_key("Order", user="u1", ts="2026") → ("USER#u1", "ORDER#2026")
    """
    if entity == "User":
        return ("USER#%s" % parts["user"], "PROFILE")
    return ("USER#%s" % parts["user"], "%s#%s" % (entity.upper(), parts["ts"]))


@dataclass
class Item:
    pk: str
    sk: str
    attrs: Dict[str, str] = field(default_factory=dict)
    size_bytes: int = 0

    def key(self) -> Tuple[str, str]:
        return (self.pk, self.sk)

    def attr(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)


# ---------------------------------------------------------------------
# 索引
# ---------------------------------------------------------------------
@dataclass
class Index:
    name: str
    kind: str                      # "LSI" | "GSI"
    sk_name: str                   # 索引的排序键属性名(LSI 必须换排序键)
    projection: str = "KEYS_ONLY"  # KEYS_ONLY | INCLUDE | ALL
    included: List[str] = field(default_factory=list)
    throughput: Optional[Tuple[int, int]] = None   # GSI 自有 (RCU, WCU);LSI 为 None

    def entry_size(self, item: Item) -> int:
        """索引条目大小:ALL 与主表同大;KEYS_ONLY 仅键;INCLUDE 加投影属性。"""
        if self.projection == "ALL":
            return item.size_bytes
        keys = len(item.pk.encode("utf-8")) + len(item.sk.encode("utf-8")) + 32
        if self.projection == "KEYS_ONLY":
            return keys
        return keys + sum(len(item.attr(a, "").encode("utf-8")) for a in self.included)

    def indexed(self, item: Item) -> bool:
        """索引键属性缺失 → 不写索引条目。

        官方原文(GSI):"If you write an item to a table, you don't have to specify
        the attributes for any global secondary index sort key ... In this case,
        DynamoDB does not write any data to the index for this particular item."
        口径说明:本模型只用一个索引键属性(LSI 沿用主表分区键,GSI 的键在本模型里
        也简化为单个 sk_name),因此判定只看 sk_name 是否存在;投影类型(INCLUDE/ALL)
        影响的是条目大小,不影响"是否进索引"。
        """
        return bool(item.attr(self.sk_name))


def projected_attr_count(indexes: List[Index]) -> int:
    """官方:一个表所有二级索引的用户指定投影属性合计 ≤ 100;同名属性进两个索引计 2。"""
    total = 0
    for ix in indexes:
        if ix.projection == "ALL":
            continue
        total += len(ix.included)
    return total


# ---------------------------------------------------------------------
# 表
# ---------------------------------------------------------------------
@dataclass
class PutResult:
    table_units: int
    index_units: Dict[str, int] = field(default_factory=dict)
    index_entries: List[str] = field(default_factory=list)
    rewritten_base: bool = False


class Table:
    def __init__(self, name: str, pk_name: str = "PK", sk_name: str = "SK",
                 provisioned: Tuple[int, int] = (100, 100)):
        self.name = name
        self.pk_name = pk_name
        self.sk_name = sk_name
        self.provisioned = provisioned
        self.items: Dict[Tuple[str, str], Item] = {}
        self.lsis: List[Index] = []
        self.gsis: List[Index] = []
        self.partition_usage: Dict[str, Dict[str, int]] = {}
        # 官方值 10 GB。做成实例属性只是为了让自检能用小阈值跑同一段逻辑
        # (400 KB 单条上限意味着真造一个 10 GB 集合需要 2.6 万条,放在自检里不划算)。
        self.lsi_collection_limit = LSI_ITEM_COLLECTION_LIMIT

    # ---------------- 索引管理 ----------------
    def add_lsi(self, ix: Index, at_creation: bool = False) -> None:
        """官方:LSI 必须在建表时一起定义,建表后不能再加。"""
        if not at_creation:
            raise ValidationException("lsi_must_be_created_at_table_creation: %s" % ix.name)
        if len(self.lsis) >= MAX_LSI_PER_TABLE:
            raise ValidationException("too_many_lsi: max %d" % MAX_LSI_PER_TABLE)
        if ix.throughput is not None:
            raise ValidationException("lsi_shares_base_throughput: %s" % ix.name)
        self.lsis.append(ix)

    def add_gsi(self, ix: Index) -> None:
        if len(self.gsis) >= MAX_GSI_PER_TABLE:
            raise ValidationException("too_many_gsi: max %d" % MAX_GSI_PER_TABLE)
        if ix.throughput is None:
            raise ValidationException("gsi_needs_own_throughput: %s" % ix.name)
        self.gsis.append(ix)

    def all_indexes(self) -> List[Index]:
        return self.lsis + self.gsis

    # ---------------- 项目集合 ----------------
    def item_collection_bytes(self, pk: str) -> int:
        """官方:同一分区键值的项目集合 = 主表条目 + 各 LSI 条目。"""
        total = 0
        for (ipk, _isk), item in self.items.items():
            if ipk != pk:
                continue
            total += item.size_bytes
            for ix in self.lsis:
                if ix.indexed(item):
                    total += ix.entry_size(item)
        return total

    # ---------------- 写 ----------------
    def put(self, item: Item) -> PutResult:
        problem = validate_item_size(item.size_bytes) or (
            "ItemSizeTooLarge" if item.size_bytes > MAX_ITEM_BYTES else "")
        if problem:
            raise ValidationException(problem)
        if item.size_bytes <= 0:
            raise ValidationException("empty item")

        # LSI 项目集合上限:只在"增长型"写上触发;缩小集合的写仍允许(官方)
        if self.lsis:
            old = self.items.get(item.key())
            delta = item.size_bytes - (old.size_bytes if old else 0)
            if delta > 0 and self.item_collection_bytes(item.pk) + delta > self.lsi_collection_limit:
                raise ItemCollectionSizeLimitExceededException(
                    "item_collection_over_10gb: %s" % item.pk)

        self.items[item.key()] = item
        res = PutResult(table_units=write_units(item.size_bytes))
        for ix in self.lsis:                      # LSI 与主表共用吞吐
            if ix.indexed(item):
                res.index_units[ix.name] = 0
                res.index_entries.append(ix.name)
        for ix in self.gsis:
            if not ix.indexed(item):
                continue
            need = write_units(ix.entry_size(item))
            res.index_units[ix.name] = need
            res.index_entries.append(ix.name)
            rcu, wcu = ix.throughput
            if need > wcu:                        # 官方:GSI 容量不足 → 主表写被限流
                raise ProvisionedThroughputExceededException(
                    "gsi_write_capacity_exceeded: %s needs %d > %d" % (ix.name, need, wcu))
        return res

    # ---------------- 读 ----------------
    def query(self, pk: str, index: Optional[str] = None,
              strongly_consistent: bool = False,
              need_attrs: Optional[List[str]] = None) -> Dict[str, object]:
        ix = None
        for cand in self.all_indexes():
            if cand.name == index:
                ix = cand
        if index is not None and ix is None:
            raise ValidationException("no_such_index: %s" % index)
        if ix is not None and ix.kind == "GSI" and strongly_consistent:
            raise ValidationException("gsi_eventually_consistent_only: %s" % ix.name)

        rows = [it for (ipk, _s), it in self.items.items() if ipk == pk]
        # 官方:索引查询的读容量"based on the sizes of the index entries, rather than
        # the size of the item in the base table"(GSI 文档原文;LSI 同口径)
        if ix is not None:
            total = sum(ix.entry_size(it) for it in rows)
        else:
            total = sum(it.size_bytes for it in rows)
        units = read_units(total, "strong" if strongly_consistent else "eventual")

        fetched_extra = 0
        if ix is not None and need_attrs:
            projected = set(ix.included) | {"PK", "SK", ix.sk_name}
            missing = [a for a in need_attrs if a not in projected and ix.projection != "ALL"]
            if missing:
                if ix.kind == "GSI":
                    raise ValidationException("gsi_cannot_fetch_from_base_table: %s" % ix.name)
                # LSI 回表:每个条目按**整条**计算读单位(官方原文)
                fetched_extra = len(rows)
                units += sum(read_units(it.size_bytes) for it in rows)
        return {"items": len(rows), "units": units, "fetched_from_base": fetched_extra,
                "strongly_consistent": strongly_consistent,
                "index": ix.name if ix else "base_table"}


# ---------------------------------------------------------------------
# 分区限流(自适应容量抬不动单分区硬顶)
# ---------------------------------------------------------------------
class PartitionLedger:
    def __init__(self, per_partition_rcu: int = PER_PARTITION_RCU,
                 per_partition_wcu: int = PER_PARTITION_WCU):
        self.limits = {"read": per_partition_rcu, "write": per_partition_wcu}
        self.usage: Dict[str, Dict[str, int]] = {}

    def spend(self, pk: str, write: int = 0, read: int = 0) -> None:
        slot = self.usage.setdefault(pk, {"read": 0, "write": 0})
        slot["read"] += read
        slot["write"] += write

    def throttled(self) -> List[str]:
        return sorted(pk for pk, u in self.usage.items()
                      if u["read"] > self.limits["read"] or u["write"] > self.limits["write"])

    def busiest(self) -> Tuple[str, int]:
        if not self.usage:
            return ("", 0)
        pk = max(self.usage, key=lambda k: self.usage[k]["write"])
        return (pk, self.usage[pk]["write"])

    def reset(self) -> None:
        self.usage = {}
