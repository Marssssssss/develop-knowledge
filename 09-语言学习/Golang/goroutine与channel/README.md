# Goroutine 与 Channel — CSP 通信模型

> 单一 demo 用一个示例串讲 goroutine + channel + select 三大并发原语,把它们视为一个相互配合的**系统**而非零散 API。

## 简介

**Go 的并发模型直接继承 Hoare 1978 提出的 CSP(Communicating Sequential Processes)**——goroutine 是顺序进程,channel 是它们之间类型化的"通信线路",select 是"同时监听多条线路"的等待原语。Go 的口号「不要通过共享内存来通信,而要通过通信来共享内存」正是 CSP 思想的工程化转述。

**关键概念**:
- **goroutine**:由 Go runtime 调度的极轻量协程,初始栈 2 KB(可按需增长/收缩),创建/销毁成本远低于 OS thread
- **channel**:`chan T` 类型化的并发安全队列;make 创建,close 关闭,`<-` 收发
- **select**:同时等待多个 channel 操作;多个 case 就绪时**随机选择**(避免饥饿)
- **nil channel**:永远不就绪——可用于动态"屏蔽"某些 case
- **comma-ok 接收**:`v, ok := <-ch`,`ok=false` 表示通道已关闭且缓冲已空

**历史**:2007 年 Rob Pike / Robert Griesemer / Ken Thompson 在 Google 设计 Go 时,把 CSP 从学术语言(Occam / Newsqueak / Alef)移植到工业语言。Newsqueak(1989 末 Pike 在 Bell Labs)是直接前驱——channel 关键字几乎原样保留。

## 原理详解

### 1. goroutine 与 channel 的协作流程

```
调度:  main goroutine ──go f()──> 新 goroutine
        │                          │
        │   ch := make(chan T)     │
        │   ch <- v (sender)       │  v := <-ch (receiver)
        │                          │
        ▼                          ▼
      runtime scheduler ── 在两个 goroutine 之间传递数据
      (M:N 调度到 GOMAXPROCS 个 OS thread)
```

