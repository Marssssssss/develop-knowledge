"""
mongo_wc.py — MongoDB 副本集 + writeConcern / readConcern 完整最小实现

演示:
  1. 副本集成员角色(Primary / Secondary / Arbiter)
  2. oplog (capped collection) — 写产生 oplog 条目
  3. secondary 通过 tailable cursor 拉 oplog
  4. writeConcern 决策(w / j / wtimeout)、隐式默认值公式
  5. readConcern 5 级语义比较(local / available / majority / linearizable / snapshot)
  6. commit point — majority 读要求节点 oplog 到达
  7. 回滚(rollback):primary failover 时旧 primary 上未复制的写被 revert
  8. causal consistency session 与 afterClusterTime

权威来源:
  - MongoDB manual: Write Concern (https://www.mongodb.com/docs/manual/reference/write-concern/)
  - MongoDB manual: Read Concern (https://www.mongodb.com/docs/manual/reference/read-concern/)
  - MongoDB source: Replication Internals (src/mongo/db/repl/README.md)
  - MongoDB 5.0 Compatibility: Implicit Default Write Concern formula

运行: python3 mongo_wc.py
"""
from __future__ import annotations
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# =====================================================================
# 1. 数据结构 — OpLog + Members + Cluster
# =====================================================================

@dataclass
class OpLogEntry:
    """oplog.rs 中的一条记录 — capped collection,按时间有序。
    op: 'i' insert, 'u' update, 'd' delete, 'c' command (createIndexes etc.)
    """
    ts: int              # Timestamp(秒+序数); 单调递增
    op: str
    ns: str              # "db.coll"
    o2: Optional[dict]   # update filter / insert key
    o: Optional[dict]    # update modifier / insert doc
    hash: int = 0        # for idempotency on replay


@dataclass
class OpObserver:
    """Primary 端的 OpObserver,把所有写产生 oplog 条目。"""
    next_ts: int = 1
    oplog: List[OpLogEntry] = field(default_factory=list)

    def record(self, op: str, ns: str, o: dict, o2: Optional[dict] = None) -> OpLogEntry:
        e = OpLogEntry(ts=self.next_ts, op=op, ns=ns, o2=o2, o=o, hash=hash((op, ns, str(o), str(o2))))
        self.next_ts += 1
        self.oplog.append(e)
        # capped collection 行为:旧条目会被覆盖。演示无限长。
        return e


@dataclass
class Member:
    """副本集成员"""
    name: str
    role: str  # "PRIMARY" / "SECONDARY" / "ARBITER"
    priority: int = 1
    hidden: bool = False
    delayed_sec: int = 0
    # secondary 上 oplog 拉取 cursor 模拟
    repl_cursor: int = 0        # 当前拉到 oplog 的 ts index
    store: Dict[str, dict] = field(default_factory=dict)   # 真实数据
    last_applied_ts: int = 0

    def apply_oplog_entry(self, e: OpLogEntry) -> None:
        """secondary 应用一条 oplog 条目,模拟幂等重放。"""
        if e.op == "i":
            self.store[e.o2["_id"]] = e.o
        elif e.op == "u":
            _id = e.o2["_id"]
            if _id in self.store:
                # u 用 $set 语义;演示简化
                self.store[_id].update(e.o.get("$set", e.o))
        elif e.op == "d":
            self.store.pop(e.o2["_id"], None)
        self.last_applied_ts = max(self.last_applied_ts, e.ts)


