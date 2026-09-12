"""
cassandra_cl.py — Cassandra 一致性级别完整最小实现

演示:
  1. 8 个 CL 决策(ANY/ONE/TWO/THREE/QUORUM/LOCAL_QUORUM/EACH_QUORUM/ALL)
  2. 强一致性公式 W + R > RF
  3. hinted handoff 队列(replica down 时把写转交下一个 + hint)
  4. read repair(读 R+1 时检查后副本并补写)
  5. 轻量事务 LWT(Paxos prepare/propose/accept/commit) — 4 阶段简化版
  6. Anti-entropy repair 用 Merkle tree 对账

权威来源:
  - DataStax 官方 0.7/0.8 archived docs(https://docs.datastax.com/en/archived/cassandra/...)
  - let's build solutions 'How Cassandra Works'(宽表 / gossip / tunable consistency)
  - Cassandra source(cassandra.yaml 默认值:hinted_handoff / read_repair_chance / gc_grace_seconds)

运行:
  python3 cassandra_cl.py
"""
from __future__ import annotations
import hashlib, random, sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ====================================================================
# (1) CL 决策 + 强一致性公式
# ====================================================================

CL_ANY = "ANY"
CL_ONE, CL_TWO, CL_THREE = "ONE", "TWO", "THREE"
CL_QUORUM = "QUORUM"
CL_LOCAL_QUORUM = "LOCAL_QUORUM"
CL_EACH_QUORUM = "EACH_QUORUM"
CL_ALL = "ALL"

WRITE_CLS = [CL_ANY, CL_ONE, CL_TWO, CL_THREE, CL_QUORUM, CL_LOCAL_QUORUM, CL_EACH_QUORUM, CL_ALL]
READ_CLS = [CL_ONE, CL_TWO, CL_THREE, CL_QUORUM, CL_LOCAL_QUORUM, CL_EACH_QUORUM, CL_ALL]


