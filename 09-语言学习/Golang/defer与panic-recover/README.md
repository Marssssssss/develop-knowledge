# Defer / Panic / Recover — 异常路径与栈展开

> Go 的「异常机制」三件套:`defer`(函数返回前执行,LIFO 逆序)+ `panic`(运行时异常,触发栈展开)+ `recover`(在 deferred 函数内捕获 panic,阻止传播)。与 try/catch 范式根本不同——**只能用于真正不可恢复的错误**。

## 简介

**Go 不鼓励把 panic/recover 当控制流**(与 try/catch 范式不同)。`panic` 用于「程序无法继续」(配置损坏、nil 指针、数组越界等);`defer` 用于「函数退出前必做」(关闭文件/解锁/记录耗时);`recover` 用于「在最外层兜底,让程序优雅退出」(如 HTTP server middleware)。

**关键概念**:
- **`defer f(x)`**:`f` 的参数 `x` 在 `defer` 语句处**立即求值**;`f` 的执行**推迟到外层函数返回前**;多个 defer **LIFO 逆序**
- **`panic(v)`**:以 `v` 为值启动栈展开;**运行时错误**(越界、空指针等)自动触发 panic
- **`recover()`**:**必须**在 deferred 函数内直接调用,才能阻止当前 panic;返回 panic 时传入的值
- **栈展开**:panic 沿调用栈向上传播,**每一层都执行其 defer 链**(LIFO),直到被 recover 或进程崩溃
- **defer 性能**:Go 1.14+ 引入**开放编码 defer**,普通场景开销接近零;**热路径避免 defer 在循环里**

**历史**:`defer` 与 `panic`/`recover` 是 Go 1.0 就有;**Go 1.14 (2020) 引入 "open-coded defers"** 把 defer 性能从 ~30 ns 降到 ~1 ns;1.22 修复 defer 闭包中的循环变量问题。

## 原理详解

### 1. defer 三特性

```go
// 特性 1: 参数在 defer 语句处立即求值(快照)
i := 0
defer fmt.Println(i) // i=0 立即快照, 输出 0
i = 99
// 输出 0, 不是 99

// 特性 2: 函数推迟到外层函数返回前执行
defer cleanup() // cleanup() 在外层函数 return/panic/正常结束时执行

// 特性 3: 多个 defer 逆序(LIFO)
defer fmt.Println("first")  // 第 3 个执行
defer fmt.Println("second") // 第 2 个执行
defer fmt.Println("third")  // 第 1 个执行
// 输出: third → second → first
```

### 2. defer 修改命名返回值

```go
func double(x int) (result int) {
    defer func() { result *= 2 }()
    return x + 1
}
double(5) // 返回 (5+1)*2 = 12, 不是 11
```

**延迟函数可访问并修改外层函数的具名返回值**。

### 3. panic 触发时机

| 触发源 | 示例 |
| --- | --- |
| 显式调用 | `panic("disk full")` |
| 运行时检测 | 除以 0、数组越界、nil 指针解引用、类型断言失败、**向已关闭 channel 发送** |
| 编译期 | 不会触发(编译期错误是另外的范畴) |

### 4. panic 的栈展开

```
panic("err") → f1() defer 链 LIFO → f2() defer 链 LIFO → ... → goroutine 顶层 → 进程崩溃(stack trace + 退出码非 0)
```

### 5. recover 的硬性规则

> **"If recover is called outside the deferred function it will not stop the panicking sequence."**

```go
// 正确:
defer func() {
    if r := recover(); r != nil {
        // 处理 panic
    }
}()

// 错误:
func tryRecover() {
    if r := recover(); r != nil { // 无效, recover 返回 nil
    }
}
```

### 6. 内部实现

- Go 1.13 之前:`_defer` 结构体构成链表,每次 defer 都分配(~30 ns)
- Go 1.14+:**开放编码 defer**——编译器在函数末尾生成 defer 调用的直接代码(类似 try/catch 的 zero-cost),无链表无堆分配(~1 ns)
- Go 1.14+ 回退栈式 defer:**只有以下场景**才退化:循环内有 defer、有 defer in defer、goroutine 数量 > GOMAXPROCS

## 对比 / 选型

| 维度 | Go defer/panic/recover | Java try/catch/finally | Python try/except/finally | C++ RAII / std::terminate |
| --- | --- | --- | --- | --- |
| 资源清理 | defer | finally / try-with-resources | finally / with | RAII(析构函数) |
| 异常触发 | panic + 运行时检测 | throw + 受检/非受检异常 | raise + 任意异常 | throw |
| 异常捕获 | recover(必须在 defer 内) | catch(类型匹配) | except(类型匹配) | catch(类型匹配) |
| 性能 | 1 ns(开放编码) | ~100 ns(try 块) | ~1 μs(进入 except) | ~0 ns(RAII) |
| 设计意图 | "程序无法继续" | 控制流 | 控制流 | 错误传递 + RAII |

