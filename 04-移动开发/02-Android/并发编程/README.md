# Android · 并发编程

研究 Android 上"把工作搬到别的线程、把结果搬回主线程"的几套机制:消息队列(Handler/Looper)、协程与 Flow、后台任务调度(WorkManager)。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [Handler消息机制/](./Handler消息机制/) | Handler / Looper / MessageQueue 异步消息机制(post / sendMessage / HandlerThread / postDelayed / 内存泄漏修复) |
| [协程上下文与Flow/](./协程上下文与Flow/) | CoroutineContext 合并与继承、调度器(Default/IO/Unconfined)、结构化并发与失败传播、冷流 vs 热流、`flowOn` 边界 |
| [WorkManager约束与重试/](./WorkManager约束与重试/) | 约束条件(AND 语义 + 默认全 false)、退避与重试、唯一任务策略(KEEP/REPLACE/APPEND)、10 分钟执行窗口 |

## 共同主线

| 层 | 机制 | 谁负责调度 | 适合什么 |
| --- | --- | --- | --- |
| 消息 | `Handler` / `Looper` | 自己 | 线程间投递单条消息,UI 更新 |
| 协程 | `Dispatchers` + `CoroutineScope` | 库 | 结构化并发、可取消的异步流程 |
| 任务 | `WorkManager` | 系统 | 需要约束/持久化/保证执行的延迟任务 |

## 待研究

- [ ] `Flow` 操作符实现原理(`map` / `buffer` / `conflate` / `flatMapLatest` 的通道模型)
- [ ] `StateFlow` / `SharedFlow` 的重放与去重语义
- [ ] 协程异常处理器(`CoroutineExceptionHandler` 与 `SupervisorJob` 的配合边界)
- [ ] `WorkManager` 与 `ForegroundService` 的取舍(长任务如何不被中断)
- [ ] `Handler` 的同步屏障(`sync barrier`)与异步消息
- [ ] `Looper` 空闲消息(`IdleHandler`)与帧回调
- [ ] 线程池选型(`Executors` / `ThreadPoolExecutor` 参数)与协程调度器的关系
- [ ] `CoroutineDispatcher.limitedParallelism` 与信号量限流的差异