def required_acks(cl: str, rf: int, dc_count: int = 1) -> int:
    """需要多少副本 ack 才算成功。
    LOCAL_QUORUM / EACH_QUORUM 需多 DC 信息,这里简化为各 DC 都算 local_quorum。"""
    if cl == CL_ANY:
        return 1
    if cl == CL_ONE:
        return 1
    if cl == CL_TWO:
        return 2
    if cl == CL_THREE:
        return 3
    if cl in (CL_QUORUM, CL_LOCAL_QUORUM):
        return rf // 2 + 1
    if cl == CL_EACH_QUORUM:
        return (rf // 2 + 1) * dc_count
    if cl == CL_ALL:
        return rf
    raise ValueError(f"unknown CL: {cl}")


def is_strongly_consistent(W: int, R: int, rf: int) -> bool:
    """W + R > rf ⇒ 至少一个读副本必定包含最新写 — 强一致。"""
    return W + R > rf


def explain_table() -> None:
    print("=== Cassandra 一致性级别详解 ===\n")
    print(f"{'CL':15s} {'语义':50s} {'需 ack (RF=3)':15s}")
    print("-" * 80)
    rows = [
        (CL_ANY, "任何 1 个副本(包括 hint-only)", "1"),
        (CL_ONE, "1 个 replica 即可", "1"),
        (CL_TWO, "2 个 replica", "2"),
        (CL_THREE, "3 个 replica", "3"),
        (CL_QUORUM, f"多数派 = RF/2+1", "2"),
        (CL_LOCAL_QUORUM, "本 DC 多数派(避免跨 DC RTT)", "2"),
        (CL_EACH_QUORUM, "所有 DC 都达到 quorum", "(2 × dc)"),
        (CL_ALL, "全部 RF 个 replica", "3"),
    ]
    for cl, sem, need in rows:
        print(f"{cl:15s} {sem:50s} {need:15s}")


# ====================================================================
# (2) 集群模型 — RF=3 + N=6,key→3 个 coordinator
# ====================================================================

@dataclass
class Cell:
    """单个 (key) 在某 replica 上的值。
    LWW 决定时,wall_ts 用微秒 — Cassandra 默认 timestamp=now() 微秒精度"""
    value: str
    wall_ts_us: int
    tombstone: bool = False    # TTL 或 delete 写入的墓碑

    def __repr__(self):
        flag = "[T]" if self.tombstone else "    "
        return f"{flag} {self.value!r} @ {self.wall_ts_us}"


class Replica:
    def __init__(self, name: str):
        self.name = name
        self.alive = True
        # 主存储:key → list[Cell],LWW 通常取最新非墓碑;这里列表用于演示读 reconcile
        self.store: Dict[str, List[Cell]] = defaultdict(list)
        self.hints: Dict[str, Tuple[str, Cell]] = {}     # key -> (intended_for, cell)

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

    def __repr__(self):
        return f"Replica({self.name}, alive={self.alive})"


class Cluster:
    def __init__(self, n_replicas: int = 6, rf: int = 3):
        self.rf = rf
        self.replicas = [Replica(f"n-{i}") for i in range(n_replicas)]
        self._ts_counter = 0

    def next_ts(self) -> int:
        self._ts_counter += 1
        return self._ts_counter

    def coordinator_index(self, key: str) -> int:
        # 简化:coordinator 由 key hash % N 选
        return sum(ord(c) for c in key) % len(self.replicas)

    def preference_list(self, key: str) -> List[int]:
        """实际生产会用 Murmur3 / RandomPartitioner;这里用确定性 hash。"""
        # 6 个节点均匀分到 6 个 shard,key 选 (hash % 6) 起的 3 个不同物理节点
        # 简化:直接 round-robin 选定 3 个
        start = self.coordinator_index(key)
        return [(start + i) % len(self.replicas) for i in range(self.rf)]

    # -------- write 路径 --------
    def write(self, key: str, value: str, cl: str, dc_count: int = 1) -> Tuple[bool, str, int]:
        """coordinator 写 key=value at CL;返回 (成功?, 备注, hints 数)"""
        pref = self.preference_list(key)
        alive_pref = [i for i in pref if self.replicas[i].alive]
        # sloppy quorum:不够就从 ring 后续补 hint 路径
        hint_count = 0
        pref_used = list(alive_pref)
        if len(pref_used) < len(pref):
            missing = [i for i in pref if not self.replicas[i].alive]
            # 用顺序扩展填充位置
            j = pref[-1] + 1
            for m in missing:
                while j in pref_used:
                    j = (j + 1) % len(self.replicas)
                pref_used.append(j)
                # 找一个 hint 接收者
                hint_recv = j
                target_down = m
                # 演示:把 hint 写到 pref_used 的最后一个节点 (即 hint_recv)
                cell = Cell(value, self.next_ts())
                self.replicas[hint_recv].put_hint(
                    intended_for=self.replicas[target_down].name,
                    key=key, cell=cell,
                )
                hint_count += 1
                j = (j + 1) % len(self.replicas)

        # 真正写活的副本
        cell = Cell(value, self.next_ts())
        for idx in alive_pref:
            self.replicas[idx].write_cell(key, cell)

        need = required_acks(cl, self.rf, dc_count)
        ack = len(alive_pref)
        if cl == CL_ANY:
            # ANY:全部 down 但有 hint 也算
            ok = ack + hint_count >= 1
            return ok, f"acks={ack} hints={hint_count} (CL_ANY accepts hint-only)", hint_count
        if cl == CL_ALL:
            return (ack >= self.rf, f"acks={ack} (need RF={self.rf})", hint_count)
        ok = ack >= need
        return ok, f"acks={ack}/{need} ({cl}) — need {cl}", hint_count

    # -------- read 路径 + read repair --------
    def read(self, key: str, cl: str) -> Tuple[List[Cell], int, int]:
        """返回 (reconciled values, read_repair_writes, 不一致副本数)"""
        pref = self.preference_list(key)
        alive_pref = [i for i in pref if self.replicas[i].alive]
        need = required_acks(cl, self.rf)
        # 取前 CL 个要求的活副本
        contact = alive_pref[:need]
        collected: List[Cell] = []
        for idx in contact:
            collected.extend(self.replicas[idx].read(key))

        # 取所有参与副本(不止 CL 个)做 read repair
        rr_writes = 0
        stale = []
        if contact:
            winner = max((c for c in collected if not c.tombstone), key=lambda c: c.wall_ts_us, default=None)
            if winner:
                # 其他 replica 上没有 winner 的 → 补写
                # 简化:把 alive_pref 中所有 idx 拉出来对比
                for idx in pref:
                    r = self.replicas[idx]
                    cells_here = r.read(key)
                    if not cells_here or max(c.wall_ts_us for c in cells_here) < winner.wall_ts_us:
                        if r.alive:
                            r.write_cell(key, winner)
                            rr_writes += 1
                            stale.append(r.name)
        return collected, rr_writes, len(stale)

    # -------- LWT(Paxos 简化版 — prepare/propose/commit) --------
    def lwt_insert_if_not_exists(self, key: str, value: str) -> Tuple[bool, str]:
        """Cassandra 的 INSERT ... IF NOT EXISTS 走 Paxos:
        4 阶段: prepare → promise → propose → accept → commit。
        这里给一个乐观并发控制简化版: ballot+timestamp+CAS-like。"""
        cell = Cell(value, self.next_ts())
        # 收集所有偏好副本,看是否已有 key
        pref = self.preference_list(key)
        for idx in pref:
            r = self.replicas[idx]
            if not r.alive:
                continue
            if r.read(key):
                return False, f"拒绝:已有 key (replica {r.name})"
        # ballot 没人反对 → commit
        for idx in pref:
            if self.replicas[idx].alive:
                self.replicas[idx].write_cell(key, cell)
        return True, f"Paxos commit by coordinator (ballot={cell.wall_ts_us})"

    # -------- Anti-Entropy Repair(Merkle tree 简化版) --------
    def merkle_digest(self, replica_idx: int, keys: List[str]) -> str:
        """对给定 replica 上的 keys 做 SHA-256(用于反熵对比)"""
        items = []
        for k in sorted(keys):
            cells = self.replicas[replica_idx].read(k)
            # 一致性:取最新非墓碑
            live = [c for c in cells if not c.tombstone]
            v = live[-1].value if live else "<tombstone>"
            items.append(f"{k}={v}")
        return hashlib.sha256("|".join(items).encode()).hexdigest()[:16]

    def anti_entropy_repair(self, replica_i: int, replica_j: int, keys: List[str]):
        """演示:两副本对比 Merkle 摘要,差异 keys 同步。"""
        ri, rj = self.replicas[replica_i], self.replicas[replica_j]
        di, dj = self.merkle_digest(replica_i, keys), self.merkle_digest(replica_j, keys)
        print(f"  [{ri.name}] digest={di}")
        print(f"  [{rj.name}] digest={dj}")
        if di != dj:
            diffs = []
            for k in keys:
                if ri.read(k) != rj.read(k):
                    diffs.append(k)
            print(f"  → 差异 keys = {diffs},逐 key 流式同步")
            for k in diffs:
                # 取时间戳最新者
                cands = ri.read(k) + rj.read(k)
                winner = max((c for c in cands if not c.tombstone),
                             key=lambda c: c.wall_ts_us, default=None)
                if winner:
                    ri.write_cell(k, winner)
                    rj.write_cell(k, winner)
        else:
            print("  → 一致,无需同步")


# ====================================================================
# 演示场景
# ====================================================================

def demo_cl_decision():
    print("\n=== DEMO 1: CL 决策表(RF=3)+ 强一致性公式 ===\n")
    rf = 3
    print(f"RF={rf}, 各 CL 需 ack 数: {', '.join(f'{c}={required_acks(c, rf)}' for c in WRITE_CLS if c not in (CL_LOCAL_QUORUM, CL_EACH_QUORUM))}")
    # 强一致性 4 组合
    combos = [
        ("QUORUM/QUORUM", 2, 2, 3),
        ("ALL/ONE",        3, 1, 3),
        ("ONE/ONE",        1, 1, 3),
        ("QUORUM/ONE",     2, 1, 3),
    ]
    print()
    print(f"{'组合':15s} {'W':3s} {'R':3s} {'RF':3s} {'W+R':5s} {'一致性':10s}")
    print("-" * 50)
    for name, W, R, rf_ in combos:
        strong = is_strongly_consistent(W, R, rf_)
        print(f"{name:15s} {W:3d} {R:3d} {rf_:3d} {W+R:5d} {'STRONG' if strong else 'eventual':10s}")
    print("\n启示:本地独立看读写 CL 不够,必须看组合。QUORUM+QUORUM 在 RF=3 上 strongest;")


def demo_write_read_with_cl():
    print("\n=== DEMO 2: 不同 CL 在 1 副本 down 时表现 ===\n")
    cluster = Cluster(n_replicas=6, rf=3)
    cluster.replicas[1].alive = False  # n-1 down

    print(f"初始:n-1 = DOWN (其他 5 节点健康)")
    for cl in [CL_ONE, CL_QUORUM, CL_ALL, CL_ANY]:
        ok, msg, hint_n = cluster.write("user:1", f"v@CL={cl}", cl)
        print(f"  CL={cl:13s} put(user:1) → {'OK' if ok else 'FAIL'} | {msg}")

    # 模拟 read repair
    print("\n--- 读 + read repair ---")
    values, rr, stale = cluster.read("user:1", CL_QUORUM)
    print(f"  read(CL=QUORUM) 返回 {len(values)} 个版本, 触发 {rr} 次 read-repair 补写")
    # 修复 n-1
    cluster.replicas[1].alive = True
    print(f"\n--- n-1 上线,replay hints ---")
    cluster.replicas[1].replay_hints()


def demo_lwt():
    print("\n=== DEMO 3: 轻量事务 LWT(Paxos 简化版,INSERT ... IF NOT EXISTS) ---")
    cluster = Cluster(n_replicas=6, rf=3)
    ok, msg = cluster.lwt_insert_if_not_exists("username:alice", "alice@x.com")
    print(f"  第 1 次尝试 → {'OK' if ok else 'FAIL'}: {msg}")
    ok, msg = cluster.lwt_insert_if_not_exists("username:alice", "alice@x.com")
    print(f"  第 2 次尝试 → {'OK' if ok else 'FAIL'}: {msg}")


def demo_anti_entropy():
    print("\n=== DEMO 4: Anti-entropy repair (Merkle 树简化版) ===")
    cluster = Cluster(n_replicas=6, rf=3)
    # 模拟长期 down 期间累积的不一致
    cluster.write("k1", "v1-old", CL_ONE)
    cluster.write("k2", "v2-old", CL_ONE)
    cluster.write("k3", "v3", CL_ONE)
    cluster.replicas[2].alive = False
    cluster.write("k1", "v1-new", CL_ONE)
    cluster.write("k2", "v2-new", CL_ONE)
    cluster.replicas[2].alive = True
    print(f"  3 节点不一致 — 比对 n-0 vs n-2 的 keys [k1,k2,k3]:")
    cluster.anti_entropy_repair(0, 2, ["k1", "k2", "k3"])


def main():
    explain_table()
    demo_cl_decision()
    demo_write_read_with_cl()
    demo_lwt()
    demo_anti_entropy()
    print("\n" + "="*70)
    print("Cassandra 调优 tips(节选)")
    print("="*70)
    print("  1. 生产写读都用 LOCAL_QUORUM 起步(多 DC 部署)")
    print("  2. 监控 read_repair_activity / write_repair_activity 计数")
    print("  3. 周期性跑 nodetool repair -pr -par <keyspace>")
    print("  4. gc_grace_seconds (默认 10d) 内必须 repair 完一次,否则 tombstone purge")
    print("  5. 增量修复(4.0+)只扫未修复过的 SSTable")
    print("  6. LWT 4-5x 慢于普通写,只在唯一性约束/库存超卖时使用")


if __name__ == "__main__":
    main()
