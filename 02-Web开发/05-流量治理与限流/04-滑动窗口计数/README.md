# 滑动窗口计数

Alibaba Sentinel `LeapArray` 的滑动窗口实现，以及它相对固定窗口解决了什么问题。核心结论：**滑动窗口把「窗口边界处的双倍突发」压掉了**，代价是需要在环形数组上做桶的创建 / 复用 / 重置 / 过期四态管理。

## 一、数据结构

`LeapArray(sampleCount, intervalInMs)` 用一个定长环形数组覆盖 `intervalInMs`：

```
sampleCount = intervalInMs / windowLengthInMs
```

构造期三条 `AssertUtil` 断言：`sampleCount > 0`、`intervalInMs > 0`、`intervalInMs % sampleCount == 0`（原文：*time span needs to be evenly divided*）。

下标与窗口起点：

```java
private int calculateTimeIdx(long timeMillis) {
    long timeId = timeMillis / windowLengthInMs;
    return (int)(timeId % array.length());       // 取模实现环形复用
}
protected long calculateWindowStart(long timeMillis) {
    return timeMillis - timeMillis % windowLengthInMs;
}
```

## 二、`currentWindow` 的四条分支

这是整个类最精妙的地方：

| 情形 | 处理 |
| --- | --- |
| `old == null` | 新建 `WindowWrap` 并 CAS 写入；竞争失败则 `Thread.yield()` 重试 |
| `windowStart == old.windowStart()` | 直接返回旧桶（同一窗口内累加） |
| `windowStart > old.windowStart()` | 拿到 `updateLock` 后 **复用对象并 reset**（不是新建） |
| `windowStart < old.windowStart()` | 时间回拨：返回**一个新桶但不写回数组** |

最后一条分支源码注释写着「Should not go through here」，但它是真实可达的（时钟回拨或传入历史时间）。它返回的桶是**临时对象**——连续调用两次拿到两个不同实例，两次的累加都丢掉了。这是实现里一个值得注意的语义：回拨时间的写入不落盘。

第三条分支「复用对象 + reset」而不是新建，配合 `updateLock`（只在桶过期时才生效的条件锁）避免写放大——Sentinel 明确注释这是为了在绝大多数情况下不损失性能。

## 三、过期判据是严格大于

```java
public boolean isWindowDeprecated(long time, WindowWrap<T> windowWrap) {
    return time - windowWrap.windowStart() > intervalInMs;
}
```

`time - start == intervalInMs` 时**尚未过期**。以 `sampleCount=2, interval=1000ms` 为例：`start=500` 的桶在 `t=1500` 时仍计入，到 `t=1501` 才被排除。这个 1 毫秒的差别直接决定「边界突发什么时候被放行」，自检里成对断言。

## 四、持续流量下所有桶都有效

在持续有流量的情况下，最老桶的起点距当前时刻最多 `intervalInMs - 1` 毫秒，因此 `time - start > intervalInMs` 恒为假——**所有 `sampleCount` 个桶都在 `values()` 的统计范围内**。

`isWindowDeprecated` 真正起作用的时刻是**流量中断**：数组里躺着上一个纪元的桶，长时间没人 reset 它，此时才需要靠过期判据把它排除。自检里 `t=1001` 时 `start=0` 的桶被判过期、有效桶数从 2 掉到 1，补一次 `add` 后又回到 2，钉住的正是这条。

覆盖的跨度因此落在「interval - windowLength」到「interval」之间（**左闭右开**）——略短于名义区间，属于实现固有的偏差。

## 五、边界突发：滑动窗口 vs 固定窗口

以 100 次/秒为例，在 `t=900ms` 一次性打满 100 次：

| 实现 | t=900 放行 | t=1001 放行 | 2 毫秒内合计 |
| --- | --- | --- | --- |
| 固定窗口（1s 网格） | 100 | 100 | **200** |
| LeapArray `sampleCount=2` | 100 | 0 | **100** |
| LeapArray `sampleCount=10` | 100 | 0 | **100** |

固定窗口在 `t=1001` 已进入新窗口、计数归零，于是又放行 100 次。滑动窗口在 `t=1001` 时 `start=500` 的旧桶仍未过期（1001−500 = 501 ≤ 1000），`values()` 已经是 100，因此全部拒绝；直到 `t=1501`（1001−500 = 1001 > 1000）旧桶过期才重新放行。

`sampleCount=1` 时 LeapArray 退化为固定窗口，自检里也保留了这一组便于对照。

## 六、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/leap_array.py` | `MetricBucket` / `WindowWrap` / `LeapArray`（四分支 + 过期判据）/ `FixedWindow` / `SlidingWindowLimiter` |
| `python/selfcheck_leap_array.py` | 37 条断言（实跑全绿） |
| `python/main.py` | 环形复用轨迹、三种 sampleCount 的边界突发对比、严格大于判据演示 |
| `go/leap_array.go` | Go 侧实现；CAS + `updateLock` 换成切片 + 互斥锁，分支结构与判据逐条对应 |

## 参考资料

- alibaba/Sentinel@master `sentinel-core/src/main/java/com/alibaba/csp/sentinel/slots/statistic/base/LeapArray.java` — <https://raw.githubusercontent.com/alibaba/Sentinel/master/sentinel-core/src/main/java/com/alibaba/csp/sentinel/slots/statistic/base/LeapArray.java>（构造断言、`calculateTimeIdx`、`calculateWindowStart`、`currentWindow` 四分支与注释、`isWindowDeprecated`、`list`）
- alibaba/Sentinel@master `.../base/WindowWrap.java` — <https://raw.githubusercontent.com/alibaba/Sentinel/master/sentinel-core/src/main/java/com/alibaba/csp/sentinel/slots/statistic/base/WindowWrap.java>
