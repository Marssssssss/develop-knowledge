# Swift 并发:async/await · actor · 结构化并发 · 协作式取消

把 Swift 并发的五个机制拆成可执行、可断言的小模型:Python 侧跑在**虚拟时钟**上(断言是精确
数字),Swift 侧跑在**真实运行时**上(断言结构性事实),ObjC 侧给出**语言特性之前**的工程手法
(GCD)作为对照。

## 1. 术语先对齐:job 与挂起点

SE-0304 给"任务的一段连续运行"起了名字 —— **job**:

> The execution of a task can be seen as a succession of periods where the task was
> running, each of which ends at a suspension point or — finally — at the completion
> of the task. These periods are called **jobs**.

一次 `await` 就是一次挂起,把任务切成若干个 job。**job 内部不会被打断**,这是本 demo 里所有
"原子性"结论的唯一来源。Swift 官方书对同一件事的说法是
"suspension is never implicit or preemptive" —— 挂起只可能发生在你写下 `await` 的地方。

## 2. 五个机制

### 2.1 `await` 串行,`async let` 并行(`python/concurrency_check.py` [1])

三个各耗时 30 / 50 / 20ms 的下载:

| 写法 | 虚拟时钟实测 | 原因 |
| --- | --- | --- |
| `await a(); await b(); await c()` | **100ms** | 每次 await 都把当前任务挂到子任务完成 |
| `async let a/b/c` + 最后一起等 | **50ms** | 三个子任务在声明处就起飞,取最大值 |

断言不只看总耗时,还看**日志重叠**:并行时 `B 开始` 出现在 `A 结束` 之前 —— 总耗时对不代表
真的重叠(可能只是计时误差)。

### 2.2 actor = exclusive executor:`python` [2]

SE-0304 对 exclusive executor 的定义是"提交的 job 永不并发执行"——
*the end of one must happen-before the beginning of the other*。落在代码上就是一句话:
**把"读-改-写"整段放进同一个 job**。

5 个并发 `deposit(10)` 的结果:余额 **50**,观察序列 `[10,20,30,40,50]` 严格递增,
actor 上执行了 10 个 job(5 个 × 挂起前后各一段)。

对照组是把同一个读-改-写拆到两次 `await` 之间 —— **每一步都是原子的,结果依然是错的**:
5 次 `+10` 只剩 **10**。这一步很关键:actor 保证的从来不是"操作原子",而是"job 原子"。

### 2.3 可重入:保护范围只有 job(`python` [3] [4])

SE-0306 让 actor 的 async 方法**可重入**:挂起点释放 actor,别的消息可以插进来。目标是
*guarantees forward progress* —— 否则两个 actor 互相调用就是死锁。

代价是经典的 **check → await → act**:

```swift
if balance >= amount {              // check
    try await Task.sleep(...)        // await:actor 在这里对别人开放
    balance -= amount                // act:前提可能已经失效
}
```

两笔 80 的提款都判为可提,余额被扣成 **-60**;把复检挪回 act 前面(同一个 job 内)后,
第二笔被正确拒绝,余额 **20**。

本 demo 把代价与收益都量化了:两个 actor 互相 `await` 时,`maxConcurrentEntries == 2`
—— 同一个 actor 上确实有 2 个调用同时处于挂起中,这就是可重入的观测证据。

### 2.4 结构化并发:父任务不会忘记等子任务(`python` [6])

Swift 官方书列的四个好处:父任务**无法忘记**等待子任务;给子任务设更高优先级时父任务优先级
自动被抬高;父任务取消会级联取消子任务;task-local 值自动传播。

实测:父任务(优先级 1)挂上两个优先级 9 的子任务后立刻被抬到 **9**;父任务在**最慢**的子任务
处(40ms)才收工,而不是自己先跑完。子任务之间的完成顺序**没有任何保证**。

### 2.5 取消是协作式的(`python` [5])

`Task.cancel()` 只做两件事:置标志、级联给子任务。它**不会**中断正在跑的代码。所以:

* 检查了 `Task.isCancelled` / `Task.checkCancellation()` 的子任务:9 项只做完 2 项就全部退出;
* 从不检查的任务:标志一直是 `true`,它照样跑满 3 轮,正常返回 —— **取消不是强杀**。

还有一个容易搞反的点:取消只**向下**传播。父任务被取消后,它 `await` 一个已被取消的子任务
**不会自动抛出**,父任务照样拿到正常返回值 —— 父任务自己也得检查。

## 3. GCD 对照:语言特性之前要手写什么(`objc/main.m`)

