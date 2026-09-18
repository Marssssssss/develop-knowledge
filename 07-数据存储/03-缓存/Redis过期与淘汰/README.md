# Redis 过期与淘汰（EXPIRE / activeExpireCycle / LFU）

## 一、简介

Redis 的「缓存」身份决定它必须回答两个问题：**键什么时候消失**（过期）和**内存满了牺牲谁**（淘汰）。本 demo 把这两块的**语义边界**和**可调参数的换算公式**做成可验证的模型，全部数值来自 Redis 官方文档与 `redis/src` 源码。

重点不在 API 用法，而在那些「写错不报错、但行为完全不同」的地方：`GT`/`LT` 比较的是过期时间点而不是剩余 TTL、`volatile-*` 在没有 TTL 键时**静默退化成 noeviction**、LFU 的 16 位分钟时间戳跨回绕时会少算 1 分钟。

## 二、原理详解

### 2.1 EXPIRE 的 NX / XX / GT / LT（Redis 7.0 加入）

```
EXPIRE key seconds [NX | XX | GT | LT]
```

| 选项 | 语义 |
| --- | --- |
| `NX` | 仅当键**没有**过期时间时设置 |
| `XX` | 仅当键**已有**过期时间时设置 |
| `GT` | 仅当新的过期时间 **>** 当前过期时间 |
| `LT` | 仅当新的过期时间 **<** 当前过期时间 |

文档原文对非易失键的处理是：

> A non-volatile key is treated as an **infinite TTL** for the purpose of `GT`.

于是产生一个反直觉组合：**`GT` 对永久键永远失败，`LT` 对永久键永远成功**。想「给永久键加一个过期时间」只能写 `LT`（或 `NX`），写 `GT` 会静默返回 0。

另一个易错点是 **`GT`/`LT` 比的是过期的绝对时间点，不是剩余 TTL**。本 demo 第一版实现就写成了比较剩余 TTL，被断言抓出来：键在 `now=900` 时过期时间是 `1000`（剩余 100），调 `EXPIRE key 10 GT` 的新过期时间是 `910` —— 比剩余 TTL（100）大得多 → 错误地判定为「更大」，而按文档应比较 `910` vs `1000` → 不成立。

### 2.2 过期 ≠ 删除：键事件不同

文档原文：

> calling `EXPIRE`/`PEXPIRE` with a non-positive timeout or `EXPIREAT`/`PEXPIREAT` with a time in the past will result in the key being **deleted** rather than expired (accordingly, the emitted **key event will be `del`, not `expired`**)

`ttl=0`、`ttl<0`、过去时间这三种情况都是**立即删除**，键空间通知发的是 `del`。如果你的业务在监听 `expired` 事件做清理（比如关单、释放库存），这些路径**收不到通知**。

### 2.3 哪些命令会清掉 TTL

文档原文：

> The timeout will only be cleared by commands that **delete or overwrite the contents** of the key, including `DEL`, `SET`, `GETSET` and all the `*STORE` commands. ... all the operations that conceptually *alter* the value stored at the key without replacing it with a new one will leave the timeout untouched. For instance, incrementing the value of a key with `INCR`, pushing a new value into a list with `LPUSH`, or altering the field value of a hash with `HSET` are all operations that will leave the timeout untouched.

| 清 TTL | 保留 TTL |
| --- | --- |
| `DEL` / `SET` / `GETSET` / 所有 `*STORE` | `INCR` / `LPUSH` / `HSET` / `APPEND` |

判据是「**整体替换**还是**就地修改**」。`SET` 会清（整体替换），`INCR` 不会（就地修改） —— 所以 `SET k 1` 之后再 `EXPIRE` 的顺序很重要，而 `INCRBY` 可以放心用。

### 2.4 定期删除：activeExpireCycle 的参数换算

Redis 的过期是**惰性删除 + 定期删除**双管齐下。定期删除的节奏由 `redis/src/expire.c` 里五个常量和 `active_expire_effort` 决定：

```c
#define ACTIVE_EXPIRE_CYCLE_KEYS_PER_LOOP 20    /* Keys for each DB loop. */
#define ACTIVE_EXPIRE_CYCLE_FAST_DURATION 1000  /* Microseconds. */
#define ACTIVE_EXPIRE_CYCLE_SLOW_TIME_PERC 25   /* Max % of CPU to use. */
#define ACTIVE_EXPIRE_CYCLE_ACCEPTABLE_STALE 10 /* % of stale keys after which we do extra efforts. */
```

