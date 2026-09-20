# Go GMP 调度器：P 是并行度，M 是线程，G 是要跑的活

## 一、简介

Go 的调度器常被概括成三个字母：**G**（goroutine，带栈的执行体）、**M**（machine，操作系统线程）、**P**（processor，执行 Go 代码所需的**资源凭证**）。真正决定「同时能跑几个」的是 **P 的数量 = GOMAXPROCS**，不是 goroutine 数、也不是线程数。M 只是「载具」，进系统调用时会把 P 摘下来交给别的 M，这样阻塞的线程不会占用并行度。

本 demo 把调度骨架（入队/出队、全局队列、工作窃取、sysmon、抢占）建成可执行模型，常量与分支全部对齐 `src/runtime/proc.go` 与 `runtime2.go` 的源码。

## 二、原理详解

### 2.1 P 的本地队列：256 个槽 + 一个 runnext

`runtime2.go` 里 P 的字段是：

```go
runqhead uint32
runqtail uint32
runq     [256]guintptr
runnext  guintptr
```

`runnext` 是**最近被当前 G 唤醒的那个 G**，它 *"will inherit the time left in the current time slice"* —— 注释解释得很直白：*"If a set of goroutines is locked in a communicate-and-wait pattern, this schedules that set as a unit and eliminates the (potentially large) scheduling latency that otherwise arises from adding the ready'd goroutines to the end of the run queue."*

于是 `runqput(next=true)` 的语义是：新来的占住 `runnext`，**原来那个被踢到普通队列的尾部**。模型实测：

| 操作 | runnext | 本地队列 |
| --- | --- | --- |
| `put(g1)` | — | `[g1]` |
| `put(g2, next)` | g2 | `[g1]` |
| `put(g3, next)` | g3 | `[g1, g2]` |
| `get()` → g3 / g1 / g2 | — | 空 |

还有一处防饥饿的细节：`runqput` 开头写着 *"If there is no sysmon, we must avoid runnext entirely or risk starvation"* —— 没有 sysmon 时 `next` 被强制降级为 false，因为一对互相唤醒的 goroutine 会一直共享同一个时间片。

### 2.2 本地满了：搬一半到全局

`runqputslow()` 在本地队列满（256）时把**一半（128）+ 新来的这个**一起推进全局队列，所以全局一次收到 **129** 个，本地剩 128。

反过来取的时候，P *"every 61 scheduler ticks"* 才看一眼全局队列（`if pp.schedtick%61 == 0`），而且一次要一批：`globrunqgetbatch(int32(len(pp.runq)) / 2)`。这里的 `len(pp.runq)` 是**数组容量 256 而不是当前长度**，所以批量恒为 **128**。模型实测：61 次调度里只查了 1 次全局，一次从 300 个里拿走 128 个（第一个直接运行，其余 127 个进本地）。

61 这个质数的作用是让「偶尔看一眼全局」不与其它周期性动作共振。

### 2.3 工作窃取：偷靠头的一半，跑其中最新的那个

```go
n := t - h
n = n - n/2          // runqgrab：取 ceil(n/2)
```

注意是从 **head 侧**（更早入队的）拿走一半。而 `runqsteal()` 拿到这批之后：

```go
n--
gp := pp.runq[(t+n)%uint32(len(pp.runq))].ptr()   // 这批里**最后一个**（最新的）
if n == 0 { return gp }
atomic.StoreRel(&pp.runqtail, t+n)                 // 更早的 n 个留在本地
```

也就是说：**小偷把偷来的「老的一半」留下排队，自己先跑这批里最新那个**。模型实测（受害者 8 个 `v0..v7`）：偷走 `v0..v3`，小偷手里剩 `[v0, v1, v2]`，先跑 `v3`，受害者剩 `[v4, v5, v6, v7]`；7 个时仍是取 4 个（ceil）。

小偷只在最后手段才动别人的 `runnext`（`stealRunNextG`），而且会先 `usleep(3)` 让对方有机会自己调度它 —— 注释算过账：*"A sync chan send/recv takes ~50ns as of time of writing, so 3us gives ~50x overshoot."*

### 2.4 sysmon：20us 起步、翻倍、封顶 10ms

sysmon 是独立线程，循环里的休眠间隔：

