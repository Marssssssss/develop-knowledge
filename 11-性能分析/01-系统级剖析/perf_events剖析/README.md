# perf_events 剖析(perf stat / perf record)

## 简介

perf_events 是 Linux 内核的主观测框架(2.6.31 起合入),`perf` 命令是其用户态前端。它把 CPU 的 PMU(性能监视单元)硬件计数器、内核软件事件、静态 tracepoint、kprobes/uprobes 动态探针统一成一套"事件"接口,支持三种插桩模式——**计数(counting)开销最低、采样(sampling)次之、逐事件追踪(trace)最高**。Gregg 的核心建议:**先 `perf stat` 计数,不够时再 `perf record` 采样**。

- **`perf stat`**:运行一条命令并打印各计数器汇总,不产生 perf.data。
- **`perf record -F 99 -a -g`**:全 CPU 99Hz 采样并抓栈,写入 perf.data,`perf report`/`perf script` 分析。
- **`perf record -e L1-dcache-load-misses -c 10000`**:事件计数触发采样——计数器每溢出 10000 次才中断一次内核。
- **硬件事件**:`cycles`、`instructions`、`branch-misses`、`L1-dcache-loads`、`LLC-load-misses`、`stalled-cycles-frontend/backend` 等,由 CPU PMU 提供;**软件事件**:`context-switches`、`cpu-clock`、`page-faults` 等,由内核维护。
- **历史**:perf_events 由 Ingo Molnar 等人在 Linux 2.6.31 合入,常被称为 "perf"。

## 原理详解

### perf stat 与关键指标

`perf stat gzip file1` 的典型输出(Gregg 页面实测数据):

```text
5,649,595,479 cycles                    #    2.942 GHz
1,808,339,931 stalled-cycles-frontend   #   32.01% frontend cycles idle
1,171,884,577 stalled-cycles-backend    #   20.74% backend cycles idle
8,625,207,199 instructions              #    1.53  insns per cycle
                                         #   0.21  stalled cycles per insn
1,488,797,176 branches                  #  775.351 M/sec
   53,395,139 branch-misses             #    3.59% of all branches
```

| 指标 | 含义 | 解读 |
| --- | --- | --- |
| IPC(`insns per cycle`) | 每周期指令数 | 通常 >1 视为好;但要警惕自旋循环(高指令率低有效工作) |
| frontend cycles idle | 取指/分支预测/译码停滞 | 流水线前端喂不上 |
| backend cycles idle | 乱序执行引擎停滞 | 后端可并行 3-4 个 uops,这是 IPC 能 >1 的原因 |
| SCPI(`stalled cycles per insn`) | 每指令停滞周期 | 停滞=延迟;Gregg 希望它像 IPC 一样普及 |

### 三种插桩模式与数据通路

```text
counting : 事件发生 -> 内核上下文里 count++            -> 汇总打印(最便宜)
sampling : 事件发生 -> 计数器溢出中断 -> 样本写环形缓冲   -> perf 异步读出 perf.data
trace    : 每个事件  -> 全量记录细节                      -> 开销 ∝ 事件频率
```

采样触发的两种方式:
1. **定时**:`-F 99` 每秒 99 次;用 99 而非 100 避免与周期性活动"步调一致"导致偏斜。
2. **事件计数阈值**:`-c 10000` 由处理器实现,达到阈值才中断内核,避免逐事件采样的高开销。

软件事件有**默认采样周期**(如 `context-switches` 默认 sample_freq=4000,采样是子集);要全量需 `-c 1`。

### 采样偏斜(Skew)与精确采样

PMC 溢出到捕获 IP 之间有延迟,且乱序执行使 IP 指向"恢复指令"而非触发事件的指令。精确采样:`:p` 修饰符,0~3 个 p(越多越精确;Intel PEBS / AMD IBS);PEBS 失效常见原因是 CPU errata,查 `dmesg | grep -i pebs`。

### 栈采集三方案(`-g`)

