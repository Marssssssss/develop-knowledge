# 432 CPU 利用率口径与 IPC 归因

> `%CPU` 不是「处理器有多忙」，而是**「没有在跑 idle 线程的时间」**。现代 CPU 远快于主存，等待内存的周期被算进了 `%CPU`——所以 90% 的 `%CPU` 里，很可能绝大部分在等 DRAM，压根不是处理器在算。判别口径是 **IPC（instructions per cycle）**。

## 1. 简介

本 demo 把 Brendan Gregg《CPU Utilization is Wrong》(2017-05-09) 确立的口径做成可计算函数：

- 从 `/proc/stat` 两次采样还原 `us/sy/ni/id/wa/hi/si/st` 八态占比；
- 给出「工具口径 busy」与「真正跑 idle 线程」两套口径，并量化它们的**重叠 = iowait**；
- 由 `cycles` / `instructions` 算 IPC，按 1.0 分界线定性（内存停顿 vs 指令受限）；
- 把 non-idle 周期拆成**退休周期 %INS** 与**停顿周期 %STL**（作者呼吁监控产品展示的正是这对指标）；
- 复现四类「%CPU 说谎」：自旋锁、平均值掩盖突发、变主频、超线程把可用周期记成已用。

## 2. 原理详解

### 2.1 `%CPU` 的真实定义：non-idle time

内核在**上下文切换**处记账：一个非 idle 线程开始运行、100 毫秒后停止，内核就把这 100 毫秒整段记作「utilized」。这个指标和时间片系统一样老（Apollo 登月舱制导计算机的 idle 线程叫 `DUMMY JOB`）。

问题在于：处理器厂商把时钟频率堆得比 DRAM 访问延迟下降快得多（"CPU DRAM gap"），2005 年 3 GHz 之后转向多核 / 超线程 / 多 socket，全部在给内存子系统加压。于是你看到的高 `%CPU`，瓶颈常常是那几排 DRAM，而不是散热片下面的那颗芯片。

### 2.2 IPC：唯一能拆穿它的派生指标

```
IPC = instructions / cycles
```

- **IPC < 1.0 ⇒ 大概率内存停顿（memory bound）**：软件侧减少内存 I/O、改善缓存局部性（NUMA 上尤其明显）；硬件侧上更大的缓存、更快的内存与互连。
- **IPC > 1.0 ⇒ 大概率指令受限（instruction bound）**：减少代码执行量、消除无用工作、加缓存，配 CPU 火焰图；硬件侧提高主频、加核。

> 作者明说：1.0 这条线是**他拍的**（"I made it up, based on my prior work with PMCs"）。正确的自校准做法是写两个哑负载——一个 CPU 密集、一个内存密集——各测 IPC 取中点。

**峰值换算比绝对值更重要**。原文那台机器 IPC = 0.78，听起来「78% 忙」，但它是 **4-wide**（每周期最多退休 4 条指令，即峰值 IPC = 4.0），所以实际只跑到峰值的 **19.5%**。更新的 Intel 可能到 5-wide。

### 2.3 %INS / %STL：作者建议监控产品给出的拆分

满速时 `instructions` 条指令本该只用 `instructions / width` 个周期：

```
retired = instructions / width
stalled = cycles − retired
%INS    = retired / cycles        %STL = stalled / cycles
```

原文实测 `cycles = 1,433,972,173,374`、`instructions = 1,118,336,816,068`，4-wide 下：

| 指标 | 值 |
| --- | --- |
| IPC | 0.7799（原文四舍五入 0.78） |
| 峰值占比 | 19.50% |
| retired | 279,584,204,017 |
| stalled | 1,154,387,969,357 |
| %STL | **80.50%** |

也就是说那 10 秒里八成的「CPU 时间」是停顿。

### 2.4 顺带可复现的两个量

- `cycles / task-clock` = 平均主频 = **2.236 GHz**（与 `perf stat` 输出列一致）；
- `task-clock / 墙钟` = **64.116 CPUs utilized**（10 秒内平均占用 64 个 CPU）。

### 2.5 另外四类误导

