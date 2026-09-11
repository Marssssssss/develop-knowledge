# Swift / Objective-C GCD 同步原语

> 04-移动开发 / 01-iOS / 并发编程 / GCD 同步原语 — demo 025

## 简介

Grand Central Dispatch (GCD) 是 Apple 自 macOS 10.6 / iOS 4 起提供的**系统级并发任务调度框架**,底层为 libdispatch。它把任务作为 block/闭包提交到 **FIFO 队列**,系统从全局线程池分配线程执行。GCD 用队列抽象隐藏线程管理复杂度,提供四类同步原语:**DispatchQueue(任务调度)**、**DispatchSemaphore(计数信号量)**、**DispatchGroup(fan-out / fan-in)**、**barrier(读写锁)**。

- **关键概念**
  - DispatchQueue —— 串行(serial)/并发(concurrent)队列,主队列是特殊串行队列。
  - DispatchSemaphore —— 经典计数信号量,`wait` 阻塞减计数、`signal` 非阻塞加计数;可限制并发上限。
  - DispatchGroup —— 一组异步任务的协调器,`enter`/`leave` 配对 + `notify`/`wait`。
  - barrier flag —— `DispatchWorkItemFlags.barrier` 让任务在并发队列上独占执行,前面的读完才能写、后面的写必须等。
- **历史背景**:GCD 于 2009 年随 Snow Leopard (10.6) 推出(Apple 内部由 Pierre Habouzit / Patrick Chesnais 等主导),最初是 C API(`dispatch_*` 前缀),Swift 5 后通过 `Dispatch` framework 提供 Swift 友好的封装。

## 原理详解

### 工作机制分步

1. **任务提交**:`DispatchQueue.async { ... }` 把闭包作为 `DispatchWorkItem` 压入队列。
2. **线程池调度**:libdispatch 全局线程池根据 QoS 等级从可用核心拉取任务。串行队列把任务挂在同一线程上,保证 FIFO 顺序;并发队列允许多个任务同时跑在不同线程。
3. **同步原语交互**:
   - `DispatchSemaphore.wait` —— 若计数 > 0 立即减并返回,否则阻塞当前线程直到 `signal` 把计数加回(超时版 `wait(timeout:)`)。
   - `DispatchGroup.enter` / `leave` —— 计数 +1/-1;`notify(queue:execute:)` 在计数归零时异步触发闭包,`wait(timeout:)` 阻塞当前线程。
   - `.barrier` —— 在自定义并发队列上,barrier 任务会等待当前所有已入队任务执行完,然后独占执行,后续任务(包括其他并发读)都被推迟直到 barrier 完成;**全局并发队列(`DispatchQueue.global()`)上 barrier 无效**(Apple 文档原话)。
4. **死锁触发条件**:在某个串行队列上执行同步提交到**同一队列** —— 任务已在跑,提交的任务要等它完成,但它又在等提交的任务 → 永久等待。`DispatchQueue.main.sync { ... }` 在主线程调用必死锁,这是新手最常见错误。

### 核心 API 速查

| 类别 | Swift | Objective-C | 说明 |
| --- | --- | --- | --- |
| 主队列 | `DispatchQueue.main` | `dispatch_get_main_queue()` | 串行,绑定主线程 |
| 全局并发 | `DispatchQueue.global(qos:)` | `dispatch_get_global_queue(qos, 0)` | 系统池 |
| 自定义 | `DispatchQueue(label:qos:attributes:)` | `dispatch_queue_create(label, DISPATCH_QUEUE_CONCURRENT/SERIAL)` | 默认串行 |
| 异步提交 | `queue.async { ... }` | `dispatch_async(q, ^{...})` | 非阻塞 |
| 同步提交 | `queue.sync { ... }` | `dispatch_sync(q, ^{...})` | 阻塞 |
| 延迟 | `queue.asyncAfter(deadard:execute:)` | `dispatch_after(dispatch_time_t, q, ^{...})` | |
| 信号量 | `DispatchSemaphore(value:)` | `dispatch_semaphore_create(count)` | |
| sem.wait | `sem.wait()` | `dispatch_semaphore_wait(sem, t)` | 阻塞 |
| sem.signal | `sem.signal()` | `dispatch_semaphore_signal(sem)` | 非阻塞 |
| Group 创建 | `DispatchGroup()` | `dispatch_group_create()` | |
| Group enter/leave | `group.enter()` / `group.leave()` | `dispatch_group_enter(g)` / `dispatch_group_leave(g)` | 配对 |
| Group notify | `group.notify(queue:execute:)` | `dispatch_group_notify(g, q, ^{...})` | 异步 |
| Group wait | `group.wait()` / `group.wait(timeout:)` | `dispatch_group_wait(g, t)` | 阻塞 |
| Barrier | `queue.async(flags: .barrier) { ... }` | `dispatch_barrier_async(q, ^{...})` | 并发队列独占 |

### QoS 等级(从高到低)

