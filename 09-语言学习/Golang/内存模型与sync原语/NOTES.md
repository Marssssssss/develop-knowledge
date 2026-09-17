# NOTES.md — 同步原语强度全表 与 选型对照

> 本文件是 `README.md` 的补充材料（README 受 ≤200 行硬约束，长表移到这里）。
> 表格内容全部来自本节列出的权威来源，`python/main.py` 的 A~G 组断言逐条覆盖。

## 1. 各同步原语的强度（`go.dev/ref/mem` 原文结论）

| 原语 | 保证 |
| --- | --- |
| go 语句 | 启动语句 synchronized before 新 goroutine 的执行开始 |
| goroutine 退出 | **不保证**同步于任何事件（连 `go` 语句都可能被优化掉） |
| channel send（缓冲） | send synchronized before **对应** receive 完成 |
| channel close | close synchronized before 收到零值的 receive |
| unbuffered channel | receive synchronized before **对应 send 完成**（比缓冲强） |
| channel 容量 C | 第 k 次 receive synchronized before 第 **k+C** 次 send 完成 |
| Mutex / RWMutex | 第 n 次 Unlock synchronized before 第 m 次 Lock 返回（n < m） |
| 成功的 TryLock | 等价于 Lock；**失败则完全不建立同步关系** |
| Once | `f()` 的完成 synchronized before 任何 `once.Do(f)` 的返回 |
| atomic | 若 A 的效果被 B 观察到，则 A synchronized before B；**所有原子操作有一个 SC 全序** |
| init | `q` 的 init 完成 → `p` 的 init 开始（p 导入 q）；全部 init → `main.main` |
| Finalizer | `SetFinalizer(x, f)` synchronized before `f(x)` |

断言位置：B 组（文档四例）、C 组（其余同步机制）。

## 2. `sync.Mutex` 与同类原语的选型对照

| 维度 | Go（单一 Mutex + 双模式） | Java `ReentrantLock` | C++ `std::mutex` |
| --- | --- | --- | --- |
| 公平性 | 自适应（正常/饥饿自动切） | 可选 fair 构造参数 | 实现自定，通常不公平 |
| 公平代价 | 只在检测到长等待后才付出 | 立即付出（吞吐下降） | — |
| 状态载体 | 一个 int32 位域 + sema | AQS 内部 state | 平台原语 |
| 可重入 | **不可重入** | 可重入 | 不可重入 |
| TryLock 语义 | 成功等价 Lock、失败无同步效果 | `tryLock` 同 | `try_lock` 同 |

结论：Go 的取舍是"**默认最大化吞吐，只在出现病态长尾时短暂切换到公平**"，
而不是让调用方在构造时二选一。

## 3. 本 demo 未覆盖的相邻话题

- `RWMutex` 的 writer 优先规则与递归读锁死；
- `runtime_SemacquireMutex` 的真实实现（futex / semaRoot 树）；
- 内存屏障层面：`atomic` 的 acquire/release 语义在 x86-TSO 与 arm64 上的差异
  （本 demo 只按规范层面的 synchronized before 建模，不做硬件层重排模拟）。
