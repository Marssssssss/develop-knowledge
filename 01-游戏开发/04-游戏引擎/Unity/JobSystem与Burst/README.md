# Unity C# Job System 与 Burst

## 简介

- **Job System** 是 Unity DOTS 三件套（ECS + Job System + Burst）里的并发层：写"小单元工作"的 struct，交给 job 系统调度到所有可用 CPU 核上，并用**安全系统**在编译/调度期挡住数据竞争。
- **Burst** 是一个基于 LLVM 的编译器，把 C# 的**高性能子集 HPC#**（High Performance C#）的 IL 翻译成针对目标架构优化的本地代码；因为 Burst **不支持任何托管对象/引用类型**，数据必须用 blittable 类型或 `NativeContainer` 传递。
- 本 demo 用 Python + Go 复刻这套语义里最容易出错、也最有"原理味"的五个部分：**安全系统的冲突判定、JobHandle 依赖、job 数据的复制语义、ParallelFor 分批与 work stealing、分配器寿命 + HPC# 类型子集**。

## 原理详解

### 1. 安全系统在"调度时"报错

`NativeContainer`（如 `NativeArray`）是所有容器都内建安全系统的托管壳，它跟踪谁在读、谁在写：

| 组合 | 结果 |
| --- | --- |
| 读 + 读 | 允许并行 |
| 读 + 写 / 写 + 写 | 调度第二份 job **当场抛异常**，除非显式声明依赖 |

关键点是"**当场**"——官方文档明确：两个独立 job 写同一个 `NativeArray` 时，在**调度第二个 job 的时候**就抛出带解释的异常，而不是等到运行出乱序结果。主线程访问同样受约束：job 还在写时主线程读也会报错。

### 2. 依赖 = JobHandle 链

`Schedule()` 返回 `JobHandle`；把它传给下一个 job 的 `Schedule(handle)` 即声明"我等它"。多个依赖可用 `JobHandle.CombineDependencies(handles)` 合并。文档提醒：依赖会**延迟执行**，所以能并行就别串行；`Complete()` 则是"最晚越好"——它既保证主线程可安全访问数据，也**顺带清理安全系统的状态**，不调用就会泄漏。

### 3. job 数据是被复制的

调度时 job struct 的数据**复制一份**给 worker 线程；副本与原 `NativeContainer` 对象指向同一块非托管内存，因此**只有写进 NativeContainer 的结果在 job 结束后可见**，改动普通字段不会回传。文档同时坦白一个洞：**访问非只读的静态可变数据不受任何保护**，可能直接崩编辑器。

### 4. IJobParallelFor：分批 + 一次偷一半

`Schedule(length, batchCount)`：job 系统把 `length` 个 `Execute(i)` 切成批，每个 CPU 核跑一个原生 job，去领批次执行。batch count 越小负载越均但开销越大（官方建议从 1 开始往上试）。

**work stealing**：先干完的核会去偷别人的批次，**一次只偷一半**——偷太少不划算，偷太多破坏缓存局部性。本 demo 用确定性批耗时（`1 + (i*7) % 29`）造出负载不均，实测确实发生了偷取且每次只拿 `max(1, 剩余/2)`。

### 5. 分配器与 HPC# 子集

| 分配器 | 寿命 | 说明 |
| --- | --- | --- |
| `Allocator.Temp` | ≤ 1 帧 | 最快，不能作为 job 成员字段传递 |
| `Allocator.TempJob` | ≤ 4 帧 | **超过 4 帧未 Dispose 控制台会告警**，小 job 默认用它 |
| `Allocator.Persistent` | 任意长 | 最慢（`malloc` 包装），性能敏感路径别用 |

Burst 支持 `bool/byte/sbyte/double/float/int/uint/long/ulong/short/ushort`、struct（含 fixed 数组字段）、`LayoutKind.Sequential/Explicit`、带接口约束的泛型 struct；**不支持 `char`、`decimal`、`string`（托管类型）、托管数组、多维数组**。特例：`static readonly` 的只读托管数组可以用，但**只能直接使用、不能当参数传**，且 Burst 在编译期就把它拷成只读副本。

## 对比 / 选型

| 维度 | IJob | IJobParallelFor | IJobFor |
| --- | --- | --- | --- |
| 语义 | 单任务 | 每个下标一次 `Execute(i)`，多核并行 | 同 ParallelFor，但可调度为**不并行**执行 |
| 数据 | NativeArray 任选 | 必须以 NativeArray 为数据源并显式给 length | 同左 |
| 典型用途 | 少量粗粒度工作 | 大批量同质数据（粒子、变换） | 需要同一份代码既能并行又能串行调试 |

`IJobParallelForTransform` 是 ParallelFor 的特化：每个并行的核拿到 transform 层次结构里**独占的一个 Transform**。

