# USE 方法检查清单的程序化落地

## 简介

**USE 方法**（Utilization / Saturation / Errors）是 Brendan Gregg 提出的系统性能检查方法论，一句话概括：

> **对每一种资源，检查利用率、饱和度与错误。**
> （For every resource, check utilization, saturation, and errors.）

它的独到之处在于**先提出问题、再寻找答案**：遍历系统资源构造出「该问什么」的完整清单，然后才去找工具回答。官方明确把它的价值概括为——把**未知的未知**（unknown-unknowns）变成**已知的未知**（known-unknowns）：即使某些指标当前拿不到，你也知道自己**没检查什么**。

**关键概念（官方定义）**

- **资源（resource）**：服务器的物理功能组件（CPU、磁盘、总线……），也包含「软件资源的限制」（如连接数上限）
- **利用率（utilization）**：资源忙于服务工作的时间平均值，以百分比表示。**另一种定义**是「被使用的资源占比」——此时 100% 意味着无法再接受更多工作，与「忙」的定义不同
- **饱和度（saturation）**：资源已有额外工作但无法处理的程度，通常是**排队等待的工作量**（如 CPU 平均运行队列长度）
- **错误（errors）**：错误事件的计数（标量）

## 原理详解

### 1. 资源清单（官方原文顺序）

| 资源 | 说明 |
| --- | --- |
| CPUs | 插槽 / 核心 / 硬件线程 |
| Memory | 容量 |
| Network interfaces | — |
| Storage devices | **I/O 与容量两种身份** |
| Controllers | 存储控制器、网卡 |
| Interconnects | CPU 互连、内存互连、I/O 互连 |

官方特别指出两件事：

1. **存储设备同时是两种资源**：「服务请求资源（I/O）」与「容量资源（population）」，两者都可能成为瓶颈——所以同一个 `sda` 会出现两行，**口径完全不同**（一个是忙时间占比，一个是占用比例）。
2. **硬件缓存被有意省略**：USE 只适用于「高利用率或饱和会导致性能退化」的资源；而缓存在高利用率下**反而改善**性能。缓存命中率应在 USE 之后再看。
3. 互连常被忽略：通常不是瓶颈，**但一旦是，往往难以处理**（也许只能升级主板或降低负载）。把每条总线的最大带宽标上去，可以**在做任何测量之前**就识别出系统性瓶颈。

### 2. 构建检查清单的 6 步（官方）

1. 列出资源（或用功能框图遍历）
2. 对每种资源考虑三种指标类型
3. 为每个指标标注**如何获取**（哪个工具、读哪个统计量）
4. 让**无法获取的指标保持 "?"**
5. 重复所有组合，最终得到约 30 个指标的清单
6. 得到一份「易于快速执行、且尽可能完整」的检查清单

本 demo 把第 4 步落成硬约束：`Cell.value is None → verdict = "?"`，并算出**覆盖率**，而不是悄悄跳过。

### 3. 判定口径

官方给的解读规则是定性的：

- **利用率**：100% 通常是瓶颈信号；**高利用率（如超过 70%）可能开始成为问题**，原因有二——① 数秒/数分钟窗口上的 70% 会掩盖短时的 100% 峰值；② 某些资源（如硬盘）操作期间不可被抢占，超过 70% 后排队延迟会变得更频繁、更明显（CPU 几乎可在任意时刻被抢占，因此不在此列）
- **饱和度**：任何非零都可能是问题
- **错误**：非零的错误计数器值得调查，尤其是性能不佳时它还在增长
- **顺序技巧**：错误可以在利用率与饱和度**之前**先查——通常更快、更易解读

> ⚠️ **口径差异**：官方只给上述**定性**说法，没有给数值分级表。本 demo 的 `UTIL_CAUTION = 70 / UTIL_CRIT = 100` 是照「超过 70%」这句话自定的映射，**不是官方标准**；`avgqu-sz > 1` 这个判据则是官方 checklist 里写明的（"avgqu-sz > 1, or high await"）。

### 4. 与 Tools Method 的对比

| 维度 | USE 方法 | Tools Method |
| --- | --- | --- |
| 起点 | 资源（先有问题清单） | 可用工具 |
| 步骤 | 列资源 → 三指标 → 标注获取方式 → 补 "?" | 列工具 → 列指标 → 列解读规则 |
| 缺陷 | 只能发现**瓶颈与错误**两类问题 | 完全依赖已知工具，**视图不完整且用户不知道不完整** |
| 结论 | 遍历资源，视图更完整且能记录未知区域 | 容易漏掉没工具的维度 |

## 环境准备

- 操作系统：本 demo 的 Python/Go 版**跨平台可跑**（采集层用「命名计数器 + 两次采样求差」的 `Stats` 模型，不读 `/proc`）；C 版读真实 `/proc` 与 `statvfs()`，仅在 Linux 上有意义
- 语言：Python 3.10+ / Go 1.21+ / gcc
- 依赖：无

## 运行方式

### Python（38 项自检 + 矩阵报告）

```bash
python use_check.py
```

### Go

```bash
go run .
```

### C（解析器自检 + 真机 /proc 探测）

```bash
gcc -O2 -Wall -Wextra -pedantic use_collect.c -o use_collect
./use_collect --selftest    # 用内嵌样本自检全部解析器
./use_collect               # 在 Linux 上读真实 /proc
```

