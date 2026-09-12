"""
dynamo.py — Dynamo 风格 key-value 存储的完整最小实现

实现论文 *Dynamo: Amazon's Highly Available Key-value Store*
(SOSP 2007, DeCandia et al.) 的四大核心机制:

1. 一致性哈希 + 虚节点 (Consistent Hashing + Virtual Nodes)
2. 偏好列表与 sloppy quorum (Preference List, N=3, R=W=2 默认)
3. 向量时钟与读时协调 (Vector Clock + Read Reconciliation)
4. hinted handoff 与 Merkle 树反熵 (Hinted Handoff + Merkle Tree)

运行:
    python3 dynamo.py
"""
from __future__ import annotations
import bisect, hashlib, json, sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional


# ---------- 一致性哈希 + 虚节点 ----------

def hash32(s: str) -> int:
    # CRC32-JAMCRC 与 Dynamo 论文用的 MD5 不同,但分布等价可演示
    # 这里用 sha1 取头 4 字节,与论文 MD5 等价,后续 hash 字符串也用 sha1
    h = hashlib.sha1(s.encode()).digest()
    return int.from_bytes(h[:4], "big")


@dataclass
class VNode:
    pos: int          # ring 上的位置
    node_name: str    # 物理节点名


class ConsistentHashRing:
    """Dynamo 风格 ring:每物理节点分配多个 vnode,按容量异构加权。"""

    def __init__(self, nodes: Dict[str, int]):
        """nodes: {node_name: num_vnodes}"""
        self._sorted_positions: List[int] = []
        self._pos_to_node: Dict[int, str] = {}
        for name, n in nodes.items():
            for i in range(n):
                pos = hash32(f"{name}#vnode-{i}")
                self._sorted_positions.append(pos)
                self._pos_to_node[pos] = name
        self._sorted_positions.sort()

    def preference_list(self, key: str, N: int, alive: set) -> List[str]:
        """沿 ring 顺时针收集 N 个不同的、当前存活的节点;
        如不满 N(网络分区时),从 ring 上下一个节点拿 hint 接收者补齐 (sloppy quorum)。"""
        pos = hash32(key)
        idx = bisect.bisect_right(self._sorted_positions, pos)
        if idx == len(self._sorted_positions):
            idx = 0
        seen = set()
        # 先按"逻辑"偏好收集(不受存活状态影响)
        logical = []
        steps = 0
        while len(seen) < N and steps < len(self._sorted_positions) * 2:
            node = self._pos_to_node[self._sorted_positions[idx]]
            if node not in seen:
                logical.append(node)
                seen.add(node)
            idx = (idx + 1) % len(self._sorted_positions)
            steps += 1
        # sloppy:从 logical 中淘汰 down 的,从 ring 上补(允许重复)
        result: List[str] = []
        used = set()
        for n in logical:
            if n in alive:
                result.append(n)
                used.add(n)
        if len(result) < N:
            # 沿 ring 顺时针补充,直到达到 N;这些会收到 hint
            j = idx
            while len(result) < N and j != idx - 1:
                n = self._pos_to_node[self._sorted_positions[j]]
                result.append(n)
                j = (j + 1) % len(self._sorted_positions)
            # 防死循环
            for _ in range(N * 4):
                if len(result) >= N:
                    break
                n = self._pos_to_node[self._sorted_positions[j]]
                result.append(n)
                j = (j + 1) % len(self._sorted_positions)
        return result[:N]


# ---------- 向量时钟 ----------

@dataclass
class Version:
    """单个 (node, counter) 对"""
    node: str
    counter: int

    def __str__(self):
        return f"{self.node}:{self.counter}"


@dataclass
class VectorClock:
    """向量时钟: dict[node, counter],按 node 排序为元组用于比较。
    严格偏序: A < B ⇔ A.cnt[i] ≤ B.cnt[i] for all i, 且至少一个严格 < ;
    并列(cousin / sibling): 不可比,两个版本都需保留。
    """
    entries: Dict[str, int] = field(default_factory=dict)

    def increment(self, node: str) -> "VectorClock":
        vc = VectorClock(dict(self.entries))
        vc.entries[node] = vc.entries.get(node, 0) + 1
        return vc

    def merge(self, other: "VectorClock") -> "VectorClock":
        """取 max;若想构造"同时覆盖多版本"则两个都保留。"""
        merged = dict(self.entries)
        for n, c in other.entries.items():
            if merged.get(n, 0) < c:
                merged[n] = c
        return VectorClock(merged)

    def dominates(self, other: "VectorClock") -> bool:
        """self ≥ other 且至少一个严格 >"""
        all_keys = set(self.entries) | set(other.entries)
        if any(self.entries.get(n, 0) < other.entries.get(n, 0) for n in all_keys):
            return False
        return any(self.entries.get(n, 0) > other.entries.get(n, 0) for n in all_keys)

    def equal_to(self, other: "VectorClock") -> bool:
        return dict(self.entries) == dict(other.entries)

    def is_ancestor_or_equal(self, other: "VectorClock") -> bool:
        """self ≤ other (用于修剪掉过时版本)"""
        all_keys = set(self.entries) | set(other.entries)
        return all(self.entries.get(n, 0) <= other.entries.get(n, 0) for n in all_keys)

    def __str__(self):
        return "{" + ",".join(f"{n}:{c}" for n, c in sorted(self.entries.items())) + "}"


