# Swift / Objective-C RunLoop 主循环机制

> 04-移动开发 / 01-iOS / 事件循环 / RunLoop — demo 026

## 简介

RunLoop 是 Apple 平台(Foundation + CoreFoundation 双 API)**线程级事件处理循环**,目的:有工作时让线程忙,无工作时让线程睡。它从**输入源(input source,异步)**、**定时器源(timer,同步)**、**观察者(observer)** 三类对象接收事件,并按**模式(mode)**过滤。UIKit / AppKit 主线程自动启动主 RunLoop;Swift `async/await` 与 GCD 仍依赖 RunLoop 在 main 上驱动 UI 回调。

- **关键概念**
  - RunLoop —— 每线程一实例(lazy 创建,通过 `currentRunLoop` 触发)。
  - 输入源(input source) —— port-based / custom / performSelector 来源。
  - 定时器源(timer source) —— 按计划时间触发的事件。
  - 观察者(observer) —— 监听 RunLoop 自身状态变化(进入/即将处理 timer/即将处理 source/即将休眠/刚醒/退出)。
  - 模式(mode) —— 输入源 / timer / observer 的过滤集合,默认模式 `NSDefaultRunLoopMode` / `kCFRunLoopDefaultMode`,还有 `NSModalPanelRunLoopMode`、`NSEventTrackingRunLoopMode`、以及 `NSRunLoopCommonModes` 集合。
- **历史背景**:CFRunLoop 是 CoreFoundation 的一部分,Apple 在 macOS 10.0 (2001) 公开;NSRunLoop 是 Objective-C 包装,2002 年随 Cocoa 一起。Swift 5 起 Foundation 暴露 `RunLoop` 类型(`run(_:before:)`、`limitDate(forMode:)` 等)。

## 原理详解

### 工作机制分步

1. **RunLoop 与线程绑定**:每个 NSThread 有且只有一个 RunLoop(主线程在 app 启动时由 UIKit/AppKit 自动创建),通过字典 `pthread → CFRunLoopRef` 维护(lazily 在 `currentRunLoop` 时分配,见 `_CFRunloopGet0` opensource.apple.com)。
2. **三类对象**:`CFRunLoopSourceRef`(异步输入)、`CFRunLoopTimerRef`(定时器)、`CFRunLoopObserverRef`(观察者);通过 `CFRunLoopAddSource/Timer/Observer` 加入指定 mode。
3. **运行循环**:调用 `CFRunLoopRun()` / `[runLoop run]` 后进入 do-while 循环,每次迭代:
   - 用 `__CFRunLoopRun` 通知所有 observer 触发 `kCFRunLoopEntry`。
   - 通知 `kCFRunLoopBeforeTimers`,处理已就绪的 timer。
   - 通知 `kCFRunLoopBeforeSources`,处理已就绪的 source。
   - 若无事件,通知 `kCFRunLoopBeforeWaiting` → 阻塞在 `__CFRunLoopServiceOnMain0` 的 mach port / kqueue 上,等待事件唤醒。
   - 唤醒后通知 `kCFRunLoopAfterWaiting`,再次循环或退出。
4. **退出条件**:显式调用 `CFRunLoopStop`、`CFRunLoopRunInMode` 超时,或 mode 中没有 source/timer 可监控(空 mode)。
5. **Mode 过滤**:在某一 mode 下运行时,**只有该 mode 注册的 source/timer/observer 被处理**;其他 mode 的事件保持排队,直到该 mode 被再次进入才处理。这就是为什么主线程用 `NSTimer.scheduledTimer` 默认注册到 `NSDefaultRunLoopMode`,在滚动 ScrollView 时 ScrollView 切换到 `NSEventTrackingRunLoopMode`,timer 暂停触发。
6. **Common Modes**:`NSRunLoopCommonModes` 是一个 mode 集合(默认 default + modal + event tracking),把 source 加到 common modes 相当于同时加到集合内每个 mode。

### 核心 API 速查

