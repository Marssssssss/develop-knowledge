# iOS · 并发编程

研究 GCD(Grand Central Dispatch)与 RunLoop 在 iOS 平台上的并发原语、QoS 调度、读写锁等。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [GCD同步原语/](./GCD同步原语/) | DispatchQueue / DispatchSemaphore / DispatchGroup / barrier(并发上限、fan-out/fan-in、读写锁、死锁) |
| [Swift-Concurrency/](./Swift-Concurrency/) | Swift 并发:await 串行 vs `async let` 并行、actor 可重入、结构化并发、协作式取消 |
| [Swift6严格并发/](./Swift6严格并发/) | Swift 6 严格并发:隔离区四类与合并规则、弱传递、`Sendable` 判定表、全局变量隔离(SE-0414 / SE-0302 / SE-0412) |

## 待研究

- [ ] DispatchSource(文件描述符 / Mach port / signal 事件监听)
- [ ] DispatchWorkItem 的 cancel / wait 语义
- [ ] OperationQueue 与 GCD 的对比与混用
- [x] Swift async/await + Actor 模型的 GCD 映射 → demo 229
- [x] Swift 6 严格并发检查(Sendable / 隔离区 / 全局隔离)→ demo 452
- [ ] Swift 6 `sending` / `transferring` 参数标注的跨函数推断
- [ ] actor 可重入与 MainActor 切换的调度开销实测
- [ ] QoS 传播与线程爆炸(thread explosion)问题
- [ ] OSAllocatedUnfairLock(iOS 16+) 与 pthread_mutex 性能对比