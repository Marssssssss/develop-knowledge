# Error 处理 — error 接口、fmt.Errorf %w 与 errors.Is/As

> Go 的错误处理范式:`error` 接口 + 多返回值 + 显式 `if err != nil` + Go 1.13+ `errors.Is/As/Unwrap` 链路。**与 try/catch 范式根本不同**——错误是普通值,可包装、可传递、可断言。

## 简介

**Go 用「error 是一等公民」的处理范式**——任何可能失败的函数,把 `error` 当作最后返回值。Go 1.13 起引入 `fmt.Errorf("...%w...", err)` 错误包装,以及 `errors.Is/As/Unwrap` 三件套,让多层级错误既能保留原始信息,又能在最外层判定特定错误。

**关键概念**:
- **`error` 接口**:单方法 `Error() string`,任意实现此方法的类型都是 error
- **哨兵错误**:`var ErrFoo = errors.New("foo")`,作为可比较的"错误常量"
- **错误包装**:`fmt.Errorf("read %s: %w", path, err)` 用 `%w` 包装一个 err,保留因果链
- **`errors.Is(err, target)`**:沿错误链查找是否匹配 target(`==` 或 `Is(error) bool` 方法)
- **`errors.As(err, &target)`**:沿错误链查找类型匹配的第一个 error,并赋给 target
- **`errors.Unwrap(err)`**:取出被包装的底层 error(只 unwrap 一层)

**历史**:`error` 从 Go 1.0 就有;**Go 1.13(2019)** 引入 %w/Is/As/Unwrap;**Go 1.20(2023)** 引入 `errors.Join(...)` 把多 error 合并成一个。`errors.AsType` 在 1.26 引入(泛型版 As)。

## 原理详解

### 1. error 接口与多返回值

```go
type error interface {
    Error() string
}

// 任意实现 Error() 的类型都是 error
func doSomething() (Result, error) {
    if bad {
        return Result{}, errors.New("xxx failed")
    }
    return result, nil
}

// 调用方必须显式检查
res, err := doSomething()
if err != nil { return err }  // error 是普通值
```

### 2. 错误包装树

```
fmt.Errorf("read %s: %w", path, err)   // outer wraps inner
    │
    ▼
fmt.Errorf("decode config: %w", outer)  // outermost wraps outer
    │
    ▼
errors.Unwrap() ──┐
    errors.Unwrap() ──┐
        errors.Unwrap() ──┐
            nil   ←── 链路终止
```

`Unwrap()` 可返回单 error 或 `[]error`(后者来自 `errors.Join`,遍历时按 pre-order DFS)。

### 3. 哨兵 vs 自定义类型 vs 包装

| 范式 | 用途 | 例 |
| --- | --- | --- |
| 哨兵 `var ErrFoo = errors.New("foo")` | 跨包可比较 | `io.EOF`, `sql.ErrNoRows` |
| 自定义 type | 携带上下文 | `type PathError struct{ Op, Path string; Err error }` |
| `fmt.Errorf("...%w...", err)` | 临时包装一层 | 调用者不暴露内部细节 |
| 自定义 `Is(error) bool` | 伪装成其他 sentinel | `syscall.Errno.Is` |
| 自定义 `As(any) bool` | 提供 As 时的类型桥接 | 跨包错误的别名识别 |

### 4. 何时用 Is vs As

- `errors.Is(err, io.EOF)`:判断 err 链路上是否有 io.EOF(**关心"是不是这个错误"**)
- `errors.As(err, &pathErr)`:把链路上第一个 `*fs.PathError` 赋给 pathErr(**关心"提取出这个类型的错误"**)

### 5. errors.Join(Go 1.20+)

```go
err := errors.Join(err1, err2, err3)
// 实现 Unwrap() []error
// 三个 err 都被 errors.Is/As 检查
```

适合"并行执行多任务,所有错误一并返回"的场景。

## 对比 / 选型

| 维度 | Go error 返回值 | Java checked exception | Rust Result<T,E> | Python try/except |
| --- | --- | --- | --- | --- |
| 错误是值 | 是(普通值) | 否(语言级异常) | 是(enum 类型) | 否(异常类) |
| 强制处理 | 否(可能忘检查,但 `errcheck` linter 提示) | 是(编译期) | 是(`?` 操作符) | 否(可能漏 except) |
| 类型信息 | 通过 As 提取 | 异常类型 | E 类型 | 异常类型 |
| 包装 | `fmt.Errorf("...%w...", err)` | `new RuntimeException(cause)` | `anyhow::Error` | `raise X from Y` |
| 性能 | ~30 ns(返回 err) | ~10 μs(异常 throw/catch) | ~0 ns(`Ok` 路径) | ~1-10 μs |

