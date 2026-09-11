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

## 待研究

- [ ] perf record + 火焰图生成完整流程
- [ ] bpftrace 一行式追踪系统调用延迟
- [ ] Off-CPU 时间火焰图（与 on-CPU 对比）
- [ ] 用 PMC/IPC 判断 CPU 密集还是内存瓶颈
- [ ] 容器性能分析（Gregg DockerCon 演讲方法）

## 参考资料（已读）

- [Brendan Gregg — Linux Performance](https://www.brendangregg.com/linuxperf.html)
- 配套书：《Systems Performance》2nd（2020）、《BPF Performance Tools》
