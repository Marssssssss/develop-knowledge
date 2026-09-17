# -*- coding: utf-8 -*-
"""lsm_engine.py — LSM-Tree 存储引擎本体:写路径 / 读路径 / flush。

权威来源(官方仓库文档原文,出处同时标注在 lsm_core.py / lsm_compact.py / lsm_formats.py):
  - google/leveldb  doc/impl.md
      * log 文件约 4MB → 转成 sorted table;内存里保留一份 memtable 副本供读
      * young(level-0)文件超过阈值(当前 4 个)→ 与 L1 重叠文件一起合并
      * level-L 文件总大小超过 ``10^L`` MB 时与 L+1 重叠文件合并
      * 每个新 L1 文件 2MB;输出文件的 key range 覆盖超过 10 个 L+2 文件时另开新文件
      * 压缩会丢弃被覆盖的值;**只有当更高层没有任何文件覆盖该 key 时才丢弃删除标记**
  - facebook/rocksdb  wiki/Leveled-Compaction.md
      * L0 触发条件 ``level0_file_num_compaction_trigger``
      * 非 0 层得分 = 该层大小 / 目标大小;L0 得分 = max(文件数/触发数, 大小/max_bytes_for_level_base)
      * 动态层级 ``level_compaction_dynamic_level_bytes``:末层目标 = 末层实际大小,
        逐层除以 multiplier,低于 ``base/multiplier`` 的层保持空 → 保证 ~90% 数据在末层
      * 开动态层级时得分 = 层大小 / (层目标 + total_downcompact_bytes)
      * Intra-L0 压缩:把多个 L0 文件合成更大文件,牺牲 1 倍写放大换读放大
  - facebook/rocksdb  wiki/Write-Stalls.md —— 三类写停顿的触发条件

本文件只放 LSMEngine 的写/读/flush 部分:
  * 基础数据结构(Entry / MemTable / SSTable / 版本合并)→ lsm_core.py
  * 分层目标、挑层、压缩执行、放大系数     → lsm_compact.py(CompactionMixin)
  * WAL 日志格式与 SSTable 文件格式        → lsm_formats.py
"""

from typing import Optional, Tuple

from lsm_compact import CompactionMixin
from lsm_core import (
    DELETE, MB, PUT, Entry, MemTable, SSTable, WriteStopped, compact_entries,
)


