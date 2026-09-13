# Golang

> 一门**静态强类型** + **编译型** + **天生并发** 的系统级语言,由 Robert Griesemer / Rob Pike / Ken Thompson 在 Google 设计(2007 公开,2012 1.0 发布)。核心主张:**用通信共享内存,而非用共享内存通信**(基于 CSP 1978 的思想)。

## 一、Golang 关键差异(对比主流语言)

| 维度 | Go | C / Rust | Java / Kotlin | Python |
| --- | --- | --- | --- | --- |
| 类型系统 | 静态 + 类型推断 | 静态 | 静态 | 动态 |
| 并发模型 | goroutine + channel(CSP)| OS thread / async | thread + Future | asyncio / 多进程 |
| 内存管理 | GC(三色标记 + 写屏障)| 手动 / RAII | GC | GC(引用计数)+ GC |
| 错误处理 | error 返回值(error wrapping)| panic / Result | checked Exception | try/except |
| 编译速度 | 快(秒级) | 中 / 慢 | 慢 | 解释 |
| 空值 | 无(null nil 是零值但易别)| 无 | null | None |
| OO 范式 | 接口+组合(无类继承)| trait / class | class | class |

## 二、Golang 独有/标志性的语言特性

按学习路径排序:

| 特性 | 必学? | 备注 |
| --- | --- | --- |
| `go` 关键字 | ✅ | goroutine 是 Go 最核心特性 |
| `chan` 与 `<-` 操作符 | ✅ | typed conduit, 同步原语 |
| `defer` | ✅ | 函数返回前执行, 多个 defer 逆序执行(LIFO) |
| `select { case ... }` | ✅ | 多 channel 多路复用 |
| 切片(slice) | ✅ | 三字头 array+len+cap, append 触发 growslice |
| 错误处理(error / `errors.Is/As`) | ✅ | `if err != nil { ... }` 显式检查 |
| 接口(interface 动态派发)| ✅ | itab 记录类型方法集 |
| map | ✅ | hash table, 不支持并发读写 |
| for-range | ✅ | 唯一循环关键字 |
| struct 值/指针接收者 | ✅ | 影响是否修改原对象 |
| goroutine 调度器(GPM) | 进阶 | M:N 调度, work-stealing |
| panic/recover | 进阶 | 类异常机制但语义不同 |
| context 包 | 进阶 | cancellation / timeout / value |
| type parameter(Go 1.18+)| ⏳ 计划 | 泛型 |

## 三、待研究清单(按"教学价值"排序)

1. **goroutine 与 channel** ✅ `09-语言学习/Golang/goroutine与channel/` — CSP 模型 + buffered/unbuffered + select 多路复用 + close 广播
2. **slice 底层机制** ✅ `09-语言学习/Golang/slice底层机制/` — 三字头结构、growslice 1.18+ 1.25x+192 几何增长、共享底层数组陷阱
3. **error 处理与错误包装** ✅ `09-语言学习/Golang/error处理/` — error 接口 + fmt.Errorf %w + errors.Is/As/Unwrap + errors.Join (Go 1.20)
4. **defer / panic / recover** ✅ `09-语言学习/Golang/defer与panic-recover/` — defer LIFO、参数立即求值、recover 硬性规则、Go 1.14+ 开放编码 defer
5. **map 底层实现** ✅ `09-语言学习/Golang/map底层实现/` — hmap + bmap + 装载因子 6.5 + 渐进式扩容 + Swiss Table (Go 1.24+)
6. **interface 与 itab** — 鸭子类型 + type assertion + type switch
7. **GPM 调度模型** — goroutine / processor / machine 三层 + work-stealing
8. **GC 三色标记 + 写屏障** — 1.5+ 引入并发标记, 1.8+ 引入混合写屏障
9. **context 包** — cancellation tree, value 传递
10. **type parameter(Go 1.18+ 泛型)** — type sets + constraints 包

## 四、权威来源(本 README 主要依据)

- [Go 语言规范 — The Go Programming Language Specification](https://go.dev/ref/spec) — 语法与语义的权威定义, 含 channel、select、defer、slice 完整规范。
- [《Go Under The Hood》(golang.design 中文版)](https://www.golang.design/under-the-hood/en/part1overview/ch01intro/csp) — 详细讲解 CSP 1978 历史脉络 + Go 选择 channel 的设计哲学; 还含 5.1 Arrays and Slices 章节讲 growslice 算法。
- [《The Go Blog — Effective Go》](https://go.dev/doc/effective_go) — 官方惯用法文档, channel 与 goroutine 用法示范。
- [《A Tour of Go》Concurrency chapter](https://go.dev/tour/concurrency/1) — 标准入门课程, buffered/unbuffered channel、select、default 文段。
- [runtime 包注释](https://pkg.go.dev/runtime) — `GOMAXPROCS`、`Gosched`、`Goexit`、`NumGoroutine` 等运行时 API。
- [go.dev / ref 文档](https://pkg.go.dev/) — 标准库权威 API 索引。

## 五、与已有 demo 的边界

- **领域 demo 里的 Go 实现**: 聚焦在该知识点的 Go 用法, **不展开** Go 语言其他特性
- **本目录的 demo**: 围绕 Go 本身的语言机制, 可引用领域 demo, 但核心是讲透 **Go 这门语言**

例:
- `07-数据存储/01-关系型/MVCC/go/main.go` 只讲用 channel/sync 协作的 MVCC 模拟
- `09-语言学习/Golang/slice底层机制/` 讲的是 slice 的三字头结构、growslice 几何增长、共享底层数组陷阱

## 进度

由 [`_docs/STATE.md`](../../_docs/STATE.md)(主,≤ 5 KB)+ [`archive`](../../_docs/archive/)(历史回溯)统一追踪。