| 方案 | 要求 | 特点 |
| --- | --- | --- |
| 帧指针 | 编译加 `-fno-omit-frame-pointer`、内核 `CONFIG_FRAME_POINTER=y` | 全深完整 |
| DWARF | 内核 3.9+,libunwind | 不需重编译,开销更大 |
| LBR | 处理器特性 | 8/16/32 帧深度上限,深栈不合火焰图;多数云环境禁用 |

### 开销实测(Gregg:dd 重系统调用负载)

```text
直接运行        5.2GB, 3.53s,  1.5 GB/s
perf stat 追踪  5.2GB, 9.14s,  573 MB/s   ← 慢 ~2.5x
strace -c 追踪  5.2GB, 218.9s, 23.9 MB/s  ← 慢 ~62x
```

strace 基于 ptrace 像"调试器一样逐系统调用停下进程";perf 在内核缓冲数据,差一个数量级。

### PMC 物理限制

PMU 计数器是固定硬件资源,同时只能编程**少数几个**——数千个可用事件里每条 `perf stat -e` 只能选一小撮;且多数计数器型号特定、虚拟化环境常不可用。

## 环境准备

- Linux + `linux-tools-$(uname -r)`;PMU 事件需真机(虚拟机常不可用)
- 本 demo 的 Python/C 实现不依赖 perf,解析/模型可离线运行

## 运行方式

### 真实工具链(本机为 Windows 时跳过)
```bash
perf stat -e cycles,instructions,branch-misses,cache-misses gzip file1
perf record -F 99 -a -g -- sleep 10 && perf report
perf stat -e 'syscalls:sys_enter_*' -a sleep 5     # 按类型统计全系统调用
```

### Python(perf stat 输出解析器)
```bash
python3 python/main.py < samples/perf_stat.txt    # 或内置示例:python3 python/main.py
```

### C(rdtsc 周期计数 + 溢出采样模型)
```bash
gcc -O2 -Wall -Wextra c/main.c -o pmu && ./pmu
```

## 关键代码片段

C 版 `-c 10000` 阈值采样的软件模型(对应"事件计数触发采样"):

```c
/* 软件模型:每 counter_period 次"事件"才产生一次采样中断 */
if (++hw_counter >= counter_period) {   /* PMC 溢出 -> 溢出中断 */
    hw_counter = 0;
    samples++;                          /* 内核抓 IP/栈,写环形缓冲 */
}
```

Python 版解析 `perf stat` 输出的 `(value) (unit) (event) (# comment)` 四段结构,计算 IPC / SCPI / miss 率,并按 hardware / software / tracepoint 分类事件名。

## 性能与边界

- `perf record -F 99 -a -g` 10 秒约产生 3.2 MB 数据;高频事件追踪可膨胀至数百 MB。
- 动态探针(`perf probe`)未使用时零开销,使用中开销 ∝ 事件频率 × 每次插桩工作量;用完 `--del` 删除。
- PMC 并发争用:多个 profiler 同时只能各占几个计数器。

## 注意事项与常见坑

- **IPC 高 ≠ 性能好**:自旋循环 IPC 很高但没做有效工作。
- **虚拟机里没有 cycles/instructions**:云环境报 "PMU Hardware doesn't support sampling" 时改用软件事件(如 `cpu-clock`)。
- **栈全是十六进制地址**:`-O2` 默认 `-fomit-frame-pointer` 所致;三个解法见"栈采集三方案"。
- **JIT 符号**:Java/Node 需维护 `/tmp/perf-PID.map`(perf-map-agent / `--perf_basic_prof`)。
- 事件名拼错不报错而是计数为 0;用 `perf list` 核对。

## 参考资料(实际阅读过的权威来源)

- [Linux perf Examples — Brendan Gregg](https://www.brendangregg.com/perf.html) — perf stat 输出解读、三种插桩模式、-F/-c 采样、skew 与 PEBS、栈采集三方案、dd 开销实测
- [CPU Flame Graphs — Brendan Gregg](https://www.brendangregg.com/FlameGraphs/cpuflamegraphs.html) — perf record → stackcollapse → flamegraph 工具链、99Hz 频率理由
- [getitimer(2) — man7](https://man7.org/linux/man-pages/man2/getitimer.2.html) — CPU 时间定时器语义(定时采样的老根脉,与 -F 采样同源)