> Go 优势:**无 try/catch 控制流扭曲,错误处理是直线代码**;代价:容易忘 `if err != nil`。

## 环境准备

- 操作系统:任何支持 Go 的平台
- Go 版本:**Go 1.20+**(`errors.Join`);`fmt.Errorf %w` 1.13+ 即可
- 依赖:无第三方依赖

## 运行方式

```bash
cd 09-语言学习/Golang/error处理/go
go run error_wrap.go
```

## 关键代码片段

```go
// 1) sentinel error + errors.New
var ErrNotFound = errors.New("not found")
// 2) 自定义 error type
type NotFoundError struct {
    Resource string
    ID       int
}
func (e *NotFoundError) Error() string {
    return fmt.Sprintf("%s#%d not found", e.Resource, e.ID)
}
// 3) 包装一层
err := fmt.Errorf("query user#42: %w", &NotFoundError{Resource: "user", ID: 42})
// 4) errors.Is 沿链查找
errors.Is(err, ErrNotFound)                       // true
errors.Is(err, &NotFoundError{Resource: "user"}) // true(类型对比)
// 5) errors.As 提取特定类型
var nfe *NotFoundError
errors.As(err, &nfe)                              // nfe.ID == 42
// 6) 哨兵 IS 字段类型
errors.Is(err, io.EOF)                            // false, 不在链上
// 7) errors.Join 并行多任务
err := errors.Join(dbErr, cacheErr, logErr)
// errors.Is(err, sql.ErrNoRows) // 任一匹配即 true
```

## 性能与边界

- **error 返回**:~10-30 ns 一次,远快于 throw/catch(~10 μs)
- **errors.Is/As**:走 O(chain depth) 遍历,常规包装 1-3 层时几乎免费
- **fmt.Errorf %w**:会分配 error 字符串(~100-500 ns),热路径避免
- **errors.Join**:多个 err 形成 error 树,Is/As 走 DFS,深度较深时遍历 O(n)
- **大错误链**:单次 As 可遍历 100 层 wrapper,生产代码应避免过深包装

## 注意事项与常见坑

1. **不要忘 `if err != nil`**:`errcheck` linter 或 `golangci-lint` 配置默认规则检查
2. **不要只 log 不 return**:在低层 log 后忘记往上 return,导致上层继续执行;要么 return,要么包成新 err 返回
3. **不要 string match 错误**:绝不 `err.Error() == "foo"`,改用 `errors.Is(err, ErrFoo)`
4. **不要混用 `==` 和 `errors.Is`**:`==` 不走链路,包装后失效;统一用 `errors.Is`
5. **`%v` vs `%w`**:`fmt.Errorf("...%v...", err)` 只保留字符串,不保留 wrap 链;**`%w` 才是包装**
6. **`errors.As` 第二个参数必须是指针**:传值会编译错误或运行时无效
7. **不要 panic 普通错误**:panic 应保留给"程序无法继续"的情况(如配置损坏、nil 指针);普通错误用 error 返回
8. **`fmt.Errorf("inner: %w", io.EOF)` 后,Is(err, io.EOF) == true**——这是 %w 的核心价值

## 参考资料

- [pkg.go.dev/errors — error 包装/Is/As/Unwrap/Join 完整 API](https://pkg.go.dev/errors) — 官方标准库文档
- [The Go Blog — Working with Errors in Go 1.13](https://go.dev/blog/go1.13-errors) — %w/Is/As 设计动机与最佳实践
- [Effective Go — Errors](https://go.dev/doc/effective_go#errors) — 错误处理惯用法
- [Go FAQ — Why does Go not have exceptions?](https://go.dev/doc/faq#exceptions) — 设计哲学:为何用 error 而非 exception
- [pkg.go.dev/fmt — Errorf %w verb](https://pkg.go.dev/fmt#Errorf) — %w 格式动词的官方定义

---

> 文件清单:`error_wrap.go`(本 demo 一文件涵盖 errors.New/sentinel/自定义 type/fmt.Errorf %w/errors.Is/errors.As/errors.Unwrap/errors.Join/Unwrap() []error/自定义 Is method 10 个示例),`go.mod`,`README.md`(本文)。