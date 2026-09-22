# 11 性能分析

> 性能分析（Performance Analysis / Profiling）：用系统化方法定位性能瓶颈——先方法论（USE、Off-CPU、60 秒分析）再选工具（perf / eBPF / pprof），从"快不快"的直觉判断走向"慢在哪"的量化证据。2026-09-12 新增顶层类目（Agent 判断放置：跨语言、跨系统层级的横切研发学科）。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-系统级剖析/](./01-系统级剖析/) | perf / Ftrace / eBPF / bpftrace、火焰图、USE 方法、内核观测 |
| [02-应用级剖析/](./02-应用级剖析/) | Go pprof、Python cProfile/py-spy、Java JFR 等语言级剖析器 |
| [03-基准测试方法论/](./03-基准测试方法论/) | 微基准陷阱（DCE/常量折叠/循环优化）、统计显著性、主动基准测试 |
| [04-全链路性能/](./04-全链路性能/) | Core Web Vitals（LCP/INP/CLS）、field vs lab、OpenTelemetry 端到端追踪、延迟预算、扇出放大 |
| [05-容量规划与性能建模/](./05-容量规划与性能建模/) | Little 定律、M/M/1 拐点、Amdahl vs Gustafson、Universal Scalability Law、容量余量 |
| [06-数据库性能/](./06-数据库性能/) | 执行计划与 cost 口径、索引选择率、连接池、慢查询归因（2026-09-21 S2 建目录） |
| [07-网络与传输性能/](./07-网络与传输性能/) | 拥塞控制、RTT/BDP、TLS 握手开销、QUIC（2026-09-21 S2 建目录） |

## 已建子类目

- [x] 01-系统级剖析（perf / ftrace / eBPF / 火焰图 / USE，2026-09-12 起）
- [x] 02-应用级剖析（Go pprof / Python cProfile / Java JFR，2026-09-13）
- [x] 03-基准测试方法论（微基准陷阱 / 统计显著比较 CI 门禁，2026-09-14 S2 建目录 → 2026-09-15 首批 5 demo）
- [x] 04-全链路性能（Core Web Vitals / OpenTelemetry / 延迟预算，2026-09-15 S2 建目录 → 2026-09-22 首批 5 demo）
- [x] 05-容量规划与性能建模（排队论 / 扩展性定律 / 余量论证，2026-09-15 S2 建目录）
- [x] 06-数据库性能（PostgreSQL EXPLAIN cost 口径 / 索引选择率 / 连接池 / 慢查询归因，2026-09-21 S2 建目录）
- [x] 07-网络与传输性能（RFC 9002 QUIC 丢包检测与拥塞控制 / RTT-BDP / TLS 握手，2026-09-21 S2 建目录）

## 待拓展子类目（由自动巡检按类目拓展规则逐步补建）

- [ ] 08-编译器与运行时优化（JIT 分层编译、GC 停顿与分配速率、内联与逃逸分析）
- [ ] 09-前端与渲染性能（关键渲染路径、长任务与 INP、布局抖动、内存泄漏定位）

## 待研究

