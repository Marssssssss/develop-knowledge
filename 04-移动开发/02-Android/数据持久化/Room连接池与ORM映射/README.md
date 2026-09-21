# Room 连接池与 ORM 映射

Room 常被当成「SQLite 上的注解处理器」，但它真正复杂的地方是**连接管理**：
同一份数据库文件该开几个连接、什么时候配置 PRAGMA、迁移在什么事务里跑、打不开时怎么办。
本 demo 把这些决策抽成可执行模型，并补上底层的 AOSP 连接池语义。

对应源码（androidx 主干）：

- `room3/room3-runtime/src/commonMain/kotlin/androidx/room3/ConnectionPoolConfiguration.kt`
- `room3/room3-runtime/src/commonMain/kotlin/androidx/room3/RoomConnectionManager.kt`
- `room3/room3-runtime/src/androidMain/kotlin/androidx/room3/RoomConnectionManager.android.kt`

以及 AOSP `platform_frameworks_base/core/java/android/database/sqlite/SQLiteConnectionPool.java`。

## 一、原理详解

### 1.1 连接池配置：sealed interface + 两个实现

```kotlin
public sealed interface ConnectionPoolConfiguration
public object SingleConnection : ConnectionPoolConfiguration
public data class MultipleConnection(val numOfReaders: Int, val numOfWriters: Int)
    : ConnectionPoolConfiguration {
    init {
        require(numOfReaders > 0) { "Number of readers must be greater than 0" }
        require(numOfWriters > 0) { "Number of writers must be greater than 0" }
    }
}
internal const val WAL_DEFAULT_NUMBER_OF_READERS = 4
internal const val WAL_DEFAULT_NUMBER_OF_WRITERS = 1
```

即：**WAL 默认 4 个读连接 + 1 个写连接**。用户要么单连接，要么显式指定读写数量，没有第三种。

### 1.2 选池的四路分支（Android 侧）

```kotlin
connectionPool =
  if (config.sqliteDriver.hasConnectionPool) PassthroughConnectionPool(...)   // ①
  else if (config.name == null)              newSingleConnectionPool(...)     // ②
  else when (val poolConfig = configuration.connectionPoolConfiguration) {
      is SingleConnection   -> newSingleConnectionPool(...)                  // ③
      is MultipleConnection -> newConnectionPool(maxNumOfReaders = ..., ...) // ④
  }
```

三条隐含结论：

- ① 框架自带驱动（`AndroidSQLiteDriver`）内部**已有线程封闭的连接池**，Room 不再叠一层；
- ② **内存数据库必须单连接**——多连接会各自打开一个独立的 `:memory:` 库，数据互不可见；
- ③④ 只有自己提供 driver 时，Room 的读写池配置才真正生效。

### 1.3 openLocked：文件锁 + 递归检测

```kotlin
ExclusiveMutex(
    filename = resolvedFileName,
    useFileLock = !isConfigured && !isInitializing && resolvedFileName != ":memory:",
).withLock(
    onLocked = {
        check(!isInitializing) {
            "Recursive database initialization detected. Did you try to use the " +
                "database instance during initialization? Maybe in one of the callbacks?"
        }
        ...
    },
    onLockError = { throw IllegalStateException("Unable to open database '...'. Was a proper path / name used ...") }
)
```

两把锁各管一件事：**文件锁**保证多进程不并发初始化；**`isInitializing`** 保证
「在 `Callback.onCreate/onOpen` 里又去用数据库」这种死锁被立即发现，而不是挂住。

文件锁刻意排除了 `:memory:`（没有跨进程共享）与「已配置过」的情形。

### 1.4 tryOpenInitialConnection：三段式容错

```kotlin
try { return openAndVerify(driver, fileName) } catch (_: Throwable) { /* ignore to retry */ }
delay(500.milliseconds)                       // 退避
val openRetryError = try { return openAndVerify(...) } catch (t: Throwable) { t }
if (openRetryError !is SQLiteException || fileName == ":memory:" || !configuration.allowDataLossOnRecovery) {
    throw openRetryError                      // 不可恢复 → 直接抛
}
deleteDatabaseFiles(fileName)                 // 破坏性恢复：删库重开
return openAndVerify(driver, fileName)
```

三个条件**全部满足**才会删库：`SQLiteException` + 非内存库 + 显式开启
`allowDataLossOnRecovery`。注意 `CancellationException` 会被单独放行（不吞协程取消）。

`openAndVerify` 本身还有一步损坏探测：

```kotlin
connection.prepare("PRAGMA schema_version").use { it.step() }
```

失败即关连接再抛出——**没有 finally 之外的清理路径**，避免半开连接泄漏。

### 1.5 configureDatabase：PRAGMA 的顺序是有意义的

```kotlin
configureBusyTimeout(connection)
configureJournalMode(connection)      // WAL → "PRAGMA journal_mode = WAL"；否则 TRUNCATE
configureSynchronousFlag(connection)  // WAL → NORMAL；否则 FULL
val version = ... "PRAGMA user_version"
if (version != openDelegate.version) {
    connection.executeSQL("BEGIN EXCLUSIVE TRANSACTION")
    runCatching {
        if (version == 0) onCreate(connection) else onMigrate(connection, version, openDelegate.version)
        connection.executeSQL("PRAGMA user_version = ${openDelegate.version}")
    }.onSuccess { "END TRANSACTION" }.onFailure { "ROLLBACK TRANSACTION"; throw it }
}
onOpen(connection)
```

