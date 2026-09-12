"""
dynamo.py — Dynamo 风格 key-value 存储 — 数据结构与算法层

实现论文 *Dynamo: Amazon's Highly Available Key-value Store*
(SOSP 2007, DeCandia et al.) 的核心数据结构。

本文件不含 demo 演示;demo 见 dynamo_demos.py。
"""
from __future__ import annotations
import bisect, hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


# ---------- 哈希 ----------

def hash32(s: str) -> int:
    """取 SHA-1 前 4 字节,分布等价于 Dynamo 论文的 MD5 哈希。"""
    h = hashlib.sha1(s.encode()).digest()
    return int.from_bytes(h[:4], "big")


# ---------- 一致性哈希 + 虚节点 ----------

@dataclass
class VNode:
    pos: int          # ring 上的位置
    node_name: str    # 物理节点名


class ConsistentHashRing:
    """Dynamo 风格 ring:每物理节点分配多个 vnode,异构加权。"""

    def __init__(self, nodes: Dict[str, int]):
        self._sorted_positions: List[int] = []
        self._pos_to_node: Dict[int, str] = {}
        for name, n in nodes.items():
            for i in range(n):
                pos = hash32(f"{name}#vnode-{i}")
                self._sorted_positions.append(pos)
                self._pos_to_node[pos] = name
        self._sorted_positions.sort()

    def preference_list(self, key: str, N: int, alive: set) -> List[str]:
        """沿 ring 顺时针收集 N 个不同物理节点;不足时 sloppy quorum 补。"""
        pos = hash32(key)
        idx = bisect.bisect_right(self._sorted_positions, pos)
        if idx == len(self._sorted_positions):
            idx = 0
        seen = set()
        logical = []
        steps = 0
        while len(seen) < N and steps < len(self._sorted_positions) * 2:
            node = self._pos_to_node[self._sorted_positions[idx]]
            if node not in seen:
                logical.append(node); seen.add(node)
            idx = (idx + 1) % len(self._sorted_positions)
            steps += 1
        # sloppy:从 logical 中淘汰 down 的,从 ring 后续补
        result: List[str] = []
        for n in logical:
            if n in alive:
                result.append(n)
        if len(result) < N:
            j = idx
            while len(result) < N and j != idx - 1:
                n = self._pos_to_node[self._sorted_positions[j]]
                result.append(n)
                j = (j + 1) % len(self._sorted_positions)
            for _ in range(N * 4):
                if len(result) >= N:
                    break
                n = self._pos_to_node[self._sorted_positions[j]]
                result.append(n)
                j = (j + 1) % len(self._sorted_positions)
        return result[:N]


# ---------- 向量时钟 ----------

@dataclass
class VectorClock:
    """VC:dict[node, counter]。Dominates ⇒ 旧版可丢;sibling ⇒ 保留。"""
    entries: Dict[str, int] = field(default_factory=dict)

    def increment(self, node: str) -> "VectorClock":
        vc = VectorClock(dict(self.entries))
        vc.entries[node] = vc.entries.get(node, 0) + 1
        return vc

    def merge(self, other: "VectorClock") -> "VectorClock":
        merged = dict(self.entries)
        for n, c in other.entries.items():
            if merged.get(n, 0) < c:
                merged[n] = c
        return VectorClock(merged)

    def dominates(self, other: "VectorClock") -> bool:
        keys = set(self.entries) | set(other.entries)
        if any(self.entries.get(n, 0) < other.entries.get(n, 0) for n in keys):
            return False
        return any(self.entries.get(n, 0) > other.entries.get(n, 0) for n in keys)

    def is_ancestor_or_equal(self, other: "VectorClock") -> bool:
        keys = set(self.entries) | set(other.entries)
        return all(self.entries.get(n, 0) <= other.entries.get(n, 0) for n in keys)

    def __str__(self):
        return "{" + ",".join(f"{n}:{c}" for n, c in sorted(self.entries.items())) + "}"


# ---------- KV + hint 队列 ----------

@dataclass
class StoredValue:
    value: str
    vc: VectorClock
    tombstone: bool = False


class DynamoNode:
    """单个物理节点:kv 存储 + hint 队列。"""
    def __init__(self, name: str, ring: ConsistentHashRing):
        self.name = name
        self.alive = True
        self.store: Dict[str, List[StoredValue]] = defaultdict(list)
        self.hints: Dict[str, Tuple[str, List[StoredValue]]] = {}

    def _local_put(self, key: str, value: str, vc: VectorClock, tombstone: bool = False):
        existing = self.store[key]
        new_values = [sv for sv in existing if not vc.dominates(sv.vc)]
        new_values.append(StoredValue(value, vc, tombstone))
        self.store[key] = new_values

    def replay_hints(self) -> int:
        n = 0
        for key, (owner, values) in list(self.hints.items()):
            print(f"  [REPLAY] {self.name} 投递 key={key} 给 owner={owner}")
            for sv in values:
                self._local_put(key, sv.value, sv.vc, sv.tombstone)
            del self.hints[key]
            n += 1
        return n
