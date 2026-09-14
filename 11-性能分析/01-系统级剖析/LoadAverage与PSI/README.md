# LoadAverage 与 PSI

## 简介

两个最常被误读的系统指标，放在一起讲因为它们回答的是**相邻但不同**的问题：

- **loadavg（负载平均值）**：`uptime` 里那三个数。它是"**需求**"指标——有多少线程**在跑或等着跑**（Linux 还把 D 状态算进去）。它**不告诉你**是 CPU、磁盘还是锁在忙。
- **PSI（Pressure Stall Information）**：内核 4.20 引入的"**停顿**"指标——**有多少时间任务真的被卡住了**。它能区分"忙"（做有用功）与"卡"（thrashing），这是 loadavg 做不到的。

关键概念：

- **D 状态（`TASK_UNINTERRUPTIBLE`）**：`ps`/`top` 显示 `D`，通常是磁盘 I/O 或某些锁；**1993 年起被计入 loadavg**
- **指数衰减移动和**：1/5/15 分钟三个数**不是**算术平均，而是以 5 秒为周期的 EMA（`EXP_1=1884`/`EXP_5=2014`/`EXP_15=2037`，基数 2048）
- **some / full**：PSI 的两行——`some` 是"至少部分任务停顿"的时间占比，`full` 是"**所有**非 idle 任务同时停顿"的时间占比
- **运行队列延迟**：比 loadavg 更好的**饱和度**指标（任务就绪但等 CPU 的时间）

历史：loadavg 的计算方式来自 **TENEX**（1970 年代初，`SCHED.MAC` 里就有 `EXP` 表），1/5/15 分钟只是那条公式里的**常数**，不是真正的平均窗口。PSI 则由 Johannes Weiner 于 2018 年提交入内核（`CONFIG_PSI`）。

## 原理详解

### 1. loadavg 到底在数什么

内核每次更新时取：

```text
active = nr_running + nr_uninterruptible        # 可运行 + 不可中断睡眠(D 状态)
```

`active` 再以 5 秒为周期喂进三个指数衰减移动和。这与"CPU 负载"的差别就来自那个 `nr_uninterruptible`。

**为什么加上 D 状态**：1993 年 10 月的补丁（Linux 0.99 patchlevel 14）作者 Matthias Urlichs 写的理由是——原来只数"可运行"进程，于是**把快 swap 盘换成慢 swap 盘，负载平均值反而下降**，这与直觉相反；把等待不可中断 I/O 的进程也算进来，负载才与"系统的主观速度"一致。

**今天的副作用**：Linux 0.99.14 里只有 13 条代码路径会置 `TASK_UNINTERRUPTIBLE`；到 4.12 已有**近 400 条**，**包括一些锁原语**。所以 loadavg 偏高时，可能是 CPU、磁盘、也可能是锁——必须再查别的指标。

### 2. 那三个数不是平均值

```c
/* include/linux/sched/loadavg.h */
#define FIXED_1   (1<<11)        /* 2048,定点基数 */
#define EXP_1     1884           /* 1/exp(5sec/1min) 的定点表示 */
#define EXP_5     2014           /* 1/exp(5sec/5min) */
#define EXP_15    2037           /* 1/exp(5sec/15min) */
```

对应浮点衰减因子 `α = 1 - EXP/FIXED_1`：

| 常数 | 定点值 | α | 理论 `1 - e^(-5/T)` |
| --- | --- | --- | --- |
| `EXP_1` | 1884 | 0.080078 | 0.079956（T=60 s） |
| `EXP_5` | 2014 | 0.016602 | 0.016529（T=300 s） |
| `EXP_15` | 2037 | 0.005371 | 0.005540（T=900 s） |

**直接后果**：在一台空闲机器上启动一个 100% CPU 线程，"1 分钟平均"在 60 秒时只到约 **0.62**——因为 `1 - e^(-60/60) = 0.6321`，它拖着一截历史（1 分钟均值的半衰期约 41.6 s）。

`/proc/loadavg` 的五个字段：

```text
25.72 23.19 23.35 42/3411 43603
 ↑1min ↑5min ↑15min  ↑running/total  ↑最近分配的 PID
```

`uptime` / `top` 只显示前三列。

### 3. 为什么 loadavg 会误导

