# -*- coding: utf-8 -*-
"""Neo4j 并发控制最小实现: 读已提交隔离 / 丢失更新 / 锁管理器 + 死锁检测.

依据 Neo4j Operations Manual "Database internals -> Concurrent data access":
1. 默认隔离级别 READ_COMMITTED: 读不加锁, 已提交即可见 -> 丢失更新可发生.
2. SET 右侧直接依赖被读属性时 Cypher 自动加写锁; 否则须用哑属性技巧手工加锁.
3. 锁的粒度: 实体级(节点/关系). 创建/删除关系锁"该关系 + 两端节点";
   更新属性锁"该实体". 锁持有到事务结束, 回滚立即释放.
4. 死锁: 等待图(wait-for graph)出现环 => 检测到的事务被终止
   (TransientError.Transaction.DeadlockDetected), 其余事务继续.
"""
import threading


class DeadlockError(RuntimeError):
    pass


class LockManager:
    """实体级锁管理器 + 等待图死锁检测.

    关键语义: 阻塞的事务在等待图中保留边(tx -> entity), 直到拿到锁
    或被死锁检测终止 —— 否则环永远不会闭合.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._holders = {}      # entity -> set(txid)   (写锁互斥, 独占)
        self._waiting = {}      # txid -> entity        等待图边: tx 在等谁
        self._tx_entities = {}  # txid -> set(entity)   事务已持有的实体

    def acquire(self, txid, entity):
        """获取 entity 写锁. 成功 True; 阻塞 False(等待边已登记); 死锁抛异常."""
        with self._lock:
            holders = self._holders.setdefault(entity, set())
            if not holders or holders == {txid}:
                holders.add(txid)                        # 拿到锁, 等待边消失
                self._tx_entities.setdefault(txid, set()).add(entity)
                self._waiting.pop(txid, None)
                return True
            # 拿不到: 登记等待边(保留), 再检查等待图是否成环
            self._waiting[txid] = entity
            if self._find_cycle(txid):
                self._waiting.pop(txid, None)            # 死锁受害者放弃等待
                raise DeadlockError(f"tx {txid} -> {entity}")
            return False                                 # 调用方应阻塞/重试

    def release_all(self, txid):
        """事务结束(提交或回滚)释放全部锁."""
        with self._lock:
            for entity in self._tx_entities.pop(txid, set()):
                self._holders.get(entity, set()).discard(txid)
            self._waiting.pop(txid, None)

    def _find_cycle(self, start_tx):
        """等待图投影: 等待资源的 tx -> 持有该资源的 tx; 找回到起点的环."""
        stack, seen = [start_tx], {start_tx}
        while stack:
            tx = stack.pop()
            entity = self._waiting.get(tx)
            if entity is None:
                continue
            for holder in self._holders.get(entity, set()):
                if holder == start_tx:
                    return True                           # 回到起点: 成环
                if holder not in seen:
                    seen.add(holder)
                    stack.append(holder)
        return False


class Store:
    """read-committed 存储: 读不加锁(返回最后已提交值), 写须先拿实体写锁."""

    def __init__(self):
        self.data = {}
        self.locks = LockManager()

    def read(self, key):
        return self.data.get(key, 0)                      # 读不加锁


def lost_update_unprotected(store, n_threads=100):
    """无写锁的丢失更新(官方场景): 并发 +1, 最终值远小于 n_threads."""
    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()                                    # 全部先读完
        v = store.read("counter")                         # 各自读到同一旧值
        store.data["counter"] = v + 1                     # 写回(绕过锁, 模拟无锁写)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return store.data["counter"]


def lost_update_protected(store, n_threads=100):
    """官方规则: SET n.prop = n.prop + 1 右侧直接依赖被读属性 -> 自动写锁.

    等价实现: 读之前先 acquire 实体写锁(读写临界区串行化).
    """
    barrier = threading.Barrier(n_threads)

    def worker():
        txid = f"tx-{threading.get_ident()}"
        barrier.wait()
        while True:                                       # 阻塞重试直到拿到锁
            if store.locks.acquire(txid, "counter"):
                v = store.read("counter")
                store.data["counter"] = v + 1
                store.locks.release_all(txid)             # 事务结束释放
                return

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return store.data["counter"]


def cross_order_deadlock():
    """交叉顺序加锁: T1 持 A 等 B, T2 持 B 等 A -> 等待图成环."""
    locks = LockManager()
    locks.acquire("T1", "A")
    locks.acquire("T2", "B")
    try:
        locks.acquire("T1", "B")   # T1 等待 B(等待边保留)
        locks.acquire("T2", "A")   # T2 等待 A -> 环闭合 -> 死锁检测触发
        return "ok"
    except DeadlockError:
        return "deadlock"


def same_order_no_deadlock():
    """官方建议: 固定加锁顺序(先 A 后 B) -> 等待图无环."""
    locks = LockManager()
    locks.acquire("T1", "A")
    blocked = locks.acquire("T2", "A") is False            # T2 只等 A, 不碰 B
    locks.acquire("T1", "B")                               # T1 按序拿完
    locks.release_all("T1")                                # T1 提交, 全部释放
    locks.acquire("T2", "A")                               # T2 重试成功
    locks.acquire("T2", "B")
    return "ok" if blocked else "not-blocked"


def demo():
    print("== 1. 读已提交: 读不加锁 -> 丢失更新 ==")
    s = Store()
    s.data["counter"] = 0
    result = lost_update_unprotected(s, 100)
    print(f"  100 个并发 +1 无保护: 最终值 = {result} (官方: 最坏低至 1)")

    print("\n== 2. 写锁保护(SET 直接依赖被读属性 -> 自动加锁) ==")
    s2 = Store()
    s2.data["counter"] = 0
    result2 = lost_update_protected(s2, 100)
    print(f"  100 个并发 +1 有写锁: 最终值 = {result2} (确定性 = 100)")

    print("\n== 3. 哑属性技巧: 无直接依赖时手工加锁 ==")
    print("  MATCH (n:Example {id:42}) SET n.dummy=true REMOVE n.dummy")
    print("  -- 官方 workaround: 先写哑属性强制拿写锁, 再读 n.prop 计算新值")

    print("\n== 4. 死锁: 交叉 vs 相同加锁顺序 ==")
    print(f"  T1(A→B) 与 T2(B→A) 交叉: {cross_order_deadlock()} (等待图成环, 终止其一)")
    print(f"  固定顺序(先 A 后 B): {same_order_no_deadlock()} (官方建议防死锁)")

    print("\n== 5. 锁粒度(官方锁获取表) ==")
    table = [
        ("创建/删除节点", "该节点写锁"),
        ("创建/删除关系", "该关系 + 两端节点写锁"),
        ("更新属性", "该节点/关系写锁"),
        ("更新标签", "该节点写锁"),
        ("密集节点(≥50 关系)", "共享度锁代替独占锁, 提交期才取精确排他锁"),
    ]
    for op, lock in table:
        print(f"  {op:18s} -> {lock}")


if __name__ == "__main__":
    demo()
