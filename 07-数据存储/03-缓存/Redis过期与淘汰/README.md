# Redis 过期与淘汰（EXPIRE / activeExpireCycle / LFU）

## 一、简介

Redis 的「缓存」身份决定它必须回答两个问题：**键什么时候消失**（过期）和**内存满了牺牲谁**（淘汰）。本 demo 把这两块的**语义边界**与**参数换算公式**做成可验证模型，数值全部来自 Redis 官方文档与 `redis/src` 源码。

重点不在 API 用法，而在「写错不报错、但行为完全不同」的地方：`GT`/`LT` 比的是过期时间点而不是剩余 TTL、`volatile-*` 在无 TTL 键时**静默退化成 noeviction**、LFU 的 16 位分钟时间戳跨回绕少算 1 分钟。

## 二、原理详解

### 2.1 EXPIRE 的 NX / XX / GT / LT（Redis 7.0 加入）

| 选项 | 语义 |
| --- | --- |
| `NX` / `XX` | 仅当键**没有** / **已有**过期时间时设置 |
| `GT` / `LT` | 仅当新的过期时间 **>** / **<** 当前过期时间 |

文档原文：*A non-volatile key is treated as an **infinite TTL** for the purpose of `GT`.* 于是产生反直觉组合：**`GT` 对永久键永远失败，`LT` 永远成功**。想给永久键加过期时间只能写 `LT`（或 `NX`），写 `GT` 会静默返回 0。

另一个易错点：**比的是过期的绝对时间点，不是剩余 TTL**。本 demo 第一版就写成比较剩余 TTL，被断言抓出来——键在 `now=900` 时过期时间是 `1000`（剩余 100），`EXPIRE key 10 GT` 的新过期时间是 `910`；比剩余 TTL（910 > 100）会误判成立，按文档应比 `910` vs `1000` → 不成立。

### 2.2 过期 ≠ 删除：键事件不同

文档原文：*calling `EXPIRE`/`PEXPIRE` with a non-positive timeout or `EXPIREAT`/`PEXPIREAT` with a time in the past will result in the key being **deleted** rather than expired (accordingly, the emitted key event will be `del`, not `expired`)*。

`ttl=0`、`ttl<0`、过去时间都是**立即删除**，键空间通知发 `del`。监听 `expired` 事件做清理的业务（关单、释放库存）**收不到这些路径的通知**。

### 2.3 哪些命令清 TTL

判据是「**整体替换**还是**就地修改**」。清 TTL：`DEL` / `SET` / `GETSET` / 所有 `*STORE`；保留 TTL：`INCR` / `LPUSH` / `HSET` / `APPEND`。所以 `SET k 1` 后再 `EXPIRE` 的顺序很关键，而 `INCRBY` 可以放心用。

### 2.4 定期删除：activeExpireCycle 参数换算

`redis/src/expire.c` 的五个常量：

```c
#define ACTIVE_EXPIRE_CYCLE_KEYS_PER_LOOP 20    /* Keys for each DB loop. */
#define ACTIVE_EXPIRE_CYCLE_FAST_DURATION 1000  /* Microseconds. */
#define ACTIVE_EXPIRE_CYCLE_SLOW_TIME_PERC 25   /* Max % of CPU to use. */
#define ACTIVE_EXPIRE_CYCLE_ACCEPTABLE_STALE 10 /* % of stale keys ... extra efforts. */
```

换算（`effort = server.active_expire_effort - 1`，取值 1..10，默认 1）：

```c
config_keys_per_loop         = 20   + 20/4   * effort;
config_cycle_fast_duration   = 1000 + 1000/4 * effort;
config_cycle_slow_time_perc  = 25   + 2 * effort;
config_cycle_acceptable_stale= 10   - effort;
```

| `active_expire_effort` | 每轮扫键数 | fast 上限(µs) | slow CPU 上限(%) | 容忍陈旧(%) |
| --- | --- | --- | --- | --- |
| 1（默认） | 20 | 1000 | 25 | 10 |
| 10 | 65 | 3250 | 43 | 1 |

方向性：**effort 越大，扫得越多、占 CPU 越多，但容忍的陈旧比例越低**。注意 `acceptable_stale = 10 - effort` **永远不会到 0**。

### 2.5 LFU：24 位 lru 字段布局

