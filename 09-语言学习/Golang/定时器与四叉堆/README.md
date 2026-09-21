# Go 定时器与四叉堆（runtime/time.go）

## 简介

`time.Timer` / `time.Ticker` / `time.After` 背后不是「每个定时器一个线程」，而是
**每个 P（Processor）一个四叉最小堆**：堆顶就是下一个要到点的定时器，调度器和
网络轮询器只看堆顶就能知道「最多能睡多久」。

关键概念：

| 概念 | 一句话 |
| --- | --- |
| `timers`（heap） | per-P 的定时器集合，元素类型 `timerWhen{timer, when}` |
| `timerWhen` | 堆里同时存**指针**和**时刻快照**；快照允许延后与 `t.when` 不一致 |
| `timerHeaped/Modified/Zombie` | 三个状态位，决定「在堆里 / 改过还没同步 / 已停还没摘」 |
| `siftUp/siftDown` | 四叉堆的上下沉，`timerHeapN = 4` |
| `Stop/Reset` | 都只改状态位，**不动堆**；真正的摘除延后到 `cleanHead`/`adjust` |

历史：Go 1.9 之前是**全局** `timersBucket`（64 个分桶 + 全局锁），Go 1.10~1.13 改为 per-P 堆，
Go 1.14 引入 netpoll 与定时器的协同唤醒，Go 1.23 又重写了 `time.Timer`/`Ticker` 的语义
（不再需要 `Stop` 后手动 drain channel）。本 demo 对应 **master 分支现行实现**。

## 原理详解

### 1. 为什么是四叉堆

```go
const timerHeapN = 4

p := int(uint(i-1) / timerHeapN)   // parent
leftChild := i*timerHeapN + 1      // 孩子是 4i+1 .. 4i+4
```

四叉堆把「堆高度」压到二叉堆的一半左右，缓存局部性更好（一次把 4 个孩子读进来比较）。
代价是 `siftDown` 每步要扫 4 个孩子取最小。实测同一批数据下，四叉堆的上浮次数约为二叉堆的
**0.57 倍**（1000 个递减插入：四叉 4547 次 vs 二叉 7987 次，见自检 E5）。

节点数与层数的关系（闭式）：深度 0..d 共 `(4^(d+1)-1)/3` 个节点，所以
**85 个定时器刚好填满 4 层**（最大深度 3）。

### 2. 堆里存的是「快照」——这是全部复杂性的来源

```go
type timerWhen struct {
    timer *timer
    when  int64   // 快照，可能与 t.when 不同
}
```

`Reset` 走 `t.modify()`：它只更新 `t.when` 并置 `timerModified`，**不去动堆里的快照**。
同步发生在两处：

- `t.updateHeap()`：只处理**堆顶**那一个（zombie 就 `deleteMin`，modified 就改快照后 `siftDown(0)`）；
- `ts.adjust(now, force)`：扫全堆，清 zombie、把 modified 的 `when` 写回快照，最后 `initHeap()` O(n) 重建。

> `adjust` 有早退：`minWhenModified == 0 || minWhenModified > now` 时直接返回。
> 「来回 Reset 但很少真的到期」的程序因此几乎不吃这份扫描成本（自检 E9）。

### 3. 三个状态位

```go
timerHeaped   uint8 = 1 << iota  // 1：在某个 P 的堆里
timerModified                    // 2：t.when 已改，快照未同步
timerZombie                      // 4：已 Stop，但还留在堆里
```

- `Stop()`：置 `Modified|Zombie`、`zombies++`、`t.when = 0`；返回值 `pending = t.when > 0`
  （**在清零之前**取的值）表示「是否抢在触发前停下了」。**堆长度不变**。
- `Reset()`（= `modify`）：如果它是 zombie，就 `zombies--` 并清掉 zombie 位——**被 Stop 的定时器会被 Reset 复活**。
- 触发时（`unlockAndRun`）：`period == 0` 的一次性定时器把 `next` 算成 0，于是置 zombie 后
  立刻被 `updateHeap → deleteMin` 摘掉。`zombies` 的 +1/-1 是瞬时的（自检 E11 用峰值记录到了）。

> 注意 `timerHeaped` 不是在 `addHeap` 里置的，而是在调用方 `maybeAdd()` 里（time.go:718）。
> 本 demo 的模型把它并进 `add_heap`/`AddHeap`，走的是公开路径。

### 4. Ticker 用闭式跳过 missed tick

```go
delay := now - t.when
next = t.when + t.period*(1 + delay/t.period)   // 整数除法
```

不是「补发」落后的每一拍。例：`when=10, period=5, now=27` → `delay=17`，`17/5=3`，
`next = 10 + 5*4 = 30`，**15/20/25 三拍被直接跳过**，只触发一次。

### 5. 谁负责清理

`cleanHead()` 在每次 `maybeAdd` 之前调用，策略是：

1. 先看**堆尾**：是 zombie 就直接 `heap = heap[:n-1]`，**零堆调整**；
2. 再看堆顶：快路径是 `astate & (Modified|Zombie) == 0` 直接返回；
3. 否则 `updateHeap()`（删除或下沉），循环。

