# GPM 调度模型

## 简介

- Go runtime 调度器的三层抽象:**G**(goroutine,执行路径)/ **M**(machine,OS 线程)/ **P**(processor,执行 Go 代码所需的逻辑资源)。M 必须绑定一个 P 才能执行用户代码;G 在 M 上被切入切出。
- 关键概念:
  - **LRQ/GRQ**:每个 P 一个本地运行队列;全局运行队列存放尚未分配给 P 的 G
  - **work stealing**:P 没活时从别的 P 的 LRQ **偷一半**;内部称为 spinning the M
  - **1/61 规则**:`schedule()` 中 1/61 的概率先查 GRQ(防饿死),否则 LRQ → 偷 → GRQ
  - **M-P 解绑**:同步阻塞系统调用时 M 带着阻塞的 G 离开 P,调度器调新 M 服务该 P
  - **协作式调度**:调度点在函数调用等安全点;Go 1.14 起支持基于信号的异步抢占
- 历史背景:Ardan Labs 三部曲(2018,基于 Go 1.11 语义,2024 年转载确认概念仍适用)是最常被引用的中文圈英文原始资料;GOMAXPROCS/runtime API 语义以 pkg.go.dev/runtime(Go 1.27.1)为准。

## 原理详解

### 三层结构与队列

```text
   G1 G2 G3      G7 G8          全局运行队列 GRQ(未分配给 P 的 G)
    │ │ └──────────┐
    ▼ ▼            ▼
  ┌─────── LRQ ────────┐      ┌─────── LRQ ────────┐
  │        P0           │ steal │        P1          │
  └────────┬────────────┘◀─────┴─────────┬──────────┘
           │ 绑定                        │ 绑定
        M0(OS 线程)                 M1(OS 线程)
```

调度循环(`runtime.schedule()` 语义,Ardan Labs Listing 2):

1. **1/61 的时间**先查 GRQ(全局队列防饿死);
2. 查本地 LRQ;
3. 都没有 → 尝试从其他 P **偷一半**;
4. 还没有 → 再查 GRQ → poll network(网络轮询器)。

### 两类阻塞的不同处理(核心差异)

| 场景 | G 的去向 | M 的去向 | P 的去向 |
| --- | --- | --- | --- |
| channel/mutex/atomic 阻塞 | 重新排队,可再被任意 M 执行 | **不阻塞**,立刻切下一个 G | 不动 |
| 同步阻塞系统调用(文件 I/O、cgo) | 挂在 M 上等系统调用返回 | **带 G 与 P 解绑**,完成后挂起备用("placed on the side for future use") | **换新 M 服务** |
| 异步网络调用(epoll/kqueue/iocp) | 移交 network poller | 立即自由,无需新 M | 不动 |

关键结论(Ardan Labs):"Go has turned IO/Blocking work into CPU-bound work at the OS level."——两个 goroutine 传消息时同一 OS 线程 + 核心从头用到尾,OS 视角线程从未 waiting,不需要线程池。

### runtime 观测 API(pkg.go.dev/runtime)

- `GOMAXPROCS(n)`:设置可同时执行的最大 CPU 数(即 P 数);**n < 1 只查询不修改**;默认 = min(逻辑 CPU 数, 亲和掩码, cgroup 配额),且不会被设到 < 2(除非机器 < 2);**阻塞在系统调用里的线程不计入**;自定义后禁用自动更新(Go 1.25 起可 `SetDefaultGOMAXPROCS` 恢复)。
- `NumGoroutine()`:当前存在的 goroutine 总数。
- `Gosched()`:让出处理器但不挂起自己,自动恢复。
- `Goexit()`:只终止调用者一个 goroutine,先跑完所有 defer;因不是 panic,defer 中 `recover()` 返回 nil;main goroutine 里调用则程序继续跑其他 goroutine,全退出后崩溃。
- `LockOSThread()`:把 G 绑定到当前 OS 线程(调用依赖线程状态的 OS 服务前使用),须与 Unlock 配对。

### 抢占语义

