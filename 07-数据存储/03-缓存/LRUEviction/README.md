# 缓存淘汰算法：Memcached 精确 LRU vs Redis 近似 LRU

## 简介

当缓存容量耗尽时，谁该被淘汰？**Memcached** 用经典 LRU（每 slab 一个双向链表，O(1) 移动头/尾）；**Redis**（自 3.0 起）用 **近似 LRU**（随机采样 N 个 key，淘汰最久未访问的那个，配合**淘汰候选池**在多次淘汰之间共享样本）。两者权衡：Memcached 牺牲内存做精确追踪；Redis 牺牲精度换吞吐和内存效率。

**关键概念**
- **精确 LRU（textbook LRU）**：哈希表 + 双向链表；每次访问 O(1) 移动到头，超容 O(1) 淘汰尾；额外 2 指针 / entry。
- **近似 LRU（approximated LRU）**：每个 key 一个 24-bit LRU 时间戳；淘汰时随机采样 `maxmemory-samples`（默认 5）个 key，选最旧。采样池在多次淘汰之间复用候选。
- **Slab 分配器**：Memcached 把内存切成 80 个 size class（48B–1MB），每 class 内部独立 LRU；淘汰只在该 class 触发，因此不同大小 key 之间**互不感知**。
- **LFU（least frequently used）**：Redis 4.0+ 的替代策略；用访问频次 + 衰减时间解决 scan pollution。

**历史背景**：Memcached 1.x 即采用 slab + 精确 LRU；1.5.x 引入「分段 LRU」(HOT/WARM/COLD) 解决 slab 间碎片。Redis 在 2.6 起就支持近似 LRU；3.0 用样本池改进；4.0 加 LFU；6.0 加 LFU 衰减可配置。

## 原理详解

### Memcached 精确 LRU（slab 内部）

```text
┌─────────────────────────────────────────────────┐
│ Slab Class 3 (size = 256B)                      │
│   ┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐    │
│   │ item │←─→│ item │←─→│ item │←─→│ tail │    │
│   │ head │   │      │   │      │   │ (LRU)│    │
│   └──────┘   └──────┘   └──────┘   └──────┘    │
│      ▲                                          │
│      └── 新访问的 item 插入 head                  │
│                                                 │
│ 超容 → 淘汰 tail (O(1))                          │
└─────────────────────────────────────────────────┘
```

后台 **LRU Crawler** 线程每 100 ms 扫描 LRU 尾，回收已过期 (TTL) 的 item（不计入淘汰统计）。

**分段 LRU（1.5+）**：每 slab 拆 HOT/WARM/COLD 三段，hit 1 次升段、回收降段，让热数据不会被同 slab 冷数据挤出。

### Redis 近似 LRU（默认策略）

```text
maxmemory-policy: allkeys-lru       # 或 volatile-lru
maxmemory-samples: 5                 # 每次淘汰采样几个 key
```

淘汰流程：

```text
1. 内存达到 maxmemory → 触发 eviction loop
2. 每次 evict:  随机抽 N=5 个 key
3. 比较其 lru (24-bit time-since-last-access)
4. 淘汰最旧者
5. 把本次采样中"次旧"的 N 个候选存入 eviction pool
6. 下次 evict: 池中候选 + 新随机采样混合,扩大采样窗
```

**为什么有效？**
- 加大 `maxmemory-samples` 到 10 → 命中率接近精确 LRU（Redis 文档原图：误差 ~1%）
- 默认 5 → 命中率约比精确 LRU 低 3-5%，CPU 开销仅一次随机扫描

**24-bit LRU 字段**：每个 redisObject 存 24-bit `lru`（秒数距 `redis.lruclock` 之差），约 194 天回绕。

### LFU（Redis 4.0+）

```text
lfu-log-factor: 10       # 访问 N 次,counter 增量 ≈ log10(N)*10
lfu-decay-time: 1        # 1 分钟不访问,counter 减 1
```

LFU 在扫描访问（scan pollution）场景优于 LRU：冷 key 一次性访问后频次低，不会立即淘汰热 key。

### 时序对比

```text
         access(K)  access(K2) ... insert(K99) → K99 needs room
Memcached O(1)       O(1)            O(1) - evict tail
Redis     O(1)       O(1)            O(N=5) scan + O(1) evict
```

## 对比 / 选型

| 维度 | Memcached 精确 LRU | Redis 近似 LRU | Redis LFU |
|---|---|---|---|
| 命中率 | 高 | 高 (sample=10 接近精确) | 更高（抗 scan pollution） |
| 淘汰开销 | O(1) | O(samples) | O(samples) |
| 每 entry 开销 | 2 指针 (≈16B) | 24-bit timestamp (3B) | 24-bit counter+decay (3B) |
| 跨大小感知 | ❌（slab 内隔离） | ✅ | ✅ |
| 多线程 | 支持（libevent） | 单线程（6.0 起 I/O 多线程） | 同 LRU |
| 典型场景 | HTML 片段缓存 | 通用缓存 | 抗扫描、长尾分布 |

## 环境准备

