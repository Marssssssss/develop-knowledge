"""
两阶段锁 (2PL) 与隔离级别 — 最小实现 (Pure Python stdlib)

核心机制:
- 共享锁 S-lock (共享,多 reader 共存, 排他写)、排他锁 X-lock (独占,阻止其他任何锁)
- 两个阶段: Growing (只能获取锁, 不能释放) + Shrinking (只能释放锁, 不能获取)
- 兼容性矩阵:
        | holder requests
  held  |   S    X
   none |  OK    OK
   S    |  OK    --(blocked)
   X    |  --    --

- 死锁检测: waits-for 图, DFS 找环, 检测到环则选 youngest victim abort
- Strict 2PL (S2PL): 所有 X-lock 持有到 commit/abort 才释放 (避免 cascading abort)
- 4 隔离级别异常矩阵:
  - Dirty Read (脏读): 未 commit 的写被读到
  - Non-repeatable Read (不可重复读): 同一行读两次结果不同
  - Phantom Read (幻读): 同一范围读两次行集不同

参考:
- CMU 15-445 L16 Two-Phase Locking PDF
  https://15445.courses.cs.cmu.edu/spring2023/notes/16-twophaselocking.pdf
- Wikipedia: Two-phase locking (snapshot 引用)
- Aerospike Blog "Serializable transactions and the price of getting concurrency right"
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LockRequest:
    """等待某资源的某个事务的请求"""
    tx_id: int
    mode: str  # 'S' or 'X'
    granted: bool = False


@dataclass
class Lock:
    """某资源上的锁状态 (multiple granted + waiting queue)"""
    granted_x: Optional[int] = None  # 持 X 锁的 tx (单一)
    granted_s: list = field(default_factory=list)  # 持 S 锁的 tx 列表
    waiting: list = field(default_factory=list)  # 等待请求队列 (LockRequest)


class TwoPhaseLockDB:
    def __init__(self):
        self.next_tx_id = 1
        self.txs: dict[int, dict] = {}  # tx_id -> {state, locks held, mode (growing/shrinking)}
        self.locks: dict[str, Lock] = {}
        # 等待图: 记录谁在等谁持有的锁, 用于死锁检测
        self.waits_for: dict[int, set] = {}  # tx_id -> set of txs it waits for

    def begin(self) -> int:
        tx_id = self.next_tx_id; self.next_tx_id += 1
        self.txs[tx_id] = {'state': 'A', 'locks': [], 'phase': 'growing'}
        return tx_id

    def _add_wait_edge(self, waiter: int, holder: int):
        self.waits_for.setdefault(waiter, set()).add(holder)

    def _remove_wait_edges_from(self, waiter: int):
        self.waits_for.pop(waiter, None)

    def _has_cycle(self) -> Optional[list]:
        """DFS 找环, 返回环路径或 None"""
        visited = set()
        def dfs(node, path):
            if node in path:
                return path[path.index(node):] + [node]
            if node in visited:
                return None
            visited.add(node)
            for nxt in self.waits_for.get(node, ()):
                res = dfs(nxt, path + [node])
                if res: return res
            return None
        for start in list(self.waits_for.keys()):
            r = dfs(start, [])
            if r: return r
        return None

    def lock(self, tx_id: int, res: str, mode: str, raise_deadlock: bool = True):
        """申请 S 或 X 锁。返回是否 granted, 失败时设 waits_for 边"""
        if self.txs[tx_id]['phase'] != 'growing':
            raise RuntimeError("cannot acquire lock during shrinking phase")
        lock = self.locks.setdefault(res, Lock())
        # 检查现有 holder 能否兼容
        # S 模式: 已有 X -> 必须等;  已有 S 锁 + 还有人在等 X -> 必须等 (避免饿)
        # X 模式: 任何持锁 -> 必须等
        can_grant = False
        if mode == 'S':
            if lock.granted_x is None and not any(not w.granted for w in lock.waiting if w.mode == 'X'):
                can_grant = True
        else:  # 'X'
            if lock.granted_x is None and len(lock.granted_s) == 0 and not lock.waiting:
                can_grant = True

        if can_grant:
            req = LockRequest(tx_id=tx_id, mode=mode, granted=True)
            lock.waiting.append(req)
            if mode == 'S':
                lock.granted_s.append(tx_id)
            else:
                lock.granted_x = tx_id
            self.txs[tx_id]['locks'].append((res, mode))
            return True

        # 阻塞: 把请求挂到队列
        req = LockRequest(tx_id=tx_id, mode=mode, granted=False)
        lock.waiting.append(req)
        # 决定谁导致阻塞: X 等待等所有 S + X holders; S 等待等 X holder (若有)
        blockers = []
        if mode == 'S':
            if lock.granted_x is not None:
                blockers.append(lock.granted_x)
        else:  # 'X'
            blockers.append(lock.granted_x) if lock.granted_x is not None else None
            blockers.extend(lock.granted_s)
        for b in blockers:
            if b is not None and b != tx_id:
                self._add_wait_edge(tx_id, b)
        # 死锁检测
        cycle = self._has_cycle()
        if cycle:
            # 选 youngest victim 中止 (假定 wait_id 最大者为最新)
            victim = max(c for c in cycle if c != cycle[-1])
            self.waits_for = {k: v for k, v in self.waits_for.items() if k != victim}
            for lk in self.locks.values():
                lk.waiting = [w for w in lk.waiting if w.tx_id != victim]
            if raise_deadlock:
                raise DeadlockError(cycle, victim)
            return False
        return False

    def unlock_all(self, tx_id: int):
        """事务结束: 进入 shrinking phase, 释放所有锁"""
        if self.txs[tx_id]['phase'] == 'growing':
            self.txs[tx_id]['phase'] = 'shrinking'
        for (res, mode) in self.txs[tx_id]['locks']:
            self._release(tx_id, res, mode)
        self.txs[tx_id]['locks'] = []
        self.txs[tx_id]['state'] = 'C'

    def _release(self, tx_id: int, res: str, mode: str):
        lock = self.locks.get(res)
        if lock is None: return
        # 从 granted 移除
        if mode == 'S' and tx_id in lock.granted_s:
            lock.granted_s.remove(tx_id)
        elif mode == 'X' and lock.granted_x == tx_id:
            lock.granted_x = None
        # 从 waiting 中移除
        lock.waiting = [w for w in lock.waiting if w.tx_id != tx_id or w.granted]
        # 唤醒后续 wait 列表中能 grant 的; 简化: 把同 key 的 S-mode grants 全部 satisfy
        # 重新评估队列
        for w in lock.waiting[:]:
            if w.granted:
                continue
            can = False
            if w.mode == 'S':
                if lock.granted_x is None and not any(not ww.granted and ww.mode == 'X' for ww in lock.waiting):
                    can = True
            else:
                if lock.granted_x is None and not lock.granted_s and not lock.waiting:
                    can = True
            if can:
                w.granted = True
                if w.mode == 'S':
                    lock.granted_s.append(w.tx_id)
                else:
                    lock.granted_x = w.tx_id
                # 唤醒后从 waits_for 移除该等待者的边
                self._remove_wait_edges_from(w.tx_id)

    def abort(self, tx_id: int):
        self.unlock_all(tx_id)
        self.txs[tx_id]['state'] = 'A'


class DeadlockError(Exception):
    def __init__(self, cycle, victim):
        self.cycle = cycle; self.victim = victim
        super().__init__(f"deadlock victim={victim}, cycle={cycle}")


# ---------- Demos ----------

def demo_basic_xlock():
    """Demo 1: X 锁互斥 — T1 取 X(A) 后 T2 取 X(A) 必须阻塞"""
    db = TwoPhaseLockDB()
    t1 = db.begin(); t2 = db.begin()
    assert db.lock(t1, 'A', 'X'), "T1 X(A) granted"
    got = db.lock(t2, 'A', 'X', raise_deadlock=False)
    assert not got, "T2 X(A) must block"
    print(f"[1] T1 X(A) granted; T2 X(A) blocked = {not got}")
    db.unlock_all(t1)
    # T1 release 后唤醒 T2
    print(f"[1] T1 commit; after release, T2 X(A) blockers should be empty")


def demo_shared_compat():
    """Demo 2: S 锁共享 — T1+T2 都可获 S(A); 但 T3 想 X(A) 必须等"""
    db = TwoPhaseLockDB()
    t1 = db.begin(); t2 = db.begin(); t3 = db.begin()
    assert db.lock(t1, 'A', 'S')
    assert db.lock(t2, 'A', 'S'), "S-S compatible"
    got = db.lock(t3, 'A', 'X', raise_deadlock=False)
    assert not got, "T3 X(A) blocked while 2 S holders"
    print(f"[2] S-S 共存; T3 X 被 block ({not got})")
    db.unlock_all(t1)
    db.unlock_all(t2)
    assert db.lock(t3, 'A', 'X'), "T3 X(A) finally granted"


def demo_strict_2pl():
    """Demo 3: Strict 2PL — X 锁持有到 commit 才释放, 防止 cascading abort"""
    db = TwoPhaseLockDB()
    t1 = db.begin()
    db.lock(t1, 'A', 'X')  # 进入 growing phase
    # 在 commit 前查询: lock 还在
    state = db.txs[t1]
    assert ('A', 'X') in state['locks'], "X lock held"
    print(f"[3] T1 phase={state['phase']}, locks={state['locks']}")
    db.unlock_all(t1)
    print(f"[3] commit 后 phase={state['phase']}, locks={state['locks']}")


def demo_deadlock_and_abort():
    """Demo 4: T1↔T2 互相等对方的 X, 死锁检测后 victim abort"""
    db = TwoPhaseLockDB()
    t1 = db.begin(); t2 = db.begin()
    # T1 X(A), T2 X(B), 各试图取对方的 resource → cycle
    db.lock(t1, 'A', 'X')
    db.lock(t2, 'B', 'X')
    db.lock(t1, 'B', 'X', raise_deadlock=False)  # T1 waits for T2
    db.lock(t2, 'A', 'X', raise_deadlock=False)  # T2 waits for T1 → cycle
    # 检测 cycle 应得到 [t1, t2, t1] 之类的环
    cycle = db._has_cycle()
    print(f"[4] 死锁检测结果 cycle={cycle}")
    assert cycle is not None
    # 选 victim: max(t1, t2) = t2 (假定 t2 后加入)
    db.abort(max(t1, t2))
    print(f"[4] Victim aborted. After abort locks released.")


def demo_isolation_levels():
    """Demo 5: 隔离级别异常演示 — 不同策略与可见性组合"""
    # 用更抽象的方式表达: 不实现完整 SQ 引擎, 只展示规则
    print("[5] 隔离级别异常矩阵 (SQL 标准 vs 生产实现):")
    print("    Level          | Dirty | Non-Repeat | Phantom")
    print("    READ UNCOMMITTED| ✓ possible | ✓ possible | ✓ possible")
    print("    READ COMMITTED  | ✗       | ✓ possible | ✓ possible")
    print("    REPEATABLE READ | ✗       | ✗          | ✓ possible (SQL std) / ✗ (MySQL)")
    print("    SERIALIZABLE    | ✗       | ✗          | ✗")
    # 注意: PostgreSQL 在 RR 下也防 phantom, 比标准严 (超出 SQL 标准)
    print("    注: PostgreSQL RR 还防 phantom (超出 SQL 标准); MySQL RR 用 next-key lock 防 phantom")


if __name__ == "__main__":
    print("== 两阶段锁 2PL 与隔离级别 ==")
    demo_basic_xlock()
    demo_shared_compat()
    demo_strict_2pl()
    demo_deadlock_and_abort()
    demo_isolation_levels()
    print("All 5 demos OK.")