class LSMEngine(CompactionMixin):
    """单机 LSM 引擎:一条写路径 + 一条后台 flush/compaction 路径。"""

    def __init__(
        self,
        write_buffer_size: int = 4 * MB,
        max_write_buffer_number: int = 2,
        num_levels: int = 7,
        level0_file_num_compaction_trigger: int = 4,
        target_file_size_base: int = 2 * MB,
        max_bytes_for_level_base: int = 10 * MB,
        max_bytes_for_level_multiplier: int = 10,
        level0_slowdown_writes_trigger: int = 20,
        level0_stop_writes_trigger: int = 36,
        soft_pending_compaction_bytes: int = 128 * MB,
        hard_pending_compaction_bytes: int = 256 * MB,
        dynamic_level_bytes: bool = False,
    ) -> None:
        self.write_buffer_size = write_buffer_size
        self.max_write_buffer_number = max_write_buffer_number
        self.num_levels = num_levels
        self.level0_file_num_compaction_trigger = level0_file_num_compaction_trigger
        self.target_file_size_base = target_file_size_base
        self.max_bytes_for_level_base = max_bytes_for_level_base
        self.max_bytes_for_level_multiplier = max_bytes_for_level_multiplier
        self.level0_slowdown_writes_trigger = level0_slowdown_writes_trigger
        self.level0_stop_writes_trigger = level0_stop_writes_trigger
        self.soft_pending_compaction_bytes = soft_pending_compaction_bytes
        self.hard_pending_compaction_bytes = hard_pending_compaction_bytes
        self.dynamic_level_bytes = dynamic_level_bytes

        self.mem = MemTable(write_buffer_size)
        self.immutables: List[MemTable] = []
        self.levels: Dict[int, List[SSTable]] = {l: [] for l in range(num_levels)}
        self.wal_batches: List[List[Entry]] = [[]]  # 当前活跃 WAL 的批次
        self.seq = 0
        self.next_file_number = 1
        self.pending_compaction_bytes = 0
        self.user_bytes_written = 0
        self.disk_bytes_written = 0

    # ------------------------------------------------------------ 写路径
    def put(self, key: str, value: str) -> None:
        self._write(Entry(key=key, seq=0, kind=PUT, value=value))

    def delete(self, key: str) -> None:
        """删除 = 插入一条删除标记(与 Cassandra 墓碑同构)。"""
        self._write(Entry(key=key, seq=0, kind=DELETE, value=""))

    def _write(self, entry: Entry) -> None:
        state, why = self.write_stall_state()
        if state == "stop":
            raise WriteStopped("write stopped because of " + why)
        self.seq += 1
        entry.seq = self.seq
        self.mem.add(entry)
        self.wal_batches[-1].append(entry)
        self.user_bytes_written += entry.size_bytes()
        if self.mem.is_full():
            self.rotate_memtable()
            self.background_work()

    def write_stall_state(self) -> Tuple[str, str]:
        """三层写停顿判定,顺序与官方 LOG 文案一致。返回 (ok|stall|stop, 原因)。"""
        n = len(self.immutables)
        if n >= self.max_write_buffer_number:
            return "stop", "too_many_immutable_memtables"
        # 官方:max_write_buffer_number > 3 时,提前一个开始 stall
        if self.max_write_buffer_number > 3 and n >= self.max_write_buffer_number - 1:
            return "stall", "too_many_immutable_memtables"
        l0 = len(self.levels[0])
        if l0 >= self.level0_stop_writes_trigger:
            return "stop", "too_many_level0_files"
        if l0 >= self.level0_slowdown_writes_trigger:
            return "stall", "too_many_level0_files"
        if self.pending_compaction_bytes >= self.hard_pending_compaction_bytes:
            return "stop", "pending_compaction_bytes"
        if self.pending_compaction_bytes >= self.soft_pending_compaction_bytes:
            return "stall", "pending_compaction_bytes"
        return "ok", ""

    def rotate_memtable(self) -> None:
        """当前 memtable 冻结为不可变,新开一个 memtable + 一个新 WAL 文件。"""
        self.immutables.append(self.mem)
        self.mem = MemTable(self.write_buffer_size)
        self.wal_batches.append([])

    # ------------------------------------------------------------ flush
    def flush_one(self) -> Optional[SSTable]:
        """把最老的不可变 memtable 落成 L0 文件。flush 时做一次行内 GC。"""
        if not self.immutables:
            return None
        table = self.immutables.pop(0)
        merged = compact_entries(table.drain(), can_drop_delete=lambda k: False)
        if not merged:
            return None
        sst = SSTable(self.next_file_number, 0, merged)
        self.next_file_number += 1
        self.levels[0].append(sst)
        self.disk_bytes_written += sst.size_bytes()
        # 该 memtable 对应的 WAL 已无用了
        if len(self.wal_batches) > 1:
            self.wal_batches.pop(0)
        return sst

    def background_work(self) -> None:
        """一轮后台工作:先尽量 flush,再挑得分最高的层做一次 compaction。"""
        while self.immutables:
            self.flush_one()
        for _ in range(8):
            if not self.maybe_compact():
                break

    # ------------------------------------------------------------ 读路径
    def get(self, key: str) -> Optional[str]:
        """memtable → 不可变 memtable(新→旧) → L0(新→旧) → L1..Ln。"""
        for mt in [self.mem] + list(reversed(self.immutables)):
            e = mt.get(key)
            if e is not None:
                return None if e.is_delete() else e.value
        for l in range(self.num_levels):
            tables = sorted(self.levels[l], key=lambda t: t.number, reverse=True)
            for t in tables:
                e = t.get(key)
                if e is not None:
                    return None if e.is_delete() else e.value
        return None
