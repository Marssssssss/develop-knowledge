# 应用级剖析

> 语言/应用层的 profiler 体系。以 Go pprof 为代表（官方文档）：CPU profile 每秒约采样 100 次 goroutine 栈的程序计数器；heap profile 按约 1/524288 采样率记录分配；采集三通道——独立程序用 `runtime/pprof`、基准测试用 `go test -cpuprofile/-memprofile`、在线服务只需 `import _ "net/http/pprof"` 后从 `/debug/pprof/` 拉取。官方案例证明"垃圾回收语言更要盯内层循环的分配量"：一轮剖析优化让程序快 11 倍、省 3.7 倍内存。

## 核心研究主题

- **Go pprof**：三种采集方式、`go tool pprof` 分析（top / top -cum / list / web 调用图）
- **解读技能**：自运行时间 vs 累计时间（调用栈占比）、`--nodefraction` 去噪
- **Python**：cProfile / profile、py-spy（无需重启的采样剖析）
- **Java**：JFR（Java Flight Recorder）、async-profiler
- **通用模式**：采样 vs 插桩的取舍、剖析开销控制、微基准的正确姿势

## 已完成 demo

- [x] net/http/pprof 在线采集最小示例（[pprof在线采集/](./pprof在线采集/)，2026-09-18）：Index 路由/delta profile/gc 仅 heap/debug 明文/GET 限定
- [x] pprof top/top -cum/list 三板斧解读（[pprof-top解读/](./pprof-top解读/)，2026-09-18）：flat=叶子帧、cum=在栈上、100 帧截断保叶子侧、nodefraction 去噪
- [x] py-spy dump/top 对运行中进程的采样（[py-spy采样剖析/](./py-spy采样剖析/)，2026-09-18）：process_vm_readv 跨进程读内存、PyFrameObject back 链、--gil 过滤、--nonblocking 撕裂读、attach 权限模型
- [x] cProfile 输出的 tottime vs cumtime（[cProfile-tottime-cumtime/](./cProfile-tottime-cumtime/)，2026-09-18）：确定性插桩事件流、primitive 调用、递归 cumtime 只按顶层记账
- [x] Go benchmark b.ReportAllocs 的分配统计（[benchmark-ReportAllocs/](./benchmark-ReportAllocs/)，2026-09-18）：b.N 自适应重跑、B/op=MemBytes/N 整数除法、ResetTimer/StopTimer、SetBytes 的 MB/s ≠ B/op、RunParallel 墙钟口径
- [x] pprof 调用图与火焰图视图的过滤组合（[pprof调用图与火焰图过滤/](./pprof调用图与火焰图过滤/)，2026-09-21）：过滤改的是样本集、focus/ignore 优先级、hide/show 行级裁剪、ShowFrom 栈截断、nodefraction 用 cum 且临界值保留、火焰图宽度=cum 而颜色只按包名
- [x] Go execution tracer（[Go执行追踪器/](./Go执行追踪器/)，2026-09-21）：pprof 采样看不见阻塞、事件乱序落盘需重排、Go 1.22 trace 拆分、flight recorder 的 MinAge/MaxBytes、log/region/task 三类标注
- [x] py-spy `--native` 原生扩展采样（[py-spy原生扩展采样/](./py-spy原生扩展采样/)，2026-09-21）：Cython 生成的 C 文件里的 `/* "x.pyx":N` 标记表、0-based 键与右开区间查找、EOF 哨兵位置、demangle 四条官方向量、平台支持表
- [x] Java JFR 与 async-profiler（[Java-JFR与async-profiler/](./Java-JFR与async-profiler/)，2026-09-21）：safepoint 偏差把热点挪到轮询点、cpu/itimer/ctimer 三引擎对照、FP/DWARF/VM Structs 栈行走、JFR 的 base-128 编码与环形缓冲、Event Streaming 的秒级刷新
- [x] Go 1.24 `testing.B.Loop` 新式基准（[Go-bLoop基准循环/](./Go-bLoop基准循环/)，2026-09-21）：基准函数每 -count 只跑一次、首次调用 ResetTimer / 结束 StopTimer、循环内 b.N 置 0、毒化位 1<<63、predictN 的四条夹紧规则

## 待研究

- [ ] pprof 剖析的 label（pprof.Labels/WithLabels）在在线服务按请求维度聚合
- [ ] `-tagfocus/-tagroot/-tagleaf` 把标签做成伪栈帧之后的调用图形态
- [ ] 连续剖析（continuous profiling）：常开采样的存储与降采样策略
- [ ] eBPF 语言无关剖析器与语言级 profiler 的栈对齐问题
- [ ] 剖析数据的时间序列对比：多份 profile 的自动回归定位

## 参考资料（已读）

- [Go 官方博客 — Profiling Go Programs（Russ Cox）](https://go.dev/blog/pprof)
- [net/http/pprof - Go Packages](https://pkg.go.dev/net/http/pprof)
- [runtime/pprof - Go Packages](https://pkg.go.dev/runtime/pprof)
- [golang/go src/net/http/pprof/pprof.go（源码）](https://raw.githubusercontent.com/golang/go/master/src/net/http/pprof/pprof.go)
- [testing - Go Packages](https://pkg.go.dev/testing)
- [The Python Profilers - Python 3 官方文档](https://docs.python.org/3/library/profile.html)
- [benfred/py-spy README](https://github.com/benfred/py-spy)
- [google/pprof — profile/filter.go（FilterSamplesByName / ShowFrom）](https://raw.githubusercontent.com/google/pprof/master/profile/filter.go)
- [google/pprof — internal/driver/driver_focus.go（applyFocus 顺序）](https://raw.githubusercontent.com/google/pprof/master/internal/driver/driver_focus.go)
- [google/pprof — internal/graph/graph.go（getNodesAboveCumCutoff / TrimLowFrequencyEdges）](https://raw.githubusercontent.com/google/pprof/master/internal/graph/graph.go)
- [google/pprof — proto/profile.proto（leaf at location_id[0]）](https://raw.githubusercontent.com/google/pprof/master/proto/profile.proto)
- [Go 官方博客 — More powerful Go execution traces（2024）](https://go.dev/blog/execution-traces-2024)
- [Go 官方博客 — Flight Recorder in Go 1.25](https://go.dev/blog/flight-recorder)
- [async-profiler — docs/CpuSamplingEngines.md](https://raw.githubusercontent.com/async-profiler/async-profiler/master/docs/CpuSamplingEngines.md)
- [async-profiler — docs/StackWalkingModes.md](https://raw.githubusercontent.com/async-profiler/async-profiler/master/docs/StackWalkingModes.md)
- [JEP 328: Java Flight Recorder](https://openjdk.org/jeps/328) / [JEP 349: JFR Event Streaming](https://openjdk.org/jeps/349)
- [Go 1.24 Release Notes — testing.B.Loop](https://go.dev/doc/go1.24) + [src/testing/benchmark.go](https://raw.githubusercontent.com/golang/go/master/src/testing/benchmark.go)
