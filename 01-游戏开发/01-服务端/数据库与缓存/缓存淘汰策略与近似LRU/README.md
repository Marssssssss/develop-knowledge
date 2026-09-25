# maxmemory 淘汰策略与近似 LRU

> 游戏服拿 Redis 当缓存(玩家档案热数据、会话、排行快照)时,
> `maxmemory` 打满后的行为完全由 `maxmemory-policy` 决定。
> **默认值是 noeviction:不淘汰、写报错**——缓存用途必须显式改。

## 1. 策略矩阵(redis.conf 自文档清单)

| 算法 \ 键范围 | 只带过期键(volatile-) | 全键(allkeys-) |
| --- | --- | --- |
| 近似 LRU | volatile-lru | allkeys-lru |
| 近似 LFU | volatile-lfu | allkeys-lfu |
| 随机 | volatile-random | allkeys-random |
| 最近 TTL | volatile-ttl | — |
| 不淘汰 | noeviction(**默认**,写操作报错) | |

- `volatile-*` 只在设了过期的键里挑驱逐对象:永久键(配置、常驻结构)永不被驱逐;
  但反过来,若全部键都没设过期,它们**无事可做**;
- 新版本另引入 lrm 变体(volatile-lrm/allkeys-lrm),见本仓库 redis.conf 摘录。

## 2. 近似算法与采样(redis.conf 原文)

> "LRU, LFU and minimal TTL algorithms are **not precise algorithms but
> approximated algorithms** (in order to save memory)... By default Redis
> will check **five** keys and pick the one that was used least recently."

- `maxmemory-samples` 默认 5;**10** "Approximates very closely true LRU
  but costs more CPU";**3** faster but not very accurate;上限 64;
- 每键维护完整 LRU 链表太费内存,采样近似是**内存换精度**的工程折中;
- 模型验证:冷热交错访问下,近似 LRU(50%) 仍显著优于随机(36%)——
  采样偏差小于算法之间的差距。

## 3. 游戏服的选型直觉

- 玩家档案缓存:allkeys-lru(全部可重建);
- 会话/Token 与持久数据混布:volatile-* 只驱逐会话类(持久键设永不过期);
- 绝不能裸跑默认 noeviction——打满后**写全挂**。

## 自检

`python python/eviction.py` —— 4 项断言:策略矩阵与默认值 /
volatile 候选集排除永久键 / 采样近似语义(全采样=精确,小采样有抖动)/
命中率对照。Go 侧 `go/eviction.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [redis 仓库 redis.conf(maxmemory-policy 清单与 maxmemory-samples 注释)](https://github.com/redis/redis/blob/unstable/redis.conf)
