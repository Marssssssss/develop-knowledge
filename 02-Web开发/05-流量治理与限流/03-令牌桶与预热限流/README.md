# 令牌桶与预热限流

Google Guava `RateLimiter` 的两种平滑实现——`SmoothBursty` 与 `SmoothWarmingUp`——的源码级转写。它的关键设计是：**限流器记住「过去的欠使用（underutilization）」**，用一个 `storedPermits` 维度把它折算成等待时间，而折算函数决定了一个限流器是「突发型」还是「预热型」。

## 一、公共骨架

`SmoothRateLimiter` 只有两个公共方法：

```java
final void resync(long nowMicros) {
  if (nowMicros > nextFreeTicketMicros) {
    double newPermits = (nowMicros - nextFreeTicketMicros) / coolDownIntervalMicros();
    storedPermits = min(maxPermits, storedPermits + newPermits);
    nextFreeTicketMicros = nowMicros;
  }
}

final long reserveEarliestAvailable(int requiredPermits, long nowMicros) {
  resync(nowMicros);
  long returnValue = nextFreeTicketMicros;              // ← 注意取的是**旧值**
  double storedPermitsToSpend = min(requiredPermits, this.storedPermits);
  double freshPermits = requiredPermits - storedPermitsToSpend;
  long waitMicros = storedPermitsToWaitTime(this.storedPermits, storedPermitsToSpend)
      + (long) (freshPermits * stableIntervalMicros);
  this.nextFreeTicketMicros = LongMath.saturatedAdd(nextFreeTicketMicros, waitMicros);
  this.storedPermits -= storedPermitsToSpend;
  return returnValue;
}
```

三个容易踩的点：

1. **返回值是旧的 `nextFreeTicketMicros`**。`acquire()` 的等待时长是 `max(momentAvailable - nowMicros, 0)`，而初始 `nextFreeTicketMicros = 0L` 位于过去，所以**第一次 `acquire()` 恒返回 0**，哪怕它已经把内部时钟推到了 3 秒之后（源码注释「15 fresh permits 需要 3 秒」说的正是内部时钟的位移，不是首次调用的返回值）。
2. **`resync` 只在 `nowMicros > nextFreeTicketMicros` 时推进**，且新令牌被 `maxPermits` 钳住——闲置再久也不会攒出超额突发。
3. **`nextFreeTicketMicros` 用饱和加法**（`LongMath.saturatedAdd`），超大 `acquire()` 钳到 `Long.MAX_VALUE` 而不是回绕成负数。

`stableIntervalMicros = 1e6 / permitsPerSecond`，单位一律是微秒。

## 二、SmoothBursty（默认）

- `maxPermits = maxBurstSeconds * permitsPerSecond`，`RateLimiter.create(qps)` 用的 `maxBurstSeconds = 1.0`。
- `storedPermitsToWaitTime` **恒为 0**：存储令牌免费，只用掉不用等。
- `coolDownIntervalMicros() = stableIntervalMicros`：冷却速率就是稳定速率。
- **初态 `storedPermits = 0`**（`oldMaxPermits == 0.0 → 0.0 // initial state`）——创建后立刻突发是没有额度的，额度靠闲置 `resync` 累积。

## 三、SmoothWarmingUp

`create(qps, warmupPeriod)` 走 `coldFactor = 3.0`。`doSetRate` 算三个派生量（单位均为微秒）：

```
coldInterval    = stableInterval * coldFactor
thresholdPermits= 0.5 * warmupPeriod / stableInterval
maxPermits      = thresholdPermits + 2 * warmupPeriod / (stableInterval + coldInterval)
slope           = (coldInterval - stableInterval) / (maxPermits - thresholdPermits)
```

`coolDownIntervalMicros() = warmupPeriod / maxPermits`——预热期的「攒令牌」速率比稳定期慢。

代价函数是一张梯形：`permitsToTime(p) = stableInterval + p * slope`，从 `stableInterval`（p=0）线性爬到 `coldInterval`（p=thresholdPermits 及以上）。取令牌时对这张图做积分：

- 右半段（高于 threshold）：梯形面积 `takeAbove * (f(t0) + f(t1)) / 2`。
- 左半段（低于 threshold）：`stableInterval * 剩余`。

源码注释给了两条面积恒等式，自检里各钉一条：

- `maxPermits → thresholdPermits` 的梯形面积 `= 0.5*(stable+cold)*(max-threshold) = warmupPeriod`
- `thresholdPermits → 0` 的面积 `= thresholdPermits * stableInterval = warmupPeriod / 2`

于是**从冷启动满桶一次取光所有令牌要花 1.5 × warmupPeriod**。以 `qps=10, warmup=1s` 为例：`threshold=5, max=10, slope=40000`，取光 10 张耗时 **1.5 秒**。

## 四、两种初态相反（最容易被搞混的一处）

| | `oldMaxPermits == 0.0`（初态） | `oldMaxPermits == +Infinity` |
| --- | --- | --- |
| `SmoothBursty` | `storedPermits = 0.0` | `storedPermits = maxPermits` |
| `SmoothWarmingUp` | `storedPermits = maxPermits` （注释：initial state is cold） | `storedPermits = 0.0` |

两种实现在这个分支上**恰好相反**。合起来的语义是：突发型「出厂时桶是空的、攒出来的才是突发」，预热型「出厂时桶是满的、但满桶意味着冷、要按最贵的价取」。自检用四条断言成对钉住。

## 五、tryAcquire 不刷新状态

`canAcquire(now, timeout)` 用的是 `queryEarliestAvailable()`，而 `SmoothRateLimiter.queryEarliestAvailable` 的实现是**直接返回 `nextFreeTicketMicros`，不调用 `resync`**。因此判据使用的是上一次 `reserve` 留下的值；其 javadoc 也承认「如果立即可用，返回的是一个任意的过去或当前时刻」。长时间闲置后未做 `reserve` 时，`nextFreeTicketMicros` 仍停留在过去，判据恒真——这与「闲置能攒出突发额度」的语义恰好自洽。

## 六、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/guava_ratelimiter.py` | `SmoothRateLimiter` 基类 + `SmoothBursty` + `SmoothWarmingUp` + `saturated_add` |
| `python/selfcheck_guava_ratelimiter.py` | 39 条断言（实跑全绿） |
| `python/main.py` | 闲置→突发额度、冷启动代价曲线、两种实现累计等待对比 |
| `go/warmingup.go` | Go 侧实现；因 Go 无虚派发，两个抽象钩子做成 `Limiter` 的函数字段由构造函数注入 |

## 参考资料

- google/guava@master `guava/src/com/google/common/util/concurrent/RateLimiter.java` — <https://raw.githubusercontent.com/google/guava/master/guava/src/com/google/common/util/concurrent/RateLimiter.java>（`create` / `coldFactor=3.0` / `tryAcquire` / `canAcquire` / `reserveAndGetWaitLength`）
- google/guava@master `guava/src/com/google/common/util/concurrent/SmoothRateLimiter.java` — <https://raw.githubusercontent.com/google/guava/master/guava/src/com/google/common/util/concurrent/SmoothRateLimiter.java>（设计说明长注释、`resync`、`reserveEarliestAvailable`、`SmoothWarmingUp.doSetRate` 与 `storedPermitsToWaitTime`、`SmoothBursty`）
