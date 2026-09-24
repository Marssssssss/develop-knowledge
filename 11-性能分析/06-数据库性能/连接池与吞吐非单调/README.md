# 连接池与吞吐的非单调关系

> "连接不够就加"是最常见的数据库性能误操作。吞吐随连接数**先升后降**:
> 峰值在数据库可用并发(≈CPU 核数)附近,继续加连接只买到排队与上下文切换。
> 本 demo 把 pgbouncer 的池化语义/容量公式与这条曲线放在一起讲。

## 1. 三档池化(pgbouncer 官方语义)

| 档 | 持有连接的粒度 | 代价 |
| --- | --- | --- |
| session | 客户端连接全期独占 | 支持全部 PostgreSQL 特性;复用率最低 |
| transaction | 事务期间持有,结束即归还 | **破坏会话级特性**(SET 会话变量/ advisory lock 等),应用必须配合 |
| statement | 每条语句独占 | 破坏更多(多语句事务不可用),最激进 |

复用数学:100 客户端 × 池 20——session 模式只有 20 个客户端能同时活动;
transaction 模式全体客户端靠事务间隙轮转 20 条服务器连接。

## 2. 容量口径(config.html 公式)

- `default_pool_size = 20`(默认):**每个 user/database 对**一条池,可按库/用户覆盖;
- 文件描述符上限要提前备好,理论最大:
  `max_client_conn + pool_size × 库数 × 用户数`(共用一个 DB 用户时用户数=1)——
  `max_client_conn` 只是客户端侧,别漏了服务器侧那半。

## 3. 吞吐为什么非单调(解析模型)

```text
X(N) = N/D                    (N ≤ C:并发线性扩展)
X(N) = C / (D × (1 + α(N-C)/C))  (N > C:共享容量 + 上下文切换税)
```

- D=单查询服务需求,C=可用并发(≈核数),α=切换开销系数;
- Little 定律 **N = X × R** 是排队论的算术底线:500qps × 200ms = 100 在途;
  在途数远超池大小时,加客户端连接只是把队列从池里挪到池外。

**实践口径**:池大小从 ≈ C 起步,压测扫描峰值;吞吐掉了说明付了切换税,
先降单查询 D(索引/计划),而不是加连接。

> 曲线公式是本 demo 的解析模型(README 与代码中均已标注);
> 池化语义、默认值与 fd 公式为 pgbouncer 官方口径。

## 自检

`python poolthr_check.py` —— 6 项断言:三档语义 / session vs transaction 复用数学 /
fd 公式两种用户口径 / Little 定律算术 / 非单调曲线与峰值池=容量 /
在途数超池必排队。Go 侧 `poolthr.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [pgbouncer — Features(session/transaction/statement pooling)](https://www.pgbouncer.org/features.html)
- [pgbouncer — Configuration(max_client_conn 的 fd 公式与 default_pool_size)](https://www.pgbouncer.org/config.html)
