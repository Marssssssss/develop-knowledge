"""
mongo_wc.py — MongoDB 副本集 + writeConcern/readConcern 数据模型

权威来源:
  - MongoDB manual: Write Concern (https://www.mongodb.com/docs/manual/reference/write-concern/)
  - MongoDB manual: Read Concern (https://www.mongodb.com/docs/manual/reference/read-concern/)
  - MongoDB 5.0 Compatibility: Implicit Default Write Concern formula
  - MongoDB source (GitHub): src/mongo/db/repl/README.md

运行 demo: python3 mongo_wc_demos.py
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# =====================================================================
# 1. 数据结构 — OpLogEntry + OpObserver + Member + ReplicaSet
# =====================================================================

@dataclass
class OpLogEntry:
    """oplog.rs 中的一条记录 — capped collection,按时间有序。
    op: 'i' insert, 'u' update, 'd' delete, 'c' command (createIndexes 等)
    """
    ts: int
    op: str
    ns: str
    o2: Optional[dict]   # update filter / insert key
    o: Optional[dict]    # update modifier / insert doc
    hash: int = 0


@dataclass
class OpObserver:
    next_ts: int = 1
    oplog: List[OpLogEntry] = field(default_factory=list)

    def record(self, op: str, ns: str, o: dict, o2: Optional[dict] = None) -> OpLogEntry:
        e = OpLogEntry(ts=self.next_ts, op=op, ns=ns, o2=o2, o=o,
                       hash=hash((op, ns, str(o), str(o2))))
        self.next_ts += 1
        self.oplog.append(e)
        return e


@dataclass
class Member:
    """副本集成员"""
    name: str
    role: str  # "PRIMARY" / "SECONDARY" / "ARBITER"
    priority: int = 1
    hidden: bool = False
    delayed_sec: int = 0
    repl_cursor: int = 0
    store: Dict[str, dict] = field(default_factory=dict)
    last_applied_ts: int = 0

    def apply_oplog_entry(self, e: OpLogEntry) -> None:
        """secondary 应用一条 oplog 条目,模拟幂等重放。"""
        if e.op == "i":
            self.store[e.o2["_id"]] = e.o
        elif e.op == "u":
            _id = e.o2["_id"]
            if _id in self.store:
                self.store[_id].update(e.o.get("$set", e.o))
        elif e.op == "d":
            self.store.pop(e.o2["_id"], None)
        self.last_applied_ts = max(self.last_applied_ts, e.ts)


class ReplicaSet:
    def __init__(self, members: List[Member]):
        self.members = members
        for m in self.members:
            if m.role != "ARBITER":
                m.role = "PRIMARY"
                self.primary = m
                break
        self.opobserver = OpObserver()

    # ------------------ 写入路径 ------------------
    def write(self, coll: str, doc: dict, wc: dict) -> Tuple[bool, str, int]:
        """coordinator(primary)按 wc 决定何时返回。隐式默认 w=majority(5.0+)"""
        if "w" not in wc:
            wc["w"] = "majority"
        _id = doc.get("_id")
        if _id is None:
            _id = f"gen-{len(self.primary.store)}"
            doc["_id"] = _id
        self.primary.store[_id] = doc
        entry = self.opobserver.record("i", coll, doc, {"_id": _id})

        # 多数派计算
        voting = [m for m in self.members if m.priority > 0 and not m.hidden]
        n_voting = len(voting)
        majority = n_voting // 2 + 1

        target = wc["w"]
        if isinstance(target, str) and target == "majority":
            need = majority
        elif isinstance(target, int):
            need = target
        else:
            need = majority

        # secondary 拉 oplog
        for m in self.members:
            if m is self.primary or m.role == "ARBITER":
                continue
            m.apply_oplog_entry(entry)

        commit = min(m.last_applied_ts for m in self.members if m.role != "ARBITER")
        acked = sum(1 for m in self.members if m.last_applied_ts >= entry.ts and m.role != "ARBITER")
        ok = acked >= need
        notes = f"acks={acked}/{need}, commit_point_ts={commit}, primary_ts={entry.ts}"
        return ok, notes, entry.ts

    # ------------------ 读路径 ------------------
    def read(self, coll: str, query: dict, rc: str, from_secondary: bool = False
             ) -> Tuple[Optional[dict], str]:
        """readConcern 5 级语义返回"""
        target = None
        if from_secondary and any(m.role == "SECONDARY" for m in self.members):
            target = next(m for m in self.members if m.role == "SECONDARY")
        else:
            target = self.primary
        _id = query.get("_id")

        if rc == "local":
            d = target.store.get(_id)
            return d, f"readConcern=local from {target.name} (可能未 commit)"

        if rc == "available":
            d = target.store.get(_id)
            return d, f"readConcern=available from {target.name} (分片最快;非一致)"

        if rc == "majority":
            commit = min(m.last_applied_ts for m in self.members if m.role != "ARBITER")
            if target.last_applied_ts < commit:
                return None, f"节点未达 commit_point ({target.last_applied_ts} < {commit}),等待..."
            d = target.store.get(_id)
            return d, f"readConcern=majority from {target.name} (commit_point={commit})"

        if rc == "linearizable":
            if target is not self.primary:
                return None, "linearizable 仅可在 primary 上"
            commit = min(m.last_applied_ts for m in self.members if m.role != "ARBITER")
            d = self.primary.store.get(_id)
            return d, f"readConcern=linearizable from primary (最强一致;会等并发写)"

        if rc == "snapshot":
            d = target.store.get(_id)
            return d, f"readConcern=snapshot (事务内一致性;需 w=majority 提交)"

        return None, f"unknown readConcern {rc}"

    # ------------------ 默认 WC 计算 ------------------
    @staticmethod
    def compute_default_wc(voting_total: int, n_arbiters: int) -> str:
        if voting_total == 0:
            return "w=1 (standalone)"
        n_non_arbiters = voting_total - n_arbiters
        majority = voting_total // 2 + 1
        if n_arbiters > 0 and n_non_arbiters <= majority:
            return f"w=1 (P-S-A trap! voting={voting_total} arbiters={n_arbiters})"
        return f"w=majority (voting={voting_total} arbiters={n_arbiters})"

    # ------------------ 故障切换 + 回滚 ------------------
    def simulate_failover_and_rollback(self, lost_writes: List[dict]):
        """旧 primary 上的未复制写 failover 后被回滚。"""
        print(f"  \u2192 旧 primary 失败;secondaries 触发选举")
        secondaries = [m for m in self.members if m.role == "SECONDARY"]
        new_primary = max(secondaries, key=lambda m: (m.priority, m.last_applied_ts))
        new_primary.role = "PRIMARY"
        self.primary = new_primary
        print(f"  \u2192 新 primary: {new_primary.name}")
        print(f"  \u2192 回滚 {len(lost_writes)} 条 lost writes:")
        for w in lost_writes:
            print(f"      \u21b3 _id={w.get('_id')} value={w.get('value')!r}")
