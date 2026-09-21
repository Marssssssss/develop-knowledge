"""Room 连接管理模型自检：判据来自 androidx room3 源码与 AOSP SQLiteConnectionPool。"""

from main import (
    SingleConnection, MultipleConnection, PoolConfigError, RoomConfigError, SQLiteException,
    choose_pool, FakeDriver, RoomDatabase, framework_pool_size,
    WAL_DEFAULT_NUMBER_OF_READERS, WAL_DEFAULT_NUMBER_OF_WRITERS,
    JOURNAL_MODE_WAL, JOURNAL_MODE_TRUNCATE,
)
from sqlite_pool import (
    SQLiteConnectionPool, SQLiteConnection, PoolError, CONNECTION_POOL_BUSY_MILLIS,
    CONNECTION_FLAG_PRIMARY_CONNECTION_AFFINITY, CONNECTION_FLAG_INTERACTIVE,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, label
    PASS += 1


def raises(exc, fn, label, needle=None):
    global PASS
    try:
        fn()
    except exc as e:
        if needle is not None:
            assert needle in str(e), "%s: 消息里没有 %r，实得 %r" % (label, needle, str(e))
        PASS += 1
    except Exception as e:  # noqa: BLE001
        raise AssertionError("%s: 期望 %s，实得 %s(%s)" % (label, exc.__name__, type(e).__name__, e))
    else:
        raise AssertionError("%s: 期望抛 %s，但没有抛" % (label, exc.__name__))


# ---- 1. ConnectionPoolConfiguration 的 require ----
raises(PoolConfigError, lambda: MultipleConnection(0, 1), "readers=0", "Number of readers must be greater than 0")
raises(PoolConfigError, lambda: MultipleConnection(1, 0), "writers=0", "Number of writers must be greater than 0")
raises(PoolConfigError, lambda: MultipleConnection(-1, 1), "readers 为负")
ok((WAL_DEFAULT_NUMBER_OF_READERS, WAL_DEFAULT_NUMBER_OF_WRITERS) == (4, 1),
   "WAL 默认 4 读 1 写（源码常量）")
ok(MultipleConnection(2, 3).num_of_writers == 3, "合法构造")

# ---- 2. 连接池选型四路分支 ----
ok(choose_pool(True, "app.db", MultipleConnection(4, 1)) == "PassthroughConnectionPool",
   "driver 自带池 → Passthrough（Android 框架驱动）")
ok(choose_pool(False, None, MultipleConnection(4, 1)) == "newSingleConnectionPool",
   "内存库强制单连接（否则每个连接各是一个库）")
ok(choose_pool(False, "app.db", SingleConnection()) == "newSingleConnectionPool", "SingleConnection")
ok(choose_pool(False, "app.db", MultipleConnection(4, 1)) == "newConnectionPool", "MultipleConnection")

# ---- 3. configureDatabase 的 PRAGMA 顺序（WAL）----
d = FakeDriver(user_version=0)
RoomDatabase(d, journal_mode=JOURNAL_MODE_WAL, target_version=3).open()
ok(d.log[0] == "PRAGMA busy_timeout = 2500", "首条是 busy_timeout")
ok(d.log[1] == "PRAGMA journal_mode = WAL", "WAL 下 journal_mode = WAL")
ok(d.log[2] == "PRAGMA synchronous = NORMAL", "WAL 下 synchronous = NORMAL")
ok("BEGIN EXCLUSIVE TRANSACTION" in d.log, "版本不一致时开排他事务")
ok("END TRANSACTION" in d.log, "迁移成功提交 END TRANSACTION")
ok("PRAGMA user_version = 3" in d.log, "写回目标版本")
ok(d.user_version == 3, "PRAGMA user_version 生效")

# ---- 4. 非 WAL：TRUNCATE + FULL ----
d2 = FakeDriver(user_version=1)
RoomDatabase(d2, journal_mode=JOURNAL_MODE_TRUNCATE, target_version=1).open()
ok("PRAGMA journal_mode = TRUNCATE" in d2.log,
   "非 WAL 时 journal_mode 是 TRUNCATE（不是 DELETE，源码如此）")
ok("PRAGMA synchronous = FULL" in d2.log, "非 WAL 时 synchronous = FULL")
ok("BEGIN EXCLUSIVE TRANSACTION" not in d2.log, "版本一致不做迁移")

# ---- 5. onCreate vs onMigrate ----
class CB:
    def __init__(self):
        self.events = []

    def onCreate(self, conn):
        self.events.append("onCreate")

    def onMigrate(self, conn, o, n):
        self.events.append("migrate:%d->%d" % (o, n))

    def onOpen(self, conn):
        self.events.append("onOpen")


cb = CB()
d3 = FakeDriver(user_version=0)
RoomDatabase(d3, target_version=1, callbacks=[cb]).open()
ok(cb.events[0] == "onCreate", "user_version=0 → onCreate")
cb2 = CB()
d4 = FakeDriver(user_version=2)
RoomDatabase(d4, target_version=5, callbacks=[cb2]).open()
ok(cb2.events[0] == "migrate:2->5", "旧版本存在 → onMigrate")

# ---- 6. 非首个连接：不做 journal_mode 与迁移 ----
d5 = FakeDriver(user_version=1)
db = RoomDatabase(d5, target_version=1)
db.open()
d5.log.clear()
db.open()
ok(d5.log == ["PRAGMA busy_timeout = 2500", "PRAGMA synchronous = NORMAL", "onOpen"],
   "已配置过的连接只做 busy_timeout + synchronous + onOpen")
ok("PRAGMA journal_mode" not in d5.log, "非首个连接不再设置 journal_mode")
ok("BEGIN EXCLUSIVE TRANSACTION" not in d5.log, "非首个连接不做迁移")

# ---- 7. tryOpenInitialConnection 的重试与破坏性恢复 ----
d6 = FakeDriver(fail_times=1, user_version=1)
db6 = RoomDatabase(d6, target_version=1)
db6.open()
ok(d6.open_count == 2, "第一次失败后重试一次")
ok(d6.delays == [500], "重试前有 500ms 退避（源码 delay(500.milliseconds)）")

d7 = FakeDriver(fail_times=5, user_version=1)
raises(SQLiteException, lambda: RoomDatabase(d7, target_version=1, allow_data_loss_on_recovery=False).open(),
       "重试仍失败且不允许丢数据 → 直接抛出", "unable to open database file")
ok(d7.deleted == [], "未开启 allowDataLossOnRecovery 时不删库")

d8 = FakeDriver(fail_times=2, user_version=1)
db8 = RoomDatabase(d8, target_version=1, allow_data_loss_on_recovery=True)
db8.open()
ok(d8.deleted == ["app.db"], "SQLiteException + 非内存库 + 允许丢数据 → 删库后重开")
ok(d8.open_count == 3, "删库后是最后一次尝试")

d9 = FakeDriver(fail_times=5, user_version=1)
raises(SQLiteException,
       lambda: RoomDatabase(d9, name=":memory:", target_version=1, allow_data_loss_on_recovery=True).open(),
       "内存库不参与破坏性恢复")
ok(d9.deleted == [], ":memory: 不会被删")

# ---- 8. openAndVerify 的损坏探测 ----
d10 = FakeDriver(corrupt_schema=True, user_version=1)
raises(SQLiteException, lambda: RoomDatabase(d10, target_version=1).open(),
       "PRAGMA schema_version 失败即判定损坏", "malformed")
ok(d10.log == ["close(connection)", "close(connection)"],
   "每次探测失败都立刻关连接，避免泄漏（两次尝试各一次）")

# ---- 9. 递归初始化检测 ----
d11 = FakeDriver(user_version=1)
db11 = RoomDatabase(d11, target_version=1)


class Reentrant:
    def __init__(self, db):
        self.db = db

    def onCreate(self, conn):
        pass

    def onMigrate(self, conn, o, n):
        pass

    def onOpen(self, conn):
        if not self.db._fired:
            self.db._fired = True
            try:
                self.db.open()
            except RoomConfigError as e:
                self.db.recursive_msg = str(e)


r = Reentrant(db11)
db11._fired = False
db11.callbacks = [r]
db11.open()
ok("Recursive database initialization detected" in getattr(db11, "recursive_msg", ""),
   "在回调里再用数据库 → 递归初始化被 check 拦下")

# ---- 10. AOSP 连接池：主连接唯一 ----
pool = SQLiteConnectionPool(max_connection_pool_size=1)
c1 = pool.acquire_connection(thread="t1")
ok(c1.is_primary, "首次取到的是主连接")
ok(pool.available_primary is None, "主连接被取走后不再空闲")
c2 = pool.acquire_connection(thread="t2")
ok(not c2.is_primary, "第二个线程取到的是非主连接")
pool.release_connection(c1)
ok(pool.available_primary is c1, "主连接被回收到 mAvailablePrimaryConnection")

# ---- 11. releaseConnection 三分支 ----
pool2 = SQLiteConnectionPool(max_connection_pool_size=1)
a = pool2.acquire_connection()
b = pool2.acquire_connection()
pool2.release_connection(b)
ok(b in pool2.available_non_primary, "空闲非主连接数未达上限 → 回收进列表")
extra = SQLiteConnection(99, False)
pool2.acquired[extra] = "NORMAL"   # 模拟池中同时持有的另一个非主连接
pool2.release_connection(extra)
ok(pool2.closed_connections == 1 and not extra.is_open,
   "非主连接数已达上限时释放 → 直接关闭而不是入池")
pool2.release_connection(a)
ok(pool2.available_primary is a, "主连接释放后回到 mAvailablePrimaryConnection")
raises(PoolError, lambda: pool2.release_connection(a), "重复释放报异常",
       "has already been released")

# ---- 12. 等待队列按优先级插入 ----
pool3 = SQLiteConnectionPool(max_connection_pool_size=0)
c = pool3.acquire_connection(connection_flags=0, thread="bg", now=0)
ok(pool3.acquire_connection(connection_flags=0, thread="low", now=1) is None, "无可用连接时入队")
ok(pool3.acquire_connection(connection_flags=CONNECTION_FLAG_INTERACTIVE, thread="ui", now=2) is None,
   "第二个等待者也入队")
ok(pool3.waiter_queue.thread == "ui", "高优先级 waiter 插到队首（priority > successor）")
ok(pool3.waiter_queue.next.thread == "low", "低优先级排在后面")
ok(not pool3.should_yield_connection(now=1000), "未到 30s 不触发 busy 判定")
ok(not pool3.should_yield_connection(now=CONNECTION_POOL_BUSY_MILLIS + 1),
   "队首 waiter 从 now=2 起算，此时等待 29999ms 仍未到阈值")
ok(pool3.should_yield_connection(now=CONNECTION_POOL_BUSY_MILLIS + 2),
   "等待满 30s（CONNECTION_POOL_BUSY_MILLIS）触发 busy 处理")
ok(CONNECTION_POOL_BUSY_MILLIS == 30000, "忙等待阈值是 30 秒（源码常量）")

# ---- 13. 释放后唤醒等待者 ----
pool3.release_connection(c)
ok(pool3.waiter_queue is None or pool3.waiter_queue.thread != "ui",
   "释放连接后高优先级 waiter 优先被唤醒")
pool4 = SQLiteConnectionPool(max_connection_pool_size=2)
x = pool4.acquire_connection()
pool4.release_connection(x)
ok(x.is_open and pool4.available_primary is x, "主连接释放后被回收复用而不是关闭")

# ---- 14. 池关闭后释放会关连接 ----
pool5 = SQLiteConnectionPool(max_connection_pool_size=1)
z = pool5.acquire_connection()
pool5.close()
ok(z.is_open, "关闭时仍在使用的连接先不关")
pool5.release_connection(z)
ok(not z.is_open, "池已关闭时释放 → 直接关连接（源码注释明确允许）")
raises(PoolError, lambda: pool5.acquire_connection(), "池关闭后不能再取连接")

# ---- 15. Room 配置 → 框架层池大小 ----
ok(framework_pool_size(SingleConnection()) == 0, "单连接模式下没有非主连接")
ok(framework_pool_size(MultipleConnection(4, 1)) == 4, "WAL 多连接 → 4 个读连接")
ok(framework_pool_size(MultipleConnection(8, 2), JOURNAL_MODE_TRUNCATE) == 0,
   "非 WAL 下框架层不提供多连接（只有主连接）")

print("PASS %d" % PASS)
