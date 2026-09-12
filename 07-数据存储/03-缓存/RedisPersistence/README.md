# Redis 持久化：RDB 快照 + AOF 日志 + Fork 写时复制

## 简介

Redis 提供两种互补的磁盘持久化机制：**RDB**（Redis Database，定时生成内存快照）与 **AOF**（Append Only File，追加每个写命令）。两者在「恢复粒度」「写入放大」「启动速度」上各有权衡，Redis 7.0 起还支持二者同时开启时的「混合持久化」（RDB 全量 + AOF 增量）。所有持久化都借助 `fork(2)` 子进程 + 写时复制（CoW）实现，父进程对外服务不阻塞。

**关键概念**
- **RDB**：二进制单文件 `dump.rdb`，记录某一时刻内存的全量数据；适合冷备 / 灾备 / 快速重启。
- **AOF**：纯文本协议日志，每条写命令一行；按 `appendfsync` 策略刷盘，默认 `everysec`。
- **AOF Rewrite**：AOF 增长过大时，Redis 后台 fork 子进程扫描内存、按当前数据集生成最小命令集到新文件，原文件继续追加。
- **Copy-on-Write**：`fork()` 后父子共享同一物理页，子进程写入新页时父进程才分配新页；父进程因 CoW 实际占用 ≈ 干净页，热点页随写入逐渐翻倍。

**历史背景**：RDB 自 Redis 1.0 即存在；AOF 自 1.1 引入；AOF 多文件（base + increment）自 Redis 7.0 加入以解决 AOF 重写期内存膨胀。

## 原理详解

### RDB 触发与生成

```text
SAVE / BGSAVE / 自动 save 策略触发
        │
        ▼
  ┌────────────────────┐
  │ 父进程 fork() 子进程 │   ← 一致性快照点(从 fork 开始内存不再变)
  └────────────────────┘
        │
        ▼
  子进程遍历 dict 全部 key → rdbSaveRio() → 临时文件 temp-{pid}.rdb
        │
        ▼
  rename(2) temp-{pid}.rdb  → dump.rdb (原子替换)
```

`save 60 1000` 表示「60 秒内至少 1000 次写入则触发 BGSAVE」。RDB 内容是二进制流：头部 + 数据库编号 + 每个 key 的 SDS 类型字节 + 实际数据。

### AOF 写入与刷盘

```text
client: SET k v
        │
        ▼
  命令追加到 aof_buf 内存缓冲区
        │
        ▼
  按 appendfsync 策略:
    always  → 每次都 fdatasync()  (同步,慢,~1000 ops/s)
    everysec → 后台线程每秒 fdatasync()  (默认,丢 ≤ 1s)
    no      → OS 自行刷盘 (最快,丢 ≤ 30s)
        │
        ▼
  AOF 文件按 RESP 协议追加 (例: *3\r\n$3\r\nSET\r\n$1\r\nk\r\n$1\r\nv\r\n)
```

### AOF Rewrite（压缩 AOF）

AOF 长期追加会无限增长。Redis 后台 BGREWRITEAOF 重写步骤：

```text
1. fork() 子进程 (CoW)
2. 子进程遍历内存,按当前数据集生成最小命令序列
   (例: 100 次 INCR → 1 次 SET key 100)
3. 父进程把 rewrite 期间新增的写命令同时写到
   - 旧 AOF 末尾 (旧 base + 新增)
   - 新 AOF incr 文件 (Redis 7.0+)
4. 子进程完成后,父进程用新 base 替换旧 base + incr
5. atomic rename manifest
```

### Fork + Copy-on-Write

`fork()` 后父子共享同一物理页表。**只有父进程修改的页才会被复制**，因此：
- 父进程读命中 → 共享干净页，零成本
- 父进程写入 → 触发 CoW，给该页分配新物理页，旧页仍归子进程
- 实际内存峰值 ≈ `父: 写脏页` + `子: 全量干净页` = 子进程大小 ≈ 整个实例 RSS

**这是为什么大 key 集合做 BGSAVE 会阻塞**：fork 需要把整个页表（虚拟地址 → 物理地址）从父复制到子，进程 RSS 越大、页表越大，fork 越慢。Linux 上可观察 `INFO` 的 `latest_fork_usec` 字段。

### 混合持久化 (Redis 7.0+)

开启 `aof-use-rdb-preamble yes`：AOF 文件 = RDB 二进制快照（前半部分）+ AOF 增量日志（后半部分）。恢复时先加载 RDB 快照（快），再回放 AOF 增量（精确），兼顾 RDB 的恢复速度与 AOF 的低丢失率。

## 对比 / 选型

| 维度 | RDB | AOF (everysec) | 混合 (RDB+AOF) |
|---|---|---|---|
| 数据丢失 | 分钟级 (取决于 save 点) | ≤ 1 秒 | ≤ 1 秒 |
| 文件体积 | 小 (二进制压缩) | 大 (每条命令) | 中 (RDB 头 + AOF 尾) |
| 启动恢复 | 快 (单文件反序列化) | 慢 (重放 N 条命令) | 较快 (RDB 头快) |
| 写性能影响 | 几乎无 (子进程做) | 轻微 (后台 fsync) | 轻微 |
| 适用 | 冷备、灾备、缓存 | 不可丢数据 | 默认推荐 |

