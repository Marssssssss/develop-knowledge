# 434 Off-Wake：把调度延迟归因到「阻塞栈 + 唤醒者栈」

> `runqlat` 只告诉你**等了多久**，Off-CPU 火焰图只告诉你**在哪儿等**。真正的原因常常在**另一个线程**——那个唤醒你的 waker。Off-Wake 剖析把两者缝在一起：宽度仍是 off-CPU 时间，键变成 `(waker 栈, 阻塞栈)` 二元组。

## 1. 简介

本 demo 依据 Brendan Gregg《Off-CPU Analysis》实现：

- 原文伪代码级别的 `finish_task_switch()` 记账器（含「`sleeptime` 清零而非删除」这个细节）；
- off-CPU 时间的**三段拆分**：真阻塞 / 调度延迟 / 膨胀比，量化"时间膨胀"；
- `--state` 状态过滤（排除非自愿切换）；
- 线程池陷阱的定量化：等待工作的栈占比 vs 请求同步路径占比；
- **off-wake 联合归因**：同一阻塞栈因唤醒者不同被拆成多个塔。

## 2. 原理详解

### 2.1 off-CPU time 的定义与唯一插桩点

> "Off-CPU time consists of everything from when a thread blocked to when it **began running again**, including scheduler delay."

关键：**它包含调度延迟**。CPU 饱和时线程被唤醒后还要在运行队列里排队，这段等待也算进 off-CPU 时间——这就是**时间膨胀（time inflation）**，也是 Off-CPU 分析最容易被误读的地方。

所有测量发生在同一个点：上下文切换例程的结尾 `finish_task_switch(prev)`，**在下一个线程（next/cur）的上下文中**执行。原文伪代码：

```
on context switch finish:
    sleeptime[prev_thread_id] = timestamp
    if !sleeptime[thread_id]
        return
    delta = timestamp - sleeptime[thread_id]
    totaltime[pid, execname, user stack, kernel stack] += delta
    sleeptime[thread_id] = 0
```

两个细节值得单独记：**栈只在开头或结尾测一次就够**（"Application stack traces don't change while off-CPU."）；**结算后是清零而不是删除**（`sleeptime[tid] = 0`），所以 `0` 兼任「没在睡」的哨兵值——本 demo 的断言 `首次切换不给被切上的线程记账` 就是验这一点。

### 2.2 状态过滤：把非自愿切换剔掉

| 状态 | 值 | 含义 |
| --- | --- | --- |
| `TASK_RUNNING` | 0 | **非自愿**上下文切换（时间片用完被踢下） |
| `TASK_INTERRUPTIBLE` | 1 | 可中断阻塞（锁、条件变量、网络等待） |
| `TASK_UNINTERRUPTIBLE` | 2 | 不可中断阻塞（多数存储 I/O） |

非自愿切换产生的 off-CPU 栈**毫无意义**（线程本来就在 CPU 密集路径上跑得好好的），`offcputime --state 2` 是常用解法。

### 2.3 线程池陷阱

带线程池的服务里，绝大部分阻塞时间落在「**等待工作**」的栈上（MySQL 的 `do_nanosleep` / `futex_wait_queue_me` / `read_events`），把真正影响客户延迟的请求同步路径完全淹没。原文给了两条解药：

1. 折叠格式每行一个栈，直接 `grep do_command`（后处理）；
2. 插桩应用请求边界，只记录发生在请求期间的 off-CPU 时间（更高效，但需要应用知识）。

本 demo 用原文那组 MySQL 栈量化了这一点：**等待工作占 95%，请求同步路径只占 5%**。

### 2.4 唤醒者视角（off-wake）

很多 off-CPU 栈只显示**阻塞路径**，不显示**为什么**被阻塞——答案在发起 wakeup 的那个线程里。于是：

- `wakeuptime`：度量唤醒栈；
- `offwaketime`：把唤醒栈与 off-CPU 栈关联，输出折叠格式；
- 宽度仍然是 **off-CPU 时间（微秒）**，键变成二元组。

本 demo 的断言直接证明了它的信息量：`by_stack` 只有 2 个塔，`off_wake` 有 3 个——同一条 `app;read` 阻塞栈被 `net_rx_action` 与 `blk_complete` 两个不同的唤醒者拆开了。

