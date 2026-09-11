# iOS · 并发编程

研究 GCD(Grand Central Dispatch)与 RunLoop 在 iOS 平台上的并发原语、QoS 调度、读写锁等。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [GCD同步原语/](./GCD同步原语/) | DispatchQueue / DispatchSemaphore / DispatchGroup / barrier(并发上限、fan-out/fan-in、读写锁、死锁) |

## 待研究

- [ ] DispatchSource(文件描述符 / Mach port / signal 事件监听)
- [ ] DispatchWorkItem 的 cancel / wait 语义
- [ ] OperationQueue 与 GCD 的对比与混用
- [ ] Swift async/await + Actor 模型的 GCD 映射
- [ ] QoS 传播与线程爆炸(thread explosion)问题
- [ ] OSAllocatedUnfairLock(iOS 16+) 与 pthread_mutex 性能对比