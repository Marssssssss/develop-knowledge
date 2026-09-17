# -*- coding: utf-8 -*-
"""lsm_core.py — LSM 引擎的基础数据结构:Entry / MemTable / SSTable / 版本合并。

拆文件只为满足单文件 ≤300 行的行数约束;权威来源与出处见 lsm_engine.py 文件头。
"""

from dataclasses import dataclass
from typing import List, Optional

KB = 1024
MB = 1024 * 1024

PUT = 0
DELETE = 1


class WriteStopped(Exception):
    """写被完全停止:等待 flush/compaction 追上。"""


@dataclass
class Entry:
    """带序号的一个版本。序号越大越新(与 LevelDB 的 sequence number 同义)。"""

    key: str
    seq: int
    kind: int = PUT
    value: str = ""

    def size_bytes(self) -> int:
        # 8 字节内部 key 前缀(seq<<8 | type) + 变长 key/value,取一个稳定的近似
        return 8 + len(self.key) + len(self.value)

    def is_delete(self) -> bool:
        return self.kind == DELETE


def _newest_first(entries: List[Entry]) -> List[Entry]:
    """按 (key 升序, seq 降序) 归一化 —— memtable / SSTable 的物理顺序。"""
    return sorted(entries, key=lambda e: (e.key, -e.seq))


def _visible(entries: List[Entry], key: str) -> Optional[Entry]:
    """取该 key 序号最大的版本(数组已按 seq 降序)。"""
    best: Optional[Entry] = None
    for e in entries:
        if e.key != key:
            continue
        if best is None or e.seq > best.seq:
            best = e
    return best


class MemTable:
    """内存中的有序写缓冲。满了就转成不可变 memtable 并触发 flush。"""

    def __init__(self, write_buffer_size: int = 4 * MB) -> None:
        self.write_buffer_size = write_buffer_size
        self.entries: List[Entry] = []

    def add(self, entry: Entry) -> None:
        self.entries.append(entry)

    def size_bytes(self) -> int:
        return sum(e.size_bytes() for e in self.entries)

    def is_full(self) -> bool:
        return self.size_bytes() >= self.write_buffer_size

    def get(self, key: str) -> Optional[Entry]:
        return _visible(self.entries, key)

    def drain(self) -> List[Entry]:
        out = _newest_first(self.entries)
        self.entries = []
        return out


class SSTable:
    """不可变有序文件。keys 有序、同一 key 的多个版本按 seq 降序排列。"""

    def __init__(self, number: int, level: int, entries: List[Entry]) -> None:
        self.number = number
        self.level = level
        self.entries = _newest_first(entries)

    def size_bytes(self) -> int:
        return sum(e.size_bytes() for e in self.entries)

    def get(self, key: str) -> Optional[Entry]:
        return _visible(self.entries, key)

    def smallest(self) -> str:
        return self.entries[0].key if self.entries else ""

    def largest(self) -> str:
        return self.entries[-1].key if self.entries else ""

    def overlaps(self, lo: str, hi: str) -> bool:
        """[lo, hi] 与本文件闭区间 key 范围是否相交。"""
        if not self.entries:
            return False
        return not (hi < self.smallest() or lo > self.largest())

def compact_entries(entries: List[Entry], can_drop_delete) -> List[Entry]:
    """合并多路版本流:(key, seq) 去重保留最新;删除标记按官方规则择机丢弃。

    LevelDB 原文:压缩会丢弃被覆盖的值;删除标记只有在**更高编号(更深)的层里
    没有任何文件覆盖该 key** 时才丢弃 —— 否则下层那些更旧的值会"复活"。
    """
    ordered = _newest_first(entries)
    out: List[Entry] = []
    prev_key: Optional[str] = None
    for e in ordered:
        if e.key == prev_key:
            continue  # 被更新的版本覆盖
        prev_key = e.key
        if e.is_delete() and can_drop_delete(e.key):
            continue
        out.append(e)
    return out