| 类型 | 现象 | 判据 |
| --- | --- | --- |
| **自旋锁** | `%CPU` 高、IPC 也高，但业务吞吐为 0 | `busy ≥ 90% && IPC ≥ 2.0 && 无前进` |
| **平均值掩盖突发** | 一分钟均值 80%，实际是 48 秒 100% + 12 秒 0% | 看峰值与「达到峰值样本占比」 |
| **变主频** | Turboboost / speedstep / 温度降频，`cycles` 相同但墙钟不同 | 比较 `cycles/task-clock` 与标称主频 |
| **超线程** | 停顿周期本可被另一线程用掉，却被记成已用 | 兄弟线程停顿占比决定可偷余量（保守取最小值） |

## 3. 对比：`%CPU` 与几个易混口径

| 口径 | 定义 | 说明 |
| --- | --- | --- |
| `busy`（工具口径） | `100 − idle` | **iowait 被算作忙** |
| 真正跑 idle 线程 | `idle + iowait` | iowait 本质是「空闲但有未完成 I/O」 |
| 两者之和 | `100 + iowait` | 不是 100——重叠的那一块就是 iowait |
| `%STL` | 停顿周期 / cycles | 内存停顿，**与 iowait 无关** |

作者在 Update 里专门澄清：**"I'm not talking about iowait at all (that's disk I/O)"**——iowait 是磁盘 I/O 口径，不要拿它当 `%STL` 用。

## 4. 环境与运行方式

```bash
cd 11-性能分析/01-系统级剖析/CPU利用率口径与IPC
python cpu_util_check.py     # 29 条断言，全部实跑通过
go run cpu_util.go           # 需 Go 工具链（本机无，走人工审查 + 机械核查）
```

无依赖，纯标准库。

## 5. 关键代码

```python
def busy_pct(pcts):        return 100.0 - pcts["idle"]          # 工具口径，吞掉 iowait
def idle_thread_pct(pcts): return pcts["idle"] + pcts["iowait"] # 真正空闲线程
def verdict(v):            return "memory" if v < 1.0 else "instruction"
def stall_split(cycles, instructions, width=4.0):
    retired = instructions / width
    stalled = max(cycles - retired, 0.0)
    return {"pct_ins": retired * 100 / cycles, "pct_stl": stalled * 100 / cycles}
```

## 6. 性能边界

- `/proc/stat` 是**累计值**，必须两次采样求差；间隔太短（< 100 ms）时 jiffies 粒度（通常 10 ms）会带来整数量化误差。
- `stall_split` 的 `width` 是**假设峰值**：真实机器会随端口占用、超线程共享而低于标称 width，算出的 `%STL` 是乐观下界。
- 云环境（如 EC2 Xen 时代）未必开放 PMC，`instructions`/`cycles` 可能读不到，此时 IPC 无从谈起。
- `is_spin_lock` 是**形态判据**，不是证明：需要业务吞吐（`progressed`）这个外部信号，纯计数器推不出来。

## 7. 注意事项与常见坑

1. **别把 `busy` 当成「处理器在算」**——它连 iowait 都吞，更别说内存停顿。
2. **别拿 iowait 当内存停顿指标**——它是磁盘 I/O（作者原话）。
3. **IPC 是比值不是效率**：不同架构 width 不同，跨机型比 IPC 没有意义，要跟各自的峰值比。
4. **1.0 分界线是自校准的起点而非真理**，换机器换运行时要重测。
5. 本 demo 的 `total()` 只配平 8 个经典字段，`guest`/`guest_nice` 只解析不计入——因为官方归属口径未在本文涉及，不写无据断言。
6. 平均利用率做告警阈值会**漏掉突发**：务必同时看峰值与突发样本占比。

## 8. 参考资料（已读）

- [Brendan Gregg — CPU Utilization is Wrong (2017-05-09)](https://www.brendangregg.com/blog/2017-05-09/cpu-utilization-is-wrong.html)——non-idle time 定义、IPC 1.0 分界与「我拍的」说明、4-wide/19.5%、%INS/%STL 建议、五类误导、iowait 澄清、%CPU→%CYC 改名建议、`tiptop` 逐进程 IPC 输出
- [Brendan Gregg — The PMCs of EC2: Measuring IPC (2017-05-04)](https://www.brendangregg.com/blog/2017-05-04/the-pmcs-of-ec2.html)（由上文引用，云上 PMC 可用性）
- 同目录 [PMU硬件计数器/](../PMU硬件计数器/)（demo 099，计数器编码与组内缩放）、[perf_events剖析/](../perf_events剖析/)（demo 094，IPC/SCPI 派生）
