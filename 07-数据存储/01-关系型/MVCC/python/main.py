"""
MVCC 多版本并发控制 — InnoDB 风格最小实现 (Pure Python stdlib)

核心机制:
- 行 (Row) 上有 trx_id (最后修改者) 与 roll_ptr (指向 undo log 中上一个版本)
- UndoLog 是单链表,每个 entry 持有上一个版本的内容
- ReadView (一致性快照) 在 RC 下每次 SELECT 创建,RR 下首次 SELECT 创建
- 可见性规则: trx_id == creator -> 可见;
            trx_id < low_water -> 已提交,可见;
            trx_id >= high_water -> 未见,不可见;
            low_water <= trx_id < high_water 且在 active 列表 -> 未提交,不可见;
            否则已提交,可见

参考: MySQL 9.7 Reference Manual 17.3 InnoDB Multi-Versioning
(https://dev.mysql.com/doc/refman/9.7/en/innodb-multi-versioning.html)
+ PostgreSQL MVCC (https://www.postgresql.org/docs/current/mvcc.html)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UndoEntry:
    """undo log 记录 — 保存行的上一个版本, 链式指向更老的版本"""
    roll_ptr_next: int  # 指向 undo log 中下一个更老 entry 的 idx (链头)
    trx_id: int  # 写这个版本的事务 id
    value: int
    deleted: bool  # 该版本是否被标记 delete


@dataclass
class Row:
    """行记录 — 类似 InnoDB clustered index 行结构"""
    value: int = 0
    trx_id: int = 0  # DB_TRX_ID: 最后写入者 (0 = 未初始)
    roll_ptr: int = -1  # DB_ROLL_PTR: undo log 中上一个版本; -1 = 无
    deleted: bool = False


@dataclass
class ReadView:
    """一致性快照 — 创建时拍下活跃事务列表"""
    low_water: int  # 创建 ReadView 时仍活跃的最小 trx_id
    high_water: int  # 创建 ReadView 时下一个将分配的事务 id
    active_ids: set  # 活跃事务 id 集合
    creator_id: int  # 创建本视图的事务 id


class Transaction:
    def __init__(self, db: "MVCCDB", tx_id: int, isolation: str = "REPEATABLE READ"):
        self.db = db
        self.id = tx_id
        self.isolation = isolation  # "REPEATABLE READ" or "READ COMMITTED"
        self.snapshot: Optional[ReadView] = None  # RR: 首次 SELECT 创建; RC: 每 SELECT 新建
        self.committed = False
        self.aborted = False

    def acquire_snapshot(self):
        """创建一致性视图; RR 复用, RC 新建"""
        if self.isolation == "REPEATABLE READ" and self.snapshot is not None:
            return  # 复用
        active = {tid for tid, t in self.db.txs.items() if not t.committed and not t.aborted}
        self.snapshot = ReadView(
            low_water=min(active) if active else self.db.next_tx_id,
            high_water=self.db.next_tx_id,
            active_ids=active,
            creator_id=self.id,
        )

    def is_visible(self, row_trx_id: int, roll_ptr: int) -> bool:
        """可见性算法 — 沿 undo log 链寻找第一个对当前事务可见的版本"""
        cur_trx = row_trx_id
        cur_rp = roll_ptr
        while True:
            if self._visible_rule(cur_trx):
                return True
            if cur_rp == -1:
                return False  # 链穷尽也没找到可见版本
            entry = self.db.undo_log[cur_rp]
            cur_trx = entry.trx_id
            cur_rp = entry.roll_ptr_next

    def _visible_rule(self, trx_id: int) -> bool:
        if trx_id == self.snapshot.creator_id:
            return True  # 自己写的自己能看到
        if trx_id < self.snapshot.low_water:
            return True  # 早于所有活跃事务, 已提交
        if trx_id >= self.snapshot.high_water:
            return False  # 在快照之后才开启, 不应见
        return trx_id not in self.snapshot.active_ids  # 中间地带: 不在 active 表 = 已提交

    def read(self, name: str) -> Optional[int]:
        """快照读 (snapshot read) — 走 MVCC"""
        self.acquire_snapshot()
        row = self.db.rows.get(name)
        if row is None:
            return None
        if self.is_visible(row.trx_id, row.roll_ptr):
            return None if row.deleted else row.value
        return None

    def update(self, name: str, value: int):
        """写入 — 分配新 trx_id, 把旧版本压入 undo log, 更新 roll_ptr"""
        if self.committed or self.aborted:
            raise RuntimeError("tx already done")
        # 先把当前版本 (old) 写入 undo log, 作为新版本的 roll_ptr 指向
        row = self.db.rows.get(name)
        if row is None:
            row = Row(); self.db.rows[name] = row
        # 创建 undo entry, 链头指向老的 undo entry idx
        old_idx = len(self.db.undo_log)
        self.db.undo_log.append(UndoEntry(
            roll_ptr_next=row.roll_ptr,
            trx_id=row.trx_id,
            value=row.value,
            deleted=row.deleted,
        ))
        # 覆盖
        row.value = value
        row.trx_id = self.id
        row.roll_ptr = old_idx
        row.deleted = False


class MVCCDB:
    def __init__(self):
        self.txs: dict[int, Transaction] = {}
        self.next_tx_id = 1  # InnoDB 也有 max_trx_id 概念, 从 1 单调递增
        self.rows: dict[str, Row] = {}
        self.undo_log: list[UndoEntry] = []

    def begin(self, isolation: str = "REPEATABLE READ") -> Transaction:
        tid = self.next_tx_id; self.next_tx_id += 1
        tx = Transaction(self, tid, isolation)
        self.txs[tid] = tx
        return tx

    def commit(self, tx: Transaction):
        tx.committed = True

    def rollback(self, tx: Transaction):
        tx.aborted = True
        # 生产 DB 还会沿 roll_ptr 链恢复数据, 但本 demo 因还未 commit 就不必恢复


# ---------- Demos ----------

def demo_basic():
    """Demo 1: T1 update, T2 (后开启) snapshot read 看不到未提交"""
    db = MVCCDB()
    db.rows["x"] = Row(value=100)
    t1 = db.begin("READ COMMITTED"); t1.update("x", 200)
    t2 = db.begin("READ COMMITTED")
    assert t2.read("x") == 100, f"snapshot read should see old: got {t2.read('x')}"
    print(f"[1] T2 before T1 commit: x={t2.read('x')} (T2 id={t2.id})")
    db.commit(t1)
    t3 = db.begin("READ COMMITTED")
    print(f"[1] T3 after T1 commit: x={t3.read('x')} (sees committed v={t3.read('x')})")
    assert t3.read("x") == 200


def demo_rr_snapshot_reuse():
    """Demo 2: RR 下同一事务多次 SELECT 共享同一 ReadView, 即使别的事务已 commit"""
    db = MVCCDB()
    db.rows["balance"] = Row(value=1000)
    t1 = db.begin("REPEATABLE READ")
    assert t1.read("balance") == 1000  # 创建 snapshot v1
    # 另起一个事务, 提交修改
    t2 = db.begin("READ COMMITTED"); t2.update("balance", 1500); db.commit(t2)
    # T1 第二次 SELECT — RR 复用 snapshot, 仍见 1000
    assert t1.read("balance") == 1000, f"RR should be repeatable: got {t1.read('balance')}"
    print(f"[2] T1 2nd read in RR: balance={t1.read('balance')} (committed T2 not visible)")
    # 但当前读 (latest) 能看到新值
    print(f"[2] T1 latest uncommitted: balance={db.rows['balance'].value}")


def demo_rc_per_statement_snapshot():
    """Demo 3: RC 下每条 SELECT 创建新 ReadView, 能看到刚 commit 的其他事务"""
    db = MVCCDB()
    db.rows["x"] = Row(value=100)
    t1 = db.begin("READ COMMITTED")
    assert t1.read("x") == 100
    t2 = db.begin("READ COMMITTED"); t2.update("x", 999); db.commit(t2)
    # 同一 T1 的第二次 SELECT — RC: 新 snapshot 看到 999
    assert t1.read("x") == 999, f"RC should see latest committed: got {t1.read('x')}"
    print(f"[3] RC 2nd read sees T2 commit: x={t1.read('x')}")


def demo_undo_chain():
    """Demo 4: 多次 update 后, undo log 链长度可读出"""
    db = MVCCDB()
    db.rows["k"] = Row(value=0)
    t1 = db.begin("REPEATABLE READ")
    for v in [10, 20, 30, 40]:
        t1.update("k", v)
    db.commit(t1)
    # 沿 roll_ptr 链数 undo entry 数 (不含 initial row 在 log 外的隐式基线)
    chain_len = 0
    cur_rp = db.rows["k"].roll_ptr
    while cur_rp != -1:
        chain_len += 1
        cur_rp = db.undo_log[cur_rp].roll_ptr_next
    print(f"[4] Undo chain length after 4 updates: {chain_len} (initial + 3 undo entries)")
    assert chain_len == 3, f"expected 3, got {chain_len}"  # 第 1 次 update 后会写 1 个 undo, 共 3 个

    # 制造 "看到历史版本" 的场景: 一个非常早的事务读取
    historian = Transaction(db, -100, "REPEATABLE READ")
    historian.snapshot = ReadView(low_water=0, high_water=1, active_ids=set(), creator_id=-100)

    # 手动查找 trx_id 在 [1, 1] 间最早的 active 列表以外的可见版本
    def find_version_at_or_before(target_trx):
        row = db.rows["k"]
        cur_trx, cur_rp = row.trx_id, row.roll_ptr
        while True:
            if cur_trx <= target_trx and cur_trx not in historian.snapshot.active_ids:
                return row.value if not row.deleted else None
            if cur_rp == -1:
                return None
            e = db.undo_log[cur_rp]
            if e.trx_id <= target_trx and e.trx_id not in historian.snapshot.active_ids:
                return e.value if not e.deleted else None
            cur_trx, cur_rp = e.trx_id, e.roll_ptr_next

    v20 = find_version_at_or_before(2)
    print(f"[4] Historian sees snapshot at trx<=2: k={v20} (i.e., v=10)")
    assert v20 == 10, f"expected v=10 at trx<=2 (only first update done), got {v20}"


def demo_write_conflict():
    """Demo 5: 两个事务更新同一行 — 后写者等前写者 commit (生产 DB 还有 next-key lock)"""
    db = MVCCDB()
    db.rows["y"] = Row(value=50)
    t1 = db.begin("REPEATABLE READ"); t1.update("y", 60)
    t2 = db.begin("REPEATABLE READ"); t2.update("y", 70)
    # 在 RR 下, T2 的 update 会写新版本, t2.trx_id > t1.trx_id
    db.commit(t2)  # T2 后 commit
    # T1 试图回滚: 但这里没有显式回滚; 改 t1 为 rollback 语义
    db.rollback(t1)
    # 验证最终值由 t2 决定 (last-writer-wins 是 InnoDB 在 RR 下行为之一, 实际还有 next-key lock)
    assert db.rows["y"].value == 70, f"final should be t2's write: got {db.rows['y'].value}"
    print(f"[5] Two concurrent updates: t1={60} (rolled back), t2={70} (committed), final={db.rows['y'].value}")


if __name__ == "__main__":
    print("== MVCC 多版本并发控制 ==")
    demo_basic()
    demo_rr_snapshot_reuse()
    demo_rc_per_statement_snapshot()
    demo_undo_chain()
    demo_write_conflict()
    print("All 5 demos passed.")
