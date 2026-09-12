# 采样剖析原理(Sampling Profiler)

## 简介

采样剖析(Sampling Profiling)是定位 CPU 热点的低开销手段:以固定频率定时中断进程,每次中断抓取当前指令指针 / 整条调用栈,采样数按比例近似 CPU 时间占比。与之相对的是"追踪"(tracing)——记录每次函数进入/返回,开销大得多;火焰图作者 Brendan Gregg 正是为避开追踪的高开销才改用采样。

- **关键概念**
  - `ITIMER_PROF`:POSIX 间隔定时器,按进程消耗的 **用户+系统 CPU 时间** 递减,到期投递 `SIGPROF`——专为剖析设计,进程不在 CPU 上时不计时。
  - `ITIMER_VIRTUAL`:只按用户态 CPU 时间递减,到期投递 `SIGVTALRM`。
  - `ITIMER_REAL`:按墙钟时间递减,到期投递 `SIGALRM`;剖析较少用(睡眠也计)。
  - 采样偏差(sampling bias):频率与周期性活动"步调一致"会得出偏斜结果,故 Gregg 推荐用 99 Hz 而非 100 Hz 这类奇数频率。
  - 采样数 = 时间占比的统计估计:某函数栈出现在 N 个样本中,即约占总 CPU 时间的 N/总数(置信度随样本数上升)。

- **历史背景**:定时采样剖析由 4.2BSD 的 profil/gprof 一脉相承(man7 指出 setitimer 源自 4.2BSD);现代 Linux 生态中 perf_events(-F 99)、py-spy、Go runtime pprof 均基于"定时中断 + 栈回溯"这一模型。

## 原理详解

工作机制(对应本 demo 三种实现):

1. 用 `setitimer(ITIMER_PROF, ...)` 设置周期(如 10ms = 100Hz)。
2. 定时器按 **CPU 时间** 递减——进程被调度下 CPU 时暂停计时,保证样本只落在"真正执行"的代码上。
3. 到期投递 `SIGPROF`,信号处理函数被异步插入当前执行流。
4. 处理函数抓取当前栈:C 用 `backtrace()`;Python 用 `sys._current_frames()`(逐线程取帧);Go 模拟用 `runtime.Stack(buf, all=true)`。
5. 把栈折叠为单行(`main;foo;bar`),计数 +1,存入哈希表。
6. 结束时输出 folded 样本,配合火焰图(见同目录 `火焰图生成/` demo)可视化。

`setitimer` 关键结构(man7 getitimer(2)):

```c
struct itimerval {
    struct timeval it_interval; /* 周期间隔;两字段均 0 = 单次 */
    struct timeval it_value;    /* 距下次到期;两字段均 0 = 拆除定时器 */
};
```

| 定时器 | 递减时钟 | 到期信号 | 剖析适用 |
| --- | --- | --- | --- |
| `ITIMER_REAL` | 墙钟时间 | SIGALRM | 含睡眠的墙钟剖析 |
| `ITIMER_VIRTUAL` | 用户态 CPU | SIGVTALRM | 纯用户态热点 |
| `ITIMER_PROF` | 用户+系统 CPU | SIGPROF | **用户+内核全覆盖,标准选择** |

三种定时器每进程各只有一个;fork 出的子进程不继承;execve 后保留。POSIX.1-2008 已将 setitimer 标记 obsolete,推荐 `timer_create()`/`timer_settime()`(POSIX 定时器),但 ITIMER_PROF 语义仍是剖析器的经典模型。

Go 与 Python 的现实:Go runtime 的 CPU profile 底层同样由 OS 定时信号驱动采样栈;py-spy 则是外部进程按频率抓目标进程栈(不侵入)。本 demo Python 版用进程内 `signal.setitimer` + `_current_frames` 复现该模型。

## 对比 / 选型

| 维度 | 采样剖析 | 插桩追踪(instrumentation) |
| --- | --- | --- |
| 开销 | 低(每秒几十~几百次中断) | 高(每次函数调用都记录) |
| 得到信息 | 热点占比(统计) | 完整调用序列与次数 |
| 调用次数 | 不可知(只知占比) | 精确 |
| 适用 | 生产环境 CPU 热点 | 深度调试 |

