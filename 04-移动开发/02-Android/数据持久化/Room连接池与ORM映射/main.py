"""Room 连接管理与连接池配置的可执行模型。

对应 androidx 主干 `room3/room3-runtime`：
  - `commonMain/.../ConnectionPoolConfiguration.kt`：SingleConnection / MultipleConnection 与默认值
  - `commonMain/.../RoomConnectionManager.kt`：openLocked / tryOpenInitialConnection /
    configureDatabase / configurationConnection
  - `androidMain/.../RoomConnectionManager.android.kt`：连接池选型的四路分支

打开流程里的每一条 PRAGMA 与事务语句都按源码顺序记录到 log，便于断言。
"""

from sqlite_pool import SQLiteConnectionPool

WAL_DEFAULT_NUMBER_OF_READERS = 4
WAL_DEFAULT_NUMBER_OF_WRITERS = 1

JOURNAL_MODE_WAL = "WRITE_AHEAD_LOGGING"
JOURNAL_MODE_TRUNCATE = "TRUNCATE"


class RoomConfigError(Exception):
    pass


class PoolConfigError(RoomConfigError):
    pass


class SQLiteException(Exception):
    """androidx.sqlite.SQLiteException / android.database.SQLiteException 的替身。"""


# ---------- 1. ConnectionPoolConfiguration ----------

class ConnectionPoolConfiguration:
    pass


class SingleConnection(ConnectionPoolConfiguration):
    def __repr__(self):
        return "SingleConnection"


class MultipleConnection(ConnectionPoolConfiguration):
    def __init__(self, num_of_readers, num_of_writers):
        if num_of_readers <= 0:
            raise PoolConfigError("Number of readers must be greater than 0")
        if num_of_writers <= 0:
            raise PoolConfigError("Number of writers must be greater than 0")
        self.num_of_readers = num_of_readers
        self.num_of_writers = num_of_writers

    def __repr__(self):
        return "MultipleConnection(r=%d,w=%d)" % (self.num_of_readers, self.num_of_writers)


def choose_pool(driver_has_pool, name, pool_config):
    """RoomConnectionManager.android.kt 构造器的四路分支，返回池类型名。"""
    if driver_has_pool:
        # Android 框架 driver 自带线程封闭的连接池，Room 不再叠一层
        return "PassthroughConnectionPool"
    if name is None:
        # 内存库必须单连接，否则每个连接各是一个库
        return "newSingleConnectionPool"
    if isinstance(pool_config, SingleConnection):
        return "newSingleConnectionPool"
    return "newConnectionPool"


# ---------- 2. 打开流程 ----------

class FakeDriver:
    """控制 open() 的成败与 PRAGMA 执行结果，用于复现 tryOpenInitialConnection 的重试链。"""

    def __init__(self, fail_times=0, corrupt_schema=False, user_version=0):
        self.fail_times = fail_times
        self.corrupt_schema = corrupt_schema
        self.user_version = user_version
        self.open_count = 0
        self.log = []
        self.deleted = []
        self.delays = []

    def open(self, file_name):
        self.open_count += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise SQLiteException("unable to open database file: %s" % file_name)
        return {"file": file_name, "driver": self}

    def execute_sql(self, conn, sql):
        self.log.append(sql)
        if sql.startswith("PRAGMA user_version = "):
            self.user_version = int(sql.rsplit("=", 1)[1])
        return True

    def query_user_version(self, conn):
        return self.user_version

    def prepare_step(self, conn, sql):
        if sql == "PRAGMA schema_version" and self.corrupt_schema:
            raise SQLiteException("database disk image is malformed")
        return True