class ReplicaSet:
    def __init__(self, members: List[Member]):
        self.members = members
        # 让第一个非 arbiter 成员成为 primary
        for m in self.members:
            if m.role != "ARBITER":
                m.role = "PRIMARY"
                self.primary = m
                break
        self.opobserver = OpObserver()

    # ------------------ 写入路径 ------------------
    def write(self, coll: str, doc: dict, wc: dict) -> Tuple[bool, str, int]:
        """coordinator 是 primary;按 wc 决定何时返回成功。"""
        if "w" not in wc:
            wc["w"] = "majority"  # 隐式默认(5.0+)

        _id = doc.get("_id")
        if _id is None:
            _id = f"gen-{len(self.primary.store)}"
            doc["_id"] = _id

        # 1) primary 本地应用写(等价于 mongod apply)
        self.primary.store[_id] = doc

        # 2) 生成 oplog
        entry = self.opobserver.record("i", coll, doc, {"_id": _id})

        # 3) wc.j = true → 等待 journal sync(C 演示不可见 — 模拟延迟)
        # 这里不做延迟模拟

        # 4) 计算 ack 数:多数派
        target = wc["w"]
        voting = [m for m in self.members if m.priority > 0 and not m.hidden]
        # arbiter 参与投票(priority 默认 1);delayed 仍 ack 但有 secondaryDelaySecs 延迟
        n_voting = len(voting)
        majority = n_voting // 2 + 1

        if isinstance(target, str) and target == "majority":
            need = majority
        elif isinstance(target, int):
            need = target
        else:
            need = majority

        # 5) 让 secondary "拉" oplog 单条 — 实际是异步批量,这里简化
        for m in self.members:
            if m is self.primary or m.role == "ARBITER":
                continue
            m.apply_oplog_entry(entry)

        # 6) 计算 commit point
        commit = min(m.last_applied_ts for m in self.members if m.role != "ARBITER")
        acked = sum(1 for m in self.members if m.last_applied_ts >= entry.ts and m.role != "ARBITER")

        ok = acked >= need
        notes = f"acks={acked}/{need}, commit_point_ts={commit}, primary_ts={entry.ts}"
        return ok, notes, entry.ts

    # ------------------ 读路径 ------------------
    def read(self, coll: str, query: dict, rc: str, from_secondary: bool = False) -> Tuple[Optional[dict], str]:
        """readConcern 决策:
        local:             该节点最新
        available:         该节点最新(分片允许)
        majority:          oplog 已 commit 的最新
        linearizable:      仅 primary,且 majority 已 commit 才返回
        snapshot:          事务快照,读已 commit
        """
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
            # 节点必须 reached commit point 才能返回
            commit = min(m.last_applied_ts for m in self.members if m.role != "ARBITER")
            if target.last_applied_ts < commit:
                return None, f"节点未达 commit_point ({target.last_applied_ts} < {commit}),等待..."
            d = target.store.get(_id)
            return d, f"readConcern=majority from {target.name} (commit_point={commit})"

        if rc == "linearizable":
            if target is not self.primary:
                return None, "linearizable 仅可在 primary 上"
            commit = min(m.last_applied_ts for m in self.members if m.role != "ARBITER")
            # 简化:等所有进行中写 ≥ commit
            d = self.primary.store.get(_id)
            return d, f"readConcern=linearizable from primary (最强一致;会等并发写)"

        if rc == "snapshot":
            # 事务快照要求 readConcern 只有事务 + snapshot 才有意义
            d = target.store.get(_id)
            return d, f"readConcern=snapshot (事务内一致性;需 w=majority 提交)"

        return None, f"unknown readConcern {rc}"

    # ------------------ 默认 WC 计算(5.0+ 隐式公式) ------------------
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
        # 把 lost_writes 当作"旧 primary 上的写,未到达 secondaries"
        print(f"  \u2192 旧 primary 失败;secondaries 触发选举")
        secondaries = [m for m in self.members if m.role == "SECONDARY"]
        new_primary = max(secondaries, key=lambda m: (m.priority, m.last_applied_ts))
        new_primary.role = "PRIMARY"
        self.primary = new_primary
        print(f"  \u2192 新 primary: {new_primary.name}")

        # 旧 primary (此时已 down) 上的 lost_writes 现在回滚
        print(f"  \u2192 回滚 {len(lost_writes)} 条 lost writes:")
        for w in lost_writes:
            print(f"      \u21b3 _id={w.get('_id')} value={w.get('value')!r}")


# =====================================================================
# 演示
# =====================================================================