- Go 1.11 及以前:协作式——调度决策发生在函数调用等安全点;**不含函数调用的紧凑循环**会造成调度与 GC 延迟。
- Ardan Labs 记录:非协作式抢占提案已于当时接受(实际落地为 Go 1.14);runtime 文档 `GODEBUG=asyncpreemptoff=1` 确认现行机制为"signal-based asynchronous goroutine preemption"。
- 调度器运行在**用户态**、随 runtime 编进程序;但行为"looks and feels preemptive"。

## 对比 / 选型

| 维度 | Go GPM | OS 线程池 | Python asyncio |
| --- | --- | --- | --- |
| 调度位置 | 用户态 runtime | 内核 | 事件循环 |
| 切换成本 | ~200ns / ~2.4k 指令(Ardan Labs) | ~1000-1500ns / ~12-18k 指令 | 单线程内协程切换 |
| 阻塞系统调用 | M-P 解绑,线程数可超 GOMAXPROCS | 线程阻塞占槽 | 直接阻塞整个循环 |

## 环境准备

- 操作系统:任意(Go 跨平台;Python 模拟同样跨平台)
- 语言版本:Go 1.21+ / Python 3.10+
- 依赖:无

## 运行方式

### Go

```bash
cd go
go run gpm_scheduler.go
```

### Python(调度模拟,确定性断言)

```bash
python3 python/gpm_sim.py
```

## 关键代码片段

work stealing(对应原理"调度循环"第 3 步):

```python
for other in self.ps:
    if other is not p and len(other.lrq) >= 2:
        n = len(other.lrq) // 2          # 偷一半
        for _ in range(n):
            p.lrq.append(other.lrq.pop())
```

M-P 解绑(对应"两类阻塞"第二行):

```python
m.g = g          # 阻塞的 G 仍挂在 M1 上
m.p = None       # M1 与 P 解绑并挂起备用
m2 = self._free_m(); m2.p = p; p.m = m2   # 新 M 接管 P
```

## 性能与边界

- goroutine 上下文切换 ~200ns / ~2.4k 指令,OS 线程 ~1000ns+ / ~12k+ 指令(Ardan Labs 数字)。
- M 数量没有上限约束(阻塞在系统调用的线程不计入 GOMAXPROCS);P 数 = GOMAXPROCS。
- 本 demo 的 Python 模拟是**教学语义复刻**(固定种子、无真实并行),不是 runtime 复制品。

## 注意事项与常见坑

- **坑 1:`GOMAXPROCS(1)` 后"并行"消失**。现象:CPU 密集任务不再重叠。原因:P=1 只有一个执行槽。规避:容器部署注意 cgroup 配额会被 Go 自动感知为新默认值。
- **坑 2:紧凑循环卡死调度(Go 1.13-)**。现象:无函数调用的 `for` 循环让其他 G/GC 得不到机会。原因:协作式调度只在安全点让出。规避:升级 ≥1.14 或循环内插入函数调用/`runtime.Gosched()`。
- **坑 3:误以为 Go 抢占与 OS 完全等价**。runtime 文档 `asyncpreemptoff=1` 专门用于关掉信号抢占——说明它是默认行为但也是可被 GODEBUG 干扰的行为。
- **坑 4:从 main 里 `Goexit()`**。程序不会退出,继续跑其他 goroutine,全空后 crash——应避免。

## 参考资料(实际阅读过的权威来源)

- [Scheduling In Go : Part II — Go Scheduler, Ardan Labs](https://www.ardanlabs.com/blog/2018/08/scheduling-in-go-part2.html) — G/M/P 定义、LRQ/GRQ、偷一半、1/61 调度顺序、两类阻塞的 M/P 处置、协作式调度与安全点、切换成本数字。
- [Scheduling In Go : Part I — OS Scheduler, Ardan Labs](https://www.ardanlabs.com/blog/2018/08/scheduling-in-go-part1.html) — OS 调度背景:线程三状态、上下文切换成本、Less is More、缓存行 64B 与伪共享。
- [runtime — The Go Programming Language, pkg.go.dev/runtime](https://pkg.go.dev/runtime) — GOMAXPROCS/NumGoroutine/Gosched/Goexit/LockOSThread/GC 的官方语义、GODEBUG schedtrace/asyncpreemptoff。