换算（`effort = server.active_expire_effort - 1`，`active_expire_effort` 取值 1..10，默认 1）：

```c
effort = server.active_expire_effort-1,          /* Rescale from 0 to 9. */
config_keys_per_loop      = 20   + 20/4   * effort,
config_cycle_fast_duration= 1000 + 1000/4 * effort,
config_cycle_slow_time_perc = 25 + 2 * effort,
config_cycle_acceptable_stale = 10 - effort;
```

| `active_expire_effort` | 每轮扫键数 | fast 上限(µs) | slow CPU 上限(%) | 容忍陈旧比例(%) |
| --- | --- | --- | --- | --- |
| 1（默认） | 20 | 1000 | 25 | 10 |
| 10 | 65 | 3250 | 43 | 1 |

方向性很清楚：**effort 越大，扫得越多、占 CPU 越多，但容忍的陈旧键比例越低**。`acceptable_stale` 降到 1 意味着「只要有 1% 的键过期没清掉，就加倍努力」。注意它是 `10 - effort`，**永远不会到 0**。

### 2.5 LFU：24 位 lru 字段的布局

`robj.lru` 只有 24 位，LFU 模式下把它劈成两半（`object.c`）：

```c
o->lru = (LFUGetTimeInMinutes() << 8) | LFU_INIT_VAL;
```

- **高 16 位**：最后访问时间的**分钟数**
- **低 8 位**：频率计数器（0..255）

`LFU_INIT_VAL = 5`（`server.h`）——新对象不是从 0 开始，源码注释解释得很清楚：

> some accesses before being trashed away, so they start at LFU_INIT_VAL.

即新键有 5 点「保底分」，避免刚进来就被淘汰。

### 2.6 Morris 概率计数器

8 位最多只能数到 255，但访问次数可以上亿。Redis 用的是**概率递增**（`evict.c`）：

```c
uint8_t LFULogIncr(uint8_t counter) {
    if (counter == 255) return 255;
    double r = (double)rand()/RAND_MAX;
    double baseval = counter - LFU_INIT_VAL;
    if (baseval < 0) baseval = 0;
    double p = 1.0/(baseval*server.lfu_log_factor+1);
    if (r < p) counter++;
    return counter;
}
```

`p = 1 / ((counter - 5) * lfu_log_factor + 1)` —— 计数器越大，再 +1 越难，于是 8 位能表示**对数级**的访问量。默认 `lfu-log-factor 10`。

官方给出的对照表（`lfu_log_factor` × 访问次数 → 计数）：

| factor | 100 hits | 1K hits | 100K hits | 1M hits |
| --- | --- | --- | --- | --- |
| 0 | 104 | 255 | 255 | 255 |
| 1 | 18 | 49 | 255 | 255 |
| **10（默认）** | **10** | **18** | **142** | 255 |
| 100 | 8 | 11 | 49 | 143 |

本 demo 用两种方式复现这张表：

- **均值场近似**：`c ← c + 1/((c-5)·f+1)`，O(hits)
- **精确马尔可夫链**：在 0..255 上跑概率分布（仅小 hits 可用）

两者相差 **< 0.5**，说明均值场近似足够用；但与官方表格仍有最多约 ±5 的偏差（最大相对误差出现在最小的格子 `factor=100 / 100 hits`：6.72 vs 8，16%）。原因是官方表格是**一次参考运行/典型值**，而本 demo 算的是**期望值**——这点差异要在读表时心里有数。

### 2.7 衰减：让计数器会「忘」

```c
unsigned long LFUDecrAndReturn(robj *o) {
    unsigned long ldt = o->lru >> 8;
    unsigned long counter = o->lru & 255;
    unsigned long num_periods = server.lfu_decay_time
        ? LFUTimeElapsed(ldt) / server.lfu_decay_time : 0;
    if (num_periods)
        counter = (num_periods > counter) ? 0 : counter - num_periods;
    return counter;
}
```

默认 `lfu-decay-time 1`：每过 1 分钟，计数减 1。**减的是绝对值而不是比例**，所以高频键（142）也扛不住长时间不访问——这正是「能适应访问模式漂移」的原因。

`lfu-decay-time 0` 表示**永不衰减**。

淘汰时选的是 `idle = 255 - LFUDecrAndReturn(kv)` 最大者，即**频率最低者**。

