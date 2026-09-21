"""AOSP 框架层连接池模型。

对应 AOSP `platform_frameworks_base/core/java/android/database/sqlite/SQLiteConnectionPool.java`：
  - CONNECTION_POOL_BUSY_MILLIS = 30 * 1000
  - 主连接唯一（mAvailablePrimaryConnection），非主连接列表受 mMaxConnectionPoolSize 约束
  - releaseConnection 的三分支回收 / 关闭 / 报错
  - waitForConnection 按优先级插入等待队列
"""

CONNECTION_POOL_BUSY_MILLIS = 30 * 1000  # 30 seconds

CONNECTION_FLAG_PRIMARY_CONNECTION_AFFINITY = 0x00000004
CONNECTION_FLAG_READ_ONLY = 0x00000002
CONNECTION_FLAG_INTERACTIVE = 0x00000001


class PoolError(Exception):
    pass


class SQLiteConnection:
    def __init__(self, cid, is_primary=False):
        self.cid = cid
        self.is_primary = is_primary
        self.is_open = True
        self.released = False

    def reconfigure(self, config):
        self.config = config

    def close(self):
        self.is_open = False

    def __repr__(self):
        return "Conn#%d%s%s" % (self.cid, "(primary)" if self.is_primary else "",
                                "" if self.is_open else "(closed)")


class ConnectionWaiter:
    def __init__(self, thread, priority, want_primary, sql, start_time=0):
        self.thread = thread
        self.priority = priority
        self.want_primary = want_primary
        self.sql = sql
        self.start_time = start_time
        self.next = None


class SQLiteConnectionPool:
    def __init__(self, max_connection_pool_size=1, opener=None):
        self.max_connection_pool_size = max_connection_pool_size
        self.available_non_primary = []
        self.available_primary = None
        self.acquired = {}
        self.is_open = True
        self.next_id = 1
        self.waiter_queue = None
        self.closed_connections = 0
        self.opener = opener or (lambda cid, primary: SQLiteConnection(cid, primary))
        self.busy_logged = []

    # ---- 创建 ----
    def _open_connection(self, primary):
        conn = self.opener(self.next_id, primary)
        self.next_id += 1
        return conn

    # ---- 优先级（源码 getPriority）----
    @staticmethod
    def get_priority(flags):
        if flags & CONNECTION_FLAG_INTERACTIVE:
            return 1
        return 0

    # ---- 获取 ----
    def acquire_connection(self, sql="", connection_flags=0, thread="main", now=0):
        if not self.is_open:
            raise PoolError("Cannot perform this operation because the connection pool is closed")
        want_primary = bool(connection_flags & CONNECTION_FLAG_PRIMARY_CONNECTION_AFFINITY)
        conn = None
        if not want_primary:
            conn = self._try_acquire_non_primary()
        if conn is None:
            conn = self._try_acquire_primary()
        if conn is not None:
            return conn

        # 无可用连接：先看能不能新开一个
        total = len(self.acquired) + len(self.available_non_primary) + (
            1 if self.available_primary else 0)
        if not want_primary and total < self.max_connection_pool_size + 1:
            conn = self._open_connection(False)
            self.acquired[conn] = "NORMAL"
            return conn

        # 实在没有：按优先级入队
        waiter = ConnectionWaiter(thread, self.get_priority(connection_flags), want_primary,
                                  sql, now)
        self._enqueue_waiter(waiter)
        return None

    def _try_acquire_non_primary(self):
        if self.available_non_primary:
            conn = self.available_non_primary.pop()
            self.acquired[conn] = "NORMAL"
            return conn
        return None

    def _try_acquire_primary(self):
        if self.available_primary is not None:
            conn = self.available_primary
            self.available_primary = None  # 主连接唯一
            self.acquired[conn] = "NORMAL"
            return conn
        if not self.acquired and not self.available_non_primary:
            conn = self._open_connection(True)
            self.acquired[conn] = "NORMAL"
            return conn
        return None

    def _enqueue_waiter(self, waiter):
        predecessor = None
        successor = self.waiter_queue
        while successor is not None:
            if waiter.priority > successor.priority:
                waiter.next = successor
                break
            predecessor = successor
            successor = successor.next
        if predecessor is not None:
            predecessor.next = waiter
        else:
            self.waiter_queue = waiter

    # ---- 释放 ----
    def release_connection(self, conn, status="NORMAL"):
        if self.acquired.pop(conn, None) is None:
            raise PoolError(
                "Cannot perform this operation because the specified connection was not acquired "
                "from this pool or has already been released."
            )
        if not self.is_open:
            conn.close()
            return
        if conn.is_primary:
            if self._recycle(conn, status):
                assert self.available_primary is None, "主连接唯一，回收前必须为空"
                self.available_primary = conn
        elif len(self.available_non_primary) >= self.max_connection_pool_size:
            conn.close()
            self.closed_connections += 1
        else:
            if self._recycle(conn, status):
                self.available_non_primary.append(conn)
        self._wake_waiters()

    def _recycle(self, conn, status):
        if status == "RECONFIGURE":
            try:
                conn.reconfigure({"pool": self})
            except RuntimeError:
                status = "DISCARD"
        if status == "DISCARD":
            conn.close()
            self.closed_connections += 1
            return False
        return True

    def _wake_waiters(self):
        """把等待队列里能被满足的 waiter 摘下来（按优先级，从队首开始）。"""
        predecessor = None
        waiter = self.waiter_queue
        while waiter is not None:
            # 与 waitForConnection 同序：先试非主，再回退到主连接
            conn = self._try_acquire_non_primary() if not waiter.want_primary else None
            if conn is None:
                conn = self._try_acquire_primary()
            if conn is None:
                predecessor = waiter
                waiter = waiter.next
                continue
            self.acquired[conn] = "NORMAL"
            if predecessor is None:
                self.waiter_queue = waiter.next
            else:
                predecessor.next = waiter.next
            waiter = waiter.next

    def should_yield_connection(self, now):
        """源码：等待超过 CONNECTION_POOL_BUSY_MILLIS 就打日志并解锁，而不是抛异常。"""
        if self.waiter_queue is None:
            return False
        waited = now - self.waiter_queue.start_time
        if waited >= CONNECTION_POOL_BUSY_MILLIS:
            self.busy_logged.append(waited)
            return True
        return False

    def close(self):
        """只关空闲连接；仍被持有的连接等 release 时再关（源码注释明确允许 release 后于 close）。"""
        self.is_open = False
        for conn in self.available_non_primary:
            conn.close()
        if self.available_primary:
            self.available_primary.close()
        self.available_non_primary.clear()
        self.available_primary = None