- **Send `<-`**:阻塞直到**接收方就绪**(unbuffered)/**缓冲未满**(buffered)
- **Receive `<-ch`**:阻塞直到**有数据**(unbuffered:对端也在发送)/**缓冲非空**(buffered)
- **close(ch)**:发送方关闭;接收方收到零值 + `ok=false`;**所有阻塞接收者一次性唤醒**(广播点)
- **select 多路复用**:任一 case 就绪即触发;多个就绪则**随机**选一个;无 default 时阻塞;有 default 时非阻塞

### 2. 内部数据结构与运行时视角

```
goroutine  ── 由 runtime.g 表示,栈指针 + PC 寄存器 + 状态
channel    ── 由 runtime.hchan 表示,环形缓冲 + sendq/recvq 等待队列 + lock
select     ── runtime.sellock + selectnbrecv / selectnbsend + sudog 链表按 channel 排序轮询
```

源码路径:`runtime/chan.go`(makechan/chanrecv/chansend/closechan/selectnbrecv 等)。channel 内部由 `sync.Mutex` 保护 ring buffer 与 wait queues,因此**多个 goroutine 收发同一 channel 无需额外同步**。

### 3. 核心 API

| 表达式 | 行为 | 阻塞条件 |
| --- | --- | --- |
| `ch := make(chan T)` | 无缓冲 channel(同步) | 收发必须同时就绪 |
| `ch := make(chan T, n)` | 容量 n 的缓冲 channel | 缓冲满发送阻塞 / 空接收阻塞 |
| `ch <- v` | 发送 | 见上 |
| `v := <-ch` | 接收 | 同上 |
| `v, ok := <-ch` | comma-ok 接收 | `ok=false` 表示已关闭且空 |
| `close(ch)` | 关闭 | 已关闭会 panic;向已关闭发送会 panic |
| `for v := range ch` | 反复接收,直到 close | close 后自动退出 |
| `select { case ... }` | 多路复用 | 多个 case 就绪时随机选;无 default 阻塞 |

### 4. 范式分类

| 范式 | 用途 | 关键技巧 |
| --- | --- | --- |
| 同步 | 1 对 1 信号/数据传递 | unbuffered channel;**发送即同步点** |
| 流水线 | 多阶段数据处理 | 每个 stage 一个 channel;`close` 串联 |
| fan-out / fan-in | 多 worker 并行处理 + 结果汇总 | 共享收 channel;`close` 触发 worker 退出 |
| 超时控制 | 取消长任务 | `select` + `time.After` / `context.Done()` |
| 信号广播 | 一对多通知 | **`close(ch)` 充当广播**:所有接收者同时收到零值 + ok=false |

## 对比 / 选型

| 维度 | Go goroutine/channel | Java Thread/Executor | Python asyncio | Rust tokio |
| --- | --- | --- | --- | --- |
| 调度模型 | M:N(GPM) | 1:1 OS thread | 单线程事件循环 | M:N(work-stealing) |
| 通信原语 | channel(语言级) | `BlockingQueue` / `Future`(库) | `Queue` / `Event`(库) | `mpsc::channel`(库) |
| 同步原语 | channel / `sync.Mutex` | `synchronized` / `Lock` | `asyncio.Lock` | `Mutex` / `RwLock` |
| 创建开销 | 2 KB 栈,可增长 | OS thread ~1 MB 栈 | coroutine ~几 KB | task ~几 KB |
| 类型安全 | channel 类型化 | 需手写泛型 | 动态 | 类型系统保证 |

> Go 的 channel 是**语言级**关键字,不需要外部库——这是它最显著的差异。

## 环境准备

- 操作系统:任何支持 Go 的平台
- Go 版本:Go 1.21+(本 demo 用 `time.After`、`sync.WaitGroup` 等标准库 API)
- 依赖:无第三方依赖

## 运行方式

```bash
cd 09-语言学习/Golang/goroutine与channel/go
go run goroutine_channel.go
```

## 关键代码片段

```go
// 1) 同步握手:unbuffered channel 让 sender 与 receiver 同步
ch := make(chan int)        // capacity=0,同步通信
go func() { ch <- 42 }()    // 发送方阻塞,直到主协程接收
v := <-ch                   // 接收;此行之后 sender 才解除阻塞
// 2) 关闭即广播:一个 sender 关闭,所有 receiver 同时被唤醒
done := make(chan struct{})
go workerA(done); go workerB(done)
close(done)                  // 同时唤醒 workerA/workerB
// 3) select 多路复用 + 超时 + 取消
select {
case v := <-ch:
    fmt.Println("got", v)
case <-time.After(100*time.Millisecond):
    fmt.Println("timeout")
case <-ctx.Done():
    fmt.Println("canceled")
}
// 4) nil channel 在 select 中"屏蔽"该 case,常用作动态取消
ch1, ch2 := make(chan int), make(chan int)
ch2 = nil                       // 屏蔽 case ch2
for {
    select {
    case v := <-ch1: return v   // 只关心 ch1
    case <-ch2:                  // 永远不就绪
    }
}
```

## 性能与边界

- **创建成本**:goroutine 创建 ~2 KB 栈 + 进入 runtime,**实测 ~150 ns**(比 OS thread 快 ~1000x);无栈切换时调度更便宜
- **channel 操作**:无竞争 ~30 ns;有竞争(uncontended)需要拿 mutex ~50 ns
- **缓冲大小选择**:经验值 0(同步)或匹配消费者并发数(异步 fan-out)
- **规模上限**:百万级 goroutine 可行;每个 goroutine 栈可独立增长至 1 GB(默认)上限
- **死锁**:未关闭 channel + 无 receiver → goroutine 永久阻塞;`go vet` 可检测部分情况

## 注意事项与常见坑

1. **向已关闭 channel 发送 panic**(`send on closed channel`);只能由 sender 关闭 channel,避免"接收方关闭"歧义
2. **关闭 nil channel 也 panic**;先 make 再 close
3. **range channel 不会自动退出**,必须由 sender `close(ch)`;忘记关闭会死锁
4. **select 多 case 就绪时随机选择**——避免基于"顺序"的隐式假设;若要优先级,改用嵌套 select
5. **channel 元素值传递**:对大结构体应传指针(`*T`),否则每次收发都复制
6. **buffered ≠ 异步**:buffer=10 仍可能阻塞,只是把阻塞往后推 10 次

## 参考资料

- [The Go Programming Language Specification — Channel types](https://go.dev/ref/spec#Channel_types) — channel 类型/方向/make/close 的形式化定义
- [A Tour of Go — Concurrency](https://go.dev/tour/concurrency/1) — goroutine/channel/select 入门示例
- [Effective Go — Concurrency](https://go.dev/doc/effective_go#concurrency) — 官方惯用法(channel vs shared memory)
- [Go Concurrency Patterns: Pipelines and cancellation](https://go.dev/blog/pipelines) — 流水线与取消的标准范式
- [The Go Blog — Share Memory By Communicating](https://go.dev/blog/codelab-share-memory-by-communicating) — 设计哲学

---

> 文件清单:`goroutine_channel.go`(本 demo 一文件涵盖 goroutine / channel / select / close / comma-ok / range / 超时 / 广播 / fan-out 9 个示例),`go.mod`(声明 module 名与 Go 1.21),`README.md`(本文)。