```go
if idle == 0 { delay = 20 } else if idle > 50 { delay *= 2 }
if delay > 10*1000 { delay = 10 * 1000 }     // 10ms
usleep(delay)
```

模型实测的前 200 次：第 1 次 20us；**前 51 次都是 20us**（`idle > 50` 才翻倍）；第 52 次 40us 开始翻倍；第 60 次撞上 10ms 封顶（真实值本该是 20×2⁹ = 10240）。

### 2.5 抢占：10ms 时间片 + 异步抢占

`retake()` 的判断是「`schedtick` 没变且超过 `forcePreemptNS`」：

```go
const forcePreemptNS = 10 * 1000 * 1000 // 10ms
if int64(pd.schedtick) != schedt { pd.schedtick = ...; pd.schedwhen = now }
else if pd.schedwhen+forcePreemptNS <= now { preemptone(pp); sysretake = true }
```

`schedtick` 不变意味着**同一个 G（或一串共享时间片的 runnext G）一直在跑**。模型实测：9.999ms 不抢，到 10ms 抢；中间换过 G（schedtick 变了）则重新计时。

系统调用里的 P 另有一套：*"Retake the P if it's there for more than 1 sysmon tick (at least 20us)"*，但只要 *"runqempty(pp) && sched.nmspinning.Load()+sched.npidle.Load() > 0 && pd.syscallwhen+10*1000*1000 > now"* 就先不夺 —— 没人等着用这个 P 的时候不必折腾。两个分支模型都验了。

**异步抢占（Go 1.14，design/24543）**解决的是协作式的老问题：安全点只在函数调用处，*"even then, not if the function is small or gets inlined"*，于是紧循环里**一个安全点都没有**。文档的原话：*"When it goes wrong, it goes spectacularly wrong, leading to mysterious system-wide latency issues and sometimes complete freezes."*

改成信号驱动的异步抢占后，*"goroutines [can] be preempted at essentially any point without the need for explicit preemption checks"*，但有两处必须绕开的 **unsafe-point**（文档 §Handling unsafe-points）：

1. `unsafe.Pointer` 转 `uintptr` 期间 —— *"there must be no safe-points while a uintptr derived from an unsafe.Pointer is live"*；
2. write barrier 中间 —— 指针还没写进目标就被 GC 扫过会漏标。

模型里对同一段「无调用的紧循环」做了对照：协作式安全点数 = 0（永远抢不到），异步在 10ms 抢到；一旦处于 unsafe-point，异步也会让路，离开后立刻可抢。

### 2.6 线程与并行度

`wakep()` 的条件写得很明确：*"We unpark an additional thread when we submit work if: 1. There is an idle P, and 2. There are no 'spinning' worker threads."*

自旋线程是那些**没活但在找活**的 M（*"out of local work and did not find work in the global run queue or netpoller"*）。设计目标是 *"smooths out unjustified spikes of thread unparking, but at the same time guarantees eventual maximal CPU parallelism utilization"*。

M 进系统调用时 `handoffp()` 把 P 交给别的 M：模型实测，原 M 与 P 解绑、P 被新 M 接管、`syscalltick` 递增，而 `GOMAXPROCS` 始终是 4 —— 这就是「M 可以比 P 多，但并行度由 P 决定」。

## 三、对比

| 维度 | P | M | G |
| --- | --- | --- | --- |
| 是什么 | 执行资源/凭证 | OS 线程 | 执行体（带可增长栈） |
| 数量 | `GOMAXPROCS`（默认 CPU 核数） | 按需，可远多于 P | 任意多 |
| 阻塞时 | 被摘下交给别的 M | 卡在系统调用里 | 状态转 `_Gsyscall` / `_Gwaiting` |
| 关键字段 | `runq[256]`、`runnext`、`schedtick` | `curg`、`spinning`、`p` | `atomicstatus`、`stackguard0` |

G 的状态（取自 `runtime2.go` 的 iota，注意 5 和 7 是历史保留位）：`_Gidle 0`、`_Grunnable 1`、`_Grunning 2`、`_Gsyscall 3`、`_Gwaiting 4`、`_Gmoribund_unused 5`、`_Gdead 6`、`_Genqueue_unused 7`、`_Gcopystack 8`、`_Gpreempted 9`。扫描位 `_Gscan = 0x1000` 可与状态叠加，`atomicstatus &^ _Gscan` 得到扫描结束后的状态。