def demo_default_wc_formula():
    print("\n=== DEMO 1: 隐式默认 writeConcern 公式 (5.0+) ===")
    cases = [(3, 0, "P-S-S"), (3, 1, "P-S-A"), (5, 1, "P-S-S-S-A"), (2, 0, "P-S")]
    for v, a, label in cases:
        print(f"  {label}: voting={v} arbiters={a} \u2192 {ReplicaSet.compute_default_wc(v, a)}")


def demo_basic_write_with_wc():
    print("\n=== DEMO 2: 写 {w:1, j:true} vs {w:majority, j:true} ===")
    rs = ReplicaSet([
        Member("n-0", "PRIMARY"),
        Member("n-1", "SECONDARY"),
        Member("n-2", "SECONDARY"),
    ])
    ok, note, ts = rs.write("db.coll", {"_id": "doc-1", "value": "hello"}, {"w": 1, "j": True})
    print(f"  write({{w:1, j:true}}) \u2192 {'OK' if ok else 'FAIL'} | {note}")

    # 增大 w 演示多数派
    ok, note, ts = rs.write("db.coll", {"_id": "doc-2", "value": "world"}, {"w": "majority", "j": True})
    print(f"  write({{w:'majority', j:true}}) \u2192 {'OK' if ok else 'FAIL'} | {note}")

    # w=3 但只有 2 个数据节点 → 阻塞 / 超时
    ok, note, ts = rs.write("db.coll", {"_id": "doc-3", "value": "v"}, {"w": 3, "wtimeout": 100})
    print(f"  write({{w:3, wtimeout:100ms}}) \u2192 {'OK' if ok else 'FAIL'} | {note}")


def demo_read_concern_5_levels():
    print("\n=== DEMO 3: readConcern 5 级语义对比 ===")
    rs = ReplicaSet([
        Member("n-0", "PRIMARY"),
        Member("n-1", "SECONDARY"),
    ])
    rs.write("db.coll", {"_id": "x", "value": "before-failover"}, {"w": "majority"})

    for rc in ["local", "available", "majority", "linearizable", "snapshot"]:
        d, msg = rs.read("db.coll", {"_id": "x"}, rc)
        print(f"  readConcern={rc:13s} \u2192 doc={d} | {msg}")


def demo_rollback():
    print("\n=== DEMO 4: failover + 回滚 (旧 primary 未复制写) ===")
    rs = ReplicaSet([
        Member("n-0", "PRIMARY"),
        Member("n-1", "SECONDARY"),
        Member("n-2", "SECONDARY"),
    ])
    # 模拟主上"未复制"(lost_writes_on_old_primary)
    lost = [{"_id": "u-1", "value": "ghost-write-1"}, {"_id": "u-2", "value": "ghost-write-2"}]
    rs.simulate_failover_and_rollback(lost)
    print("  → 应用层使用 {w:majority} 可避免大部分回滚(只有 w:1 才可能)")


def demo_causal_session():
    print("\n=== DEMO 5: 因果一致性(Causal Consistency Session) ===")
    print("  客户端在同一个 session 内:")
    print("    wc = {w:'majority'}, rc = 'majority'")
    print("    驱动会自动维护 operationTime 并设置 afterClusterTime")
    print("  → 读己之写(read-your-own-writes)自动满足")
    print("  → 跨节点的 happens-before 关系被保留")


def main():
    demo_default_wc_formula()
    demo_basic_write_with_wc()
    demo_read_concern_5_levels()
    demo_rollback()
    demo_causal_session()
    print("\n" + "=" * 70)
    print("MongoDB 推荐配置")
    print("=" * 70)
    print("  • 默认 wc={w:'majority', j:true} — 防回滚")
    print("  • 默认 rc='majority' — 强一致读")
    print("  • 应用层在 causal session 包装写读")
    print("  • 避免 P-S-A 三节点 + majority 组合")
    print("  • 监控 oplog window: db.getReplicationInfo()")


if __name__ == "__main__":
    main()
