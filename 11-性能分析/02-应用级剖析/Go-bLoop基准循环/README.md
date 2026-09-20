# `testing.B.Loop`：Go 1.24 的新式基准循环

> 从 `for range b.N` / `for i := 0; i < b.N; i++` 换成 `for b.Loop()`。
> 官方给的两个理由是：**基准函数每个 `-count` 只执行一次**（昂贵的 setup/cleanup 只做一次），
> 以及**循环体里函数调用的实参和结果被保活**（编译器没法把循环体整体优化掉）。

## 一、为什么旧写法麻烦

`b.N` 风格的基准**函数会被反复调用**：`launch()` 不断调整 `b.N` 重试，直到跑够 `-benchtime`
（默认 1 秒）。于是：

- 写在循环体**外面**的 setup 会**每轮重做一次**——造 100 万条测试数据这种 setup 会被执行 N 轮。
- 想排除 setup 就得手动 `b.ResetTimer()`，想在结束前停下就得 `b.StopTimer()`，
  忘了就把 setup/cleanup 算进 `ns/op`。
- 循环体里调用 `f(x)` 而结果没被使用 ⇒ 编译器可能把整段**优化掉**，测到的是空循环。

`for b.Loop()` 把这三件事都接管了。

## 二、Loop 的状态机（照 `benchmark.go` 逐行移植）

快路径只有一次比较——这是它能 inline 的前提：

```go
func (b *B) Loop() bool {
    if b.loop.i < b.loop.n {   // 快路径
        b.loop.i++
        return true
    }
    return b.loopSlowPath()
}
```

慢路径的三条分支（源码注释明确列出）：

1. **首次调用**（`i == 0 && n == 0`）：
   - `-benchtime=Nx` 固定次数 ⇒ `n = N`；否则 `n = 1` 起步；
   - **`b.N = 0`**（源码注释：循环内不使用 `b.N`，避免混淆）；
   - **`ResetTimer()`** ⇒ 循环之前的 setup 不计入。
2. **固定次数模式**：`i == N` 时直接结束，不再按时间标定；源码甚至对「提前进慢路径」直接 `panic`。
3. **固定时长模式**：`stopOrScaleBLoop()` —— 到点就停，否则用 `predictN` 放大目标迭代数。

结束时：`StopTimer()` → **`b.N = int(b.loop.n)`** → `loop.done = true`。
所以循环结束后的 cleanup 也不计入，而且 `b.N` 这时候才可用（可以用来算自定义平均指标）。

## 三、predictN：四条夹紧规则

```go
n := goalns * prevIters / prevns   // 先乘后除
n += n / 5                         // 多跑 20%
n = min(n, 100*last)               // 一次最多涨 100 倍
n = max(n, last+1)                 // 至少比上次多一个
n = min(n, maxBenchPredictIters)   // 上限 1e9（也保证 32 位不溢出）
```

**「先乘后除」是刻意的**（源码注释）：基准很快时 `prevIters ≈ prevns`，
先除会得到 0 或 1，把执行时间的数量级信息抹掉。

demo 里的四条断言（默认 1 秒目标）：

| 场景 | 理论值 | 实际 |
| --- | --- | --- |
| 1 次耗时 1µs | 1,000,000 | **100**（被 `100*last` 夹住） |
| 100 次耗时 1ms | 120,000 | **10,000**（被 `100*last` 夹住） |
| 1000 次耗时 100ms | 12,000 | **12,000**（没被夹） |
| 1e8 次耗时 1ms | 1.2e11 | **1e9**（被上限夹住） |

另外 `prevns == 0` 时按 1 处理——源码注释指名是 issue 70709 的除零绕行。

## 四、毒化位：为什么 `StopTimer` 之后调 Loop 会直接炸