```
.userInteractive   → QOS_CLASS_USER_INTERACTIVE   立即执行,UI 响应
.userInitiated     → QOS_CLASS_USER_INITIATED     用户主动发起,秒级完成
.utility           → QOS_CLASS_UTILITY            长时间计算/I/O,几分钟
.background        → QOS_CLASS_BACKGROUND         用户不可见,分钟级
.default           → QOS_CLASS_DEFAULT            未指定
```

### 串行 / 并发 / 主队列对比

```
Serial Queue(单车道,有序):
[T1]→[T2]→[T3]→[T4]        都跑在同一条 lane,线程可能复用

Concurrent Queue(多车道,乱序启动):
T1:[T1a][T1b][T1c]...       任务按提交顺序开始,但完成顺序随机
T2:[T2a][T2b][T2c]...
T3:[T3a][T3b][T3c]...
T4:[T4a]                   实际并行线程数 = min(任务数, GCD 池大小)

Main Queue(单车道,UI 绑定):
   main thread ──→ [T1][T2][T3][T4]    与 RunLoop 交错执行
```

### Barrier 在并发队列上的时序

```
Task1(读)  Task2(读)  Task3(读)  | Barrier(写)  | Task4(读)  Task5(读)
   ↪ 并发执行                ↪ 独占,等所有就绪读完成  ↪ 等写完成才启动
                       ↑                ↑                ↑
                  barrier 进入点    barrier 执行         barrier 退出点
```

## 对比 / 选型

| 同步原语 | 适用场景 | 不适用 |
| --- | --- | --- |
| **DispatchSemaphore** | 限制并发上限(下载连接池)、跨线程事件 count | 一次性事件通知(用 DispatchGroup) |
| **DispatchGroup** | 多个异步任务统一回调、下载一组图片后一起渲染 | 严格串行(用串行 queue) |
| **barrier(并发队列)** | 读写锁 / 多读单写缓存 | 全局并发队列(barrier 在全局队列上**无独占效果**) |
| **NSLock / pthread_mutex** | 简单互斥、跨平台代码 | 复杂协调(用 GCD) |
| **OSAllocatedUnfairLock**(iOS 16+) | 低开销互斥(无 waitlist) | 需要 `pthread_cond` 等条件变量语义 |
| **async/await + Task** | Swift 5.5+ 顺序异步 | 跨 Swift 版本兼容(老 Swift 仍用 GCD) |

> 注意:Swift `async/await` 底层仍可由 GCD executor 驱动,但**禁止在 `Task` 中调用 `DispatchQueue.main.sync`**(仍会死锁,官方 SE-0304 明确说"async functions must not be invoked from synchronous code on the same thread")。

## 环境准备

- 操作系统:macOS / iOS(本 demo 无 GUI 依赖,macOS 命令行即可)
- 语言版本:Swift 5.0+ / Objective-C 2.0(ARC)
- 依赖:Swift 标准库、Foundation(`Dispatch` framework 在 Apple 平台内置)

## 运行方式

### Swift

```bash
swift SwiftGCD.swift
```

预期 stdout(各次执行时延细节略不同,关键断言是 group/notify 顺序与 barrier 写完后读到的最新值):

```
===== 用例 1: dispatch_semaphore 限制并发 =====
[task 0] running, elapsed=0.00s
[task 1] running, elapsed=0.00s
[task 2] running, elapsed=0.00s
[task 3] running, elapsed=0.20s
...
[semaphore] all 10 tasks done in 0.67s (expected ~0.67s)

===== 用例 2: dispatch_group fan-out/fan-in =====
[group] task 0 -> 0
[group] task 3 -> 30
...
[group] all 5 done, results = [0, 10, 20, 30, 40]

===== 用例 3: concurrent + .barrier 读写锁 =====
[barrier] writer 0 -> k0=0
[barrier] snapshot = {k0:0, k1:100, k2:200}

===== 用例 4(警告): main.sync 死锁演示 =====
[deadlock] main 线程未卡死(bg sync main 永远等 main 空闲)
```

### Objective-C

```bash
clang -fobjc-arc -framework Foundation ObjCGCD.m -o objc_gcd
./objc_gcd
```

## 关键代码片段

### Swift: DispatchSemaphore 限制并发

```swift
let maxConcurrent = 3
let sem = DispatchSemaphore(value: maxConcurrent)
let q = DispatchQueue.global(qos: .userInitiated)
for i in 0..<10 {
    q.async {
        sem.wait()                                    // 计数 -1,计数 ≤ 0 时阻塞
        defer { sem.signal() }                        // 计数 +1,唤醒一个等待者
        Thread.sleep(forTimeInterval: 0.2)            // 模拟工作
        // 同一时刻最多 3 个 task 在跑,其余排队
    }
}
```

### Swift: 读写锁(ThreadSafeCache 用 barrier)