### 2.8 一个真实的小 bug：16 位分钟跨回绕少算 1

```c
unsigned long LFUTimeElapsed(unsigned long ldt) {
    unsigned long now = LFUGetTimeInMinutes();
    if (now >= ldt) return now-ldt;
    return 65535-ldt+now;
}
```

16 位能表示 **0..65535 共 65536 个值**，真正的环绕距离应是 `65536 - ldt + now`。源码用了 `65535`，于是**跨回绕时会少算 1 分钟**。

举例：`ldt = 65530`、`now = 5`，真实经过 `65536 - 65530 + 5 = 11` 分钟，源码算出 `10`。影响极小（最多让衰减少扣 1 分），但这类 off-by-one 正是手写环绕时间逻辑的经典坑。本 demo 把它写成断言固定下来。

### 2.9 淘汰策略与 volatile- 陷阱

官方文档列出 10 种策略：

`noeviction` · `allkeys-lru` · `allkeys-lrm` · `allkeys-lfu` · `allkeys-random` · `volatile-lru` · `volatile-lrm` · `volatile-lfu` · `volatile-random` · `volatile-ttl`

| 策略 | 语义 |
| --- | --- |
| `allkeys-lru` | 淘汰最近最少用（**默认推荐**） |
| `allkeys-lrm` | 淘汰最近最少**修改** —— 读多写少负载下区分「在读」与「被改」 |
| `allkeys-lfu` | 淘汰最不经常用 |
| `volatile-*` | 只在**设了 TTL** 的键里挑 |
| `volatile-ttl` | 挑剩余 TTL 最短的 |

**最大的坑**：

> The `volatile-xxx` policies behave like `noeviction` if no keys have an associated expiration.

也就是说配置写的是 `volatile-lru`，但如果业务从来不设 TTL，Redis **不会退化成 `allkeys-lru`，而是直接对写命令报错**。这个故障只在内存打满时才暴露，平时完全看不出来。

另外文档还提醒：**给键设 expire 本身也占内存**，所以 `allkeys-lru` 比任何 `volatile-*` 都省内存。

### 2.10 缓冲区不计入 maxmemory：防反馈环

`evict.c` 的 `freeMemoryGetNotCountedMemory()` 会把 AOF 缓冲区和复制缓冲区**排除**在 `maxmemory` 判据之外。源码注释解释得很直白：

> it can cause **feedback-loop** when we push DELs into them, putting more and more DELs will make them bigger, if we count them, we need to evict more keys, and then generate more DELs, maybe cause massive eviction loop, even all keys are evicted.

即：**淘汰 → 产生 DEL → 缓冲区变大 → 判定内存超限 → 再淘汰** 的正反馈。文档侧的建议是用 `INFO memory` 的 `mem_not_counted_for_evict` 观察这部分开销，并给 `maxmemory` 留出余量。

## 三、对比

| 维度 | LRU | LFU |
| --- | --- | --- |
| 记录的信息 | 最近访问时间（24 位，精度秒） | 16 位分钟 + 8 位概率计数 |
| 抗扫描污染 | 弱 | 强 |
| 适应模式漂移 | 天然适应 | 靠 `lfu-decay-time` 衰减 |
| 可调参数 | `maxmemory-samples` | `lfu-log-factor` / `lfu-decay-time` |
| 新键保护 | 无 | `LFU_INIT_VAL = 5` 保底 |

## 四、环境

- Python 3.13（纯标准库）
- C（C11，`gcc -std=c11 -O2 redis_expiry.c -lm -o re && ./re`）
- Go 1.22+（`fmt` / `math`）

## 五、运行方式

```bash
cd 07-数据存储/03-缓存/Redis过期与淘汰
python redis_expiry_selftest.py          # 64 条断言
gcc -std=c11 -O2 redis_expiry.c -lm -o re && ./re
go run redis_expiry.go redis_expiry_check.go
```

## 六、关键代码

`GT`/`LT` 必须比**绝对过期时间点**（这是本 demo 第一版写错、被断言抓出来的地方）：

```python
cur_expire_at = INF if cur is None else cur     # 非易失 = infinite
new_expire_at = now + ttl
if option == "GT":   ok = new_expire_at > cur_expire_at
elif option == "LT": ok = new_expire_at < cur_expire_at
```

Morris 计数器与衰减都只有几行，但方向性决定了整个 LFU 的行为：