| 抢占方式 | 安全点 | 紧循环 | 代价 |
| --- | --- | --- | --- |
| 协作式（Go 1.13 及以前） | 只在函数调用（且小函数/内联没有） | **抢不到** | 编译期插桩，循环里有开销 |
| 异步（Go 1.14+） | 几乎任意点，绕过 unsafe-point | 10ms 必抢 | 零运行时开销（发信号） |

## 四、环境与运行

```bash
cd 03-系统编程/02-协程/GoGMP调度器
python selfcheck_gmp.py     # 55 项断言全绿
```

`gmp_model.go` 是同一骨架的 Go 复刻，本机无 Go 工具链，人工审查。

## 五、关键代码

窃取那一半 + 「先跑最新的」：

```go
n = n - n/2                       // runqgrab：偷靠头的一半
gp := pp.runq[(t+n)%256].ptr()    // 这批里最后一个
atomic.StoreRel(&pp.runqtail, t+n) // 更早的留在本地排队
```

抢占判据（源码原样）：

```go
} else if pd.schedwhen+forcePreemptNS <= now {   // forcePreemptNS = 10ms
    preemptone(pp)
    sysretake = true                              // 在 syscall 里就自己把 P 拿回来
}
```

## 六、性能边界

- 本地队列容量 256 是**数组**容量（不是动态长度），所以「批量 128」是常量；溢出一次要搬 129 个，这解释了为什么把大量 goroutine 灌给单个 P 会退化成全局队列 + 锁竞争。
- 全局队列每 61 tick 才看一次是为了避免全局锁成为热点；副作用是全局队列里的 goroutine 可能有较长调度延迟。
- sysmon 的 10ms 封顶是「延迟 vs 开销」的折中：它决定了抢占精度上界，也决定了 P 从 syscall 被夺回的时间上界。
- 官方文档不给「应该开多少 GOMAXPROCS」的定量建议，本 demo 也不编造：只断言「并行度 = P 数」这一结构性事实。

## 七、注意事项与常见坑

1. **以为 goroutine 多就并行多** —— 并行度是 `GOMAXPROCS`；goroutine 是并发单位不是并行单位。
2. **以为 `runtime.GOMAXPROCS(n)` 改的是线程数** —— 改的是 P 的数量；M 由运行时按需创建。
3. **在 cgo / syscall 里担心「占着 P」** —— 进 syscall 会 `handoffp`，但 **`LockOSThread` 之后不会**（G 与 M 绑定，P 会一直等着）。
4. **写无函数调用的紧循环** —— Go 1.14 之前能把 STW 拖到秒级（design/24543 的动机）；现在靠异步抢占兜住了，但 `unsafe.Pointer`↔`uintptr` 与 write barrier 期间仍不可抢占。
5. **把 `runnext` 当 FIFO** —— 它是「插队且继承时间片」，会让一对互唤醒的 goroutine 粘在一起跑（没有 sysmon 时运行时干脆禁用它）。
6. **以为窃取是「偷一个」** —— 是偷一半（ceil），且跑的是这批里最新的那个，不是最早的。
7. **以为全局队列每轮都查** —— 61 tick 一次，且批量 128；延迟敏感的任务别指望它。
8. **在 GC 安全点假设上做优化** —— `_Gscan` 叠加位的存在就是为了标记「正在被扫描」，直接比较状态值会误判。

## 八、参考资料（本轮实读）

- `src/runtime/runtime2.go` — https://cdn.jsdelivr.net/gh/golang/go@master/src/runtime/runtime2.go （P 的 `runq [256]` 与 `runnext` 注释、G 状态枚举与 `_Gscan`）
- `src/runtime/proc.go` — https://cdn.jsdelivr.net/gh/golang/go@master/src/runtime/proc.go （`runqput` / `runqputslow` / `globrunqgetbatch` / `runqgrab` / `runqsteal` / `findRunnable` / `sysmon` / `retake` / `forcePreemptNS` / wakep 与 spinning 的长注释）
- Proposal: Non-cooperative goroutine preemption (design/24543) — https://cdn.jsdelivr.net/gh/golang/proposal@master/design/24543-non-cooperative-preemption.md （协作式安全点的缺陷、异步抢占的动机、unsafe-point 两处例外）