- [x] USE 方法：对每个资源检查 Utilization / Saturation / Errors（[01-系统级剖析/USE方法检查清单/](./01-系统级剖析/USE方法检查清单/)，2026-09-16）
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
- [x] `benchstat`、`-prof perfnorm`、统计检验选择、CI 回归门禁、主动基准测试清单（[03-基准测试方法论/](./03-基准测试方法论/)，2026-09-15 首批 5 demo 收官）
- [x] 容器性能分析（cgroup v2 级资源压力归因）（[01-系统级剖析/容器性能归因/](./01-系统级剖析/容器性能归因/)，2026-09-16）
- [x] CI 性能回归门禁（基准结果做成趋势而非单次断言）（[03-基准测试方法论/CI性能回归门禁/](./03-基准测试方法论/CI性能回归门禁/)，2026-09-15）
- [x] 应用级剖析首批（pprof 在线采集/top 解读、py-spy 采样、cProfile tottime-cumtime、benchmark ReportAllocs）（[02-应用级剖析/](./02-应用级剖析/)，2026-09-18 首批 5 demo）
- [x] 基准测试方法论第二批：`testing.B` 迭代标定与样本量、MDE 与样本量方程、CoV 噪声地板与降噪、多重比较校正（FWER vs FDR）、`-benchmem` 分配测量与 GC 摊销（[03-基准测试方法论/](./03-基准测试方法论/)，2026-09-19 第二批 5 demo）
- [x] 基准测试方法论第三批：JMH `-prof gc` 与 `-benchmem` 分配口径对拍、预热充分性的程序化判定（pyperf 五条不等式）、趋势存储与分片（Perfherder signature_hash vs Chrome Perf 路径主键）、容器/虚拟化下的 `-benchtime` 标定（cgroup 配额量化 + steal）、参数化基准的多维公平比较（[03-基准测试方法论/](./03-基准测试方法论/)，2026-09-21 第三批 5 demo）
- [x] 延迟预算拆分：SLO → 每跳预算的下发策略（[04-全链路性能/](./04-全链路性能/)，2026-09-22 首批 5 demo，ID 596-600）
- [ ] Core Web Vitals 字段采集口径：LCP 候选元素淘汰、INP 交互分组、CLS 会话窗口
- [x] 系统级剖析第四批：CPU 利用率口径与 IPC 归因、RPS/RFS/XPS 与中断亲和再平衡、Off-Wake 调度延迟栈归因、差分火焰图、BPF ringbuf（[01-系统级剖析/](./01-系统级剖析/)，2026-09-20 第四批 5 demo，ID 432-436）
- [x] 二进制符号与调试信息链路：ELF 重定位 / DWARF / CFI 展开 / TLS / Itanium C++ 名字改编（[10-逆向工程/01-二进制逆向/](../10-逆向工程/01-二进制逆向/)，2026-09-20 补录 ID 427-431）
- [ ] "Linux 性能分析 60 秒"（原文在 netflixtechblog → medium.com 被墙，本轮改做 CPU 利用率口径；待换可达源）
- [x] 应用级剖析第二批：pprof 调用图与火焰图过滤、Go execution tracer、py-spy `--native`、JFR 与 async-profiler 的 safepoint 偏差、`testing.B.Loop`（[02-应用级剖析/](./02-应用级剖析/)，2026-09-21 第二批 5 demo，ID 487-491）
- [ ] USL 参数拟合（从实测 (N, X) 点估 α、β、γ 并定位 Nmax）
- [ ] 中断/软中断绑核再平衡（`smp_affinity` 掩码已覆盖，缺实际调优与 RPS/RFS 侧）

## 参考资料（已读）