`object.c`：`o->lru = (LFUGetTimeInMinutes() << 8) | LFU_INIT_VAL;` —— **高 16 位是最后访问的分钟数，低 8 位是频率计数器（0..255）**。`LFU_INIT_VAL = 5`（`server.h`）：新对象有 5 点保底分，源码注释说这是为了 *some accesses before being trashed away*。

### 2.6 Morris 概率计数器

```c
uint8_t LFULogIncr(uint8_t counter) {
    if (counter == 255) return 255;
    double r = (double)rand()/RAND_MAX;
    double baseval = counter - LFU_INIT_VAL;   /* < 0 则取 0 */
    double p = 1.0/(baseval*server.lfu_log_factor+1);
    if (r < p) counter++;
    return counter;
}
```

计数器越大越难再 +1，于是 8 位能表示**对数级**访问量。官方对照表（默认 `lfu-log-factor 10` 加粗）：

| factor | 100 hits | 1K | 100K | 1M |
| --- | --- | --- | --- | --- |
| 0 | 104 | 255 | 255 | 255 |
| 1 | 18 | 49 | 255 | 255 |
| **10** | **10** | **18** | **142** | 255 |
| 100 | 8 | 11 | 49 | 143 |

本 demo 用两种方式复现：**均值场近似** `c ← c + 1/((c-5)·f+1)`（O(hits)）与**精确马尔可夫链**（0..255 上跑概率分布，仅小 hits）。两者相差 **< 0.5**，说明近似够用；但与官方表格仍有最多约 ±5 的偏差（最大相对误差在最小格子 `factor=100 / 100 hits`：6.72 vs 8，16%）——官方表是**一次参考运行/典型值**，本 demo 算的是**期望值**。

### 2.7 衰减：让计数器会「忘」

`evict.c`：`num_periods = LFUTimeElapsed(ldt) / server.lfu_decay_time; counter = (num_periods > counter) ? 0 : counter - num_periods;`

默认 `lfu-decay-time 1`：每过 1 分钟减 1。**减的是绝对值不是比例**，所以高频键（142）也扛不住长期不访问——这正是能适应访问模式漂移的原因。`lfu-decay-time 0` = **永不衰减**。淘汰时选 `idle = 255 - LFUDecrAndReturn(kv)` 最大者，即频率最低者。

### 2.8 一个真实的小 bug：跨回绕少算 1 分钟

```c
return 65535-ldt+now;    /* LFUTimeElapsed 的回绕分支 */
```

16 位能表示 **0..65535 共 65536 个值**，真正环绕距离应是 `65536 - ldt + now`。源码用 `65535`，**跨回绕时少算 1 分钟**（`ldt=65530`、`now=5`：真实 11，源码 10）。影响极小（最多少扣 1 分），但这是手写环绕时间逻辑的经典 off-by-one，本 demo 写成断言固定下来。

### 2.9 淘汰策略与 volatile- 陷阱

10 种策略：`noeviction` / `allkeys-{lru,lrm,lfu,random}` / `volatile-{lru,lrm,lfu,random,ttl}`。`allkeys-lrm` 淘汰**最近最少修改**，用于读多写少负载下区分「在读」与「被改」。

**最大的坑**（文档原文）：*The `volatile-xxx` policies behave like `noeviction` if no keys have an associated expiration.* 即配了 `volatile-lru` 但业务从不设 TTL，Redis **不会退化成 `allkeys-lru`，而是直接对写命令报错**。这故障只在内存打满时暴露，平时完全看不出来。另外**设 expire 本身也占内存**，所以 `allkeys-lru` 比任何 `volatile-*` 都省内存。

### 2.10 缓冲区不计入 maxmemory：防反馈环

`evict.c` 的 `freeMemoryGetNotCountedMemory()` 把 AOF/复制缓冲区排除在 `maxmemory` 判据外。源码注释：*it can cause **feedback-loop** when we push DELs into them ... we need to evict more keys, and then generate more DELs, maybe cause massive eviction loop, even all keys are evicted*。即「淘汰 → DEL → 缓冲区变大 → 再淘汰」的正反馈。

## 三、对比

| 维度 | LRU | LFU |
| --- | --- | --- |
| 记录的信息 | 最近访问时间（24 位，秒精度） | 16 位分钟 + 8 位概率计数 |
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

`GT`/`LT` 必须比**绝对过期时间点**（本 demo 第一版写错、被断言抓出来的地方）：