class RoomDatabase:
    def __init__(self, driver, name="app.db", journal_mode=JOURNAL_MODE_WAL,
                 allow_data_loss_on_recovery=False, target_version=1,
                 busy_timeout_ms=2500, callbacks=None):
        self.driver = driver
        self.name = name
        self.journal_mode = journal_mode
        self.allow_data_loss_on_recovery = allow_data_loss_on_recovery
        self.target_version = target_version
        self.busy_timeout_ms = busy_timeout_ms
        self.callbacks = callbacks or []
        self.is_configured = False
        self.is_initializing = False
        self.log = []

    # ---- openLocked ----
    def open(self):
        if self.is_initializing:
            raise RoomConfigError(
                "Recursive database initialization detected. Did you try to use the "
                "database instance during initialization? Maybe in one of the callbacks?"
            )
        if self.is_configured:
            conn = self.driver.open(self.name)
            self.configuration_connection(conn)
            return conn
        self.is_initializing = True
        try:
            conn = self.try_open_initial_connection()
            self.configure_database(conn)
        finally:
            self.is_initializing = False
        self.is_configured = True
        return conn

    # ---- tryOpenInitialConnection：失败→退避 500ms→重试→破坏性恢复 ----
    def try_open_initial_connection(self):
        try:
            return self.open_and_verify()
        except SQLiteException:
            pass
        self.driver.delays.append(500)
        try:
            return self.open_and_verify()
        except SQLiteException as err:
            open_retry_error = err
        if (not isinstance(open_retry_error, SQLiteException)
                or self.name == ":memory:"
                or not self.allow_data_loss_on_recovery):
            raise open_retry_error
        self.driver.deleted.append(self.name)
        return self.open_and_verify()

    def open_and_verify(self):
        conn = self.driver.open(self.name)
        try:
            self.driver.prepare_step(conn, "PRAGMA schema_version")
        except Exception:
            self.driver.log.append("close(connection)")
            raise
        return conn

    # ---- configureDatabase：首个连接才做 journal_mode 与迁移 ----
    def configure_database(self, conn):
        self._configure_busy_timeout(conn)
        self._configure_journal_mode(conn)
        self._configure_synchronous(conn)
        version = self.driver.query_user_version(conn)
        if version != self.target_version:
            self.driver.execute_sql(conn, "BEGIN EXCLUSIVE TRANSACTION")
            ok = False
            try:
                if version == 0:
                    self._on_create(conn)
                else:
                    self._on_migrate(conn, version, self.target_version)
                self.driver.execute_sql(conn, "PRAGMA user_version = %d" % self.target_version)
                ok = True
            finally:
                self.driver.execute_sql(conn, "END TRANSACTION" if ok else "ROLLBACK TRANSACTION")
                if not ok:
                    raise RoomConfigError("migration failed")
        self._on_open(conn)

    # ---- configurationConnection：非首个连接，只做这三项 ----
    def configuration_connection(self, conn):
        self._configure_busy_timeout(conn)
        self._configure_synchronous(conn)
        self.driver.log.append("onOpen")

    def _configure_busy_timeout(self, conn):
        self.driver.execute_sql(conn, "PRAGMA busy_timeout = %d" % self.busy_timeout_ms)

    def _configure_journal_mode(self, conn):
        if self.journal_mode == JOURNAL_MODE_WAL:
            self.driver.execute_sql(conn, "PRAGMA journal_mode = WAL")
        else:
            self.driver.execute_sql(conn, "PRAGMA journal_mode = TRUNCATE")

    def _configure_synchronous(self, conn):
        # 源码注释：WAL 用 NORMAL、非 WAL 用 FULL，依据 sqlite.org/pragma.html#pragma_synchronous
        if self.journal_mode == JOURNAL_MODE_WAL:
            self.driver.execute_sql(conn, "PRAGMA synchronous = NORMAL")
        else:
            self.driver.execute_sql(conn, "PRAGMA synchronous = FULL")

    def _on_create(self, conn):
        for cb in self.callbacks:
            cb.onCreate(conn)

    def _on_migrate(self, conn, old, new):
        for cb in self.callbacks:
            cb.onMigrate(conn, old, new)

    def _on_open(self, conn):
        for cb in self.callbacks:
            cb.onOpen(conn)


# ---------- 3. 由 Room 配置推出框架层池大小 ----------

def framework_pool_size(pool_config, journal_mode=JOURNAL_MODE_WAL):
    """Room 的读写上限映射到 AOSP SQLiteConnectionPool 的非主连接数上限。"""
    if isinstance(pool_config, SingleConnection):
        return 0
    if journal_mode == JOURNAL_MODE_WAL:
        return max(WAL_DEFAULT_NUMBER_OF_READERS, 1)
    return 0  # 非 WAL 下框架层不允许多连接