三条容易被忽略的事实：

1. **非 WAL 时 Room 用的是 `TRUNCATE` 而不是 SQLite 默认的 `DELETE`**（源码写死的 else 分支）；
2. `synchronous` 与 journal mode 绑定：WAL→NORMAL、非 WAL→FULL，源码注释直接引用了
   `https://www.sqlite.org/pragma.html#pragma_synchronous`；
3. 迁移跑在 **`BEGIN EXCLUSIVE TRANSACTION`** 里，成功 `END`、失败 `ROLLBACK` 后重抛。

### 1.6 configurationConnection：后续连接不重复配置

```kotlin
protected suspend fun configurationConnection(connection: SQLiteConnection) {
    configureBusyTimeout(connection)
    configureSynchronousFlag(connection)
    openDelegate.onOpen(connection)
}
```

注意这里**没有 journal_mode、没有版本迁移**——journal mode 是数据库文件级属性，
首个连接设过就够了；迁移更不能并发做。

### 1.7 框架层连接池（Android driver 内部）

AOSP `SQLiteConnectionPool` 的几个硬事实：

- `CONNECTION_POOL_BUSY_MILLIS = 30 * 1000`：等待**超过 30 秒**只是打日志并解除阻塞，
  **不是抛异常**；
- 主连接（`mAvailablePrimaryConnection`）唯一，非主连接受 `mMaxConnectionPoolSize` 限制；
- 释放连接时三分支：
  - 主连接 → 回收进 `mAvailablePrimaryConnection`（前置断言它此前为 null）；
  - 非主且 `mAvailableNonPrimaryConnections.size() >= mMaxConnectionPoolSize` → **直接关闭**；
  - 否则回收进列表；
- 重复释放 / 释放别人的连接 → `IllegalStateException`
  （*"...was not acquired from this pool or has already been released."*）；
- 获取顺序是「先非主，再回退主连接」；没连接时按 **priority 降序**插入等待队列
  （`if (priority > successor.mPriority) break;`）。

## 二、对比：三种本地持久化方案

| 维度 | Room（SQLite） | DataStore（Proto/Preferences） | 纯 SQLiteOpenHelper |
| --- | --- | --- | --- |
| 连接管理 | Room 配置 + 框架池 | 单文件，无连接池 | 自己管 |
| 类型安全 / 协程 | DAO 编译期校验 SQL、原生 Flow | 编译期 serializer | 无 |

## 三、环境要求

- Python 3.8+（模型无依赖）
- 真机需 `androidx.room:room-runtime` + `room-compiler`（KSP）

## 四、运行方式

```bash
cd 04-移动开发/02-Android/数据持久化/Room连接池与ORM映射
python selfcheck_room.py      # 期望输出 PASS 59
```

## 五、关键代码

- `main.py`：`ConnectionPoolConfiguration` / `choose_pool` / `RoomDatabase.open()` 全流程
- `sqlite_pool.py`：AOSP 连接池模型（主连接、等待队列、30s 阈值）
- `selfcheck_room.py`：59 条断言
- `RoomPool.kt`：Kotlin 侧等价实现

## 六、性能边界与注意事项

- **读连接不是越多越好**：WAL 下读不阻塞写，但每个连接都要一份 statement 缓存，默认 4 读是官方取舍值。
- **非 WAL 下多连接没有意义**：journal mode 为 TRUNCATE/DELETE 时写锁是全库级，
  多连接只会增加锁竞争（本 demo 的 `framework_pool_size` 对此返回 0）。
- `allowDataLossOnRecovery` 是**真的会删库**，只在缓存型数据上开。
- 破坏性恢复只在初始化阶段发生；运行中损坏不会触发（不重试、不删库）。
- **常见坑**：在 `Callback.onOpen` 里查询数据库 → `Recursive database initialization`；
  把 `:memory:` 当成可共享的跨进程库；以为配置了 `MultipleConnection` 就一定生效
  （在 Android 框架驱动下它是 Passthrough）。

## 七、参考资料

实际读取的源码：

- `https://raw.githubusercontent.com/androidx/androidx/androidx-main/room3/room3-runtime/src/commonMain/kotlin/androidx/room3/ConnectionPoolConfiguration.kt`
- `.../commonMain/kotlin/androidx/room3/RoomConnectionManager.kt`、`RoomDatabase.kt`
- `.../androidMain/kotlin/androidx/room3/RoomConnectionManager.android.kt`
- `https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/main/core/java/android/database/sqlite/SQLiteConnectionPool.java`

> 口径说明：`androidx/androidx` 主干中 Room 模块已迁至 `room3/`（顶层 `room/` 已不存在），
> 本轮源码均按 `room3/room3-runtime` 路径读取。所有 PRAGMA 与常量取自源码原文。
