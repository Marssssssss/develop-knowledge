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
| GPM 调度模型(GPM)| ✅ | M:N 调度, work-stealing |
| panic/recover | ✅ | 类异常机制但语义不同 |
| context 包 | ✅ | cancellation tree, value 传递 |
| type parameter(Go 1.18+)| ✅ | 泛型, type sets + constraints |

## 三、待研究清单(按"教学价值"排序)

1. **goroutine 与 channel** ✅ `09-语言学习/Golang/goroutine与channel/` — CSP 模型 + buffered/unbuffered + select 多路复用 + close 广播
2. **slice 底层机制** ✅ `09-语言学习/Golang/slice底层机制/` — 三字头结构、growslice 1.18+ 1.25x+192 几何增长、共享底层数组陷阱
3. **error 处理与错误包装** ✅ `09-语言学习/Golang/error处理/` — error 接口 + fmt.Errorf %w + errors.Is/As/Unwrap + errors.Join (Go 1.20)
4. **defer / panic / recover** ✅ `09-语言学习/Golang/defer与panic-recover/` — defer LIFO、参数立即求值、recover 硬性规则、Go 1.14+ 开放编码 defer
5. **map 底层实现** ✅ `09-语言学习/Golang/map底层实现/` — hmap + bmap + 装载因子 6.5 + 渐进式扩容 + Swiss Table (Go 1.24+)
6. **interface 与 itab** ✅ `09-语言学习/Golang/interface与itab/` — 双字对 + itable 生成/缓存 + 方法集 T/*T + type assertion/switch + typed-nil 陷阱
7. **GPM 调度模型** ✅ `09-语言学习/Golang/GPM调度模型/` — G/M/P 三层 + LRQ/GRQ + work-stealing 偷一半 + 1/61 规则 + M-P 解绑
8. **GC 三色标记 + 写屏障** ✅ `09-语言学习/Golang/GC三色标记与写屏障/` — 三色抽象 + 混合写屏障(1.8)+ 弱三色不变式 + GOGC/Pacer
9. **context 包** ✅ `09-语言学习/Golang/context包/` — 取消树 + WithTimeout + select 取消模式 + WithValue
10. **type parameter(Go 1.18+ 泛型)** ✅ `09-语言学习/Golang/type-parameter泛型/` — 类型参数 + type sets + ~ 底层类型 + 两种类型推断

### 第三批(2026-09-18) — 运行时与编译期内部机制

11. **内存分配器** ✅ `09-语言学习/Golang/内存分配器/` — 68 级 size class + span 布局 + tiny allocator + mcache/mcentral/mheap 三级缓存 + roundupsize
12. **逃逸分析与内联** ✅ `09-语言学习/Golang/逃逸分析与内联/` — 位置图 + derefs 权重 + parameter tag + 内联预算表(80/57/17/1/80) + `-l` 档位
13. **内存模型与 sync 原语** ✅ `09-语言学习/Golang/内存模型与sync原语/` — happens-before / DRF-SC + 各原语强度表 + Mutex 正常模式 vs 饥饿模式(1ms 阈值、barge、LIFO 重排、移交)
14. **反射三定律** ✅ `09-语言学习/Golang/反射三定律/` — interface 对 + `Kind` vs `Type` + flag 位(StickyRO/EmbedRO/Indir/Addr/Method) + `CanSet` 真实边界
15. **unsafe 与内存布局** ✅ `09-语言学习/Golang/unsafe与内存布局/` — `Sizeof/Alignof/Offsetof` 语义 + pointer bytes + 字段排序五规则 + size class 浪费 + `unsafe.Pointer` 六种合法模式

## 四、权威来源(本 README 主要依据)

- [Go 语言规范 — The Go Programming Language Specification](https://go.dev/ref/spec) — 语法与语义的权威定义, 含 channel、select、defer、slice 完整规范。
- [《Go Under The Hood》(golang.design 中文版)](https://www.golang.design/under-the-hood/en/part1overview/ch01intro/csp) — 详细讲解 CSP 1978 历史脉络 + Go 选择 channel 的设计哲学; 还含 5.1 Arrays and Slices 章节讲 growslice 算法。
- [《The Go Blog — Effective Go》](https://go.dev/doc/effective_go) — 官方惯用法文档, channel 与 goroutine 用法示范。
- [《A Tour of Go》Concurrency chapter](https://go.dev/tour/concurrency/1) — 标准入门课程, buffered/unbuffered channel、select、default 文段。
- [runtime 包注释](https://pkg.go.dev/runtime) — `GOMAXPROCS`、`Gosched`、`Goexit`、`NumGoroutine` 等运行时 API。
- [go.dev / ref 文档](https://pkg.go.dev/) — 标准库权威 API 索引。

第二批(2026-09-15,demo 182-186)新增:

- [Go Data Structures: Interfaces — Russ Cox](https://research.swtch.com/interfaces) — 接口双字对/itable 结构与 O(ni+nt) 生成算法。
- [Scheduling In Go : Part II — Ardan Labs](https://www.ardanlabs.com/blog/2018/08/scheduling-in-go-part2.html) — G/M/P、work stealing、两类阻塞的 M/P 处置。
- [Eliminate STW stack re-scanning(proposal 17503)](https://github.com/golang/proposal/blob/master/design/17503-eliminate-rescan.md) — 混合写屏障完整伪代码与弱三色不变式。
- [Getting to Go: The Journey of Go's GC — go.dev/blog](https://go.dev/blog/ismmkeynote) — 各版本 STW 演进数字与 Pacer。
- [A Guide to the Go Garbage Collector](https://go.dev/doc/gc-guide) — GOGC 公式、GOMEMLIMIT、mark assist。
- [Go Concurrency Patterns: Context — go.dev/blog](https://go.dev/blog/context) — Context 接口与派生树语义。
- [An Introduction To Generics — go.dev/blog](https://go.dev/blog/intro-generics) — 类型参数/type sets/两种类型推断。

第三批(2026-09-18,demo 307-311)新增:

- [The Go Memory Model(2022-06-06 版)](https://go.dev/ref/mem) — Requirement 1~3、synchronized before / happens before、数据竞争与 DRF-SC、各同步原语保证条款。
- [Internal sync: mutex.go(go1.24.0)](https://raw.githubusercontent.com/golang/go/go1.24.0/src/internal/sync/mutex.go) — 状态位、`starvationThresholdNs = 1e6`、`lockSlow`/`unlockSlow`、`queueLifo` 队首重排与饥饿模式移交。
- [The Laws of Reflection — Rob Pike](https://go.dev/blog/laws-of-reflection) — 三定律原文、interface 的 `(值, 类型)` 表示、`Kind` 与最大类型 getter、结构体字段可设置性。
- [reflect/value.go(go1.24.0)](https://raw.githubusercontent.com/golang/go/go1.24.0/src/reflect/value.go) — flag 常量块、`CanSet` 一行判定、`Elem`/`Field` 的权限位掩码与全部 panic 消息格式。
- [unsafe 包文档](https://pkg.go.dev/unsafe) — `Sizeof/Alignof/Offsetof` 语义、`unsafe.Pointer` 的六种合法模式与 `// INVALID:` 反例。
- [fieldalignment 分析器](https://pkg.go.dev/golang.org/x/tools/go/analysis/passes/fieldalignment) — `gcSizes` 布局算法、pointer bytes 三例、`optimalOrder` 五条规则、`classSize` 与 false sharing 警告。
- [runtime/sizeclasses.go(go1.24.0)](https://raw.githubusercontent.com/golang/go/go1.24.0/src/runtime/sizeclasses.go) — 68 级 `class_to_size` 与 span 布局表。
- 口径说明:本机**无 Go 工具链**,`go/` 目录的代码只做人工审查 + `_docs/tools/` 的
  结构自检(`bracket_check.py` / `syntax_sanity.py`),不作为"已编译通过"的承诺;
  Python 侧实现均实跑断言,以它为准。

## 五、与已有 demo 的边界

- **领域 demo 里的 Go 实现**: 聚焦在该知识点的 Go 用法, **不展开** Go 语言其他特性
- **本目录的 demo**: 围绕 Go 本身的语言机制, 可引用领域 demo, 但核心是讲透 **Go 这门语言**

例:
- `07-数据存储/01-关系型/MVCC/go/main.go` 只讲用 channel/sync 协作的 MVCC 模拟
- `09-语言学习/Golang/slice底层机制/` 讲的是 slice 的三字头结构、growslice 几何增长、共享底层数组陷阱

## 进度

由 [`_docs/STATE.md`](../../_docs/STATE.md)(主,≤ 5 KB)+ [`archive`](../../_docs/archive/)(历史回溯)统一追踪。
