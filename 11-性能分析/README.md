# 11 性能分析

> 性能分析（Performance Analysis / Profiling）：用系统化方法定位性能瓶颈——先方法论（USE、Off-CPU、60 秒分析）再选工具（perf / eBPF / pprof），从"快不快"的直觉判断走向"慢在哪"的量化证据。2026-09-12 新增顶层类目（Agent 判断放置：跨语言、跨系统层级的横切研发学科）。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-系统级剖析/](./01-系统级剖析/) | perf / Ftrace / eBPF / bpftrace、火焰图、USE 方法、内核观测 |
| [02-应用级剖析/](./02-应用级剖析/) | Go pprof、Python cProfile/py-spy、Java JFR 等语言级剖析器 |

## 待拓展子类目（由自动巡检按类目拓展规则逐步补建）

- [ ] 03-基准测试方法论（微基准陷阱、统计显著性、主动基准测试）
- [ ] 04-全链路性能（前端 Web Vitals / Lighthouse、APM、延迟预算）

## 待研究

- [ ] USE 方法：对每个资源检查 Utilization / Saturation / Errors
- [ ] "Linux 性能分析 60 秒"：事故排查最先执行的 10 条命令
- [x] on-CPU vs Off-CPU 分析的区别与适用场景（[01-系统级剖析/Off-CPU分析/](./01-系统级剖析/Off-CPU分析/)，2026-09-13）
- [x] 火焰图读法：宽 = 热路径，平 = 调用栈深（[01-系统级剖析/火焰图生成/](./01-系统级剖析/火焰图生成/)，2026-09-13）
- [ ] Load Average 的真正含义（含不可中断睡眠）
- [x] 采样剖析原理与 perf_events 工具链（[01-系统级剖析/采样剖析原理/](./01-系统级剖析/采样剖析原理/) + [perf_events剖析/](./01-系统级剖析/perf_events剖析/)，2026-09-13）

## 参考资料（已读）

- [Brendan Gregg — Linux Performance（工具图谱与方法论权威入口）](https://www.brendangregg.com/linuxperf.html)
- [Go 官方博客 — Profiling Go Programs（pprof 采集与分析）](https://go.dev/blog/pprof)
- [Brendan Gregg — Flame Graphs / CPU / Off-CPU / perf 四页 + flamegraph.pl 源码 + man7 getitimer(2)/ptrace(2)]（2026-09-13 首批 5 demo 的资料，详见 [01-系统级剖析/README.md](./01-系统级剖析/README.md)）
