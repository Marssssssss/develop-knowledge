# 系统级剖析

> Linux 系统层的性能观测与追踪。权威方法论来自 Brendan Gregg：**先有方法再选工具**——USE 方法（对每个资源检查 Utilization 使用率 / Saturation 饱和度 / Errors 错误）、Off-CPU 分析（研究"不在 CPU 上运行的时间"）、事故排查先跑的"60 秒分析"（10 条命令）。工具演进路径：传统工具 → Ftrace → perf → eBPF（现代 Linux 可观测性核心，前端 bcc / bpftrace）。

## 核心研究主题

- **方法论**：USE 方法检查清单、Off-CPU 分析、TSA 时间序列分析、60 秒分析命令组
- **perf**：内核自带剖析器、perf one-liners、PMC（性能计数器，IPC 指标）
- **Ftrace**：内核内置追踪器 + perf-tools 工具集
- **eBPF / bcc / bpftrace**：kprobe/uptrace 级动态追踪（"DTrace 2.0"）
- **火焰图**：CPU / Off-CPU / 内存火焰图、FlameScope 偏斜检测、延迟热图
- **关键指标**：CPU 使用率为何"不准"（内存失速周期）、运行队列延迟、Load Average 真正含义
- **专项**：ext4 I/O 延迟分布、TCP 重传/丢包追踪（tcpdrop）、调度器唤醒延迟

## 已完成 demo

| ID | Demo | 语言 | 一句话核心 |
| --- | --- | --- | --- |
| 092 | [采样剖析原理/](./采样剖析原理/) | C / Python / Go | ITIMER_PROF 按 CPU 时间递减投递 SIGPROF,信号处理函数抓栈折叠计数;99Hz 奇频率防步调一致 |
| 093 | [火焰图生成/](./火焰图生成/) | Python / Go | folded 单行栈 → 字母序合并 → 宽度∝计数的 SVG;同名函数同色(名字校验和作种子) |
| 094 | [perf_events剖析/](./perf_events剖析/) | C / Python | counting/sampling/trace 三种插桩模式;-F 99 定时 vs -c 阈值溢出采样;IPC/SCPI 派生 |
| 095 | [strace与ptrace/](./strace与ptrace/) | C / Python | PTRACE_SYSCALL 的 enter/exit-stop 由 tracer 自行配对;ptrace 每 syscall 两次停止使其慢 62x |
| 096 | [Off-CPU分析/](./Off-CPU分析/) | Python / Go | finish_task_switch() 单点插桩记账;线程池聚合等待可超墙钟(时间膨胀);--state=2 剔除抢占 |

## 待研究

- [x] perf record + 火焰图生成完整流程(092 采集 + 093 渲染两段已覆盖,实机 perf 链路待补)
- [ ] bpftrace 一行式追踪系统调用延迟
- [x] Off-CPU 时间火焰图(与 on-CPU 对比)
- [x] 用 PMC/IPC 判断 CPU 密集还是内存瓶颈(094 的 stat 解析已覆盖原理,实机 PMC 待补)
- [ ] 容器性能分析(Gregg DockerCon 演讲方法)
- [ ] USE 方法检查清单的落地
- [ ] Load Average 的真正含义(含不可中断睡眠)

## 参考资料（已读）

- [Brendan Gregg — Flame Graphs](https://www.brendangregg.com/flamegraphs.html)(093)
- [Brendan Gregg — CPU Flame Graphs](https://www.brendangregg.com/FlameGraphs/cpuflamegraphs.html)(092/093)
- [Brendan Gregg — Off-CPU Flame Graphs](https://www.brendangregg.com/FlameGraphs/offcpuflamegraphs.html)(096)
- [Brendan Gregg — Linux perf Examples](https://www.brendangregg.com/perf.html)(094/095)
- [flamegraph.pl 源码 — github.com/brendangregg/FlameGraph](https://github.com/brendangregg/FlameGraph)(093)
- [man7 getitimer(2)](https://man7.org/linux/man-pages/man2/getitimer.2.html)(092)
- [man7 ptrace(2)](https://man7.org/linux/man-pages/man2/ptrace.2.html)(095)
- [Brendan Gregg — Linux Performance](https://www.brendangregg.com/linuxperf.html)
- 配套书：《Systems Performance》2nd（2020）、《BPF Performance Tools》