### 6. 唤醒谁

`wakeTime() = min(minWhenModified, minWhenHeap)`（modified 优先，官方注释解释了读取顺序的原因）。
系统调用 `netpoll` 的等待时长就取这个值——**定时器与网络轮询器共用一次睡眠**。

## 对比 / 选型

| 维度 | 四叉堆（Go 现行） | 二叉堆 | 时间轮 |
| --- | --- | --- | --- |
| 入堆/出堆 | O(log₄ n) | O(log₂ n) | O(1) |
| 取最小 | O(1) | O(1) | O(1) 但按槽位 |
| 任意时刻（含超远期） | 好 | 好 | 差（需多级轮） |
| 缓存局部性 | 好（4 孩子连续） | 一般 | 好 |

Go 选堆而不是时间轮，是因为 `time.After(3 * time.Hour)` 这种「任意远期」定时很常见。

## 环境准备

- 操作系统：任意（模型不依赖系统调用）
- Go：1.21+（本 demo 只用标准库 `fmt`）
- Python：3.8+

## 运行方式

### Python

```bash
cd python
python selfcheck_timers.py   # 100 条断言（会自动带上 timerchecks.py 里的 E8~E14）
python main.py               # 四组可观察结论
```

### Go

```bash
cd go
go run .                     # 同样的五组结论，内置 check 断言
```

## 关键代码片段

Python 侧拆成三个模块：`timerheap.py`（四叉堆的上下沉与增删）、`timermodel.py`
（三个状态位与 `updateHeap`/`cleanHead`/`adjust`/`run`）、`timerchecks.py`（E8~E14 断言）。
最核心的 20 行是 `timerheap.py` 的 `sift_down`：

```python
def sift_down(self, i):
    heap = self.heap
    n = len(heap)
    if i >= n: bad_timer()
    if i * TIMER_HEAP_N + 1 >= n:
        return                      # 官方的提前返回：i 已经是叶子
    tw = heap[i]
    while True:
        left = i * TIMER_HEAP_N + 1
        if left >= n: break
        w, c = tw, -1
        for j, twj in enumerate(heap[left:min(left + TIMER_HEAP_N, n)]):
            if less(twj, w): w, c = twj, left + j
        if c < 0: break
        heap[i] = heap[c]; i = c
    if heap[i].timer is not tw.timer: heap[i] = tw
```

`go/heap.go` 的 `SiftDown`、`go/timers.go` 的 `UpdateHeap/CleanHead/Adjust/Run` 与之逐行对应。

## 性能与边界

- `maxWhen = 1<<63 - 1`；`newstack`/`modify` 的 `when` 必须为正，否则 `throw`
- `modify` 要求 `when > 0`、`period >= 0`
- `siftUp`/`siftDown` 对 `when <= 0` 报 `badTimer`（"timer data corruption"）；
  但 `siftDown` 的这个检查在「叶子提前返回」之后，**单元素堆里根本走不到**（自检 E14）
- 堆损坏不 panic 在持锁路径上，而是 `throw("timer data corruption")`（issue #25686）

## 注意事项与常见坑

1. **`Stop()` 不保证 channel 里没有值**：本模型里 `pending` 只反映状态位；真实代码里
   Go 1.23 之前的 `Timer.Stop` 需要手动 drain channel，1.23 起不必。
2. **`Reset()` 后堆快照没变**：想观察「改晚了」必须触发一次 `updateHeap`/`adjust`，
   否则 `heap[0].when` 还是旧值——这不是 bug，是官方的延迟同步设计。
3. **把堆顶改早只能靠 `adjust`，不能靠 `cleanHead`**：`updateHeap` 里只有 `siftDown(0)`，
   没有 `siftUp`。改早的定时器靠 `adjust` 扫到后 `initHeap()` 重建。
4. **同一时刻的次序由 `rand` 决定**（`timerWhen.less` 的第三段），只有 fake time 会设 `rand`。
5. **Ticker 不补发**：错过 3 拍就是错过 3 拍，不要依赖它做「每 5 秒必须执行一次 N 次」的语义。
6. 状态位 `Modified`/`Zombie` 只在 `Heaped` 也为真时才有意义（官方注释明写）。

## 参考资料（实际阅读过的权威来源）

- [Go 源码 src/runtime/time.go](https://github.com/golang/go/blob/master/src/runtime/time.go) — 四叉堆 `timerHeapN=4`、`siftUp/siftDown`、`stop/modify/updateHeap/cleanHead/adjust/run/unlockAndRun`、三个状态位与 `maxWhen` 全部逐行对照
- [Go 源码 src/runtime/proc.go](https://github.com/golang/go/blob/master/src/runtime/proc.go) — `checkTimers`/`findRunnable` 里 `wakeTime` 与 netpoll 等待时长的衔接
- [Go 1.23 Release Notes](https://go.dev/doc/go1.23) — `time.Timer`/`Ticker` 语义变化（无需手动 drain）的背景
- [Go 官方包文档 time](https://pkg.go.dev/time) — `Timer`/`Ticker` 的公开语义与 `Reset` 的使用约束
