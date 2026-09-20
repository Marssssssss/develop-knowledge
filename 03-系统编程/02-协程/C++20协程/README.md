# C++20 协程：把栈帧搬进堆上的状态机

## 一、简介

C++20 的协程是**无栈**的：*"Coroutines are stackless: they suspend execution by returning to the caller, and the data that is required to resume execution is stored separately from the stack."*（cppreference）

这句话决定了它的全部性格。有栈协程（ucontext / goroutine / 本大类下的「最小有栈协程」demo）切换时换的是 CPU 栈指针，谁都能在函数任意深度挂起；无栈协程只能**在协程体自己的这一层**挂起，挂起时必须把「接下来从哪继续」以及所有跨挂起点的局部变量，搬到一个堆上的**协程状态（coroutine state）**里。

本 demo 把这个状态机与 awaiter 协议做成可执行模型，逐条验证。

## 二、原理详解

### 2.1 协程状态里到底装了什么

cppreference 列出的内容：promise 对象、**参数的副本**、当前挂起点的表示（"some representation of the current suspension point, so that a resume knows where to continue, and a destroy knows what local variables were in scope"）、以及跨越挂起点的局部变量。

它是 *"dynamically-allocated storage (unless the allocation is optimized out)"* —— 也就是说**每次调用协程都可能是一次堆分配**（HALO 优化能消掉，但不是语言保证）。

模型实测：构造协程 → `allocations == 1`、`live == 1`；跑完（final_suspend 是 `suspend_always`）后状态**仍在**；显式 `destroy()` 之后才归零。这就是 `final_suspend` 为什么通常必须是 `suspend_always`：如果是 `suspend_never`，状态会在 `co_return` 时自行销毁，但外部持有的 handle 立刻失效。

### 2.2 启动：initial_suspend 决定惰性还是立即

启动顺序是：`operator new` → 拷贝参数 → 构造 promise（拿返回对象）→ `co_await promise.initial_suspend()`。

*"Typical Promise types either return std::suspend_always, for lazily-started coroutines, or std::suspend_never, for eagerly-started coroutines."*

模型实测：惰性的协程构造完**一行都没跑**，第一次 `resume()` 才进入函数体；立即启动的在构造时就跑到了第一个挂起点。`task` 一般要惰性（等被 `co_await` 才跑），`generator` 也惰性（等第一次 `next()`）。

### 2.3 co_await 的三件套与四种返回值

```
awaiter.await_ready()    → 短路：已知就绪就不必挂起
awaiter.await_suspend(h) → 挂起期间安排恢复
awaiter.await_resume()   → 无论是否挂起都会调用，其结果就是 co_await 表达式的值
```

`await_suspend` 的返回类型决定控制流（模型逐条验过）：

| 返回 | 行为 | 实测 |
| --- | --- | --- |
| `void` | 控制立刻回到调用者/resumer，本协程保持挂起 | 挂起，`suspend_calls == 1` |
| `bool`（true） | 同上 | 挂起 |
| `bool`（false） | 「已安排好」→ **立即恢复** | 一次 `resume()` 就跑完 |
| `coroutine_handle` | 恢复那个协程（可链式） | 见对称转移 |

另外两条容易漏：

- **恢复点在 `await_resume()` 之前**（*"if the coroutine was suspended ... the resume point is immediately before the call to awaiter.await_resume()"*）；
- **进入 `await_suspend` 时协程已经完全挂起**（*"the coroutine is fully suspended before entering awaiter.await_suspend()"*），所以把句柄交给别的线程、让对方在 `await_suspend` 返回前就恢复自己是**合法的**——代价是 `await_suspend` 之后不能再动 `*this`（可能已被析构）。

### 2.4 对称转移：为什么它能防栈溢出

最自然的写法是在 `await_suspend` 里直接 `other.resume()`。但那样每转移一次就多压一层栈帧 —— 一个百万次转移的链会把栈打爆。

正确写法是**返回对方的句柄**，由调用者/resumer 去恢复它，这在实现上是一次**尾调用**：*"if await_suspend returns a coroutine handle for some other coroutine, that handle is resumed (by a call to handle.resume()) ... note this may chain to eventually cause the current coroutine to resume."*

模型实测（64 节链）：

| 写法 | 最大调用深度 |
| --- | --- |
| 返回句柄（对称转移） | **1** |
| 在 `await_suspend` 里 `resume()` 下一个 | **64** |

差 63 层，且这个差距随链长线性增长。

### 2.5 co_return 与 final_suspend

`co_return` 的顺序：调用 `return_void()` / `return_value(expr)` → **逆序销毁**自动存储期的局部变量 → `co_await promise.final_suspend()`。

而 *"It's undefined behavior to resume a coroutine from this point."* —— 所以生成器模式把 `final_suspend` 设成 `suspend_always`，把「销毁」的责任交给外部（析构器里 `h_.destroy()`）；`task` 模式常设成 `suspend_never` 或把 continuation 存起来在 final_suspend 里恢复，让销毁自动化。

模型两项都验了：`suspend_always` 下 final 之后 `destroy()` 才释放；`suspend_never` 下状态自行销毁，再 `resume()` 直接抛「UB」；重复 `destroy()` 也能拦。

