# 等待事件归因(pg_stat_activity 采样)

> 慢查询归因的第一手数据是 `pg_stat_activity` 的等待事件。
> 方法与 perf 采样同理:**反复抓快照,各桶样本占比 = 时间占比**。
> 难点不在抓数据,在把桶翻译成正确的行动——尤其别把"服务器在等客户端"算成 DB 慢。

## 1. 十类等待事件(官方 Table 27.4 口径)

| 类型 | 含义 | 典型归因动作 |
| --- | --- | --- |
| Client | 等**用户应用** socket 活动(ClientRead 等) | **应用侧 think time,加 DB 资源无效** |
| IO | 等 I/O 完成 | buffer 命中率 / shared_buffers / 磁盘 |
| Lock | 重量级锁(锁管理器,保护表等 SQL 可见对象) | 查 pg_locks 阻塞链 |
| LWLock | 轻量级锁(保护共享内存结构) | 缓冲区/WAL 插入等结构争用 |
| BufferPin | 等缓冲区独占 pin(游标可拖长) | 长事务/打开的游标 |
| IPC | 等其他服务器进程 | 并行 worker、后台进程交互 |
| Timeout | 等超时到期 | 通常伴随别的等待 |
| Activity | 进程空闲,在主循环等activity(多为后台进程) | 通常非问题 |
| Extension | 扩展定义的等待 | 看扩展文档 |
| InjectionPoint | 测试注入点(生产不可见) | — |

另有一个"隐形桶":**state=active 且 wait_event_type 为 NULL = 正在 CPU 上跑**。
它不算等待,但过半样本落在 running 就是 CPU 饱和——该查执行计划,加连接只会更糟。

## 2. 归因流程(模型即代码)

```text
抓样本 → 剔除 idle 系会话(state≠active)
       → 分桶:running / 十类等待
       → 主桶判定:
           running>50%   → CPU 饱和:计划/索引
           Client 主导   → 应用 think time:查应用,不查 DB
           Lock 主导     → pg_locks 阻塞链
           LWLock 主导   → 共享内存结构争用
           IO 主导       → 缓冲/磁盘
```

## 3. 三个高频误归因

1. **Client/ClientRead 当成 DB 慢**:服务器在等客户端发下一条语句——
   这是应用 think time,DB 侧任何优化都无效;
2. **大量 idle 会话算成瓶颈**:空闲后端不占资源,账要算到连接池配置头上
   (每后端都占内存与 max_connections 名额);
3. **Lock vs LWLock 混谈**:重量级锁对应 SQL 可见对象(表/行),
   查 pg_locks 有完整的锁与等待图;LWLock 是内部结构,得靠具体 wait_event 名定位。

## 自检

`python waitevent_check.py` —— 7 项断言:十类定义关键字 / active+NULL=running /
采样占比算术 / Client 主导→应用侧话术 / running 过半→CPU 饱和话术 /
Lock 主导→阻塞链 / idle 会话剔除。Go 侧 `waitevent.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [PostgreSQL 18 — 27.2. Monitoring Statistics(Table 27.4 Wait Event Types)](https://www.postgresql.org/docs/current/monitoring-stats.html#WAIT-EVENT-TABLE)