| Foundation(Swift/ObjC) | CoreFoundation | 说明 |
| --- | --- | --- |
| `RunLoop.current` / `[NSRunLoop currentRunLoop]` | `CFRunLoopGetCurrent()` | 当前线程 run loop(lazy) |
| `RunLoop.main` / `[NSRunLoop mainRunLoop]` | `CFRunLoopGetMain()` | 主线程 run loop |
| `runLoop.run()` | `CFRunLoopRun()` | 无限循环(直到显式 stop 或无 source) |
| `runLoop.run(until:)` / `[runLoop runUntilDate:]` | `CFRunLoopRunInMode(mode, time, returnAfterSource)` | 限时运行 |
| `runLoop.acceptInput(forMode:before:)` | `CFRunLoopAcceptInputForSource` | 单次 pass |
| `add(timer, forMode:)` / `[runLoop addTimer:forMode:]` | `CFRunLoopAddTimer` | 注册 timer |
| `add(port, forMode:)` | `CFRunLoopAddSource` | 注册 port-based source |
| `perform(_:with:afterDelay:)` | — | 延迟 perform(实际注册了一个 internal timer) |
| `limitDate(forMode:)` | `CFRunLoopGetNextTimerFireDate` | 下一 timer 触发时间 |

### 6 个 Observer Activity(CFRunLoopActivity)

```
                     ┌─────────────────────────────┐
                     │   CFRunLoopRun 一轮迭代      │
                     └─────────────────────────────┘
                                       │
   ┌──── kCFRunLoopEntry ──────────────▼─────┐
   │  run loop 进入(observer 触发)            │
   │                                          │
   ├──── kCFRunLoopBeforeTimers ──────────────┤  → 处理已就绪 timer
   │                                          │
   ├──── kCFRunLoopBeforeSources ─────────────┤  → 处理已就绪 source
   │                                          │
   ├──── kCFRunLoopBeforeWaiting ─────────────┤  → 无事件,即将阻塞 mach port
   │                                          │
   ├──── kCFRunLoopAfterWaiting ──────────────┤  → 有事件唤醒,即将处理
   │                                          │
   └──── kCFRunLoopExit ──────────────────────┘  → run loop 退出
```

引用 Apple Threading Programming Guide 原文: *"The entrance to the run loop" / "When the run loop is about to process a timer" / "When the run loop is about to process an input source" / "When the run loop is about to go to sleep" / "When the run loop has woken up, but before it has processed the event that woke it up" / "The exit from the run loop."*

### 预定义 Mode 一览

| Mode 常量名 | 说明 |
| --- | --- |
| `NSDefaultRunLoopMode` / `kCFRunLoopDefaultMode` | 默认模式,绝大多数操作 |
| `NSConnectionReplyMode` | Cocoa NSConnection 监控回复,极少自用 |
| `NSModalPanelRunLoopMode` | Cocoa 模态面板 |
| `NSEventTrackingRunLoopMode` | Cocoa 鼠标拖动等 UI 跟踪 |
| `NSRunLoopCommonModes` / `kCFRunLoopCommonModes` | 模式集合:关联即关联到集合内每个 mode |

> Apple 原文:*"Modes discriminate based on the source of the event, not the type of the event."*(模式基于事件源而非事件类型区分。)

## 对比 / 选型

| 维度 | RunLoop | Thread | DispatchQueue |
| --- | --- | --- | --- |
| 抽象级 | 事件循环 + source | 裸线程 | 任务队列 |
| 创建方式 | lazy,每线程一实例 | 显式 `pthread_create` | 系统全局池 |
| 适用 | UI 事件、timer、port 通信 | 长驻线程、底层控制 | fire-and-forget 异步任务 |
| 取消机制 | `CFRunLoopStop` / `invalidate` | `pthread_cancel`(不推荐) | `DispatchWorkItem.cancel()` |
| 阻塞 API | `run(until:)` / `CFRunLoopRunInMode` | 自行管理 | `group.wait()` / `sem.wait()` |
| 与 Swift async/await | 仍依赖 main run loop 驱动 continuation | 不推荐 | Task 默认在 main executor 上跑 |
| 何时选 | UI 线程、port-based 进程间通信 | 不在新代码中推荐 | 默认并发原语 |

