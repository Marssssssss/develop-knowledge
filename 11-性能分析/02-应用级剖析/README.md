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

## 待研究

- [ ] pprof web 调用图与火焰图视图（--nodefraction/-focus/-ignore 过滤组合）
- [ ] Go execution tracer（`go tool trace`）：与 pprof 采样剖析互补的确定性事件流
- [ ] py-spy --native：C/C++/Cython 原生扩展的采样剖析
- [ ] Java JFR 与 async-profiler（async 采样如何绕开 safepoint 偏差）
- [ ] b.Loop 新式基准：与 b.N 风格的差异（循环体独占计时、免 ResetTimer）
- [ ] pprof 剖析的 label（pprof.Labels/WithLabels）在在线服务按请求维度聚合

## 参考资料（已读）

- [Go 官方博客 — Profiling Go Programs（Russ Cox）](https://go.dev/blog/pprof)
- [net/http/pprof - Go Packages](https://pkg.go.dev/net/http/pprof)
- [runtime/pprof - Go Packages](https://pkg.go.dev/runtime/pprof)
- [golang/go src/net/http/pprof/pprof.go（源码）](https://raw.githubusercontent.com/golang/go/master/src/net/http/pprof/pprof.go)
- [testing - Go Packages](https://pkg.go.dev/testing)
- [The Python Profilers - Python 3 官方文档](https://docs.python.org/3/library/profile.html)
- [benfred/py-spy README](https://github.com/benfred/py-spy)