```python
def lfu_log_incr(counter, r, lfu_log_factor=10):
    if counter == 255: return 255
    baseval = max(0, counter - LFU_INIT_VAL)
    p = 1.0 / (baseval * lfu_log_factor + 1)
    return counter + 1 if r < p else counter

def lfu_decay(counter, ldt, now_minutes, lfu_decay_time=1):
    if lfu_decay_time == 0: return counter      # 0 = 永不衰减
    num_periods = lfu_time_elapsed(ldt, now_minutes) // lfu_decay_time
    if num_periods:
        counter = 0 if num_periods > counter else counter - num_periods
    return counter
```

## 七、性能边界

- 惰性删除是 O(1)；定期删除每轮只扫 `20 + 5·effort` 个键，**过期键的清理是概率性的**，内存占用会高于「所有过期键立刻消失」的理想值
- `maxmemory-samples` 默认 5，样本越大越接近真 LRU 但 CPU 越贵；文档说样本 10 时已「非常接近」，且在幂律访问下与真 LRU 「差异极小或没有」
- LFU 的 8 位计数器在高频下饱和到 255，之后所有热键频率相同、区分度归零，只能靠衰减恢复
- `volatile-*` 在没有 TTL 键时**不淘汰而是报错**，这是配置类故障里最难发现的一种
- 精确马尔可夫链是 O(hits × 256)，1M 次访问要跑几分钟；生产上用均值场近似即可（两者差 < 0.5）

## 八、注意事项与常见坑

1. **`GT` 对永久键永远失败、`LT` 永远成功**（非易失键视为 infinite TTL）。
2. **`GT`/`LT` 比的是过期时间点，不是剩余 TTL** —— 本 demo 第一版就写错，靠断言才发现。
3. **`ttl <= 0` 或过去时间是删除，键事件是 `del` 不是 `expired`**，监听过期事件的业务会漏。
4. **`SET` 会清 TTL 而 `INCR`/`LPUSH`/`HSET` 不会**，判据是「整体替换 vs 就地修改」。
5. **`volatile-*` 在无 TTL 键时表现为 `noeviction`**，不是退化成 `allkeys-*`。
6. **设 expire 本身占内存**，`allkeys-lru` 比 `volatile-*` 省内存。
7. **LFU 衰减是减绝对值不是减比例**，长时间不访问的高频键最终也会归零。
8. **`lfu-decay-time 0` = 永不衰减**，老热数据会永远赖着。
9. **16 位分钟跨回绕少算 1 分钟**（源码用 65535 而非 65536）。
10. **AOF/复制缓冲区不计入 maxmemory**，否则会形成「淘汰 → DEL → 缓冲区变大 → 再淘汰」的反馈环。
11. **副本不独立过期键**（`EXPIRE` 文档原文：replicas "will not expire keys independently (but will wait for the DEL coming from the master)"），但副本保存完整过期状态，被选为主后可独立过期。这个语义本轮已研读但未建模成断言。

## 九、参考资料

- Redis 文档《Key eviction》—— 策略清单与语义、`volatile-xxx` 退化为 noeviction、`maxmemory-samples` 5 与候选池、LFU 的 Morris 计数器与 factor 对照表、`mem_not_counted_for_evict` 与反馈环 — https://redis.io/docs/latest/develop/reference/eviction/
- Redis 文档《EXPIRE》—— `NX/XX/GT/LT`（7.0 加入）、non-volatile 视为 infinite TTL、清/保留 TTL 的命令、非正数超时触发 `del` 而非 `expired`、`RENAME` 转移 TTL、复制链路上副本等待主的 `DEL` — https://redis.io/docs/latest/commands/expire/
- `redis/src/expire.c` — `ACTIVE_EXPIRE_CYCLE_*` 五个常量、`server.hz`（通常 10）、`effort` 换算公式（本轮抓取自 `raw.githubusercontent.com/redis/redis/unstable/src/expire.c`）
- `redis/src/evict.c` — `LFULogIncr`、`LFUDecrAndReturn`、`LFUTimeElapsed`（16 位回绕）、`idle = 255 - LFUDecrAndReturn(kv)`、`freeMemoryGetNotCountedMemory`
- `redis/src/server.h` — `#define LFU_INIT_VAL 5`
- `redis/src/object.c` — `o->lru = (LFUGetTimeInMinutes() << 8) | LFU_INIT_VAL`