## 环境准备

- Python ≥ 3.8(仅标准库);C:Linux + glibc(`-execinfo` 的 `backtrace`);Go ≥ 1.18
- 三份实现互相独立,均可单独运行

## 运行方式

### Python
```bash
python3 python/main.py            # 内置 CPU 负载,采样 3 秒,打印折叠样本
```

### C
```bash
gcc -O2 -Wall -Wextra -g c/main.c -o sampler && ./sampler
```

### Go
```bash
cd go && go run main.go
```

## 关键代码片段

Python 版核心(信号 + 双线程采样,对应原理第 3-5 步):

```python
def _on_sigprof(signum, frame):
    # 信号到达时抓取所有线程栈,折叠成 "main;work;spin" 单行计数
    stacks = sys._current_frames()
    for tid, f in stacks.items():
        names = []
        while f is not None:
            names.append(f.f_code.co_name)
            f = f.f_back
        counts[";".join(reversed(names))] += 1

signal.signal(signal.SIGPROF, _on_sigprof)
signal.setitimer(signal.ITIMER_PROF, INTERVAL, INTERVAL)  # 周期定时
```

C 版核心(`backtrace` 抓栈,注意要在信号安全上下文里做最少的事):

```c
static void on_sigprof(int sig) {
    void *frames[MAX_FRAMES];
    int n = backtrace(frames, MAX_FRAMES);   /* 只存地址,符号化延迟到事后 */
    ...
}
setitimer(ITIMER_PROF, &(struct itimerval){{0,10000},{0,10000}}, NULL);
```

## 性能与边界

- 统计误差:样本数 N 时,占比 p 的相对标准差 ≈ sqrt((1-p)/(N·p));99Hz 采样 10 秒仅 990 样本,1% 以下的热点不可信(Gregg 的 CPU Flame Graphs 页即以此说明为何要看"最宽的塔")。
- 采样中断有轻微开销,频率越高越准也越慢;99/997 Hz 是 perf 常用档位。
- 采样数可超过流逝时间:多线程并行时各线程同时被采样(Gregg 明确指出这一点)。
- C 的 `backtrace` 依赖帧指针/eh_frame;`-fomit-frame-pointer` 编译会导致栈不完整(与 perf -g 同坑)。

## 注意事项与常见坑

- **信号丢失**:同类信号同时只能 pending 一个(man7 BUGS:重负载下 ITIMER_REAL 的第二次到期会丢)——剖析用 ITIMER_PROF 且采样间隔不要太小。
- **处理函数重入**:SIGPROF 在处理函数执行中再次到达会丢失样本;Python 版处理函数必须短。
- **Python 的 GIL**:`_current_frames` 抓的是各线程帧,但被采样的负载若在 C 扩展中不释放 GIL,主线程视角仍是有效 CPU 样本。
- **fork 不继承定时器**:子进程需重新 setitimer。
- POSIX.1-2024 已删除 setitimer,新代码可用 `timer_create(CLOCK_PROCESS_CPUTIME_ID, ...)`;ITIMER_PROF 语义等价于 CLOCK_PROCESS_CPUTIME_ID + SIGEV_SIGNAL。

## 参考资料(实际阅读过的权威来源)

- [getitimer(2) — Linux man-pages](https://man7.org/linux/man-pages/man2/getitimer.2.html) — 三种定时器的时钟/信号语义、itimerval 结构、BUGS(信号丢失)、POSIX 淘汰历史
- [CPU Flame Graphs — Brendan Gregg](https://www.brendangregg.com/FlameGraphs/cpuflamegraphs.html) — 定时采样定位热点的方法论、99Hz 频率理由、样本数即时间占比
- [Linux perf Examples — Brendan Gregg](https://www.brendangregg.com/perf.html) — perf record -F 99 采样模型、与计数/追踪三种模式的对照