```go
const (
    loopPoisonTimer = uint64(1) << (63 - iota)      // iota=0 ⇒ 1<<63
    loopPoisonMask  = ^uint64((1 << (63 - (iota-1))) - 1)  // iota=1 ⇒ 只剩最高位
)
```

计时器被 `StopTimer()` 关掉时，runtime 把 `loop.i` 的最高位置 1。
快路径的 `i < n` 会因为这个巨大数值而必然失败 ⇒ 强制走慢路径 ⇒
慢路径先检查 `!b.timerOn` ⇒ `b.Fatal("B.Loop called with timer stopped")`。

**结论：`for b.Loop()` 的循环体内不能 `b.StopTimer()`**——那正是它帮你自动管的那件事。
慢路径还会检查「除了已知的毒化位之外还有别的位被置」⇒ `panic("unknown loop stop condition")`。

## 五、KeepAlive 编译期变换的三个条件

> Within the body of a `for b.Loop() { ... }` loop, arguments to and results from function calls
> and assigned variables within the loop are kept alive... **This applies only to statements
> syntactically between the curly braces of the loop, and the loop condition must be written
> exactly as `b.Loop()`.**

即：

1. 循环条件必须**字面**写成 `b.Loop()`——写成 `b.Loop() && extra` 就失效；
2. 语句必须真的写在**花括号内**；
3. 实现方式是编译期给这些变量包一层 `runtime.KeepAlive`。

这也解释了官方为什么把它叫「less error-prone」：以前要靠 `sink = f(x)` + 包外 `var sink T`
这种手工保活，现在编译器替你做。

## 六、与 `b.N` 风格的量化对比（demo 里的模型）

设定：目标时长 1ms、每次迭代 1µs、setup 500µs。

| | `for b.Loop()` | `for range b.N` |
| --- | --- | --- |
| 基准函数被调用次数 | **1** | >1（每轮重新标定） |
| setup 执行次数 | 1 | 与调用轮数相同 |
| setup 是否计入 `ns/op` | 否（首次调用里 ResetTimer） | **是**（每轮都要重做，且在计时区间内） |
| cleanup 是否计入 | 否（返回 false 时 StopTimer） | 需手动 StopTimer |

昂贵的 setup（造大数组、起服务、读文件）在旧写法下会被**重复执行并计入耗时**，
这是「同一个基准在不同机器上 ns/op 差很多」的常见来源。

## 七、运行

```bash
python python/bloop_bench.py   # 27 条断言
cd go && go run .               # 同语义 Go 版（本机无工具链，人工审查）
```

## 八、注意事项与常见坑

1. **循环体内不要用 `b.N`**——它被刻意置 0。要总迭代数就等循环结束后再读。
2. **循环体内不要 `StopTimer` / `StartTimer`**——会触发毒化位直接 Fatal。
   真要排除某段，把它移出循环体。
3. **条件必须原样写 `b.Loop()`**，别顺手加 `&&` 或包一层函数，否则保活失效、
   循环体可能被整体优化掉（测出来漂亮得离谱就是这个症状）。
4. **`-benchtime=Nx` 与 `-benchtime=1s` 是两条不同路径**：固定次数不再按时间标定。
5. `predictN` 的 100 倍夹紧意味着**从 1 次爬到百万次需要好几轮**，
   对「每轮 setup 很贵」的基准，这个爬坡成本在旧写法里是实打实付出的。
6. `b.N` 在读之前循环必须已经结束（`done` 之后），否则读到的是 0。

## 参考资料（本轮实际读过）

- [Go 1.24 Release Notes — New benchmark function（`testing.B.Loop`）](https://go.dev/doc/go1.24)
- [golang/go — src/testing/benchmark.go（Loop / loopSlowPath / stopOrScaleBLoop / predictN / benchTime 默认值）](https://raw.githubusercontent.com/golang/go/master/src/testing/benchmark.go)
- [pkg.go.dev — testing（B.N / ResetTimer / StopTimer 语义）](https://pkg.go.dev/testing)