> Apple 文档明确建议:"Use dispatch queues instead of creating your own threads."新代码几乎都用 GCD 或 async/await + Task,RunLoop 主要用于桥接传统 Cocoa/UIKit 事件循环、自定义 source、或者底层 port 通信。

## 环境准备

- 操作系统:macOS / iOS
- 语言版本:Swift 5.0+(Foundation `RunLoop` 类型) / Objective-C 2.0
- 依赖:Foundation、CoreFoundation(`CFRunLoop*` 在 macOS/iOS SDK 内置)

## 运行方式

### Swift

```bash
swift SwiftRunLoop.swift
```

预期 stdout(关键片段):

```
===== 用例 1: currentRunLoop vs mainRunLoop =====
[1] main thread: current==main? true
[1] bg thread:   current is bg's run loop, not main
[1] bg thread:   current === main? false

===== 用例 2: Mode 过滤 =====
[2] counterDefault = 10  counterCustom = 0
[2] 预期: counterDefault ≈ 10,counterCustom = 0 (未 run custom mode)

===== 用例 3: Timer 生命周期 =====
[3] timer fire #1
... (共 5~7 次,0.7s 内)
[3] final count = 7 (预期不再增长)

===== 用例 4: CFRunLoopObserver =====
[4] observer fire: kCFRunLoopEntry
[4] observer fire: kCFRunLoopBeforeTimers
[4] observer fire: kCFRunLoopBeforeSources
[4] observer fire: kCFRunLoopBeforeWaiting
[4] observer fire: kCFRunLoopAfterWaiting
[4] observer fire: kCFRunLoopExit

===== 用例 5: performSelector:afterDelay: =====
[5] perform block fired, elapsed = 0.001s
```

### Objective-C

```bash
clang -fobjc-arc -framework Foundation ObjCRunLoop.m -o objc_runloop
./objc_runloop
```

> ⚠️ Foundation 命令行程序**默认不会启动主 run loop**。本 demo 通过 `dispatch_async` / `NSTimer.scheduledTimer` 触发 run loop 内部的隐式循环;真正手动驱动可在末尾加 `dispatch_main();`(不会返回)。iOS app 中 `UIApplicationMain` 自动启动主 run loop。

## 关键代码片段

### Swift: CFRunLoopObserver 监听 6 个 Activity

```swift
let observer = CFRunLoopObserverCreate(
    kCFAllocatorDefault,
    CFRunLoopActivity.allActivities.rawValue,
    true, 0,
    { (_, activity, _) in
        switch activity {
        case .entry:         print("kCFRunLoopEntry")
        case .beforeTimers:  print("kCFRunLoopBeforeTimers")
        case .beforeSources: print("kCFRunLoopBeforeSources")
        case .beforeWaiting: print("kCFRunLoopBeforeWaiting")
        case .afterWaiting:  print("kCFRunLoopAfterWaiting")
        case .exit:          print("kCFRunLoopExit")
        default: break
        }
    }, nil)
CFRunLoopAddObserver(CFRunLoopGetMain(), observer, .defaultMode)
```

### Swift: Timer 注册到不同 mode

```swift
let t = Timer(timeInterval: 0.1, repeats: true) { _ in /* ... */ }
RunLoop.main.add(t, forMode: .default)          // 默认模式触发
RunLoop.main.add(t, forMode: customMode)        // 仅在 runLoop.run(mode: customMode, before: ...) 时触发
```

### Objective-C: 监听 Observer(C 语言 callback)

```objc
static void ObserverCallback(CFRunLoopObserverRef observer,
                             CFRunLoopActivity activity, void *info) {
    switch (activity) {
        case kCFRunLoopEntry:        NSLog(@"Entry"); break;
        case kCFRunLoopBeforeTimers: NSLog(@"BeforeTimers"); break;
        // ...
    }
}
```