## 环境准备

- Python 3.8+（标准库，零依赖）
- Go 1.21+（零依赖）
- 不需要安装 Unity：本 demo 复刻的是**语义模型**，不是运行时

## 运行方式

```bash
python3 python/selfcheck_jobsystem.py    # 29 条断言
cd go && go run .                        # 同题 Go 版（打印关键行为）
```

## 关键代码片段

安全系统的核心就是调度时的三张表比对（Python 版）：

```python
def schedule(self, job: Job) -> JobHandle:
    for uid in job.writes:
        for h in self.pending_writers.get(uid, []):
            if h.id not in self.done and h not in job.deps:
                raise SafetyException(f"{job.name} 与 {h.job.name} 同时写同一 NativeArray")
        for h in self.pending_readers.get(uid, []):
            if h.id not in self.done and h not in job.deps:
                raise SafetyException(f"{job.name} 写 / {h.job.name} 读 冲突")
    ...
```

work stealing（一次偷一半，Go 版）：

```go
victim := 0                       // 选剩余批次最多的 worker
for j := 1; j < workers; j++ {
    if len(queues[j]) > len(queues[victim]) { victim = j }
}
remaining := len(queues[victim])
if remaining < 2 { continue }
take := remaining / 2             // 关键：只偷一半，保证缓存局部性
if take < 1 { take = 1 }
queues[w] = append(queues[w], queues[victim][:take]...)
```

## 性能与边界

- `length=1000, batchCount=64` → 16 批；`batchCount=1` → 1000 批（最细粒度、最均但调度开销最大）。
- 依赖链会**串行化**：`A→B→C` 里 Complete(C) 必须先 Complete B、再 Complete A，链越长并行窗口越窄。
- 安全系统的判定是**静态的**（按 job 声明的读写集合），它管不了静态可变数据——文档明确说这类访问"绕过所有安全系统"。
- Burst 只编译被 `[BurstCompile]` 标注的 job 或静态方法（及其所在类型），不是整个程序；改 Burst 包版本需要重启编辑器。

## 注意事项与常见坑

- **`nativeArray[0]++` 是无效的**：NativeContainer 不实现 ref return，这行等价于 `temp = arr[0]; temp++`。必须"读出来 → 改 → 写回"（demo 断言 1、2）。
- **忘了 `Complete()`** 不仅读不到数据，还会**泄漏安全系统状态**；本 demo 用 `leaked` 列表把这个后果显式化。
- **不要访问可变静态字段**：这是安全系统唯一无能为力的地方。
- **`[ReadOnly]` 不只是性能优化**：标注后多个只读 job 才能真并行，否则默认是可写、会被判定冲突。
- **temp 分配器有寿命**：`Temp` ≤1 帧、`TempJob` ≤4 帧（超了控制台告警），别把 TempJob 容器挂在跨帧的系统里。
- **`string` 不能进 Burst job**（它是托管类型）；日志/调试要走 `[BurstCompile]` 之外或者在非 Burst 代码里做。
- job 应设计成**在一帧内完成**：job 系统可能让它在主线程上跑（主线程空闲时），慢 job 会拖帧。

## 参考资料（实际阅读过的权威来源）

- [Unity — Write multithreaded code with the job system](https://docs.unity3d.com/Manual/job-system.html) — 总览与主题索引。
- [Unity — Jobs overview](https://docs.unity3d.com/Manual/job-system-jobs.html) — `IJob` / `IJobParallelFor` / `IJobParallelForTransform` / `IJobFor` 四类语义。
- [Unity — Create and run a job](https://docs.unity3d.com/Manual/job-system-creating-jobs.html) — Schedule/Complete、job 数据被复制、Complete 兼清理安全状态、静态数据无保护。
- [Unity — Job dependencies](https://docs.unity3d.com/Manual/job-system-job-dependencies.html) — JobHandle 依赖链与 `CombineDependencies`。
- [Unity — Parallel jobs (IJobParallelFor)](https://docs.unity3d.com/Manual/job-system-parallel-for-jobs.html) — 分批、batch count 调参建议、一次偷一半的 work stealing 与缓存局部性。
- [Unity — Thread safe types / Introduction to NativeContainer](https://docs.unity3d.com/Manual/job-system-thread-safe-types.html) — NativeContainer 定义、安全系统规则、`[ReadOnly]`、三种分配器寿命、无 ref return 的坑。
- [Unity — Burst compiler (1.8.30)](https://docs.unity3d.com/Packages/com.unity.burst@1.8/manual/index.html) 与 [C#/.NET type support](https://docs.unity3d.com/Packages/com.unity.burst@1.8/manual/csharp-type-support.html) — Burst 用 LLVM 编译 HPC# 子集、支持/不支持的类型清单、静态只读托管数组的限制。
