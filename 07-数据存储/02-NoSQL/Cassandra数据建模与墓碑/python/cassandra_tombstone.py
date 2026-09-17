"""
cassandra_tombstone.py — 墓碑生命周期、复活(zombie)场景与压缩策略模型

权威来源(非官方二手,取自 apache/cassandra trunk 的文档源文件):
  - managing/operating/compaction/tombstones.adoc
    https://cassandra.apache.org/doc/latest/cassandra/managing/operating/compaction/tombstones.html
    原文要点:
      * "Cassandra treats a deletion as an insertion, and inserts a time-stamped
         deletion marker called a tombstone."
      * 宽限期:"The grace period for a tombstone is set with the table property
         WITH gc_grace_seconds. Its default value is 864000 seconds (ten days)"。
      * "Prior to the grace period expiring, Cassandra will retain a tombstone
         through compaction events."
      * 复活:"If the rest of the cluster purges the tombstoned data before the
         offline node comes back online, Cassandra may mistakenly treat the data on
         the recovered node as live and repair may replicate it across the cluster
         again. This scenario, where deleted data reappears, is known as a zombie."
      * 清除条件(必须**同时**满足):墓碑老于 gc_grace_seconds;且"the SSTable
         containing the partition plus all SSTables containing data older than the
         tombstone containing X must be included in the same compaction"。
      * "Note that tombstones will not be removed until a compaction event even if
         gc_grace_seconds has elapsed."
      * only_purge_repaired_tombstones 选项;完全过期的 SSTable 可整块丢弃;
        sstableexpiredblockers 工具;TWCS 下的 unsafe_aggressive_sstable_expiration。
  - managing/operating/compaction/overview.adoc(SSTable 不可变 / 小压缩自动触发)
  - stcs.adoc / lcs.adoc / twcs.adoc(三种压缩策略的触发与代价)

运行自检: python3 cassandra_check.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# 官方默认宽限期:864000 秒 = 10 天
DEFAULT_GC_GRACE_SECONDS = 864_000

LEVEL_MULTIPLIER = 10          # LCS:每层是上一层的 10 倍
LCS_L0_STCS_TRIGGER = 32       # LCS:官方"more than 32 SSTables in L0"则先按 STCS 收一次
STCS_MIN_THRESHOLD = 4         # STCS:官方默认攒够 4 个大小相近的 SSTable 触发
STCS_BUCKET_LOW = 0.5          # 官方:分桶区间 [avg × bucket_low, avg × bucket_high]
STCS_BUCKET_HIGH = 1.5


# ---------------------------------------------------------------------
# 写入与读路径:时间戳决定谁赢
# ---------------------------------------------------------------------
@dataclass
class Cell:
    """一个带时间戳的单元格。tombstone=True 表示它是一条删除标记。"""

    ts: int
    value: Optional[str] = None
    tombstone: bool = False
    ttl: Optional[int] = None          # 秒
    deleted_at: Optional[int] = None   # tombstone 的本地删除时刻(用于比较宽限期)


class SSTable:
    """不可变文件。每条记录是 (partition_key, clustering, cell)。"""

    def __init__(self, name: str, rows: Optional[List[Tuple[str, str, Cell]]] = None):
        self.name = name
        self.rows: List[Tuple[str, str, Cell]] = list(rows or [])
        self.dropped = False

    def add(self, pk: str, ck: str, cell: Cell) -> None:
        self.rows.append((pk, ck, cell))

    def keys(self) -> set:
        """本 SSTable 覆盖的分区键集合。"""
        return {pk for pk, _ck, _c in self.rows}

    def has_data_older_than(self, pk: str, ts: int) -> bool:
        """本文件里是否存在该分区、比给定时间戳更旧的数据(含非墓碑单元格)。"""
        return any(p == pk and c.ts < ts for p, _ck, c in self.rows)

    def size_bytes(self) -> int:
        return sum(64 + len(c.value or "") for _p, _c, c in self.rows)

    def expired_only(self, now: int) -> bool:
        """是否只剩墓碑 / 过期 TTL 数据(官方意义上的 fully expired SSTable)。"""
        for _p, _ck, c in self.rows:
            if c.tombstone:
                continue
            if c.ttl is None:
                return False
            if c.ts + c.ttl > now:
                return False
        return True


# ---------------------------------------------------------------------
# 读:墓碑遮挡更早的时间戳
# ---------------------------------------------------------------------
def read(rows: List[Tuple[str, str, Cell]], pk: str, ck: str) -> Optional[str]:
    """返回该行可见的值;被墓碑遮挡或不存在时返回 None。

    官方原文:"Once an object is marked as a tombstone, queries will ignore all
    values that are time-stamped previous to the tombstone insertion."
    """
    winner: Optional[Cell] = None
    for p, c, cell in rows:
        if p != pk or c != ck:
            continue
        if winner is None or cell.ts > winner.ts:
            winner = cell
    if winner is None or winner.tombstone:
        return None
    return winner.value


def ttl_expired(cell: Cell, now: int) -> bool:
    """TTL 到期后被当作删除处理(官方 TTL 注记)。"""
    if cell.ttl is None:
        return False
    return now > cell.ts + cell.ttl


# ---------------------------------------------------------------------
# 墓碑能否清除
# ---------------------------------------------------------------------
def purgeable(tombstone: Cell, now: int, partition: str,
              compacting: List[SSTable], all_tables: List[SSTable],
              repaired: bool = True, gc_grace: int = DEFAULT_GC_GRACE_SECONDS,
              only_purge_repaired: bool = False) -> Tuple[bool, str]:
    """判断一条墓碑能否在本次压缩后被真正删除。

    官方三个条件必须同时满足:
      1. 墓碑年龄 > gc_grace_seconds;
      2. 含该分区、且数据比墓碑更旧的所有 SSTable **都在同一次压缩里**;
      3. 若开启 only_purge_repaired_tombstones,数据必须已修复过。
    """
    base = tombstone.deleted_at if tombstone.deleted_at is not None else tombstone.ts
    if now - base <= gc_grace:
        return (False, "within_grace_period")
    if only_purge_repaired and not repaired:
        return (False, "only_purge_repaired_tombstones")
    for t in all_tables:
        if t in compacting:
            continue
        if t.has_data_older_than(partition, tombstone.ts):
            return (False, "shadowing_sstable_outside_compaction: %s" % t.name)
    return (True, "purgeable")


def sstableexpiredblockers(tables: List[SSTable], now: int) -> Dict[str, List[str]]:
    """官方 sstableexpiredblockers 的语义:哪些完全过期的 SSTable 可丢、谁在挡着。"""
    droppable: List[str] = []
    blockers: Dict[str, List[str]] = {}
    for i, t in enumerate(tables):
        if not t.expired_only(now):
            continue
        blocking = []
        for j, other in enumerate(tables):
            if i == j:
                continue
            if other.keys() & t.keys() and not other.expired_only(now):
                blocking.append(other.name)
        if blocking:
            blockers[t.name] = blocking
        else:
            droppable.append(t.name)
    return {"droppable": droppable, "blocked": blockers}


# ---------------------------------------------------------------------
# 复活(zombie)场景
# ---------------------------------------------------------------------
def delete_with_tombstone(nodes: List[List[str]], node_ids: List[int],
                          value: str) -> Tuple[List[List[str]], int]:
    """在指定节点上写入墓碑。返回(节点状态, 成功写入墓碑的节点数)。"""
    tomb = "TOMBSTONE(%s)" % value
    applied = 0
    for nid in node_ids:
        if value in nodes[nid]:
            nodes[nid] = [v for v in nodes[nid] if v != value] + [tomb]
        else:
            nodes[nid] = nodes[nid] + [tomb]
        applied += 1
    return (nodes, applied)


def repair(nodes: List[List[str]], partners: Tuple[int, int]) -> List[List[str]]:
    """修复:按墓碑优先合并两个节点的行版本(有墓碑即保持删除)。"""
    a, b = partners
    merged: List[str] = []
    for v in nodes[a] + nodes[b]:
        if v not in merged:
            merged.append(v)
    # 墓碑胜过同值的活数据
    for v in list(merged):
        if "TOMBSTONE(%s)" % v in merged:
            merged.remove(v)
    nodes[a] = list(merged)
    nodes[b] = list(merged)
    return nodes


def purge_everywhere(nodes: List[List[str]], value: str, n_deleted: int) -> List[List[str]]:
    """在部分节点上把墓碑也一并清掉(gc_grace 到期 + 压缩)。"""
    for nid in range(n_deleted):
        nodes[nid] = [v for v in nodes[nid] if not v.startswith("TOMBSTONE(")]
    return nodes


# ---------------------------------------------------------------------
# 压缩策略
# ---------------------------------------------------------------------
def stcs_trigger(sizes: List[int], min_threshold: int = STCS_MIN_THRESHOLD) -> Optional[List[int]]:
    """STCS:攒够 min_threshold 个"大小相近"(落进同一桶)的 SSTable 就合并。

    官方分桶口径:"groups SSTables with a size within [average-size × bucket_low] and
    [average-size × bucket_high]"(bucket_low=0.5、bucket_high=1.5)。
    """
    if len(sizes) < min_threshold:
        return None
    avg = sum(sizes) / len(sizes)
    bucket = [i for i, s in enumerate(sizes) if avg * STCS_BUCKET_LOW <= s <= avg * STCS_BUCKET_HIGH]
    return bucket if len(bucket) >= min_threshold else None


def lcs_promote(level_sizes: List[int], base: int = 160 * 1024 * 1024) -> Dict[str, object]:
    """LCS:每层目标是上一层的 10 倍(官方 "Each level is by default 10x the size of
    the previous one"),超目标的层要往下压一级。"""
    targets = [base * (LEVEL_MULTIPLIER ** i) for i in range(len(level_sizes))]
    over = [i for i, s in enumerate(level_sizes) if s > targets[i]]
    return {"targets": targets, "over_target_levels": over}


def lcs_l0_failsafe(l0_count: int) -> str:
    """LCS 的 L0 兜底:官方 "An STCS compaction will be triggered in L0 if there are
    more than 32 SSTables in L0."。"""
    return "stcs_in_l0" if l0_count > LCS_L0_STCS_TRIGGER else "lcs"


def twcs_windows(timestamps: List[int], window_seconds: int) -> Dict[int, List[int]]:
    """TWCS:按时间窗口分桶;窗口内先在活跃期用 STCS 收到大 SSTable,窗口结束再合成
    单个 SSTable,此后不再压缩该窗口(官方原文)。"""
    buckets: Dict[int, List[int]] = {}
    for ts in timestamps:
        buckets.setdefault(ts // window_seconds, []).append(ts)
    return buckets


def twcs_out_of_order(timestamps: List[int], window_seconds: int) -> bool:
    """乱序写入会破坏 TWCS 的价值(官方 Operational Concerns 一节)。"""
    ordered = sorted(timestamps)
    return timestamps != ordered


def space_amplification_risk(sizes: List[int]) -> str:
    """STCS 大压缩需要同时容纳新旧两份最大 SSTable → 官方称之为 space amplification,
    并明确 "Major compactions are not recommended for STCS."。"""
    if not sizes:
        return "none"
    biggest = max(sizes)
    if biggest * 2 > sum(sizes):
        return "high: need %d bytes free for new+old biggest sstable" % (biggest * 2)
    return "moderate"