## 性能与边界

- **RunLoop 唤醒延迟**:Apple 文档说 RunLoop 不是实时机制(timer 不保证精确到毫秒);`kCFRunLoopBeforeWaiting` 后的阻塞是 mach port,典型延迟 1-10 ms。
- **Common Modes 成本**:source 注册到 common modes 实际写入 set 的每个成员,common set 改变时需要重新注册。生产中频繁 add/remove timer 到 common set 会有开销。
- **Timer 内存**:scheduled timer 由 RunLoop 持有,引用闭包,闭包引用 self → RunLoop ↔ Timer ↔ Closure ↔ Self 循环;**必须 `invalidate()`** 才能释放。Swift 闭包如用 `[weak self]` 可缓解但不能替代 invalidate。
- **多线程 Timer**:NSTimer 只能注册到当前线程的 run loop,跨线程触发需要手动把 timer add 到目标线程的 run loop。

## 注意事项与常见坑

| 现象 | 原因 | 规避方法 |
| --- | --- | --- |
| ScrollView 滚动时 timer 暂停 | timer 注册在 default,ScrollView 切到 event tracking | 注册到 `NSRunLoopCommonModes` |
| `scheduledTimer` 闭包强引用 self 形成循环 | RunLoop 持有 Timer,Timer 持有 Closure,Closure 持有 self | 用 `[weak self]` + `deinit` 调 `invalidate()` |
| Timer fire 时间不准 | RunLoop 在长时间计算后阻塞,事件延迟派发 | 长任务移到 background queue,避免阻塞 main |
| `run()` 不返回 | 空 mode / mode 内有无限 source | 用 `run(until:)` 限时版本 |
| observer 拿不到 self | C callback 不捕获上下文 | 通过 `CFRunLoopObserverContext.info` 传指针 |
| 在 main 线程 `CFRunLoopRunInMode(time, ∞, ...)` 死锁 | main 已被本调用占用 | 同主线程 sync GCD 死锁,改用 background |
| `performSelector:withObject:afterDelay:` 不触发 | target retain 1 次,run loop 未在跑 | 在主线程调用且 main run loop 正常运行 |
| `dispatch_async` 回调没执行 | 默认 queue 不是 main queue | 显式提交到 main queue + run loop 驱动 |
| Timer 强引用的 closure 持有大量内存 | 不 invalidate 即长期驻留 | 退出界面 / `viewWillDisappear` 调 invalidate |
| async/await + main.sync | Task 内部调用 main.sync 会卡死 | 用 `await MainActor.run { }` 或 `DispatchQueue.main.async` |

## 参考资料(实际阅读过的权威来源)

- [Apple Threading Programming Guide - Run Loops](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/Multithreading/RunLoopManagement/RunLoopManagement.html) — Run loop 定义、anatomy、6 个 observer activity、5 个标准 mode、port/timer/observer 完整代码示例。
- [Apple CFRunLoopRef 官方文档](https://developer.apple.com/documentation/CoreFoundation/CFRunLoop?changes=_1&language=objc) — `CFRunLoopGetCurrent/Main`、`Run/RunInMode/Stop/WakeUp`、AddSource/Timer/Observer 完整签名。
- [Apple NSRunLoop 官方文档](https://developer.apple.com/documentation/foundation/runloop) — Swift `RunLoop.current/main`、`run/acceptInput/limitDate`、`add(Timer/Port,forMode:)`、`perform/inModes/cancelPerformSelectors`。
- [Apple CFRunLoop.c opensource 源码](https://opensource.apple.com/source/CF/CF-1153.18/CFRunLoop.c) — `_CFRunloopGet0` lazy 创建 + pthread → CFRunLoopRef 字典维护的内部实现。
- [Apple NSRunLoop Class Reference(归档)](https://developer.apple.com/library/archive/documentation/Cocoa/Reference/Foundation/Classes/NSRunLoop_Class/index.html) — Cocoa 侧 RunLoop 方法参考。