```swift
final class ThreadSafeCache {
    private var storage: [String: Int] = [:]
    private let queue = DispatchQueue(label: "cache.q", attributes: .concurrent)
    func read(_ key: String) -> Int? { queue.sync { storage[key] } }
    func write(_ key: String, value: Int) {
        queue.async(flags: .barrier) { storage[key] = value }
    }
}
```

### Objective-C: dispatch_group

```objc
dispatch_group_t group = dispatch_group_create();
for (NSInteger i = 0; i < 5; i++) {
    dispatch_group_enter(group);
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
        // 异步工作
        dispatch_group_leave(group);
    });
}
dispatch_group_notify(group, dispatch_get_main_queue(), ^{
    // 全部完成后在 main queue 上回调
});
```

## 性能与边界

- **线程池上限**:libdispatch 默认活跃线程数 ≈ 64,过多会拒绝;`Too many open files` 的并发版本就是耗尽线程池。
- **QoS 提升**:高 QoS 任务会"抢占"低 QoS 任务的 CPU 时间,Apple 在 libdispatch 内部通过 pthread QoS class 传播(`pthread_set_qos_class_self_np`)。
- **Semaphore 误用**:用信号量做"通知"是反模式 —— 应该用 `DispatchGroup` 或 `NSNotificationCenter`。用信号量做"等待异步完成"在主线程会死锁(`async` 起不来主队列就在等)。
- **barrier 全局队列上无效**:Apple DispatchQueue 文档明确"Custom serial queues and private concurrent queues are commonly used to perform their barrier tasks";`DispatchQueue.global()` 共享,不保证独占 barrier 语义。
- **QoS 推理**:高 QoS 任务用 `.userInteractive`,网络请求 `.userInitiated` 或 `.utility`,清理 `.background`。过高的 QoS 会耗电,Apple Energy Guide 提到"pick the lowest acceptable QoS"。

## 注意事项与常见坑

| 现象 | 原因 | 规避方法 |
| --- | --- | --- |
| `DispatchQueue.main.sync { ... }` 卡死 | 主队列当前在执行外部 sync,内部 sync 等自己 | 用 `async` 或 `DispatchQueue.main.async`;UI 更新才回主线程 |
| barrier 在 global queue 上没独占 | 全局并发队列被多进程共享 | 用 `DispatchQueue(label:..., attributes: .concurrent)` 自定义队列 |
| semaphore 当通知用,信号丢失 | `signal` 不保留唤醒,无等待者时直接 +1 | 用 DispatchGroup / `NSLock` + `Bool` |
| semaphore wait 在主线程永久阻塞 | wait 会占线程直到 signal | 信号量 wait 仅在 background 线程 |
| Thread.sleep 模拟工作,误以为 0ms 完成 | NSThread 在 main queue sleep 会卡住后续任务 | demo 中 sleep 仅用于演示,生产用 `URLSession` 真实异步 |
| `group.notify` 没触发 | enter/leave 次数不匹配(忘记 leave) | 严格 1:1 配对;用 `defer { group.leave() }` |
| `asyncAfter` 推迟任务"准时"运行 | 内部只是把 block 入队,延迟只是最早就绪时间 | 高精度需求用 `Timer.scheduledTimer` 或 `DispatchSourceTimer` |
| 读 cache 时数据不一致 | barrier 写未完成,有并发读 | read 必须在 barrier 后,或者用 barrier sync snapshot |
| `dispatch_group_wait` 阻塞 main | 同主线程 main.sync | 改用 `notify` 异步 |
| GCD 死锁不易调试 | log 卡在某个 sync 上 | 用 Thread Sanitizer / Instruments "System Trace" |

## 参考资料(实际阅读过的权威来源)

- [Apple Dispatch framework 官方文档](https://developer.apple.com/documentation/dispatch) — Dispatch 框架总览、QoS、DispatchSource、ARC 与 dispatch 对象的关系。
- [Apple DispatchQueue 官方文档](https://developer.apple.com/documentation/dispatch/dispatchqueue) — main/global/serial/concurrent queue、async/sync/asyncAfter、barrier 完整签名。
- [Apple Concurrency Programming Guide - Dispatch Queues](https://developer.apple.com/library/archive/documentation/General/Conceptual/ConcurrencyProgrammingGuide/OperationQueues/OperationQueues.html#//apple_ref/doc/uid/TP40008091-CH102-SW25) — Serial vs Concurrent vs Main queue 的语义、"Avoiding Excessive Thread Creation"原则。
- [Apple Grand Central Dispatch (GCD) Reference](https://developer.apple.com/library/archive/documentation/General/Conceptual/ConcurrencyProgrammingGuide/GCD-Compatibility/GCD-Compatibility.html) — `dispatch_async_f`/`dispatch_barrier_async`/`dispatch_semaphore_*` 等 C API 完整定义。
- [WWDC 2017 #706 "Modernizing Grand Central Dispatch Usage"](https://developer.apple.com/videos/play/wwdc2017/706/) — GCD 现代用法、QoS 传播、线程池行为。