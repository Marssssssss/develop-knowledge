# 应用级剖析

> 语言/应用层的 profiler 体系。以 Go pprof 为代表（官方文档）：CPU profile 每秒约采样 100 次 goroutine 栈的程序计数器；heap profile 按约 1/524288 采样率记录分配；采集三通道——独立程序用 `runtime/pprof`、基准测试用 `go test -cpuprofile/-memprofile`、在线服务只需 `import _ "net/http/pprof"` 后从 `/debug/pprof/` 拉取。官方案例证明"垃圾回收语言更要盯内层循环的分配量"：一轮剖析优化让程序快 11 倍、省 3.7 倍内存。

## 核心研究主题

- **Go pprof**：三种采集方式、`go tool pprof` 分析（top / top -cum / list / web 调用图）
- **解读技能**：自运行时间 vs 累计时间（调用栈占比）、`--nodefraction` 去噪
- **Python**：cProfile / profile、py-spy（无需重启的采样剖析）
- **Java**：JFR（Java Flight Recorder）、async-profiler
- **通用模式**：采样 vs 插桩的取舍、剖析开销控制、微基准的正确姿势

## 待研究

- [ ] net/http/pprof 在线采集最小示例
- [ ] pprof top/top -cum/list 三板斧解读
- [ ] py-spy dump/top 对运行中进程的采样
- [ ] cProfile 输出的 tottime vs cumtime
- [ ] Go benchmark b.ReportAllocs 的分配统计

## 参考资料（已读）

- [Go 官方博客 — Profiling Go Programs（Russ Cox）](https://go.dev/blog/pprof)
