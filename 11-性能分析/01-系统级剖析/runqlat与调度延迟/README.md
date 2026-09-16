# runqlat 与调度延迟 — 运行队列延迟观测

观测「线程从变为可运行,到真正开始在 CPU 上运行」之间的时间。利用率告诉你 CPU「忙」,
运行队列延迟告诉你「忙成什么样、排队排了多久」。

三种语言实现同一组自检:**Python / Go / C**(C 拆为 `runqlat_impl.h` 文本包含,保持单文件 ≤300 行)。

## 简介

- **测什么**:run queue latency = `sched_wakeup`(变为可运行)到 `sched_switch` 里该线程
  真正被选中上 CPU 之间的时间。
- **为什么名字过时**:早年 O(1) 调度器用真正的运行队列(数组),现在 CFS 用红黑树、
  EEVDF 用虚拟时间,已经没「队列」了,但 `runqlat` 这个名字沿用至今。
- **为什么重要**:CPU 使用率 90% 和 98% 在监控曲线上只差 8 个百分点,但体感可能是几倍差距 ——
  排队论的解释见下文。
- **本 demo 做什么**:不采集真实数据,而是复刻并验证**两件事的口径** ——(1) 官方直方图的
  输出格式与分桶规则;(2) `/proc/schedstat` 的字段语义与「必须两次采样求差」。所有直方图
  数据是官方样例的**形状重放**,不是采集数据。

## 原理详解

### 1. 为什么用 2 的幂直方图

延迟横跨 5 个数量级(0.1 µs ~ 100 ms),线性分桶会把样本全挤进第一格。官方 runqlat 的桶
边界是 2 的幂,且**第 0 桶是 `[0, 1]` 而不是 `[1, 2]`**(唯一特例,其余是 `[2^i, 2^(i+1))`):

```
     usecs               : count     distribution
         0 -> 1          : 233      |***********                             |
         2 -> 3          : 742      |************************************    |
         4 -> 7          : 203      |**********                              |
         8 -> 15         : 173      |********                                |
        16 -> 31         : 24       |*                                       |
         ...
       16384 -> 32767    : 809      |****************************************|
       32768 -> 65535    : 64       |***                                     |
```

前面一片 0~15 µs(唤醒后几乎立刻上 CPU),后面 16~32 ms 一整块(CPU 饱和,线程真排队
等了十几毫秒)。**双峰是最有价值的信号**:均值会把这两拨性质完全不同的延迟混成一个
没有意义的数。

### 2. wakeup / switch 配对的状态机

```
   sched_wakeup   →  @start[tid] = nsecs          (记为「变可运行」的时刻)
   sched_switch   →  该线程作为 next 被选中        (记为「真的开始跑」)
                     延迟 = now - @start[tid]     (作为 prev 离开 CPU 则不计)
```

用 `tid` 作 map 键。关键点:`sched_switch` 里的 **next** 才是「开始运行」的线程,
**prev** 是「离开 CPU」的线程 —— 取错就会把「运行了多久」当成「等了多久」,
这正是 `runqlat` 与 `cpudist` 的分界。

### 3. /proc/schedstat:9 个字段 + 必须求差

| # | 字段 | 含义 |
|---|---|---|
| 1 | `yld_count` | `sched_yield()` 次数 |
| 2 | `array_exp` | O(1) 调度器遗留,**恒为 0** |
| 3 | `sched_count` | 本 CPU 上发生的调度次数 |
| 4 | `sched_goidle` | 调度到 idle 线程的次数 |
| 5 | `ttwu_count` | try_to_wake_up 次数 |
| 6 | `ttwu_local` | 其中本地(同 CPU)唤醒 |
| 7 | `rq_cpu_time` | 本 CPU 上任务累计运行时间(ns) |
| 8 | `run_delay` | 任务在运行队列上累计等待时间(ns) |
| 9 | `pcount` | 本 CPU 上任务被迁移的次数 |

派生指标就是 **`Δrun_delay / Δrq_cpu_time`**。注意**必须两次采样求差**:两者都是只增不减的
累加器,单点读值再相除是拿「开机以来的总等待」除「总运行」,得到的是完全另一个数(本 demo
的断言会把差别打出来:42.86% vs 537.02%)。官方 `perf sched stats` 的示例形态是 `48.70% wait`,
含义是 CPU 上任务的等待时间接近运行时间的一半 —— 已经很严重了。

### 4. /proc/&lt;pid&gt;/schedstat:进程级三字段

```
<time on cpu (ns)> <time waiting on runqueue (ns)> <number of timeslices>
```

进程级排查用它:某进程 `98 ms` 等待 vs `1234 ms` 运行,说明它自己的唤醒-运行路径没大问题;
差值悬殊就说明它一直在跟别人抢 CPU。

### 5. 为什么饱和度是非线性的

M/M/1 平均排队等待时间(以服务时间为单位)`Wq = ρ / (1 - ρ)`:

| ρ(饱和度) | Wq(等待/服务时间) |
|---|---|
| 0.50 | 1.0 |
| 0.90 | 9.0 |
| 0.98 | 49.0(饱和度只涨 8.9%,等待涨 5.4 倍) |
| →1.00 | 发散 |

这就是「运行队列长 1 和长 10 完全不是一回事」的数学来源,也是为什么**看饱和度必须同时看
延迟** —— 曲线在接近 100% 前是平的,之后陡然竖起来,而直立段上的两个点在外面看起来几乎一样。

## 对比

