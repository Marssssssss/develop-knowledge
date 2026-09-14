# context 包

## 简介

- `context` 包解决的问题:**把请求范围的取消信号、超时和元数据沿调用链跨 API 边界传给所有相关 goroutine**——一个请求被取消或超时,为它干活的全部 goroutine 应当快速退出以回收资源。
- 关键概念:
  - **Context 接口**:`Done() <-chan struct{}`(关闭即广播取消)/ `Err()`(取消原因)/ `Deadline()` / `Value()`
  - **派生树**:`Background()` 是根;`WithCancel/WithTimeout/WithValue` 派生子节点;**父取消传播到所有后代**
  - **没有 Cancel 方法**:收信号的一方不是发信号的一方;取消能力(CancelFunc)单独交给派生者
  - **select 模式**:同时等工作结果 channel 与 `ctx.Done()`,谁先到听谁的
- 历史背景:Google 内部实践沉淀,2014 年由 Sameer Ajmani 发表官方博客;Google 要求"call path 上的每个函数第一个参数都是 Context"。

## 原理详解

### 接口与派生树

```text
Background(根:永不取消)
   │ WithTimeout(100ms)
   ├─ ctx1 ──┬─ WithCancel → child ── WithCancel → grand
   │         └─ WithValue(userIP)
   ctx1.Done 关闭 => child/grand 的 Done 全部关闭(向下传播)
   child 取消 => 不影响 ctx1,更不影响 Background(向上隔离)
```

分步机制:

1. `WithCancel(parent)`:返回 parent 的副本 + CancelFunc;`parent.Done` 关闭或 `cancel()` 调用,新 Context 的 Done channel 被关闭。
2. `WithTimeout(parent, d)`:再叠加定时器;新 Deadline = **min(now+d, parent 的 deadline)**;timer 还在跑时调用 cancel 会释放其资源(所以要 `defer cancel()`)。
3. **传播实现**:Done 返回的 channel 被**关闭**(而非发值)——一次关闭对所有监听者同时可见,这正是取消的广播语义。
4. `Err()`:Done 关闭**之后**返回原因(`context.Canceled` / `context.DeadlineExceeded`);未关闭时为 nil。
5. `Value(key)`:沿树向上查找;key 必须支持相等比较。

### select 取消模式(官方 httpDo 的骨架)

```go
select {
case <-ctx.Done():
    <-c // 等工作 goroutine 返回,回收资源
    return ctx.Err()
case err := <-c:
    return err
}
```

### 使用规则(官方博客结论)

- Context 作为**第一个参数**传递,命名惯用 `ctx`;不要存进 struct(框架适配器除外)。
- 传给任意数量 goroutine 都安全(并发安全),一次 cancel 广播全体。
- `WithValue` 只放**请求范围**数据(用户身份、凭据),不当可选参数通道。
- Deadline 的用途:先判断"剩余时间值不值得开工",再给 I/O 设超时。

## 对比 / 选型

| 方案 | 取消传播 | 超时 | 请求元数据 | 说明 |
| --- | --- | --- | --- | --- |
| 裸 channel + close | 手工搭树 | 手工 timer | 无 | context 之前的民间方案(Tomb 的 Dying channel 等) |
| **context** | **树形自动传播** | WithTimeout/WithDeadline | WithValue | 标准库统一,HTTP/RPC/DB 驱动全接入 |

## 环境准备

- 操作系统:任意
- 语言版本:Go 1.21+
- 依赖:无(纯标准库)

## 运行方式

### Go

```bash
cd go
go run context_demo.go
```

## 关键代码片段

取消传播(对应原理第 1/3 步):

```go
root, cancel := context.WithCancel(context.Background())
child, _ := context.WithCancel(root)
grand, _ := context.WithCancel(child)
// ... 三个 goroutine 各自 <-x.Done() ...
cancel() // root.Done、child.Done、grand.Done 同时关闭
```

select 竞争 + 资源回收(对应 httpDo 模式):

```go
select {
case <-ctx.Done():
    <-c              // 取消后仍等 worker 返回,不泄漏 goroutine
    return ctx.Err() // context deadline exceeded / canceled
case r := <-c:
    return r
}
```

## 性能与边界

- Done channel 关闭是一次性 O(1) 广播;监听者数量不限。
- 每层 With* 派生都有少量对象与 channel 开销,树深度通常 ≤ 请求调用链深度,可忽略。
- `WithValue` 是链式向上查找,深树高频取值有遍历成本;只放请求范围低频数据。

## 注意事项与常见坑

- **坑 1:忘了 `defer cancel()` 泄漏**。WithTimeout 的 timer 不 cancel 就要跑到超时为止。官方惯例:拿到 cancel 立刻 `defer cancel()`,即使不打算提前取消。
- **坑 2:把 cancel 交给子操作**。CancelFunc 不在接口上、Done 是 receive-only——子不该能取消父;子需要独立取消就自己 `WithCancel(parent)` 派生。
- **坑 3:`ctx.Err()` 在 Done 关闭前查是 nil**。判断"是否已被取消"用 `select { case <-ctx.Done(): ... default: }` 或直接看 Err 是否非 nil。
- **坑 4:WithValue 用 string/公开类型当 key**。跨包冲突 + 被别的包覆盖。官方模式:包内未导出类型 `type ctxKey int` + 常量。
- **坑 5:select 取消分支不等 worker**。直接 return 会把慢 worker 丢在后台(泄漏);官方 httpDo 取消后仍 `<-c` 等它退出。

## 参考资料(实际阅读过的权威来源)

- [Go Concurrency Patterns: Context — Sameer Ajmani, go.dev/blog/context](https://go.dev/blog/context) — Context 接口定义与逐方法注释、派生树语义、httpDo/userip 完整示例、"没有 Cancel 方法"的设计理由、Google 首参实践。
- [context — pkg.go.dev](https://pkg.go.dev/context) — 标准库 API 索引(博客声明"godoc is authoritative")。