- [Brendan Gregg — Linux Performance（工具图谱与方法论权威入口）](https://www.brendangregg.com/linuxperf.html)
- [Go 官方博客 — Profiling Go Programs（pprof 采集与分析）](https://go.dev/blog/pprof)
- Brendan Gregg — Flame Graphs / CPU / Off-CPU / perf 四页 + flamegraph.pl 源码 + man7 `getitimer(2)`/`ptrace(2)`（2026-09-13 首批 5 demo 的资料，逐条链接详见 [01-系统级剖析/README.md](./01-系统级剖析/README.md)）
- ebpf.io / Kprobes / BPF ISA / Ftrace / PSI / `perf_event_open(2)` / `proc(5)` / Valgrind Massif / Linux Load Averages（2026-09-14 第二批 5 demo 的权威来源，逐条链接详见 [01-系统级剖析/README.md](./01-系统级剖析/README.md)）
- Oracle《Avoiding Benchmarking Pitfalls on the JVM》+ CodSpeed JMH 指南 + openjdk/jmh 样例解读 + hyperfine 官方仓库与 4 篇用法指南（2026-09-14 新增 [03-基准测试方法论/](./03-基准测试方法论/)，逐条链接见该目录 README）
- pkg.go.dev `benchstat` + golang/perf `utest.go` 源码 + JMH `LinuxPerfNormProfiler.java` + jmh-dev 邮件列表 + NIST/Skis/Dropbox 等 20 条来源（2026-09-15 首批 5 demo，逐条链接见 [03-基准测试方法论/README.md](./03-基准测试方法论/README.md)）
- web.dev Core Web Vitals 阈值定义（含 p75 选型论证）+ Google Search Console 官方报告口径 + OpenTelemetry Traces 文档 + Neil Gunther USL 原文 + Cornell/UVA/HPC101 三条 Amdahl-Gustafson 讲解 + Semicolony 容量规划手册（2026-09-15 新增 [04-全链路性能/](./04-全链路性能/) 与 [05-容量规划与性能建模/](./05-容量规划与性能建模/)，逐条链接见两目录 README）
- PostgreSQL《Using EXPLAIN》官方文档 + RFC 9002（QUIC Loss Detection and Congestion Control）（2026-09-21 新增 [06-数据库性能/](./06-数据库性能/) 与 [07-网络与传输性能/](./07-网络与传输性能/)，逐条链接见两目录 README）
- bpftrace 语言参考/一行式教程/手册页 + Gregg USE Method 原文与 Linux 清单 + kernel iostats + cgroup v2 内核文档 + Gregg DockerCon 2017 容器分析 + runqlat + kernel sched-stats + IRQ affinity/irq-redir + Red Hat 调优指南（2026-09-16 新增 [01-系统级剖析/](./01-系统级剖析/) 第三批 5 demo，逐条链接见该目录 README）
- pkg.go.dev net/http/pprof、runtime/pprof、testing + golang/go pprof.go 源码 + Go Blog Profiling Go Programs + docs.python.org The Python Profilers + benfred/py-spy README（2026-09-18 新增 [02-应用级剖析/](./02-应用级剖析/) 首批 5 demo，逐条链接见该目录 README）
- golang/go `src/testing/benchmark.go` 源码 + pkg.go.dev `runtime#MemStats` + NIST e-Handbook §7.2.2.2/§1.3.5.3 + Dropbox Apogee + LLVM Benchmarking tips + statsmodels `multipletests` + Benjamini-Hochberg(1995)/Benjamini-Yekutieli(2001) 临界常数（2026-09-19 新增 [03-基准测试方法论/](./03-基准测试方法论/) 第二批 5 demo，逐条链接见该目录 README）
- W3C Trace Context Level 1 + W3C Baggage + gRPC Deadlines / Request Hedging 官方指南 + opentelemetry-collector-contrib `tailsamplingprocessor/processor.go` 与 `internal/idbatcher/id_batcher.go` + OpenTelemetry Trace API（SpanKind）+ Dean & Barroso《The Tail at Scale》（CACM）（2026-09-22 新增 [04-全链路性能/](./04-全链路性能/) 首批 5 demo，逐条链接见该目录 README）
- JMH `GCProfiler.java` + golang/go `src/testing/benchmark.go` + psf/pyperf `_worker.py`/`_utils.py`/`_runner.py` + JMH `Defaults.java`/`Warmup.java` + mozilla/treeherder `etl/perf.py`/`perf/models.py` + catapult-project `graph_data.py` + golang/go `internal/runtime/cgroup/cgroup.go`/`runtime/proc.go` + Linux `Documentation/filesystems/proc.rst` + hyperfine `range_step.rs`/`relative_speed.rs`/`benchmark_result.rs` + JMH `Param.java`（2026-09-21 新增 [03-基准测试方法论/](./03-基准测试方法论/) 第三批 5 demo，逐条链接见该目录 README）