| 问题 | 说明 |
| --- | --- |
| 混合资源 | CPU + 磁盘 + 部分锁，**不能**除以 CPU 数来判断"是否饱和"（其他 OS 的 CPU 负载可以，Linux 不行） |
| 长周期平滑 | 至少是 1 分钟量级的平均，**掩盖变化** |
| 归因缺失 | 只说"需求涨了"，不说"哪个资源" |
| 无绝对阈值 | 作者管理过一台双 CPU 邮件服务器，白天负载 11–16（比率 5.5–8）也无人抱怨 |

**它唯一稳妥的用法是相对比较**：登录一台"性能差"的机器，若 1 分钟值**远低于** 15 分钟值，说明你**来晚了**，问题可能已经过去。

### 4. PSI：some / full 与自定义窗口

```text
# /proc/pressure/{cpu,memory,io}
some avg10=0.00 avg60=0.00 avg300=0.00 total=0
full avg10=0.00 avg60=0.00 avg300=0.00 total=0
```

- `some` 行 = **至少有一些**任务在该资源上停顿的时间占比
- `full` 行 = **所有非 idle 任务同时**停顿的时间占比；此时"**CPU 周期真的在浪费**"，长时间处于该状态即 **thrashing**
- **CPU full 在系统级未定义**，但自 5.13 起会报告出来（为向后兼容**填 0**）
- `avg10/60/300` 是内核自己的平滑窗口；`total` 是**累计停顿微秒数**——用它做增量，才能算**任意自定义窗口**的停顿占比，也才能发现不显著影响平均值的**瞬时尖峰**

### 5. PSI trigger：让内核主动叫醒你

```text
<some|full> <停顿量 us> <窗口 us>
```

例如 `echo "some 150000 1000000" > /proc/pressure/memory` = "任意 1 秒窗口内累计停顿超过 150 ms 就叫醒我"。写入后对同一个 fd 用 `poll()`/`select()`/`epoll()` 等待 `POLLPRI`。

内核约束（`docs.kernel.org/accounting/psi.html`）：

| 约束 | 值 |
| --- | --- |
| 窗口范围 | **500 ms ~ 10 s**（最小更新间隔 50 ms，最大 1 s） |
| 非特权用户 | 窗口必须是 **2 s 的整数倍**（防止过度占用） |
| 一个 fd 一个 trigger | 再写返回 **`EBUSY`**；多个 trigger 必须多次 `open()` |
| 激活策略 | 进入 stall 才激活，**至少保持一个窗口**；通知**每窗口最多一次** |
| 注销 | 关闭 fd 即注销 |
| 事件源消失 | poll 返回 **`POLLERR`**（例如所属 cgroup 被删） |

### 6. cgroup v2：把停顿归因到容器

挂载了 cgroup v2 后，每个 cgroup 目录下都有 `cpu.pressure` / `memory.pressure` / `io.pressure`，**格式与 `/proc/pressure/` 完全相同**，per-cgroup 的 monitor 用法也一样。这正是 PSI 相对 loadavg 的关键优势——**loadavg 只有系统级全局值，无法归因到单个容器**。

## 对比 / 选型

| | loadavg | PSI |
| --- | --- | --- |
| 回答 | 需求有多少（线程数） | 有多少时间真的被卡住 |
| 粒度 | 系统级全局 | 系统级 + per-cgroup |
| 能否区分忙/卡 | **不能** | **能**（full 行） |
| 归因到资源 | 部分（值高但不知哪个资源） | 明确（cpu/memory/io 三个文件） |
| 主动通知 | 无 | **有**（trigger + poll） |
| 引入版本 | 0.99.14（1993） | 4.20（2018，`CONFIG_PSI`） |

更好的替代/补充指标：**运行队列延迟**（`runqlat`、`/proc/PID/schedstat`、`perf sched`）、**队列长度**（`vmstat` 的 `r` 列、`runqlen`）、利用率（`mpstat -P ALL 1`、`pidstat 1`）。

## 环境准备