### 2.5 使用纪律

- 调度器事件可能每秒数百万，**必须在内核内聚合**（eBPF），否则光 dump 到用户态就是 GB/min；
- 警惕**反馈回路**（tracer 追踪到自己引发的事件）；
- 从不熟的 tracer 开始，先只追踪 **0.1 秒**，逐步加长；
- 火焰图**左右顺序没有意义**，别拿来做"先后"推断。

## 3. 对比

| 方法 | 宽度含义 | 能回答 | 不能回答 |
| --- | --- | --- | --- |
| CPU 采样 | on-CPU 样本数 | 热代码路径在哪 | 阻塞时间 |
| Off-CPU 追踪 | off-CPU 时间 | 在哪儿阻塞、阻塞多久 | 谁唤醒的；调度延迟占比 |
| `runqlat` | 运行队列延迟 | 调度延迟分布 | 阻塞原因 |
| **Off-Wake** | off-CPU 时间 | 为什么阻塞 + 谁唤醒 | 唤醒链上游（需 chain graph） |

## 4. 环境与运行方式

```bash
cd 11-性能分析/01-系统级剖析/OffWake调度延迟归因
python offwake_check.py     # 29 条断言，全部实跑通过
go run offwake.go           # 需 Go 工具链（本机无，走人工审查 + 机械核查）
```

## 5. 关键代码

```python
def on_switch_finish(self, prev_tid, prev_state, cur_tid, cur_stack, ts):
    self.sleeptime[prev_tid] = ts          # ① prev 开始睡
    self.state[prev_tid] = prev_state
    start = self.sleeptime.get(cur_tid)
    if not start:                          # ② 0/不存在 ⇒ 不是阻塞后的唤醒
        return None
    sample = Sample(cur_tid, start, ts, self.state[cur_tid], cur_stack,
                    woken_at=self.wakets.pop(cur_tid, None))
    self.sleeptime[cur_tid] = 0            # 清零，不是删除
    return sample
```

## 6. 性能边界

- `sleeptime = 0` 既是合法起点（t=0）又是哨兵：真实 tracing 里用「不存在」判空更稳，本 demo 严格照抄伪代码，因此构造用例时必须避开 t=0 起点（自检里用 t=1）。
- 膨胀比依赖 `try_to_wake_up()` 的时间戳，事件丢失会让 `blocked` 虚高、`sched_latency` 虚低。
- off-wake 的键空间是**乘积级**的：唤醒者栈 × 阻塞栈，栈越多塔越多、SVG 越大，实际用时要先按总量截断。
- 状态过滤要放在**聚合之前**，否则 `--state 2` 的直方图会被其它状态的样本污染。

## 7. 注意事项与常见坑

1. **别把 off-CPU 时间当成"阻塞时间"**——它含调度延迟；先确认 CPU 没饱和。
2. **线程池服务必须做请求上下文过滤**，否则结论全是"等待工作"。
3. **`sleeptime` 清零而非删除**，这是复现伪代码行为的关键。
4. 火焰图**左右顺序无意义**。
5. 缺省 frame pointer（`-fomit-frame-pointer`）会让用户栈全是 `[unknown]`，先修栈回溯再看图。
6. 唤醒链超过一层要看 chain graph，off-wake 只到"直接唤醒者"这一层。

## 8. 参考资料（已读）

- [Brendan Gregg — Off-CPU Analysis](https://www.brendangregg.com/offcpuanalysis.html)——off-CPU 定义与"含调度延迟"、五种方法对比、`finish_task_switch()` 伪代码、TASK 状态与 `--state`、线程池陷阱与 MySQL 栈示例、时间膨胀、非自愿切换、wakeuptime/offwaketime 与 off-wake 组合图、开销与 0.1 秒起步建议、左右顺序无意义
- 同目录 [Off-CPU分析/](../Off-CPU分析/)（demo 096，单点插桩与 `--state=2`）、[runqlat与调度延迟/](../runqlat与调度延迟/)（demo 105，直方图与 wakeup→switch 配对）