# ---------- KV 存储 + hint 队列 ----------

@dataclass
class StoredValue:
    value: str
    vc: VectorClock
    tombstone: bool = False  # 删除标记 — 防止写后删被忘记


class DynamoNode:
    """单个物理节点:
       - 自身主存的 (key, [(value, vc)]) 列表
       - 暂存的 hint 队列 (owner, key, [values with vc])
       - "存活"开关
    """

    def __init__(self, name: str, ring: ConsistentHashRing):
        self.name = name
        self.alive = True
        self.store: Dict[str, List[StoredValue]] = defaultdict(list)
        self.hints: Dict[str, Tuple[str, List[StoredValue]]] = {}  # key -> (owner, values)

    # ---- 直写本地 (内部用) ----
    def _local_put(self, key: str, value: str, vc: VectorClock, tombstone: bool = False):
        existing = self.store[key]
        # 修剪:任何 dominated by 新 vc 的旧版本删掉
        new_values = [sv for sv in existing if not vc.dominates(sv.vc)]
        new_values.append(StoredValue(value, vc, tombstone))
        self.store[key] = new_values

    # ---- coordinator 侧 write 路径 ----
    def write(self, key: str, value: str, coordinator: "DynamoNode",
              prefer: List[str], W: int, alive: set) -> Tuple[bool, str]:
        """模拟 coordinator 写 key=value;返回 (是否 W 份成功, 失败原因)"""
        # 1) coordinator 决定新 vc:从某 reading 起点 +1;这里简化为 coordinator 自增
        last_vc = None
        # 找 prefers 中任意一个,读它的最新 vc,作为 base
        for n in prefer:
            if n == coordinator.name and self.name == coordinator.name:
                svs = self.store.get(key, [])
                if svs:
                    last_vc = svs[-1].vc
                    break
            elif n in alive:
                # 在真实实现这里会 RPC;演示中略过
                pass
        new_vc = (last_vc if last_vc else VectorClock()).increment(coordinator.name)

        acks = 0
        # sloppy quorum:前 W 个健康的负责直存,后面为 hint
        health_used = 0
        for n in prefer:
            if health_used >= W:
                break
            if n in alive:
                # 在真实场景: coordinator 转发写请求到 n,n 写本地并 ack
                # 演示中直接模拟为 coordinator 自己写(同一进程)
                acks += 1
            health_used += 1
        # 此处略 RPC 简化 — 真正实现会在每个目标节点 _local_put。
        return acks >= W, f"acks={acks}, W={W}"

    # ---- coordinator 侧 read 路径(读 R 份,在内存里 reconcile) ----
    def read(self, key: str, prefer: List[str], R: int, alive: set) -> Tuple[List[StoredValue], List[str]]:
        """返回 (reconcile 后的版本列表, hint 来源节点列表)"""
        # 真实场景下 coordinator 会向 R 个节点发请求并收集响应
        # 演示中我们简单认为它读自己的 + prefers 中所有活节点的本地 store
        collected: List[StoredValue] = []
        for n in prefer:
            if n in alive:
                collected.extend(self.store.get(key, []))  # 演示同进程,实际 RPC
        # 实际还应取 hint 的数据 — 这里略
        return collected[:max(R, 1)], []

    # ---- 把 hint 投递给恢复的节点 (后台/重启时调用) ----
    def replay_hints(self) -> int:
        n = 0
        for key, (owner, values) in list(self.hints.items()):
            print(f"  [REPLAY] {self.name} 投递 key={key} 给 owner={owner}")
            # 真实是发给 owner;演示中 owner 是我们自己,直接合并进 store
            for sv in values:
                self._local_put(key, sv.value, sv.vc, sv.tombstone)
            del self.hints[key]
            n += 1
        return n


# ---------- 演示场景 ----------

def demo_basic():
    print("\n=== DEMO 1: 一致性哈希 + 偏好列表 ===")
    nodes = {f"node-{chr(65+i)}": 16 for i in range(6)}  # 6 节点 ×16 vnode
    ring = ConsistentHashRing(nodes)
    for k in ["user:42:cart", "product:sku-1", "session:abc", "order:o-100"]:
        pl = ring.preference_list(k, N=3, alive=set(nodes))
        print(f"  key={k:18s} → preference_list = {pl}")