| 语义 | Swift | GCD / ObjC |
| --- | --- | --- |
| 保护共享状态 | `actor` + `actor-isolated` | 串行队列;但"读-改-写"必须整段在一个 block 里 |
| 等一组子任务 | `withTaskGroup`(作用域保证) | `dispatch_group` —— 忘了 `wait`/`notify` **没有任何提示**,调用方静默读到不完整结果(实测:立刻读到 0 条) |
| 顺序 | actor 不保证 FIFO | 串行队列严格 FIFO(实测完成顺序 = 提交顺序,与各自耗时无关) |
| 串起异步步骤 | `await a(); await b()` | 嵌套 completion handler:实测嵌套深度 = 步骤数(3 步 3 层),抽成扁平续体后恒为 1 层 |
| 取消 | `Task.cancel()` + `checkCancellation()` | **没有**。`dispatch_block_cancel` 只对尚未开始的 block 有效;已在跑的 block 只能靠自己检查标志位 |
| self-sync | 不存在(可重入) | 队列上再 `dispatch_sync` 回本队列 → **死锁**(实测 1 秒内无 block 返回) |

`objc/main.m` [6] 的最后一个场景**故意**复现真死锁,它会泄漏一个永久阻塞的线程和一条卡死的
队列,断言完立刻 `exit`。规避手法是队列专属键:`dispatch_queue_set_specific` +
`dispatch_get_specific` —— 之所以不能用 `dispatch_get_current_queue()`(iOS 6 起废弃),是因为
队列可以 `dispatch_set_target_queue` 成层级,而它只能返回"最内层"那个,答不出"我是不是在这条链上"。

## 4. 目录与运行

```
python/loop_core.py          # 协作式调度内核(虚拟时钟,job = 两次挂起之间的执行段)
python/concurrency_check.py  # 7 个场景 / 26 条断言 ← 本目录唯一被实际跑过的自检
swift/ConcurrencyModel.swift # 真实 async/await + actor 的实现
swift/main.swift             # 7 个场景 / 18 条断言
objc/GCDPrimitives.h/.m      # 串行队列 / dispatch_group / 回调链 三个原语
objc/main.m                  # 6 个场景(含真死锁复现)
```

```bash
python3 python/concurrency_check.py                                 # → 断言 26 通过 / 0 失败
swiftc ConcurrencyModel.swift main.swift -o swift-concurrency && ./swift-concurrency
clang -fobjc-arc -framework Foundation GCDPrimitives.m main.m -o objc-gcd && ./objc-gcd
```

环境:Python 3.8+(仅标准库);Swift 5.7+ / macOS 13+(用到 `Duration` 与顶层 `await`);
ObjC 用 ARC。**注意 `main.swift` 里不能再写 `@main`**,两者在同一模块会冲突。

## 5. 关键代码

Python 侧的调度内核只用了一个生成器技巧 —— **生成器每 `yield` 一次就是一次挂起**:

```python
def _dispatch(self, t, cmd):
    kind = cmd[0]
    if kind == "sleep":                        # await 一个 I/O
        heapq.heappush(self.timers, (self.now + cmd[1], t.id, t))
    elif kind == "await":                      # await 另一个任务
        pending = [d for d in deps if not d.done]
        if pending: self.waiting[t.id] = pending     # 挂起,等被唤醒
        else:       self.ready.append(t)             # 已完成 → 立即返回
    self._release(t)                           # 挂起点释放 actor(可重入)
```

`_release()` 是可重入开关:可重入模式下每个挂起点都释放 actor;`Actor(reentrant=False)`
只在 job 结束时释放 —— 用它复现了 SE-0306 里 OddOddy/EvenEvan 的互相递归死锁。

Swift 侧照抄了 SE-0306 的 `isEven`/`isOdd` 互相递归,并用 `maxConcurrentEntries` 把"重入发生了"
变成可断言的数字而不是主观判断。

## 6. 性能与边界

* **actor 不提升并行度,只保护不变量。** 它把并发收敛成串行,吞吐上限 = 单个 job 的长度;
  一个慢 job 会拖住该 actor 上的所有后续调用(队头阻塞)。这是取舍,不是 bug。
* **任务不是线程。** 并发几百个任务不会开几百条线程,运行时用有限的协作线程池承载它们。
  但**阻塞式调用会占死执行器**:在 actor 里做同步 I/O 或信号量等待,等价于把 actor 关掉。
* **跨 actor 调用都有代价。** 每次都要进 mailbox、可能换执行器上下文;高频小调用应该
  合并成一次粗粒度调用,或把 `nonisolated`/`isolated` 用在真正无状态的部分。
* **优先级只是提示。** SE-0304 原文:*Executors are not required to run jobs in the order
  they were submitted* —— 所以本 demo 从不断言"高优先级一定先跑",只断言"不保证 FIFO"。
