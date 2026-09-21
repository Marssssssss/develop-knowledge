// Room 连接池配置与打开流程的 Kotlin 等价实现（与 main.py / sqlite_pool.py 对偶）。

object RoomPool {

    class PoolConfigError(message: String) : IllegalArgumentException(message)
    class InitError(message: String) : IllegalStateException(message)

    // ---- 1. ConnectionPoolConfiguration ----
    sealed interface ConnectionPoolConfiguration
    data object SingleConnection : ConnectionPoolConfiguration

    data class MultipleConnection(val numOfReaders: Int, val numOfWriters: Int) :
        ConnectionPoolConfiguration {
        init {
            require(numOfReaders > 0) { "Number of readers must be greater than 0" }
            require(numOfWriters > 0) { "Number of writers must be greater than 0" }
        }
    }

    const val WAL_DEFAULT_NUMBER_OF_READERS = 4
    const val WAL_DEFAULT_NUMBER_OF_WRITERS = 1

    // ---- 2. 选池分支 ----
    fun choosePool(driverHasPool: Boolean, name: String?, cfg: ConnectionPoolConfiguration): String =
        when {
            driverHasPool -> "PassthroughConnectionPool"
            name == null -> "newSingleConnectionPool"      // 内存库必须单连接
            cfg is SingleConnection -> "newSingleConnectionPool"
            else -> "newConnectionPool"
        }

    enum class JournalMode { WRITE_AHEAD_LOGGING, TRUNCATE }

    // ---- 3. PRAGMA 配置 ----
    fun journalModeStatement(mode: JournalMode) =
        if (mode == JournalMode.WRITE_AHEAD_LOGGING) "PRAGMA journal_mode = WAL"
        else "PRAGMA journal_mode = TRUNCATE"

    fun synchronousStatement(mode: JournalMode) =
        if (mode == JournalMode.WRITE_AHEAD_LOGGING) "PRAGMA synchronous = NORMAL"
        else "PRAGMA synchronous = FULL"

    // ---- 4. 打开流程（记录每一步 SQL，便于断言顺序）----
    class Recorder {
        val log = mutableListOf<String>()
        fun sql(s: String) = log.add(s)
    }

    class Database(
        private val name: String = "app.db",
        private val mode: JournalMode = JournalMode.WRITE_AHEAD_LOGGING,
        private val busyTimeoutMs: Int = 2500,
        private val targetVersion: Int = 1,
        private val allowDataLossOnRecovery: Boolean = false,
    ) {
        var isConfigured = false
            private set
        var isInitializing = false
            private set
        var userVersion = 0

        fun open(rec: Recorder): List<String> {
            if (isInitializing) {
                throw InitError(
                    "Recursive database initialization detected. Did you try to use the " +
                        "database instance during initialization? Maybe in one of the callbacks?"
                )
            }
            if (isConfigured) {
                configurationConnection(rec)
                return rec.log
            }
            isInitializing = true
            try {
                configureDatabase(rec)
            } finally {
                isInitializing = false
            }
            isConfigured = true
            return rec.log
        }

        fun configureDatabase(rec: Recorder) {
            rec.sql("PRAGMA busy_timeout = $busyTimeoutMs")
            rec.sql(journalModeStatement(mode))
            rec.sql(synchronousStatement(mode))
            if (userVersion != targetVersion) {
                rec.sql("BEGIN EXCLUSIVE TRANSACTION")
                var ok = false
                try {
                    if (userVersion == 0) rec.sql("onCreate")
                    else rec.sql("onMigrate:${userVersion}->$targetVersion")
                    rec.sql("PRAGMA user_version = $targetVersion")
                    userVersion = targetVersion
                    ok = true
                } finally {
                    rec.sql(if (ok) "END TRANSACTION" else "ROLLBACK TRANSACTION")
                }
            }
            rec.sql("onOpen")
        }

        fun configurationConnection(rec: Recorder) {
            rec.sql("PRAGMA busy_timeout = $busyTimeoutMs")
            rec.sql(synchronousStatement(mode))
            rec.sql("onOpen")
        }

        // tryOpenInitialConnection 的可恢复判定
        fun canRecoverByDeleting(error: Throwable): Boolean =
            error is android.database.SQLiteException &&
                name != ":memory:" &&
                allowDataLossOnRecovery
    }
}