- 操作系统：Linux；PSI 需要 **`CONFIG_PSI=y`** 且未被 `psi=0` 引导参数关闭；cgroup 部分需要挂载 cgroup v2
- 语言：Python 3.8+ / Go 1.21+ / C（GCC）
- **Python 与 Go 部分完全离线可跑**

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -pedantic psi_monitor.c -o psi_monitor
./psi_monitor            # 读 /proc/pressure/* + 注册 trigger 并 poll 3 秒
./psi_monitor 500000     # 自定义阈值(us)
```

### Python

```bash
python3 loadavg_calc.py   # 复刻内核 calc_load:定点 EMA、.62 现象、D 状态贡献
```

### Go

```bash
go run .                  # 解析 some/full、trigger 校验规则、用 total 增量做窗口分析
```

## 关键代码片段

**loadavg 的定点递推**——`load` 与 `active` 都是定点数（×2048），`+ FIXED_1 - 1` 是向上取整：

```c
static inline unsigned long calc_load(unsigned long load, unsigned long exp,
                                      unsigned long active)
{
    unsigned long newload = load * exp + active * (FIXED_1 - exp);
    if (active >= load)                 /* 向上取整,避免长期低估 */
        newload += FIXED_1 - 1;
    return newload / FIXED_1;
}
```

**用 `total` 增量做自定义窗口分析**（比读 avg 线更能抓到尖峰）：

```python
d_some = cur.total - prev.total          # 微秒
ratio  = 100.0 * d_some / (window_ms * 1000)
```

## 性能与边界

- loadavg 更新周期 **5 秒**，且是 EMA——**不可能**用它看秒级变化
- PSI 的 stall 判定由内核在调度/回收/IO 路径上打点，**开销很低**（`CONFIG_PSI` 可关）
- PSI 的 `avg` 窗口固定为 10/60/300 秒，比 loadavg 的 1/5/15 分钟**更短、更灵敏**
- 同一 fd 只承受一个 trigger（`EBUSY`）；不做 `poll` 的 trigger 只是白注册
- 非特权用户窗口必须 2 s 倍数，这是**硬限制**，不是建议
- `/proc/pressure/*` 不存在时：内核未开 PSI 或用 `psi=0` 启动

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| loadavg 很高但 CPU 空闲 | D 状态任务（磁盘 I/O / 锁）被计入 | 看 PSI 的 `io.pressure` + `vmstat` 的 `b` 列 + Off-CPU 分析 |
| 以为 loadavg 是"1 分钟内的平均" | 实际是 EMA，拖着历史 | 记住 `1 - e^(-t/T)` 的形状，别用它做短窗口判断 |
| 除以 CPU 数判断"超载" | Linux loadavg 混合多资源 | 只看趋势与自身历史比较 |
| CPU `full` 行全是 0 | 系统级 CPU full **本就没有定义**（5.13 起填 0） | 看 `cpu.pressure` 的 **some** 行 |
| 写 trigger 返回 `EINVAL` | 窗口越界，或非特权用户给了非 2 s 倍数窗口 | 用 1 s（特权）或 2 s/4 s（非特权） |
| 写第二次 trigger 返回 `EBUSY` | 同一 fd 只能有一个 trigger | 每个 trigger 单独 `open()` |
| poll 一直不返回 | 未触发（阈值没到）——这是结论而非错误 | 给 `poll` 加超时；或调低阈值验证链路 |
| 容器里读不到压力 | 用错了系统级路径 | 读对应 cgroup 目录下的 `*.pressure` |

## 参考资料（实际阅读过的权威来源）

- [Linux Load Averages: Solving the Mystery — Brendan Gregg](https://www.brendangregg.com/blog/2017-08-08/linux-load-averages.html) — 1993 年补丁与其作者解释、`EXP_1/5/15` 与 `FIXED_1` 定点定义与推导、TENEX 溯源、60 秒只到 0.62 的实测、`/proc/loadavg` 五字段、loadavg 为何误导、更好的替代指标
- [PSI - Pressure Stall Information — Linux Kernel Documentation](https://docs.kernel.org/accounting/psi.html) — some/full 定义、`/proc/pressure/{cpu,memory,io}` 格式、CPU full 系统级未定义（5.13 起填 0）、trigger 格式与 500 ms~10 s 窗口、非特权 2 s 倍数限制、`EBUSY`/`POLLPRI`/`POLLERR`、cgroup2 接口
- [Ftrace — Linux Kernel Documentation](https://docs.kernel.org/trace/ftrace.html) — 用 `wakeup` tracer 测**唤醒延迟**（与运行队列延迟互补的官方入口）