- 操作系统：Linux / macOS / Windows（纯算法演示，无平台依赖）
- 语言版本：C11 / Python 3.8+ / Go 1.18+
- 依赖：无第三方依赖

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -pedantic c/lru_eviction.c -o demo
./demo
```

### Python

```bash
python3 python/lru_eviction.py
```

### Go

```bash
cd go && go run lru_eviction.go
```

## 关键代码片段

### C：Memcached 风格双向链表精确 LRU

```c
typedef struct lru_node {
    char key[32];
    char val[64];
    struct lru_node *prev, *next;
} lru_node;

/* 头插 (最近访问) / 尾删 (超容淘汰) 都是 O(1) */
void lru_touch(lru_cache *c, lru_node *n) {
    /* 解链 + 重新插入 head */
}
lru_node *lru_evict(lru_cache *c) {
    lru_node *n = c->tail;
    /* 解链 + 返回 tail */
    return n;
}
```

### Python：Redis 近似 LRU（采样 + 候选池）

```python
import random

class ApproxLRU:
    """Redis 近似 LRU: 每次淘汰采样 N 个,保留淘汰候选池。"""
    def __init__(self, capacity, samples=5):
        self.capacity = capacity
        self.samples = samples
        self.store: dict[str, tuple[str, int]] = {}     # key -> (val, lru_ts)
        self.pool: dict[str, int] = {}                  # 候选池

    def evict_one(self) -> str | None:
        # 池 + 随机采样混合
        candidates = dict(self.pool)
        if len(self.store) >= self.samples:
            for k in random.sample(list(self.store), self.samples):
                candidates[k] = self.store[k][1]
        if not candidates:
            return None
        victim = min(candidates, key=lambda k: candidates[k])  # 最久未访问
        self.pool = {k: v for k, v in candidates.items()
                     if k != victim and v > self.store.get(k, (None, 0))[1]}
        del self.store[victim]
        return victim
```

### Go：LFU 衰减近似实现

```go
func (c *LFUCache) touch(key string) {
    item := c.store[key]
    // LFU log-counter: log10(N) * lfu-log-factor
    item.counter += uint8(math.Log10(float64(item.visitCount+1)) * float64(c.logFactor))
    item.visitCount++
    item.lastTouch = time.Now()
}

func (c *LFUCache) evict() string {
    // 衰减:超过 decay-time 分钟的 counter--
    for k, it := range c.store {
        if time.Since(it.lastTouch) > c.decayTime {
            c.store[k].counter--
        }
    }
    // 选 counter 最小者淘汰
    victim := ""
    minCount := uint8(255)
    for k, it := range c.store {
        if it.counter < minCount {
            minCount = it.counter
            victim = k
        }
    }
    delete(c.store, victim)
    return victim
}
```

## 性能与边界

- **近似 LRU 命中率**：`maxmemory-samples=10` 时与精确 LRU 差距 < 1%，CPU 翻倍；默认 5 差距 3-5%。
- **Memcached 内存碎片**：slab 间大小差固定 1.25 倍；大量 < 48B 的小对象易溢出。LRU Crawler 100 ms 周期。
- **LFU 衰减**：`lfu-decay-time=1` 即 1 分钟不访问 counter -1；过短会把突发访问误判为冷。
- **平台差异**：memcached 1.6+ 默认 48 线程，LRU 链表锁粒度 slab 内；Redis 6.0+ 多线程 I/O 但执行命令仍单线程。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
|---|---|---|
| Memcached 命中率突然下降 | 某 slab 满 + 冷对象占尾 | 升级到 segmented LRU；调 slab factor |
| Redis `allkeys-lru` 把热 key 淘汰 | sample=5 太少 + 大量冷 key 稀释 | `maxmemory-samples 10`；或换 `volatile-lfu` |
| LFU 缓存被一次性扫描清空 | 衰减过慢 / log-factor 过大 | `lfu-log-factor 5`、`lfu-decay-time 10` |
| Memcached RSS 不释放 | 淘汰只移链表、不还 slab 给 OS | 监控 `stats` 的 `evictions`；重启或升级 |
| Redis 启动报 "OOM command not allowed" | maxmemory-policy=noeviction | 切换到 allkeys-lru / volatile-lru |
| 24-bit LRU 时间戳回绕 | 194 天无访问 | 自动无害；监控 uptime 触发重启 |

## 参考资料（实际阅读过的权威来源）

- [Redis LRU Cache (Official Docs)](https://redis.io/docs/latest/develop/reference/eviction/) — `maxmemory-samples` 默认 5、淘汰候选池机制、对比图表（演示图原出处）
- [Redis Configuration (Official Docs)](https://redis.io/docs/latest/operate/oss_and_stack/management/config-file/) — `maxmemory-policy` 全 6 种策略、`maxmemory-samples`、`aof-use-rdb-preamble`
- [Memcached Wiki - LRU](https://github.com/memcached/memcached/wiki/UserPhotos) — slab class 与 LRU 链表实现
- [Kablamo Engineering: Memcached vs Redis](https://engineering.kablamo.com.au/posts/2021/memcached-vs-redis-whats-the-difference) — 分段 LRU 与 48 线程实测
- [Software System Design: Cache Eviction Policies](https://softwaresystemdesign.com/caching-and-performance/cache-eviction-policies/) — 跨 CPU LRU/PLRU/DRRIP 与 DB buffer pool 对比