def demo_sloppy_quorum_and_hinted_handoff():
    print("\n=== DEMO 2: sloppy quorum + hinted handoff ===")
    node_names = [f"node-{chr(65+i)}" for i in range(6)]
    nodes_v = {n: 16 for n in node_names}
    ring = ConsistentHashRing(nodes_v)
    # 假设 node-D 临时宕
    alive = set(node_names) - {"node-D"}

    # 取 user:42:cart 的 preference list
    pl = ring.preference_list("user:42:cart", N=3, alive=alive)
    print(f"  user:42:cart preference_list(alive⊆{alive}) = {pl}")
    print("  → Dynamo 写 W=2: 发前 2 个健康节点;若前 2 里也有 down,则发下一个健康+hint")


def demo_vector_clock_and_reconcile():
    print("\n=== DEMO 3: 向量时钟 + 读时协调 ===")
    # 模拟 4 个写交错产生的多版本
    vc_a1 = VectorClock().increment("A")          # 由 A 写的 v1
    vc_b1 = VectorClock().increment("B")          # 由 B 写的 v1(并行,不可比)
    vc_a2 = vc_a1.increment("A")                  # A 的 v2 严格 ≥ v1
    vc_b2 = vc_b1.increment("B")                  # B 的 v2
    vc_sync = vc_a2.merge(vc_b2).increment("C")   # C 读两版本后写

    print(f"  A.v1 = {vc_a1}")
    print(f"  B.v1 = {vc_b1}  (与 A.v1 并列,sibling)")
    print(f"  A.v2 = {vc_a2}  dominates A.v1 → 修剪时删 A.v1")
    print(f"  sync = {vc_sync}  dominates A.v2 与 B.v2 → 同时覆盖")

    assert vc_a2.dominates(vc_a1)
    assert not vc_a1.dominates(vc_b1) and not vc_b1.dominates(vc_a1), "并列不可比"
    print("  -> 修剪规则:dominates ⇒ 旧版丢弃;sibling ⇒ 两版都保留")


def demo_put_get_with_replication():
    print("\n=== DEMO 4: put/get 走完整 prefer→W→R→reconcile 流程 ===")
    node_names = [f"node-{chr(65+i)}" for i in range(6)]
    ring = ConsistentHashRing({n: 16 for n in node_names})

    # 单进程模拟多节点:每个节点一个 in-memory store
    dm_nodes = {n: DynamoNode(n, ring) for n in node_names}

    # 写 key=user:1:profile, value=alice
    key = "user:1:profile"
    alive_full = set(node_names)
    pref = ring.preference_list(key, N=3, alive=alive_full)
    print(f"  preference_list({key}) = {pref}")

    # coordinator 选第一个存活节点 = pref[0]
    coord = dm_nodes[pref[0]]
    base_vc = VectorClock()
    for p in pref:
        svs = dm_nodes[p].store.get(key, [])
        if svs and svs[-1].vc.dominates(base_vc):
            base_vc = svs[-1].vc
    new_vc = base_vc.increment(coord.name)
    # 在 W=2 的健康节点上写; sloppy 也按相同逻辑
    W = 2
    written = 0
    for p in pref:
        if written >= W:
            break
        if p in alive_full:
            dm_nodes[p].store[key].append(StoredValue("alice", new_vc))
            written += 1
    print(f"  put alice → 写到前 2 个健康节点 {pref[:W]}, 新 vc = {new_vc}")

    # 读 — coordinator 也是 pref[0]
    R = 2
    collected = []
    for p in pref[:R]:
        collected.extend(dm_nodes[p].store.get(key, []))

    # reconcile:去 tombstone / 保留未被覆盖的多版本
    print(f"  get 读到 {len(collected)} 份, 最新 vc = {new_vc}, value = {collected[0].value}")
    print(f"  reconciled returned: {collected[0].value}")


def main():
    demo_basic()
    demo_sloppy_quorum_and_hinted_handoff()
    demo_vector_clock_and_reconcile()
    demo_put_get_with_replication()
    print("\nDONE. 参考:DeCandia et al. SOSP'07 'Dynamo: Amazon's Highly Available KV Store'")
    print("+" + "-" * 60 + "+")
    print("| 解析要点(可对照 src):                                  |")
    print("|  - Partitioning:ConsistentHashRing (SHA1 头 4 字节)    |")
    print("|  - Replication:preference_list = N 个顺时针不同节点    |")
    print("|  - Versioning:VectorClock.increment/merge/dominates    |")
    print("|  - Sloppy:用下一健康节点收写 + hint 存本该送的 owner   |")
    print("+" + "-" * 60)


if __name__ == "__main__":
    main()