## 关键代码片段

CPU 利用率的官方口径——**分母含 idle/iowait，分子不含**：

```python
def cpu_utilization(s0, s1) -> float:
    """官方: system-wide 'us'+'sy'+'st',即除 %idle 与 %iowait 外全部字段求和。"""
    a, b = s0["stat"], s1["stat"]
    busy = sum(b[k] - a[k] for k in ("user", "nice", "system", "irq", "softirq", "steal"))
    total = sum(b[k] - a[k] for k in b)
    return 100.0 * busy / total if total else 0.0
```

饱和度的判据与「拿不到就记 ?」：

```python
def judge_sat(q):  return "?" if q is None else ("OK" if q == 0 else "QUEUED")

def add(self, resource, kind, metric, source, value=None, judge=judge_util):
    self.cells.append(Cell(resource, kind, metric, source, value, judge(value), note))
```

存储设备的**双重身份**用两种口径分别判定：

```python
# 请求资源(I/O): 忙时间占比          容量资源(population): 占用比例
dev_utilization(s0, s1)      # Δbusy_ms / Δt → 94%
storage_capacity_utilization(s1)  # used_kb / size_kb → 99.2%
```

## 性能与边界

- **检查顺序的开销**：官方估计完整读一遍约 30 个组合「可能非常耗时」，尤其涉及总线/互连指标。实践建议先查子集：**CPUs、内存容量、存储容量、存储设备 I/O、网络接口**。
- **采样区间决定灵敏度**：所有指标都是「两次采样求差」，区间越短越能抓尖峰，但计数器的分辨率与抖动会放大误差；本 demo 固定 10 s。
- **本 demo 的边界**：采集层用命名计数器模型，**不解析真实 `/proc` 列序**（`/proc/diskstats` 的列数随内核版本增长，内核 iostats 文档 v5.3 时仍是 11 列，新版追加了 discard/flush 字段）。换真机时只需把 C 版已被自检覆盖的解析器接到 `Stats` 上。
- **覆盖率的含义**：本 demo 实测 37 格中 14 格有值（37.8%）——互连类（需 CPC/`perf`）、锁指标（需 `CONFIG_LOCK_STATS`）拿不到。这个数字本身就是交付物。

## 注意事项与常见坑

- **不要把 `loadavg` 当 CPU 利用率**：官方 checklist 明说 `uptime` 的 load average **没有**列入 CPU 指标，因为 Linux 的 load average **包含不可中断睡眠（D 状态，通常是 I/O）**。用它当 CPU「忙」会把 IO 等待算成 CPU 压力（详见 `LoadAverage与PSI/`）。
- **`iowait` 不算 CPU 忙**：官方口径是「除 `%idle` 和 `%iowait` 外全部字段求和」。把 `iowait` 算进忙时间，本 demo 的样本会从 30.24% 变成 34.25%——量不大但方向性错误。
- **`steal` 必须算进去**：虚拟化环境里被宿主机抢走的 CPU 时间，对容器来说确实是「不可用」，漏掉它会系统性低估 CPU 压力。
- **存储的两种口径不能混用**：`%util` 是时间维度的「忙」，`df` 是容量维度的「占用」。同一台机器可以 `%util` 只有 10% 而容量 99%（本 demo 的样本就是），此时瓶颈是**容量**不是 IOPS。
- **`avgqu-sz > 1` 才叫排队**：`%util` 高不等于饱和，队列长度才是饱和的直接证据；官方把 `await` 高也列为饱和信号。
- **丢弃包同时是饱和与错误**：官方脚注明确 dropped 两处都算（可能由两类事件引起），别只记一遍。
- **无效指标要留 "?"，不要填 0**：把「拿不到」写成 0 会把 known-unknown 又变回 unknown-unknown，检查清单的全部价值就没了。

## 参考资料（实际阅读过的权威来源）

- [The USE Method — Brendan Gregg](https://www.brendangregg.com/usemethod.html) — U/S/E 三个定义（含「利用率」的两种定义）、资源清单原文、存储设备双身份原文、缓存被省略的理由、构建清单 6 步、「约 30 个指标」、70% 与 100% 的解读规则、Tools Method 对比、"unknown-unknowns → known-unknowns"
- [USE Method: Linux Performance Checklist — Brendan Gregg](https://www.brendangregg.com/USEmethod/use-linux.html) — 本次实现的「如何获取」列逐格来源（`vmstat 1` 的 `us+sy+st`、`iostat -xz 1` 的 `%util`、`iostat -xnz 1` 的 `avgqu-sz > 1`、`/proc/PID/schedstat` 第 2 字段 `sched_info.run_delay`、`free -m`、`sar -n DEV 1`、`df -h` 等）、软件资源清单、「load average 未列入 CPU 指标」原文、两处脚注（dropped 兼具饱和与错误、`vmstat` 的 `r` 字段误导性说明）
- [I/O statistics fields — Linux Kernel Documentation](https://www.kernel.org/doc/html/latest/admin-guide/iostats.html) — `/proc/diskstats` 的字段口径来源；本页仅通过检索确认其存在与「早期为 11 列、前后有 major/minor」的表述，**未逐字精读**，故 C 版解析器用自检覆盖字段下标（3/7/12/13）并在注释里标注需按内核版本复核
- 配套书：《Systems Performance》第 2 版第 2 章（Methodologies）
