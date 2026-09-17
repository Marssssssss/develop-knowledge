# Go 内存模型与 sync 原语：happens-before / DRF-SC / Mutex 双模式

## 简介

Go 的并发正确性建立在两条分开的定义上：**语言规范**给出 `happens before` 形式化语义，
**`sync` 包**给出具体同步原语的强度。很多人只记住"用 channel 或用锁"，但不知道
"先收后发"在**无缓冲** channel 上是有保证的、换成**缓冲 1** 就不保证——差别就在规范里。

本 demo 把两件事都做成可执行模型：

1. `happens before` 关系（`sequenced before` ∪ `synchronized before` 的传递闭包）、
   **数据竞争**判定与 **DRF-SC**；
2. `sync.Mutex` 的 `state` 位布局与**正常模式 / 饥饿模式**双模式切换。

关键概念：

| 概念 | 一句话 |
| --- | --- |
| memory operation | 四要素：kind（普通读写 or 同步操作）、程序位置、访问的位置、读写的值 |
| read-like / write-like | read/atomic read/lock/recv 与 write/atomic write/unlock/send/close |
| synchronized before | 同步读 `r` 观察到同步写 `w` ⇒ `w` synchronized before `r` |
| happens before | `sequenced before` ∪ `synchronized before` 的**传递闭包** |
| 数据竞争 / DRF-SC | 同位置、至少一个非同步、HB 不可比；无竞争程序等价于某顺序交错 |

历史背景：现行定义（2022-06-06 版）明确采用 Boehm & Adve 在 PLDI 2008 提出的
C++ 并发内存模型框架；相比 C/C++，Go 对"有竞争的程序"只做有限约束（更接近 Java/JS），
目标是"让出错程序更容易调试"，而不是"完全未定义"。

## 原理详解

### 1. 三个 Requirement

`go.dev/ref/mem` 的形式化定义只有三条要求：

1. **Requirement 1**：每个 goroutine 内部的操作序列必须与其 `sequenced before`
   （语言规范定义的控制流与表达式求值顺序）相容。
2. **Requirement 2**：把映射 `W` 限制在同步操作上，必须能由某个**隐式全序**解释。
3. **Requirement 3**：对普通读 `r`，`W(r)` 必须是一个**可见**的写 `w`——`w` happens
   before `r`，且不存在另一个 `w'` 也 happens before `r` 而 `w` happens before `w'`。
   可见写的唯一性正是本 demo 断言的那一条。

### 2. 各同步原语的强度（规范原文结论）

规范对每条原语给出的保证词强度不同，最反直觉的三条是：

- goroutine **退出不保证**同步于任何事件（连那条 `go` 语句本身都可能被优化掉）；
- `TryLock` **成功**等价于 `Lock`，但**失败则完全不建立同步关系**；
- 所有 atomic 操作共享**一个 SC 全序**，而普通读写没有。

完整的 12 条原语强度表见 [`NOTES.md §1`](./NOTES.md)。本 demo 把表中每一条都建成
一个执行场景并断言其 HB 关系与竞争结论（B 组、C 组断言）。

### 3. 为什么"无缓冲先收后发"与"缓冲先收后发"不同

```go
var c = make(chan int)      // 无缓冲
var a string
func f() { a = "hello, world"; <-c }
func main() { go f(); c <- 0; print(a) }   // 保证打印
```

无缓冲时规范额外给出"**receive synchronized before 对应 send 完成**"，于是
`写 a → receive → send → print` 连成一条 HB 链。把 `make(chan int)` 改成
`make(chan int, 1)`，这条边就消失了——文档明确说该程序"**不保证**打印"，可能打印空串、可能崩。
本 demo 用 B2 / B3 两个断言把这组对照钉死。

### 4. sync.Mutex 的状态位

`internal/sync/mutex.go`（Go 1.24 起从 `sync` 包移出）用**一个 int32 + 一个信号量**实现：

```text
state int32:
  bit0  mutexLocked    = 1    已加锁
  bit1  mutexWoken     = 2    已有 goroutine 认领了"唤醒权"
  bit2  mutexStarving  = 4    饥饿模式
  bit3..                等待者计数（mutexWaiterShift = 3）
sema uint32:            阻塞/唤醒用
```

### 5. 正常模式：允许插队

原文：*In normal mode waiters are queued in FIFO order, but a woken up waiter does not own
the mutex and competes with new arriving goroutines over the ownership. New arriving
goroutines have an advantage -- they are already running on CPU.*

```text
t=0     G1 快路径 CAS 拿到锁
t=10    G2 排队（队尾）            队列 [2]
t=20    G3 排队（队尾）            队列 [2,3]
t=100   解锁 → 唤醒队首 G2（置 WOKEN），队列 [3]
t=101   G4 新到 → 快路径**插队成功** ← 长尾的来源
t=102   G2 醒来发现锁被抢 → 插回**队首**（queueLifo），队列 [2,3]
```

关键细节是"输了的等待者排到**队首**而不是队尾"（源码 `queueLifo := waitStartTime != 0`
后 `runtime_SemacquireMutex(&m.sema, queueLifo, 2)`）：它保证正常模式不会出现无限饥饿。

另一个细节：`mutexWoken` 只在**已有等待者**时才会被认领
（源码条件 `!awoke && old&mutexWoken == 0 && old>>mutexWaiterShift != 0`）。

### 6. 饥饿模式：移交所有权