> Go **不允许**在非 panic 路径用 recover 替代正常错误处理——`recover()` 在无 panic 时返回 nil。

## 环境准备

- 操作系统:任何支持 Go 的平台
- Go 版本:Go 1.21+(本 demo 用 1.14+ 开放编码 defer 行为)
- 依赖:无第三方依赖

## 运行方式

```bash
cd 09-语言学习/Golang/defer与panic-recover/go
go run defer_panic.go
```

## 关键代码片段

```go
// 1) defer LIFO
func lifo() {
    defer fmt.Println("A")
    defer fmt.Println("B")
    defer fmt.Println("C")
}
// 2) defer 参数立即求值
func snapshot() {
    i := 0
    defer fmt.Println(i) // i=0 快照, 输出 0
    i = 99
}
// 3) defer 修改命名返回值
func double(x int) (result int) {
    defer func() { result *= 2 }()
    return x + 1
}
// 4) panic + recover 标准模式
func safeCall(f func()) (r interface{}) {
    defer func() {
        if r = recover(); r != nil {
            log.Printf("recovered: %v", r)
        }
    }()
    f() // 可能 panic
    return nil
}
// 5) defer 资源清理
func readFile(path string) ([]byte, error) {
    f, err := os.Open(path)
    if err != nil { return nil, err }
    defer f.Close() // 任何路径都执行, 避免泄漏
    return io.ReadAll(f)
}
// 6) panic 值可以是任意类型
panic("string")
panic(42)
panic(errors.New("..."))
panic(struct{ Msg string }{"自定义"})
// 7) runtime.Goexit 优雅退出当前 goroutine(不 panic)
// 8) recover() 只在直接 defer 调用链中生效
defer outer() // outer() 内部 defer 调 recover() 有效
```

## 性能与边界

- **defer 开销**:Go 1.14+ 普通场景 ~1 ns(开放编码);**循环内 defer** 或 **defer in defer** 会退化到 ~30 ns(链表)
- **panic 栈展开**:**O(调用栈深度)**,每层执行 defer 链;最深可影响数千层
- **recover() 调用**:常数时间(~5 ns),只取当前 goroutine 的 panic 值
- **runtime.Goexit**:**不 panic**,但也执行 defer 链;适合 goroutine 退出
- **defer 与 -race**:data race 检测器会自动检查 defer 闭包中的数据竞争(常见 bug: defer 中闭包捕获循环变量)

## 注意事项与常见坑

1. **defer 在循环里的性能**:Go 1.14+ 会退回到链表;改为显式调用 close()
2. **参数立即求值**:`defer f(i++)` `i++` 不会延迟;想延迟用闭包 `defer func() { f(i) }()`
3. **recover 必须直接在 defer 内**:嵌套调用 recover() 也无效(规范明确)
4. **panic 后无法 recover 后的清理**:recover 让 goroutine 继续,但 panic 已经发生的副作用(写了一半的文件、半发送的网络包)需自行处理
5. **不要用 panic 替代 error 返回**:`panic` 应保留给"程序无法继续";可预期的业务错误用 `error` 返回值
6. **goroutine 内未 recover 的 panic**:**整个程序崩溃**,不只 goroutine 退出;**HTTP server** 必须在每个请求 handler 里 recover
7. **defer + return 顺序**:`return x` 实际是 `result = x; ret`,然后 defer 执行,然后真的 ret;**defer 改 result 生效**

## 参考资料

- [The Go Programming Language Specification — Defer statements](https://go.dev/ref/spec#Defer_statements) — defer 求值时机、LIFO、栈展开的规范定义
- [The Go Programming Language Specification — Handling panics](https://go.dev/ref/spec#Handling_panics) — panic/recover 的规范定义与硬性规则
- [Go 1.14 Release Notes — Open-coded defers](https://go.dev/doc/go1.14#runtime) — 开放编码 defer 实现细节
- [Effective Go — defer](https://go.dev/doc/effective_go#defer) — defer 惯用法与 panic/recover 模式
- [The Go Blog — Defer, Panic, and Recover](https://go.dev/blog/defer-panic-and-recover) — Andrew Gerrand 经典教程

---

> 文件清单:`defer_panic.go`(一文件涵盖 defer LIFO/参数求值/命名返回值/recover 硬性规则/资源清理/runtime.Goexit/嵌套 panic/panic 触发源 9 个示例),`go.mod`,`README.md`(本文)。