### 2.6 无栈的直接后果：不能从嵌套函数挂起

协程里调用一个普通函数，那个函数**没法挂起协程** —— 它没有协程状态，也没有恢复点。语言层面的表现是：函数体里出现 `co_await` / `co_yield` / `co_return` 就**使该函数成为协程**，于是它的返回类型必须能给出 `promise_type`。在返回 `void` 的辅助函数里写 `co_await` 是编译错误。

模型用一个「返回类型必须能提供 promise_type」的检查表达了这一点，并断言它会被拒。

### 2.7 按引用传参的悬垂

*"by-reference parameters remain references (thus, may become dangling, if the coroutine is resumed after the lifetime of referred object ends)"*。按值参数会被移动/拷贝进协程状态，按引用参数只是个引用。

模型实测：调用方对象存活时 `byref_alive() == True`，对端一销毁就变 `False`（此时恢复就是 use-after-free）。同理，协程体里的局部变量若来自 lambda 捕获，lambda 析构后也一样 —— 标准里就有 `S{0} destroyed` / `lambda destroyed` 之后再 `h.resume()` 的 UB 例子。

## 三、对比

| 维度 | 无栈（C++20） | 有栈（ucontext / goroutine） |
| --- | --- | --- |
| 挂起位置 | 只能在协程体这一层 | 任意函数深度 |
| 状态存放 | 堆上协程状态（可 HALO 优化掉） | 独立栈 |
| 挂起代价 | 一次返回到调用者 | 换栈指针 |
| 内存 | 一个协程一个状态对象（小） | 一个协程一段栈（KB 级） |
| 能否从嵌套函数挂起 | **不能** | 能 |

| `await_suspend` 返回 | 控制流 | 用途 |
| --- | --- | --- |
| void | 挂起 | 交给 executor / 别的线程 |
| true | 挂起 | 同 void，便于条件判断 |
| false | 立即恢复 | 「已安排好」，省一次挂起 |
| handle | 转移给别的协程 | 对称转移 / 尾调用 |

## 四、环境与运行

```bash
cd 03-系统编程/02-协程/C++20协程
python selfcheck_coro.py     # 34 项断言全绿
```

`coroutine_demo.cpp` 需要 C++20（`g++ -std=c++20`），本机无工具链，人工审查未编译。

## 五、关键代码

生成器 promise 的四个钩子：

```cpp
std::suspend_always initial_suspend() noexcept { return {}; }   // 惰性：构造后不跑
std::suspend_always yield_value(T v) noexcept { value_ = v; return {}; }
void return_void() noexcept {}
std::suspend_always final_suspend() noexcept { return {}; }     // 挂起，等外部 destroy
```

对称转移的骨架：

```cpp
std::coroutine_handle<> await_suspend(std::coroutine_handle<> caller) noexcept {
    // 不要在这里 resume 别人；返回句柄让调用者尾调用恢复它
    return other_handle;
}
```

## 六、性能边界

- 协程状态**默认是一次堆分配**；是否被优化掉（HALO）取决于编译器能否证明协程的生命周期嵌套在调用者之内 —— 标准不保证。
- 挂起/恢复的成本是「一次函数返回 + 一次函数调用」，比线程切换低几个数量级，但**比普通函数调用高**（多一次间接跳转、状态在堆上可能不进缓存）。
- `await_ready()` 的存在就是为了省掉不必要的挂起：*"a short-cut to avoid the cost of suspension"*。模型里 `ready == true` 的用例中 `await_suspend` 调用次数为 0。
- 官方不给出「协程 vs 回调快多少」的定量结论，本 demo 也不编造数字。

## 七、注意事项与常见坑

1. **忘了 `destroy()`** —— `final_suspend = suspend_always` 时状态要手工释放，否则就是泄漏（模型里 `live` 始终为 1）。
2. **`final_suspend` 之后还 `resume()`** —— 标准明说是 UB。
3. **在 `await_suspend` 里 `resume()` 下一个协程** —— 能跑，但栈深线性增长；长链会爆栈。
4. **`await_suspend` 之后还访问 `*this`** —— 协程可能已被别的线程恢复并执行完，awaiter 对象已析构。
5. **按引用/捕获传参** —— 协程只留引用，对端销毁后恢复即 use-after-free；要跨挂起点存活就必须按值。
6. **以为能在辅助函数里挂起** —— 无栈协程做不到；要么把辅助函数也写成协程（返回类型要给 `promise_type`），要么在协程体里显式 `co_await`。
7. **以为 `await_ready()` 返回 true 就跳过 `await_resume()`** —— 恰恰相反，它一定会被调用，值就是从这来的。
8. **忘了 `unhandled_exception`** —— 异常从协程体逸出会进 `promise.unhandled_exception()`（默认 `terminate`）；模型里异常接住后仍走到 final_suspend。

## 八、参考资料（本轮实读）

- cppreference《Coroutines》 — https://en.cppreference.com/w/cpp/language/coroutines （stackless 定义、协程状态的组成与分配、`initial_suspend`/`final_suspend`、`co_await` 三种 `await_suspend` 返回、*"fully suspended before entering await_suspend"*、按引用参数悬垂的官方示例、`final_suspend` 后 resume 的 UB）
