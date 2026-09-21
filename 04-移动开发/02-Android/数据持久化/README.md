# 数据持久化

Android 本地存储：SQLite 封装层、连接池、迁移，以及键值型方案。

| 子目录 | 说明 |
| --- | --- |
| [Room连接池与ORM映射/](./Room连接池与ORM映射/) | Room 连接池配置、打开流程与 PRAGMA 顺序、迁移事务、框架层 SQLite 连接池 |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 510 | `Room连接池与ORM映射/` | Room 连接池与 ORM 映射(Single/Multiple 配置与 WAL 默认 4 读 1 写、选池四路分支、500ms 退避重试与破坏性恢复、`journal_mode`/`synchronous` 与迁移事务、AOSP 连接池主连接与 30s 忙等待) | Python / Kotlin |

## 待研究

- [x] Room 连接池与打开流程 → demo 510
- [ ] DataStore 与 SharedPreferences 的事务语义对比
- [ ] Room 编译期 DAO 校验与 `EntityUpsertAdapter`
- [ ] SQLite WAL 检查点与 `wal_autocheckpoint`
- [ ] 数据库加密（SQLCipher / Room 的 `SupportSQLiteOpenHelper`）

## 参考资料

- androidx `room3/room3-runtime` 源码（`ConnectionPoolConfiguration.kt` / `RoomConnectionManager.kt`）
- AOSP `android/database/sqlite/SQLiteConnectionPool.java`
- SQLite PRAGMA 文档：`https://www.sqlite.org/pragma.html`
