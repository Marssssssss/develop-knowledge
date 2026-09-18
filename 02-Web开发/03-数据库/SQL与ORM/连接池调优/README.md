# 连接池调优

## 简介

- 连接池不是"越大越好"。HikariCP 官方 wiki 的标题就是反问："Should you have a connection pool of 10,000 connections? You might be surprised that the question is not *how big* but rather *how small!*"
- 关键概念：
  - **经验公式**：`connections = ((core_count * 2) + effective_spindle_count)`，其中 **HT 线程不计入核数**；
  - **有效主轴数（effective_spindle_count）**：全内存命中时为 0，缓存命中率越低越接近真实盘数；
  - **pool-locking**：单个线程需要同时持有多个连接时，`pool size = Tn × (Cm − 1) + 1` 是**避免死锁的最小值**；
  - **小池 + 排队**：正确形态是"少量连接 + 大量线程阻塞在池上等待"，而不是"连接数 ≈ 前端用户数"；
  - **阻塞创造机会**：之所以连接数可以略多于核数，是因为线程在 I/O 等待时不占 CPU，别的连接可以顶上。
- 历史背景：该 wiki 引用 Oracle Real-World Performance 的演示 —— **只把连接池调小这一件事**，就让应用响应时间从 ~100ms 降到 ~2ms（50 倍以上）。

## 原理详解

1. **为什么小池更快**：单核同一时刻只能执行一个线程，时间片轮转只是假象；线程数超过核数后，每多一个线程就多一份上下文切换开销，吞吐**下降**。这就是"无阻塞时最优池 = 核数"的原因（本 demo 的纯 CPU 曲线复现了这一点：K=4 时峰值就在 P=4）。
2. **为什么可以略多于核数**：查询会阻塞在磁盘与网络上（寻道、旋转延迟、TCP 缓冲区满），阻塞期间 CPU 可以跑别的连接。于是最优池 ≈ `核数 × 2 + 有效主轴数`。
3. **SSD 的反直觉推论**：官方原文 "Don't be tricked into thinking, 'SSDs are *faster* and therefore I can have *more* threads'. That is exactly 180 degrees backwards." —— 快、无寻道、无旋转 = 更少阻塞 = 更接近核数更好。
4. **算例**：4 核 + 1 块盘 → `4 × 2 + 1 = 9`，取整 10。官方的评论是"即使 96 对一台 16/32 核机器也偏高"，并且 "100 connections, overkill"。
5. **下界（pool-locking）**：`Tn=3 / Cm=4 → 3 × 3 + 1 = 10`；`Tn=8 / Cm=3 → 8 × 2 + 1 = 17`。官方注明这是**最小值**、不是最优值 —— 本 demo 同时演示了池=9 时三个线程各持 3 个连接后集体卡死、池=10 则全部完成。
6. **形态目标**：池应当"饱和 + 有线程在等"，即池大小恰好等于数据库能同时处理的查询数；池设得过大反而让数据库内部排队（锁、闩、CPU 争抢），把应用侧的可控排队变成数据库侧的不可控排队。

### 本 demo 简化模型的形状（K=4，每请求 5 CPU tick + 20 I/O tick，吞吐=完成数/tick）

| 池大小 P | 1 | 2 | 4 | 6 | 8 | 10 | 16 | 32 | 100 | 1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 吞吐 | 0.04 | 0.08 | 0.16 | 0.23 | 0.31 | **0.38** | 0.19 | 0 | 0 | 0 |

- 峰值落在"略大于核数"的 8~12 区间，与公式给出的 9~10 同一量级；
- 把 I/O 置零（纯 CPU）后峰值**精确落在核数**，之后单调下降，与官方 "going slower by adding more threads" 一致。

> 模型的边界：切换税按"每多一个并发任务扣 20% 核时间片"线性计，因此池极大时会归零。这是为了复现"1000 still horrible"的定性形状，**不是**数据库基准 —— 绝对值不可外推。

## 环境准备

- Python 3.9+（仅标准库）；Go 1.18+（可选对照）。无需真实数据库。

## 运行方式

### Python

```bash
python3 python/pool_sizing.py
```

### Go

```bash
cd go && go run pool_sizing.go
```

## 关键代码片段

```python
def formula_pool_size(core_count: int, spindle_count: int) -> int:
    """官方公式：((core_count * 2) + effective_spindle_count)，HT 线程不计入 core_count。"""
    return core_count * 2 + spindle_count          # 4 核 + 1 盘 = 9
```

```python
def pool_locking_min(threads: int, max_conns: int) -> int:
    """官方资源分配公式：pool size = Tn x (Cm - 1) + 1（避免死锁的最小值）。"""
    return threads * (max_conns - 1) + 1           # Tn=3/Cm=4 -> 10；Tn=8/Cm=3 -> 17
```

```python
# 简化队列模型：核时间片按队列发放，I/O 阶段不占核，超出核数按比例扣切换税
waste = min(cores, (len(in_flight) - cores) * switch_tax)
grants = max(0, int(cores - waste))
```

## 性能与边界

- 池"过小"的代价是**排队延迟**，池"过大"的代价是**数据库侧争抢 + 切换开销**；两者都不好，所以公式只是**起点**，官方要求 "You should test your application, i.e. simulate expected load, and try different pool settings *around* this starting point"。
- 混合负载最难调：官方建议长事务与实时查询用**两个独立的池**。
- 以任务队列驱动的系统（如限制并发作业数）应当"**让队列大小去匹配池**"，而不是反过来放大池。
- 连接本身的成本：每个连接都有独立的内存与握手开销，官方在演示中把 Oracle 的连接数从 2048 降到 96。
- 用 JTA 事务管理器时，`getConnection()` 会把同一事务内已持有的连接直接返回给线程，因此**所需连接数会显著下降**。

## 注意事项与常见坑

- **按前端用户数配池**：10,000 用户 → 池 10,000 是"sheer insanity"，100 也 overkill；正确做法是让用户线程阻塞在池上。
- **把 HT 线程算进核数**：官方明确 "Core count should not include HT threads"，8 核 16 线程应按 8 算。
- **以为 SSD 可以开更多连接**：方向正好相反（阻塞更少 → 更少线程更好）。
- **把 pool-locking 下界当最优值**：下界只保证不死锁；本 demo 中 `pool_locking_min(3,4)=10`，而吞吐峰值区间是 8~12，两者含义完全不同。
- **单线程持多连接**：ORM 的 lazy-load + 多数据源容易让一个请求同时占用多个连接，这类模式必须按 `Tn × (Cm − 1) + 1` 反推池大小，否则会在高并发下"看起来随机地"卡死。
- **忽略池等待指标**：池够不够要看"等待获取连接的时长/比例"，而不是看连接使用率是否接近 100%。

## 参考资料（实际阅读过的权威来源）

- [HikariCP Wiki — About Pool Sizing](https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing) — 经验公式与 PostgreSQL 出处、HT 不计入核数、SSD 反直觉推论、4 核+1 盘=9 的算例、pool-locking 公式与两个算例、小池饱和、混合负载双池建议、JTA 对连接数的削减。
