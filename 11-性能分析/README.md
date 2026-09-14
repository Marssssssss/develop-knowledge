# 11 性能分析

> 性能分析（Performance Analysis / Profiling）：用系统化方法定位性能瓶颈——先方法论（USE、Off-CPU、60 秒分析）再选工具（perf / eBPF / pprof），从"快不快"的直觉判断走向"慢在哪"的量化证据。2026-09-12 新增顶层类目（Agent 判断放置：跨语言、跨系统层级的横切研发学科）。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-系统级剖析/](./01-系统级剖析/) | perf / Ftrace / eBPF / bpftrace、火焰图、USE 方法、内核观测 |
| [02-应用级剖析/](./02-应用级剖析/) | Go pprof、Python cProfile/py-spy、Java JFR 等语言级剖析器 |
| [03-基准测试方法论/](./03-基准测试方法论/) | 微基准陷阱（DCE/常量折叠/循环优化）、统计显著性、主动基准测试 |

## 待拓展子类目（由自动巡检按类目拓展规则逐步补建）

- [ ] 04-全链路性能（前端 Web Vitals / Lighthouse、APM、延迟预算）
- [ ] 05-容量规划与性能建模（排队论、Amdahl/Gustafson 上限、Little 定律）

## 待研究

- [ ] USE 方法：对每个资源检查 Utilization / Saturation / Errors
- [ ] "Linux 性能分析 60 秒"：事故排查最先执行的 10 条命令
- [x] on-CPU vs Off-CPU 分析的区别与适用场景（[01-系统级剖析/Off-CPU分析/](./01-系统级剖析/Off-CPU分析/)，2026-09-13）
- [x] 火焰图读法：宽 = 热路径，平 = 调用栈深（[01-系统级剖析/火焰图生成/](./01-系统级剖析/火焰图生成/)，2026-09-13）
- [x] Load Average 的真正含义（含不可中断睡眠）（[01-系统级剖析/LoadAverage与PSI/](./01-系统级剖析/LoadAverage与PSI/)，2026-09-14）
- [x] PSI 的 some/full 与 trigger 主动通知（[01-系统级剖析/LoadAverage与PSI/](./01-系统级剖析/LoadAverage与PSI/)，2026-09-14）
- [x] eBPF 程序生命周期：加载/验证/JIT/挂载（[01-系统级剖析/eBPF动态追踪/](./01-系统级剖析/eBPF动态追踪/)，2026-09-14）
- [x] ftrace function_graph 解析与动态补丁（[01-系统级剖析/ftrace内核追踪/](./01-系统级剖析/ftrace内核追踪/)，2026-09-14）
- [x] PMU 多路复用与缩放估算误差（[01-系统级剖析/PMU硬件计数器/](./01-系统级剖析/PMU硬件计数器/)，2026-09-14）
- [x] 页缺失口径与 PSS/RSS 差异、堆剖析抽样（[01-系统级剖析/页缺失与内存剖析/](./01-系统级剖析/页缺失与内存剖析/)，2026-09-14）
- [x] 采样剖析原理与 perf_events 工具链（[01-系统级剖析/采样剖析原理/](./01-系统级剖析/采样剖析原理/) + [perf_events剖析/](./01-系统级剖析/perf_events剖析/)，2026-09-13）
- [ ] 容器性能分析（cgroup v2 级资源压力归因）
- [ ] CI 性能回归门禁（基准结果做成趋势而非单次断言）

## 参考资料（已读）

- [Brendan Gregg — Linux Performance（工具图谱与方法论权威入口）](https://www.brendangregg.com/linuxperf.html)
- [Go 官方博客 — Profiling Go Programs（pprof 采集与分析）](https://go.dev/blog/pprof)
- [Brendan Gregg — Flame Graphs / CPU / Off-CPU / perf 四页 + flamegraph.pl 源码 + man7 getitimer(2)/ptrace(2)]（2026-09-13 首批 5 demo 的资料，详见 [01-系统级剖析/README.md](./01-系统级剖析/README.md)）
- [ebpf.io / Kprobes / BPF ISA / Ftrace / PSI / perf_event_open(2) / proc(5) / Valgrind Massif / Linux Load Averages]（2026-09-14 第二批 5 demo 的权威来源，详见 [01-系统级剖析/README.md](./01-系统级剖析/README.md)）
- [Oracle《Avoiding Benchmarking Pitfalls on the JVM》+ CodSpeed JMH 指南 + openjdk/jmh 样例解读 + hyperfine 官方仓库与 4 篇用法指南]（2026-09-14 新增 [03-基准测试方法论/](./03-基准测试方法论/)，逐条链接见该目录 README）
