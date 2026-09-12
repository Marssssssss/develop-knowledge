"""
cassandra_cl.py — Cassandra 一致性级别(数据/集群模型)

权威来源:
  - DataStax [Cassandra 0.7 archived docs Consistency](https://docs.datastax.com/en/archived/cassandra/0.7/docs/consistency/index.html)
  - DataStax [0.8 archived docs About Client Requests](https://docs.datastax.com/en/archived/cassandra/0.8/docs/cluster_architecture/about_client_requests.html)

运行:
  python3 cassandra_cl_demos.py  # 主入口 demo
"""
from __future__ import annotations
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


# ====================================================================
# (1) CL 决策 + 强一致性公式
# ====================================================================

CL_ANY, CL_ONE, CL_TWO, CL_THREE = "ANY", "ONE", "TWO", "THREE"
CL_QUORUM = "QUORUM"
CL_LOCAL_QUORUM = "LOCAL_QUORUM"
CL_EACH_QUORUM = "EACH_QUORUM"
CL_ALL = "ALL"

WRITE_CLS = [CL_ANY, CL_ONE, CL_TWO, CL_THREE, CL_QUORUM, CL_LOCAL_QUORUM, CL_EACH_QUORUM, CL_ALL]


def required_acks(cl: str, rf: int, dc_count: int = 1) -> int:
    """需要多少副本 ack 才算成功。LOCAL/EACH_QUORUM 简化为各 DC 都算 local_quorum。"""
    if cl == CL_ANY:        return 1
    if cl == CL_ONE:        return 1
    if cl == CL_TWO:        return 2
    if cl == CL_THREE:      return 3
    if cl in (CL_QUORUM, CL_LOCAL_QUORUM):
        return rf // 2 + 1
    if cl == CL_EACH_QUORUM:
        return (rf // 2 + 1) * dc_count
    if cl == CL_ALL:        return rf
    raise ValueError(f"unknown CL: {cl}")


def is_strongly_consistent(W: int, R: int, rf: int) -> bool:
    """W + R > rf ⇒ 至少一个读副本必含最新写 — 强一致。"""
    return W + R > rf


# ====================================================================
# (2) 集群模型 — Cell / Replica / Cluster
# ====================================================================

@dataclass
class Cell:
    """(key) 在某 replica 上的值;LWW 由 wall_ts 微秒时间戳决定"""
    value: str
    wall_ts_us: int
    tombstone: bool = False


class Replica:
    """单个副本节点:kv 存储 + hint 队列 + role。"""
    def __init__(self, name: str, role: str = "SECONDARY"):
        self.name = name
        self.role = role
        self.alive = True
        self.store: Dict[str, List[Cell]] = defaultdict(list)
        self.hints: Dict[str, Tuple[str, Cell]] = {}

    def write_cell(self, key: str, cell: Cell) -> None:
        self.store[key].append(cell)

    def read(self, key: str) -> List[Cell]:
        return list(self.store.get(key, []))

    def put_hint(self, intended_for: str, key: str, cell: Cell) -> None:
        self.hints[key] = (intended_for, cell)

    def replay_hints(self) -> int:
        n = 0
        for key, (intended, cell) in list(self.hints.items()):
            if intended == self.name:
                print(f"  [REPLAY] {self.name} 投递 (for={intended}, key={key}, value={cell.value!r})")
                self.write_cell(key, cell)
                del self.hints[key]
                n += 1
        return n


class Cluster:
    """6 节点 RF=3 集群;coordinator 永远由 key 选第一个偏好节点。"""
    def __init__(self, n_replicas: int = 6, rf: int = 3):
        self.rf = rf
        self.replicas = [Replica(f"n-{i}", role=("PRIMARY" if i == 0 else "SECONDARY"))
                          for i in range(n_replicas)]
        self.primary = self.replicas[0]
        self._ts = 0

    def next_ts(self) -> int:
        self._ts += 1
        return self._ts

    def coordinator_index(self, key: str) -> int:
        return sum(ord(c) for c in key) % len(self.replicas)

    def preference_list(self, key: str) -> List[int]:
        """演示用确定性:start=(hash key) % N + 顺延 rf-1 个不同物理节点。"""
        start = self.coordinator_index(key)
        out = []
        i = start
        while len(out) < self.rf:
            out.append(i % len(self.replicas))
            i += 1
        return out

    # -------- write 路径 --------
    def write(self, key: str, value: str, cl: str, dc_count: int = 1
              ) -> Tuple[bool, str, int]:
        """coordinator 写;返回 (成功?, 备注, hints 数)"""
        pref = self.preference_list(key)
        alive_pref = [i for i in pref if self.replicas[i].alive]
        hint_count = 0
        pref_used = list(alive_pref)
        # sloppy:目标 down 时,从后序节点补上,加 hint
        if len(pref_used) < len(pref):
            missing = [i for i in pref if not self.replicas[i].alive]
            j = pref[-1] + 1
            for m in missing:
                while j in pref_used:
                    j = (j + 1) % len(self.replicas)
                pref_used.append(j)
                cell = Cell(value, self.next_ts())
                self.replicas[j].put_hint(
                    intended_for=self.replicas[m].name,
                    key=key, cell=cell)
                hint_count += 1
                j = (j + 1) % len(self.replicas)

        # 真正写活的副本
        cell = Cell(value, self.next_ts())
        for idx in alive_pref:
            self.replicas[idx].write_cell(key, cell)

        need = required_acks(cl, self.rf, dc_count)
        ack = len(alive_pref)
        if cl == CL_ANY:
            ok = ack + hint_count >= 1
            return ok, f"acks={ack} hints={hint_count} (CL_ANY accepts hint-only)", hint_count
        if cl == CL_ALL:
            return (ack >= self.rf, f"acks={ack} (need RF={self.rf})", hint_count)
        return ack >= need, f"acks={ack}/{need} ({cl}) — need {cl}", hint_count

    # -------- read 路径 + read repair --------
    def read(self, key: str, cl: str) -> Tuple[List[Cell], int, int]:
        """read + (后台)read-repair:返回 (collected cells, rr_writes 数, stale 数)"""
        pref = self.preference_list(key)
        alive_pref = [i for i in pref if self.replicas[i].alive]
        need = required_acks(cl, self.rf)
        contact = alive_pref[:need]
        collected = [c for idx in contact for c in self.replicas[idx].read(key)]
        rr_writes = 0; stale = []
        if contact:
            winner = max((c for c in collected if not c.tombstone),
                         key=lambda c: c.wall_ts_us, default=None)
            if winner:
                for idx in pref:
                    r = self.replicas[idx]
                    cells_here = r.read(key)
                    if not cells_here or max(c.wall_ts_us for c in cells_here) < winner.wall_ts_us:
                        if r.alive:
                            r.write_cell(key, winner)
                            rr_writes += 1
                            stale.append(r.name)
        return collected, rr_writes, len(stale)

    # -------- LWT(Paxos 简化版) --------
    def lwt_insert_if_not_exists(self, key: str, value: str) -> Tuple[bool, str]:
        cell = Cell(value, self.next_ts())
        pref = self.preference_list(key)
        for idx in pref:
            r = self.replicas[idx]
            if not r.alive:    continue
            if r.read(key):
                return False, f"拒绝:已有 key (replica {r.name})"
        for idx in pref:
            if self.replicas[idx].alive:
                self.replicas[idx].write_cell(key, cell)
        return True, f"Paxos commit by coordinator (ballot={cell.wall_ts_us})"

    # -------- Anti-Entropy Repair(Merkle 树简化版) --------
    def merkle_digest(self, replica_idx: int, keys: List[str]) -> str:
        items = []
        for k in sorted(keys):
            cells = self.replicas[replica_idx].read(k)
            live = [c for c in cells if not c.tombstone]
            v = live[-1].value if live else "<tombstone>"
            items.append(f"{k}={v}")
        return hashlib.sha256("|".join(items).encode()).hexdigest()[:16]

    def anti_entropy_repair(self, replica_i: int, replica_j: int, keys: List[str]):
        ri, rj = self.replicas[replica_i], self.replicas[replica_j]
        di, dj = self.merkle_digest(replica_i, keys), self.merkle_digest(replica_j, keys)
        print(f"  [{ri.name}] digest={di}")
        print(f"  [{rj.name}] digest={dj}")
        if di != dj:
            diffs = [k for k in keys if ri.read(k) != rj.read(k)]
            print(f"  → 差异 keys = {diffs},逐 key 流式同步")
            for k in diffs:
                cands = ri.read(k) + rj.read(k)
                winner = max((c for c in cands if not c.tombstone),
                             key=lambda c: c.wall_ts_us, default=None)
                if winner:
                    ri.write_cell(k, winner); rj.write_cell(k, winner)
        else:
            print("  → 一致,无需同步")