| 维度 | `runqlat`(bcc/bpftrace) | `/proc/schedstat` 求差 | `cpudist` |
|---|---|---|---|
| 观测对象 | 等待时间(可运行 → 上 CPU) | 同上,但只到 CPU 粒度 | **on-CPU 时间**(上 CPU → 离开) |
| 精度 | 每事件,带直方图 | 累加器,只能出比值 | 每事件,带直方图 |
| 开销 | 需 eBPF / 内核支持 | 读文件,开销≈0 | 同 runqlat |
| 缺什么 | 需 root/权限 | 无分布,看不出双峰 | 不回答排队问题 |

两者互补:比值负责「宏观趋势 + 低成本常驻」,直方图负责「微观形态 + 定位尖峰」。

## 环境准备

- Python 3.9+ / Go 1.21+ / 任意 C99 编译器 —— 自检**不需要** root、eBPF,也不需要 Linux。
- 若要 `--real` 读真实 `/proc/schedstat`,需 Linux。
- `kernel.sched_schedstats` 默认为 `0` 时,`run_delay` 字段**存在但恒为 0** —— 不是「没人等」,
  是「没统计」。开启:`sysctl -w kernel.sched_schedstats=1`。

## 运行方式

```bash
# Python(38 项自检,含直方图打印)
cd python && python runqlat_check.py

# Go
cd go && go run runqlat_check.go

# C(可加 --real 在 Linux 上读本机 /proc/schedstat)
cd c && cc -O2 -o runqlat_check runqlat_check.c -lm && ./runqlat_check --real
```

## 关键代码片段

分桶(注意第 0 桶特例)与列宽:

```c
static int bucket_index(double usec) {
    int i = 0;
    if (usec < 2) return 0;                       /* 第 0 桶是 [0,1] 而不是 [1,2] */
    while ((1LL << (i + 1)) <= (long long)usec) i++;
    return i;
}
/* 一行 = "%10s -> %-11s: %-9d|%-40s|" —— 逐字符对齐官方样例 */
snprintf(out, n, "%10lld -> %-11lld: %-9d|%-*s|",
         bucket_low(i), bucket_high(i), c, BAR_W, stars);
```

配对时对「没有 wakeup 的首次上 CPU」的处理:

```python
if tid in pending:
    lat_us = (ev[1] - pending.pop(tid)) / 1000.0
    paired += 1
else:
    orphan += 1        # 不能算成 now - 0,否则造出巨值污染整个直方图
```

统计开关的语义保护:

```python
if a["run_delay"] == 0 and b["run_delay"] == 0:
    return None        # 「未统计」而不是「0%」—— 两者结论完全相反
```

## 性能与边界

- 自检是纯计算,毫秒级;直方图打印柱区固定 40 字符(与官方一致)。
- 真实采集下 `tracepoint:sched:sched_wakeup` 在极高频唤醒场景(如网络小包)可能每秒数万次,
  是全系统最重的追踪点之一;生产上要采样或只挂单进程过滤。
- `/proc/schedstat` 的 `domain<N>` 行有 **45 个字段**(调度域拓扑),本 demo 只解析 `cpu<N>` 行。
- 直方图桶内均值不可反推,要真均值请从原始值或 `lhist` 算。

## 注意事项与常见坑

1. **`@start[tid]` 必须配 `/@start[tid]/` 谓词**。否则从未被 `sched_wakeup` 记过的线程进入
   `sched_switch` 时会算成 `now - 0`,造出几十亿微秒的样本,把直方图标尺彻底带偏。
2. **第 0 桶是 `[0, 1]`**,不是 `[1, 2]`。按 `[2^i, 2^(i+1))` 硬套会让标签整体错一位,
   而 0 µs 恰恰是最常见的值。
3. **`/proc/schedstat` 必须两次采样求差**,且累加器单点读值的比值可以差一个数量级。
4. **`run_delay = 0` 有三种可能**:(a) 真没人等;(b) `kernel.sched_schedstats = 0`;
   (c) 采样窗口内没有任务被唤醒过。必须两次采样都判,才能区分,不能直接把 0 当结论。
5. **`array_exp` 恒为 0 是正常的**,别当成解析错了 —— 它是 O(1) 调度器时代的字段。
6. **阈值选多少决定你看到几个峰**。本 demo 里 5% 阈值把 `32768->65535` 那格(2.9%)排除在慢峰
   之外,降到 2% 就恢复 —— 判读双峰时阈值是**分析参数**,要写进结论。
7. **别把 `runqlat` 和 `cpudist` 的数据混着解释**:前者是「等待」,后者是「占用」,同一个
   `sched_switch` 事件被两边各取走一半信息。
8. **抖动**:微秒级延迟受频率调节、SMT 兄弟线程、中断影响,容器里跑出来的绝对值通常比裸机
   高一档;对比时务必用同一环境。

## 参考资料

实际读取的权威来源:

- Brendan Gregg — *runqlat: Run queue latency*:定义(「从变为可运行到真正开始运行」)、
  官方直方图样例与列宽。<https://www.brendangregg.com/runqlat.html>
- Linux 内核文档 — *Scheduler Statistics*(`Documentation/scheduler/sched-stats.*`):
  `/proc/schedstat` 的 `cpu<N>` 9 字段、`domain<N>` 45 字段、`/proc/<pid>/schedstat` 三字段,
  以及 `perf sched stats` 的 48.70% 示例。
  <https://www.kernel.org/doc/html/latest/scheduler/sched-stats.html>
- Brendan Gregg — *Linux Performance*:runqlat / cpudist 在 sched 分类下的定位。
  <https://www.brendangregg.com/linuxperf.html>
- bpftrace — *Tutorial: One-Liners*:`@start[tid]` / `hist()` / `nsecs` 的官方写法,以及
  「必须检查 map 是否有值」的说明。
  <https://github.com/bpftrace/bpftrace/blob/master/docs/tutorial_one_liners.md>