某个等待者连续失败超过 **1ms**（`starvationThresholdNs = 1e6`）后，它自己在 CAS 时把
`mutexStarving` 写进 state（前提：**当前确实被锁住**，否则 Unlock 会看到"饥饿但没有等待者"的矛盾状态）。
进入饥饿模式后：

- 新到者**不抢、不自旋**，直接排到**队尾**；
- Unlock 把所有权**直接移交**给队首，并让出时间片（`runtime_Semrelease(&m.sema, true, 2)`）；
- 拿到所有权的等待者若能满足「**自己是最后一个等待者**」或「**等待时间 < 1ms**」，
  就清掉 `mutexStarving` 退回正常模式。

原文对此的解释是本 demo 想传达的重点：*Normal mode has considerably better performance...
Starvation mode is important to prevent pathological cases of tail latency.*
即两种模式是**吞吐 vs 尾延迟**的取舍，而切换标准就是那 1ms。

## 对比 / 选型

与 Java `ReentrantLock`（构造时二选一公平策略）、C++ `std::mutex`（通常不公平）相比，
Go 的路线是"**默认最大化吞吐，只在检测到病态长尾时短暂切到公平**"。
完整对照表见 [`NOTES.md §2`](./NOTES.md)。

## 环境准备

任意操作系统（模型与平台无关）；Python 3.8+（实测 3.13.12）；Go 仅 `go/` 目录需要，
复现真实竞争检测（`-race`）才需 Go 工具链。

## 运行方式

```bash
cd python && python3 main.py    # 推荐先跑：54 项断言，退出码 0 表示全绿
cd go && go run .               # main.go + memory_model.go + mutex.go，同一批断言
go test -race ./...             # 有工具链时：把本 demo 的"竞争"场景写成真实程序即可复现
```

## 关键代码片段

`python/memory_model.py` —— 传递闭包（Floyd–Warshall）：

```python
for a, b in self.sync_edges:
    hb[idx[id(a)]][idx[id(b)]] = True
for k in range(n):
    for i in range(n):
        if hb[i][k]:
            for j in range(n):
                if hb[k][j]:
                    hb[i][j] = True      # happens before = sequenced ∪ synchronized 的闭包
```

`python/sync_mutex.py` —— 饥饿模式的移交与退出条件：

```python
def unlock_slow(self, now):
    if not (self.state & STARVING):
        ...                                   # 正常模式：唤醒队首，它须与新到者竞争
    self.handoffs += 1                        # 饥饿模式：直接移交，且让出时间片
    return self.queue[0]

def acquire_handoff(self, gid, now, w):
    delta = LOCKED - (1 << WAITER_SHIFT)      # 拿到锁 + 自己不再算等待者
    leaving = (not w.starving) or self.waiters == 1
    if leaving:
        delta -= STARVING                     # 最后一个等待者 / 等得不久 → 退出饥饿模式
    self.state += delta
```

## 性能与边界

- **HB 计算复杂度**：本 demo 用 Floyd–Warshall 求闭包，O(n³)。真实程序的操作数巨大，
  所以规范只给**语义**，实际工具（`-race`）用 happens-before 的**增量**维护来做检测。
- **1ms 阈值写死**：`starvationThresholdNs = 1e6` 不可配置；它决定"多长的尾延迟才值得牺牲吞吐"。
- **饥饿模式的代价**：原文写明"*Starvation mode is so inefficient, that two goroutines can go
  lock-step infinitely once they switch mutex to starvation mode*"——所以退出条件（最后一个
  等待者 / 等待 < 1ms）必须在**拿到锁时立刻**判定。
- **未覆盖范围**：`RWMutex` writer 优先、`runtime_SemacquireMutex` 实现、硬件内存屏障，见
  [`NOTES.md §3`](./NOTES.md)。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| "我先收后发所以有同步" | 仅在**无缓冲** channel 上成立 | 缓冲 channel 需靠 send→receive 方向 |
| `go func(){ a = 1 }()` 后立刻读 `a` | goroutine 退出**不保证**同步 | 用 channel/WaitGroup 建立 HB |
| 相信 `TryLock` 失败也算"同步过" | 规范明写失败**完全无同步效果** | 别用 TryLock 做"检查可见性" |
| `sync.Mutex` 当可重入锁用 | Go 的 Mutex **不可重入** | 拆成两把锁或改成纯函数 |
| 复制含 Mutex 的结构体 | `go vet` 会报 copylocks；状态会裂开 | 用指针传递 |

建模时的实现坑（悬空 `WOKEN` 位、`awoke` 清除时机）见 [`NOTES.md §4`](./NOTES.md)。

## 参考资料（实际阅读过的权威来源）

- [The Go Memory Model（2022-06-06 版）](https://go.dev/ref/mem) — Requirement 1~3、synchronized before 与 happens before 的定义、数据竞争定义、DRF-SC，以及 Initialization / Goroutine creation / Goroutine destruction / Channel communication / Locks / Once / Atomic Values / Finalizers 全部保证条款。
- [go1.24.0 `src/internal/sync/mutex.go`](https://raw.githubusercontent.com/golang/go/go1.24.0/src/internal/sync/mutex.go) — 状态位、`starvationThresholdNs = 1e6`、`lockSlow`/`unlockSlow` 全流程、`queueLifo` 队首重排、饥饿模式移交与退出条件。
- [go1.24.0 `src/sync/mutex.go`](https://raw.githubusercontent.com/golang/go/go1.24.0/src/sync/mutex.go) — 公开语义："the n'th call to Unlock synchronizes before the m'th call to Lock for any n < m"、"A failed call to TryLock does not establish any synchronizes before relation at all"。