* 本目录里 Python 模型的耗时是**虚拟时钟**,与真机性能无关;它保证的是确定性,不是速度。

## 7. 常见坑

1. **在 actor 方法里跨 `await` 维持不变量。** check/await/act 是最高频的生产事故形状,
   而且没有任何 race 报告 —— 修法是把复检挪到 act 之前,或把整段放进同一个 job。
2. **把 actor 当 FIFO 队列。** 不是。Actor 上的 job 既不保证按到达顺序开始,也不保证按到达顺序收尾。
3. **以为 `cancel()` 会停下任务。** 它只置标志;循环体里不检查,任务就是不停。
4. **以为取消会向上传播。** 只向下。父任务被取消后 await 子任务不会抛错,自己也得检查。
5. **在 actor 里做同步阻塞等待。** 会占死执行器;严重时演变成死锁(与 GCD 的 self-sync 同类)。
6. **写断言时的自伤。** 本目录开发过程中出现过 3 次"实现对了、断言错了":把 job 数当成
   "5 个"实际是"5 个 × 挂起前后各一段 = 10";用前缀匹配取父任务完成时刻,把子任务误当父任务;
   `io()` 是工厂函数却当成生成器直接传。**断言失败先怀疑断言**。
7. **`Task.sleep` 不是"让出 CPU",是"挂起点"。** 它一定会释放 actor —— 这正是可重入窗口的常见来源。

## 8. 参考资料

官方文档与提案(核心结论均出自这里):

1. *The Swift Programming Language · Concurrency* —— suspension 语义、`async let`、
   结构化并发四条好处、协作式取消两件工具。 https://docs.swift.org/swift-book/documentation/the-swift-programming-language/concurrency/ (镜像:https://docs.swift.org/main/documentation/the-swift-programming-language/concurrency)
2. *SE-0306: Actors* —— 可重入(reentrancy)章节、`@reentrant(never)` 与 OddOddy/EvenEvan
   互相递归死锁的例子、`@reentrant` 未采纳的现状。 https://apple-docs.everest.mt/docs/swift-evolution/0306-actors
3. *SE-0304: Structured concurrency* —— **job** 的定义、exclusive executor 的
   happens-before 要求、优先级提升的两种情形、"执行器不保证按提交顺序运行"。 https://apple-docs.everest.mt/docs/swift-evolution/0304-structured-concurrency
4. *TaskPriority* —— 子任务继承父任务优先级、被高优先级任务等待时永久提升、
   actor 上运行时临时提升。 https://docs.swift.org/latest/documentation/swift/taskpriority/
5. *[Accepted with Modification] SE-0306: Actors*(Swift Forums)—— 核心团队对可重入的
   取舍说明:"if every async method on an actor locked that actor until its completion,
   it would be very easy to run into deadlocks"。 https://forums.swift.org/t/swift-c-enum-case-mapping/47662
6. *WWDC 2023 Session 10170 · Beyond the basics of structured concurrency* —— 优先级提升
   与任务组、`withDiscardingTaskGroup` 的兄弟任务取消。 https://developer.apple.com/videos/play/wwdc2023/10170/

工程实践(用于交叉验证机制在生产中的表现):

7. *Understanding how priority escalation works* — Hacking with Swift,
   `Task.currentPriority` 在等待后的变化。https://www.hackingwithswift.com/books/concurrency/understanding-how-priority-escalation-works
8. *Swift 6 Actors in Practice* — Emrld Labs,ThumbnailStore 的 check/await/act 缓存击穿案例。 https://emrldlabs.com/blog/swift-6-actors-isolating-shared-state-multi-app/
9. *Actors in Swift: The Problem They Solve and How it Works* — Swift Differently,
   "the order calls arrive is not the order they run"。https://www.swiftdifferently.com/blog/swift/concurrency/how-actors-work
10. *Why can't we use a dispatch_sync on the current queue?* — Stack Overflow,
    含 Apple *Concurrency Programming Guide* 的原话与队列专属键写法。 https://stackoverflow.com/questions/10984732/why-cant-we-use-a-dispatch-sync-on-the-current-queue
11. *dispatch_sync inside dispatch_sync causes deadlock* — Stack Overflow,
    "dispatch_sync blocks the current thread, not the current queue"。 https://stackoverflow.com/questions/23939730/dispatch-sync-inside-dispatch-sync-causes-deadlock
12. 《编写高质量 iOS 与 OS X 代码的 52 个有效方法》· GCD 一章 —— `dispatch_get_current_queue`
    被废弃的原因与队列专属键的写法。 https://topic.alibabacloud.com/a/52-effective-ways-to-write-high-quality-ios-code-10-grand-central-dispatch-font-classtopic-s-color00c1degcdfont_1_12_30754777.html
