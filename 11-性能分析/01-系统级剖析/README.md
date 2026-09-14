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
| 097 | [eBPF动态追踪/](./eBPF动态追踪/) | C / Python / Go | 8 字节定长指令 + 验证器抽象解释(寄存器类型格) + JIT;kprobe 靠 int3(0xCC) 打补丁、kretprobe 走 trampoline |
| 098 | [ftrace内核追踪/](./ftrace内核追踪/) | C / Python / Go | 函数入口埋 nop,启用时 text patch 成跳转;function_graph 用 `{`/`}` 配对还原调用树,inclusive 减子树得 self |
| 099 | [PMU硬件计数器/](./PMU硬件计数器/) | C / Python / Go | perf_event_open 组内比值才可信;槽位不足时用 time_enabled/time_running 缩放(实测误差 1.7%) |
| 100 | [LoadAverage与PSI/](./LoadAverage与PSI/) | C / Python / Go | loadavg 是定点 EMA(非算术平均)且计入 D 状态;PSI 用 some/full 区分"忙"与"卡",trigger+poll 可主动叫醒 |
| 101 | [页缺失与内存剖析/](./页缺失与内存剖析/) | C / Python / Go | minor/major 页缺失口径;PSS 按共享页均摊(1000+1000/2=1500);Massif 抽样放大还原分配树 |

## 待研究

- [x] perf record + 火焰图生成完整流程(092 采集 + 093 渲染两段已覆盖,实机 perf 链路待补)
- [ ] bpftrace 一行式追踪系统调用延迟(097 已覆盖 eBPF 指令层与验证器,前端 DSL 待补)
- [x] Off-CPU 时间火焰图(与 on-CPU 对比)
- [x] 用 PMC/IPC 判断 CPU 密集还是内存瓶颈(094 原理 + 099 硬件计数器编码/缩放已覆盖)
- [x] eBPF 程序的生命周期:加载、验证、JIT、挂载(097 已覆盖)
- [x] ftrace function_graph 自建解析器(098 已覆盖 `{`/`}` 配对 + self time)
- [x] Load Average 的真正含义(含不可中断睡眠)(100 已覆盖定点 EMA 与 D 状态)
- [ ] 容器性能分析(Gregg DockerCon 演讲方法)
- [ ] USE 方法检查清单的落地
- [ ] PSI trigger 接入生产告警链路(cgroup v2 级压力归因)

## 参考资料（已读）

- [Brendan Gregg — Flame Graphs](https://www.brendangregg.com/flamegraphs.html)(093)
- [Brendan Gregg — CPU Flame Graphs](https://www.brendangregg.com/FlameGraphs/cpuflamegraphs.html)(092/093)
- [Brendan Gregg — Off-CPU Flame Graphs](https://www.brendangregg.com/FlameGraphs/offcpuflamegraphs.html)(096)
- [Brendan Gregg — Linux perf Examples](https://www.brendangregg.com/perf.html)(094/095)
- [Brendan Gregg — Linux Load Averages: Solving the Mystery](https://www.brendangregg.com/blog/2017-08-08/linux-load-averages.html)(100)
- [Brendan Gregg — Linux Performance](https://www.brendangregg.com/linuxperf.html)
- [flamegraph.pl 源码 — github.com/brendangregg/FlameGraph](https://github.com/brendangregg/FlameGraph)(093)
- [man7 getitimer(2)](https://man7.org/linux/man-pages/man2/getitimer.2.html)(092)
- [man7 ptrace(2)](https://man7.org/linux/man-pages/man2/ptrace.2.html)(095)
- [man7 perf_event_open(2)](https://man7.org/linux/man-pages/man2/perf_event_open.2.html)(099)
- [ebpf.io — What is eBPF?](https://ebpf.io/what-is-ebpf/)(097)
- [Kernel Probes (Kprobes) — Linux Kernel Documentation](https://docs.kernel.org/trace/kprobes.html)(097)
- [BPF Instruction Set Specification — Linux Kernel Documentation](https://docs.kernel.org/bpf/standardization/instruction-set.html)(097/099)
- [Ftrace — Linux Kernel Documentation](https://docs.kernel.org/trace/ftrace.html)(098/099)
- [PSI - Pressure Stall Information — Linux Kernel Documentation](https://docs.kernel.org/accounting/psi.html)(100)
- [proc(5) — Linux Kernel Documentation](https://docs.kernel.org/filesystems/proc.html)(101)
- [Valgrind Massif — Manual](https://valgrind.org/docs/manual/ms-manual.html)(101)
- 配套书：《Systems Performance》2nd（2020）、《BPF Performance Tools》