```python
cur_expire_at = INF if cur is None else cur     # 非易失 = infinite
new_expire_at = now + ttl
if option == "GT":   ok = new_expire_at > cur_expire_at
elif option == "LT": ok = new_expire_at < cur_expire_at
```

Morris 计数与衰减各只有几行，但方向性决定整个 LFU 行为：

```python
def lfu_log_incr(counter, r, lfu_log_factor=10):
    if counter == 255: return 255
    baseval = max(0, counter - LFU_INIT_VAL)
    return counter + 1 if r < 1.0 / (baseval * lfu_log_factor + 1) else counter

def lfu_decay(counter, ldt, now_minutes, lfu_decay_time=1):
    if lfu_decay_time == 0: return counter          # 0 = 永不衰减
    num_periods = lfu_time_elapsed(ldt, now_minutes) // lfu_decay_time
    return (0 if num_periods > counter else counter - num_periods) if num_periods else counter
```

## 七、性能边界

- 惰性删除 O(1)；定期删除每轮只扫 `20 + 5·effort` 个键，**过期键清理是概率性的**，内存占用高于「立即消失」的理想值
- `maxmemory-samples` 默认 5，样本越大越接近真 LRU 但 CPU 越贵；文档称样本 10 已「非常接近」，幂律访问下与真 LRU「差异极小或没有」
- 8 位计数器在高频下饱和到 255，之后所有热键频率相同、区分度归零，只能靠衰减恢复
- `volatile-*` 在无 TTL 键时**不淘汰而是报错**，是最难发现的配置类故障之一
- 精确马尔可夫链是 O(hits × 256)，1M 次访问要跑几分钟；均值场近似差 < 0.5，足够用

## 八、注意事项与常见坑

1. **`GT` 对永久键永远失败、`LT` 永远成功**（非易失 = infinite TTL）。
2. **`GT`/`LT` 比的是过期时间点，不是剩余 TTL** —— 本 demo 第一版就写错，靠断言才发现。
3. **`ttl <= 0` 或过去时间是删除，键事件是 `del` 不是 `expired`**，监听过期事件的业务会漏。
4. **`SET` 清 TTL 而 `INCR`/`LPUSH`/`HSET` 不清**，判据是「整体替换 vs 就地修改」。
5. **`volatile-*` 在无 TTL 键时表现为 `noeviction`**，不是退化成 `allkeys-*`；且设 expire 本身占内存。
6. **LFU 衰减减的是绝对值不是比例**，长期不访问的高频键最终也会归零；`lfu-decay-time 0` = 永不衰减。
7. **16 位分钟跨回绕少算 1 分钟**（源码用 65535 而非 65536）。
8. **AOF/复制缓冲区不计入 maxmemory**，否则形成「淘汰 → DEL → 缓冲区变大 → 再淘汰」的反馈环。
9. **副本不独立过期键**（`EXPIRE` 文档原文：replicas *will not expire keys independently (but will wait for the DEL coming from the master)*），但副本保存完整过期状态，被选为主后可独立过期。本轮已研读，未建模成断言。

## 九、参考资料

- Redis 文档《Key eviction》—— 策略清单、`volatile-xxx` 退化为 noeviction、`maxmemory-samples` 5 与候选池、LFU Morris 计数器与 factor 对照表、`mem_not_counted_for_evict` 与反馈环 — https://redis.io/docs/latest/develop/reference/eviction/
- Redis 文档《EXPIRE》—— `NX/XX/GT/LT`（7.0 加入）、non-volatile 视为 infinite TTL、清/保留 TTL 的命令、非正数超时触发 `del` 而非 `expired`、`RENAME` 转移 TTL、副本等待主的 `DEL` — https://redis.io/docs/latest/commands/expire/
- `redis/src/expire.c` — `ACTIVE_EXPIRE_CYCLE_*` 五个常量、`server.hz`（通常 10）、`effort` 换算
- `redis/src/evict.c` — `LFULogIncr` / `LFUDecrAndReturn` / `LFUTimeElapsed`（16 位回绕）/ `idle = 255 - LFUDecrAndReturn(kv)` / `freeMemoryGetNotCountedMemory`
- `redis/src/server.h`（`#define LFU_INIT_VAL 5`）、`redis/src/object.c`（lru 字段打包）