## 环境准备

- 操作系统：Linux / macOS / WSL（演示使用 `fork` 与 `fdatasync`，Windows 无 `fork` 演示路径直接 exit）
- 语言版本：C11 / Python 3.8+ / Go 1.18+
- 依赖：无第三方依赖（演示「AOF 协议 + fsync 触发」的最小化仿真；不依赖真实 redis-server）

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -pedantic c/redis_persistence.c -o demo
./demo
```

### Python

```bash
python3 python/redis_persistence.py
```

### Go

```bash
cd go && go run redis_persistence.go
```

## 关键代码片段

### C：`fork()` + 写时复制仿真

```c
// rdb_save.c 节选,演示 fork + CoW 顺序
pid_t pid = fork();
if (pid == 0) {
    // 子进程:把共享内存 dump 成临时 rdb 文件
    rdbSaveRio(&rdb);              // 遍历整个 dict,顺序写
    rename(tmp, "dump.rdb");       // 原子替换
    _exit(0);
}
// 父进程继续服务客户端,期间被改的页触发 CoW
waitpid(pid, &status, 0);           // 不阻塞服务,可异步
```

### Python：AOF 协议追加 + fsync 策略

```python
def aof_append(buf: bytearray, cmd: list[str], strategy: str):
    """按 RESP 协议追加命令;fsync 策略 always/everysec/no。"""
    buf.extend(_resp_encode(cmd))
    if strategy == "always":
        os.fdatasync(aof_fd)       # 同步落盘,慢
    elif strategy == "everysec":
        pass                       # 后台线程每秒 fsync
    # else: 'no' → OS 自行决定
```

### Go：AOF Rewrite 时增量/基础双文件

```go
// Redis 7.0+ AOF rewrite 期间
// 子进程把新数据集写到 BASE.AOF (临时文件)
// 父进程把新命令同时追加到旧的 INCR.AOF 与新的 INCR.AOF
// 重写完成后原子替换 manifest
```

## 性能与边界

- **RDB 体积**：典型 1 GB Redis 实例的 RDB ≈ 100–300 MB（依赖 key 复杂度）；用 `rdb-compression yes` 可再压 30%。
- **fork 耗时**：约 10–50 ms / GB RSS（Linux 4.x+），可通过 `vm.overcommit_memory=1` + 关闭 THP 缓解。
- **AOF 文件增长率**：`auto-aof-rewrite-percentage 100` 即 AOF 比上次重写翻倍时触发重写。
- **平台差异**：Linux/macOS `fork` 为 CoW；Windows 无 `fork`，Redis 依赖 Windows 原生 API 模拟，效率不同；本 demo 在 Windows 直接退出。
- **容量边界**：单个 RDB 文件最大受限于 `RDB_MAX_ITERATIONS`（默认无界）；AOF 默认上限 64 MB 起写、512 MB 后台 fsync。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
|---|---|---|
| 大实例 BGSAVE 时延毛刺 | fork 复制页表 + 写时复制分配新页 | `vm.overcommit_memory=1`、关闭 THP、避高峰 BGSAVE；监控 `latest_fork_usec` |
| RDB 文件损坏 | 写到一半掉电 / `kill -9` | 留多份时间戳副本；Redis 启动可容忍尾部截断 |
| AOF 重写后启动报 "short read" | 上次重写未完成被强制重启 | `redis-check-aof --fix` 修复；监控 `aof_last_rewrite_status` |
| AOF 占用磁盘爆炸 | 没开启 auto-rewrite | 配 `auto-aof-rewrite-percentage 100`、`auto-aof-rewrite-min-size 64mb` |
| 子进程退出码非 0 但父进程没察觉 | 没检查 `waitpid` 返回 | `INFO persistence` 看 `rdb_last_bgsave_status` / `aof_last_bgrewrite_status` |
| 混合持久化文件迁移回纯 RDB | 旧 Redis < 7.0 不识别 AOF 头部 | 升级路径需要 `redis-check-aof` 提前校验 |

## 参考资料（实际阅读过的权威来源）

- [Redis Persistence (Official Docs)](https://redis.io/docs/staging/DOC-6273/operate/oss_and_stack/management/persistence) — RDB / AOF 优劣与 fork 流程最权威总结
- [Redis Persistence and Durability (Official Learn)](https://redis.io/learn/2-persistence-durability) — fsync 三种策略与 AOF rewrite 时序
- [Durable Redis (Official Glossary)](https://steve.lorello@redis.io/glossary/durable-redis) — multi-part AOF（base + increment）机制
- [Redis Configuration (Official Docs)](https://redis.io/docs/latest/operate/oss_and_stack/management/config-file/) — `aof-use-rdb-preamble` 等配置项原文