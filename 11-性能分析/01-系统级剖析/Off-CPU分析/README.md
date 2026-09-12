# Off-CPU 分析(调度延迟与阻塞时间)

## 简介

Off-CPU 时间 = 线程**不在 CPU 上运行**所花的时间。当它发生在应用请求的同步路径上时,直接且成比例地影响性能——而这类延迟(CPU 阻塞、锁等待、I/O 等待、调度延迟)**在 on-CPU 剖析里完全不可见**。Off-CPU 分析是 Brendan Gregg 定义的方法论:在**上下文切换结束点**插桩,度量每个线程离开 CPU 的时长并抓取其栈,再以 off-CPU 火焰图可视化。Gregg 的量化结论:off-CPU 火焰图约解决 40% 的问题,配合 off-wake 火焰图约 70%。

- **插桩点**:`finish_task_switch()`(Linux 调度器上下文切换收尾)——处于**下一个**线程的上下文中。
- **栈不变性**:线程醒来时抓的栈与睡去时相同(期间未运行),因此切换结束点抓栈即可。
- **线程池时间膨胀**:多个线程执行相同代码路径,聚合的等待时间之和可超过流逝的墙钟。
- **`--state=2`**:过滤只保留 `TASK_UNINTERRUPTIBLE`(D 状态),剔除非自愿切换。
- **历史**:Gregg 在 Netflix 做出(与 offcputime 等 bcc 工具链配套)。

## 原理详解

### offcputime 的核心逻辑(Gregg 伪代码,本 demo 完整实现)

```text
on context switch finish:                       # finish_task_switch()
    sleeptime[prev_thread_id] = timestamp       # 刚睡去的线程记时刻
    if !sleeptime[thread_id]: return            # 新上 CPU 的线程无记录则跳过
    delta = timestamp - sleeptime[thread_id]    # 它睡了多久
    totaltime[pid, execname, user stack, kernel stack] += delta
    sleeptime[thread_id] = 0

on tracer exit:
    for each key in totaltime: print key + totaltime  # folded 输出
```

三个关键点(原文):
1. **单点插桩**:全部测量在切换**结束**处,处于下一个线程上下文——算时长的同时直接拿当前上下文(内核栈/用户栈),无需保存再检索。
2. **栈不变**:off-CPU 结束时抓的栈与开始时完全相同。
3. 若在切换**开始**处插桩(即将睡眠线程的上下文),反而需要保存/检索状态,更复杂。

### 采集与出图(Linux 实操)

```bash
/usr/share/bcc/tools/offcputime -df -p $(pgrep -nx mysqld) 30 > out.stacks
./flamegraph.pl --color=io --countname=us < out.stacks > out.svg
#   -f  输出 folded 格式(单行栈 + 总阻塞时间)
#   -d  内核栈与用户栈之间插入分隔帧 "-"
#   -p  指定 PID;30 = 追踪 30 秒
grep do_command out.stacks | ./flamegraph.pl ...   # folded 单行可 grep 过滤
```

### 工具矩阵(同一方法论在不同层的落点)

| 工具 | 插桩点 | 键 | 说明 |
| --- | --- | --- | --- |
| offcputime | 上下文切换结束 | thread_id | 通用 off-CPU |
| fileiostacks | VFS 读写 | thread_id | entry 记时戳,return 算 delta |
| biostacks | 块 I/O 队列 | request_id | 完成是异步的,**入队时**测进程/栈 |
| wakeuptime | 切换开始 + wakeup | target_tid | 记录唤醒者栈(锁竞争分析利器) |
| offwaketime | off-CPU + 唤醒栈 | — | 分隔符 `--`:向下读 off-CPU 栈,向上读倒序唤醒栈 |

### 两大坑与缓解

1. **空闲线程淹没信号**:mysqld 实测中大量"等待工作"的睡眠线程形成单线程满宽(30s 追踪里出现 25~30s 的塔),线程池列宽甚至超过 30s(多线程聚合)。缓解:grep 过滤 / 悬停看帧名识别线程类型。
2. **非自愿上下文切换混入**:CPU 争抢也被记成 off-CPU。缓解:`--state=2` 只统计 D 状态(TASK_UNINTERRUPTIBLE)。

更深的归属问题("栈只能告诉你等在哪,不能告诉你为什么这么久")需要 wakeup → off-wake → chain graphs 逐级增强因果解释。

## 对比 / 选型

| 维度 | On-CPU | Off-CPU |
| --- | --- | --- |
| 分析对象 | 线程在 CPU 上的时间 | 线程阻塞/睡眠的时间 |
| 典型问题 | 计算热点 | I/O 阻塞、锁等待、调度延迟 |
| 工具 | perf / CPU 火焰图 | offcputime / off-CPU 火焰图 |
| 关系 | **互补**:宽塔方向相反的问题 | |

## 环境准备

- Python ≥ 3.8(模拟器,仅标准库);Go ≥ 1.18(goroutine off-CPU 记账);Linux + bcc 工具用于实操

## 运行方式

### Python(上下文切换记账模拟器)
```bash
python3 python/main.py              # 模拟线程池,输出 off-CPU folded + 时间膨胀演示
```

### Go(goroutine off-CPU 记账)
```bash
cd go && go run main.go
```

## 关键代码片段

Python 版记账核心(逐行对应 Gregg 伪代码;模拟调度器只调度"切换事件"本身,不执行真实负载):

```python
def on_switch_finish(prev_tid, next_tid, ts):
    sleeptime[prev_tid] = ts                    # 刚睡去的线程记时刻
    start = sleeptime.pop(next_tid, None)       # 新上 CPU 的线程
    if start is None: return
    delta = ts - start                          # 它睡了多久
    key = (pid(next_tid), execname, stack(next_tid))
    totaltime[key] += delta
```

Go 版演示同一记账作用于 goroutine:`runtime.Gosched`/channel 收发点前记录时刻,恢复时结算 delta 聚合 folded。

## 性能与边界

- 开销警告(Gregg 原文):调度器事件可达**每秒数百万**,单事件开销 × 事件速率 = 显著总开销;bcc/eBPF 在**内核中做汇总**缓解,需 Linux 4.8+ 栈追踪支持。
- 多线程聚合的等待时间**可超过流逝时间**——不是 bug,是聚合语义(见"时间膨胀")。
- `-fomit-frame-pointer` 编译破坏帧指针回溯;JIT 语言(Java/Node)栈只剩十六进制地址。

## 注意事项与常见坑

- **睡眠时间归属**:off-CPU 栈显示"等在 futex/IO"但解释不了为何久——需要 wakeup 栈(offwaketime)补因果链。
- **同步 vs 异步 I/O**:阻塞式文件读的 off-CPU 直接等于应用延迟;异步后台刷盘则不然(改看 block I/O 火焰图)。
- **过滤再出图**:先 `grep` folded 文件聚焦感兴趣的路径,别跟空闲塔硬刚。
- 对比时同口径:off-CPU 的 countname 是微秒(us)而非样本数。

## 参考资料(实际阅读过的权威来源)

- [Off-CPU Flame Graphs — Brendan Gregg](https://www.brendangregg.com/FlameGraphs/offcpuflamegraphs.html) — offcputime 伪代码、插桩点选择、工具矩阵、mysqld 时间膨胀案例、--state=2
- [Flame Graphs — Brendan Gregg](https://www.brendangregg.com/flamegraphs.html) — 火焰图类型总览(off-CPU 在六类变体中的位置)、宽平读法
