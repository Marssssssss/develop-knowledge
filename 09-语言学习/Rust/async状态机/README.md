# async/await 状态机（惰性 Future + Waker + Pin）

> Rust 第二批 · demo 369 · 依据 std::future::Future、async-book 02_execution/02_future、
> std::pin 官方文档实读。
>
> 一句话：**`async fn` 不返回一个「正在跑的任务」，它返回一个**惰性**的状态机值；
> 不 `poll` 就一行代码都不会执行。推进它靠 `poll`，通知它「可以继续了」靠 `Waker`，
> 而 `Pin` 的存在只是为了让**自引用**的 future 敢在自己的字段里存指向自己的指针。**

## 一、原理详解

### 1.1 Future 是惰性的（inert）

std 原文："Futures alone are **inert**; they must be actively `poll`ed for the underlying
computation to make progress."

这和 JS / C# 的 Promise **根本不同**：`async fn` 调用完只是造出一个状态机值，
里面的语句一条都没跑。要跑必须有执行器主动调 `poll`。官方还专门指出
"the `poll` function should not be called repeatedly in a tight loop"，
并把它类比为 `epoll(4)` 而不是 `poll(2)` / `select(2)`（后者的问题是
"all wakeups must poll all events"）。

本 demo 断言 1.x / 4.x 直接固化这一点：构造后 `polls == 0`、时间没有前进；
**不驱动事件源就永远挂起**。

### 1.2 真实签名为什么是 `Pin<&mut Self>` + `&mut Context<'_>`

async-book 先给一个简化版，再说明两处改动：

```rust
// 简化版
fn poll(&mut self, wake: fn()) -> Poll<Self::Output>;
// 真实版
fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>;
```

| 改动 | 官方理由 |
| --- | --- |
| `&mut self` → `Pin<&mut Self>` | 允许**不可移动**的 future；async-book 原文："it allows us to create futures that are immovable"，自引用 `struct MyFut { a: i32, ptr_to_a: *const i32 }` 才有意义 |
| `wake: fn()` → `cx: &mut Context<'_>` | `fn()` 是纯函数指针，"it **can't store any data about which Future called wake**"；`Context` 提供 `Waker`，才能在「上千条连接」里只唤醒对应的那一个 task |

### 1.3 Waker 的三条契约（std 原文）

1. **只保留最近一次的 Waker**："on multiple calls to `poll`, only the Waker from the
   `Context` passed to the **most recent** call should be scheduled to receive a wakeup."
   本 demo 断言 3.x：用两个不同 `Context` 各 poll 一次，事件到达时**只有后一个**被唤醒。
2. **完成后不得再 poll**："Once a future has finished, clients should not poll it again."
   后果未定义——"may panic, block forever, or cause other kinds of problems"。
   `Join` 组合子的做法是把已完成的子 future 字段置 `None`
   （async-book 原文："This prevents us from polling a future after it has completed"）。
3. **`poll` 要尽快返回**：不允许阻塞；确定会耗时的活要丢给线程池。

### 1.4 组合子是无分配状态机

async-book：`Join` 把两个子 future 存在自己的字段里，`poll` 时交错推进，
"without needing intermediate allocations"。顺序版 `AndThenFut` 同理，
差别只在于 `first` 没 Ready 就 `return Poll::Pending`（用 return 打断流程）。

本 demo 用两个可计数的结构指标把它变成断言：
- **子 future 是内联持有**（`j.a is a`，不是装箱分配）—— 断言 6.2；
- **跑完一圈属性集合不变**（没有「每次 poll 分配一份新状态」）—— 断言 6.1。

### 1.5 没有 Waker 会怎样

async-book 原文："Without `wake()`, the executor would have no way of knowing when a
particular future could make progress, and would have to be **constantly polling every
future**."

本 demo 做了对照实验（断言 2.x）：同一个 100 tick 的定时器——
Waker 驱动只需 **2 次** poll（1 次 Pending + 1 次 Ready）；
盲轮询需要 **101 次**。

### 1.6 Pin：为什么要「钉住」

- **moving** = 编译器逐字节把值从一个地址拷到另一个地址。
- **pinning** = 保证值在生命周期内地址不变，于是"指向它的裸指针一直有效"。
- std 原文：pinning 是 "necessary to implement safe interfaces on top of things like
  **self-referential types** and intrusive data structures"。
- 大多数类型**自动**实现 `Unpin`（搬动无害），`Pin` 对它们形同虚设。
  想让一个类型**不**是 `Unpin`，标准做法是加一个 `PhantomPinned` 字段（std::pin 文档的
  `AddrTracker` 例子就是这么写的）。

本 demo 断言 9.x：自引用 future 未搬动时指针有效 → 模拟一次 move → 指针悬垂；
被 `Pin` 包住后拿不出所有权（`move_out` 抛错），但 `as_mut()` 仍可正常使用。

## 二、对比

| 维度 | Rust `Future` | JS `Promise` |
| --- | --- | --- |
| 何时开始执行 | **被 poll 时**（惰性） | 构造即开始（eager） |
| 取消 | 直接 drop 掉即可 | 无法真正取消 |
| 调度 | 运行时（executor）自行决定 | 微任务队列 |
| 分配 | 可做到零分配（状态机内联） | 每次 then 一个闭包对象 |

| 维度 | `async fn` | 线程 |
| --- | --- | --- |
| 栈 | 无独立栈（状态存在 future 里） | 独立栈（MB 级） |
| 切换成本 | 一次 `poll` 调用 | 内核上下文切换 |
| 适合 | I/O 密集、海量连接 | CPU 密集、少量任务 |
| 阻塞后果 | 卡住整个 executor 线程 | 只卡自己 |

## 三、环境

- Rust 2021 edition，纯标准库（`Wake` 需 1.51+，`poll_fn` 需 1.64+）。**不依赖 tokio**。
- Python 3 模型（含 30 项断言）：`python async_check.py`。

## 四、运行方式

```bash
cd rust && cargo run       # 会打印顺序 await 与并发 join 的实测耗时
cd python && python async_check.py
```

`rust/compile_fail/` 三个文件故意编译失败：

| 文件 | 报错 | 说明 |
| --- | --- | --- |
| `e0277_await_outside_async.rs` | E0728 | `.await` 只能在 async 上下文（它是挂起点，不是方法调用） |
| `e0277_poll_needs_pin.rs` | 签名不匹配 | `poll` 的接收者必须是 `Pin<&mut Self>` |
| `e0277_move_out_of_pin.rs` | E0507 | 钉住之后拿不出所有权（除非 `Unpin`） |

## 五、关键代码

**只保留最近一次 Waker（Rust，真代码）**

```rust
fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
    let mut shared = self.shared.lock().unwrap();
    if Instant::now() >= shared.deadline {
        Poll::Ready("timer fired".to_string())
    } else {
        shared.waker = Some(cx.waker().clone());   // 覆盖式登记
        Poll::Pending
    }
}
```

**Join：完成后置 None，杜绝二次 poll**

```rust
if o1.is_none() {
    if let Poll::Ready(v) = f1.as_mut().poll(cx) { o1 = Some(v); }
}
```

**异步的收益（Python，真跑）**：Waker 驱动 2 次 poll vs 盲轮询 101 次。

## 六、性能边界

- **每个 `.await` 都是一个状态**：`async fn` 编译出的状态机大小≈所有挂起点处存活变量之和。
  递归的 async fn 必须 `Box::pin`（否则类型无限大）。
- **future 越大，spawn 越贵**：跨 `await` 持有大缓冲区会让每个 task 都背着一个大结构体；
  把大对象放到 `Arc` 里或改成流式处理。
- **`Pin<Box<dyn Future>>` 有一次堆分配**：异构集合（`Vec<Box<dyn Future>>`）逃不掉，
  同构的用泛型或 `join!` 宏可以零分配。
- **同步阻塞调用（`std::thread::sleep`、同步 IO）会卡死整个 executor 线程**：
  官方 `poll` 契约要求"strive to return quickly"，耗时活要 `spawn_blocking` 到线程池。
- **`thread::park` 版 block_on 会被虚假唤醒**：本 demo 的执行器为教学简化，
  真实 runtime 用 `parker` + 原子状态避免漏唤醒。

## 七、注意事项与常见坑

1. **以为 `async fn()` 已经开始跑了** —— 它只是造了个值。忘了 `await` / `spawn` 就等于什么都没做
   （编译器会给 `unused` 警告，但不会报错）。
2. **在 async 里做同步阻塞** —— 会把整个 executor 线程占满，其他 task 全部饿死。
3. **把 `MutexGuard` 跨 `await` 持有** —— 极其常见的死锁源：guard 不是 `Send`，
   且持锁挂起会让别人长时间等。要用就换 `tokio::sync::Mutex`，或缩小临界区。
4. **忘了重新登记 Waker** —— 每次 `poll` 返回 `Pending` 前都必须 `cx.waker().clone()` 存好，
   否则永远没人唤醒你（future 静默挂起，不报错、不超时）。
5. **poll 完成后又被 poll** —— 违反契约，后果未定义。组合子必须自己记账（置 `None`）。
6. **以为 `Pin` 会让数据不可变** —— `Pin` 只禁止**移动**；只要 `T: Unpin`（或有
   `get_unchecked_mut`）内容照样能改。
7. **struct 里存自引用不用 `PhantomPinned`** —— 类型仍是 `Unpin`，编译器允许搬动，
   自引用指针立刻悬垂。这正是 `PhantomPinned` 存在的唯一理由。
8. **递归 async** —— 必须 `Box::pin` 打破无限大小，否则 E0072。

## 八、参考资料（实际阅读）

- [std — `future::Future`](https://doc.rust-lang.org/std/future/trait.Future.html)
  —— 真实签名 `fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>`、
  "Futures alone are inert"、"only the Waker from the Context passed to the most recent
  call"、"Once a future has finished, clients should not poll it again"、与
  `epoll(4)` 的类比。
- [async-book — The Future Trait](https://rust-lang.github.io/async-book/02_execution/02_future.html)
  —— `SimpleFuture` 简化版与两处改动的理由、SocketRead 示例、Join / AndThenFut 的
  "allocation-free state machines" 写法与"完成后置 None"的官方注释、immovable futures。
- [async-book — Waking Up](https://rust-lang.github.io/async-book/02_execution/03_wakeups.html)
  —— 无 waker 时 "would have to be constantly polling every future"。
- [std — `pin` module](https://doc.rust-lang.org/std/pin/index.html)
  —— moving / pinning 的定义、address sensitivity、self-referential struct 与
  intrusive doubly-linked list 两个例子、`AddrTracker` 里用 `PhantomPinned` 摘掉自动
  `Unpin` 的官方写法、structural vs non-structural pinning 的取舍。
- [The Book — ch17-00 Fundamentals of Asynchronous Programming](https://doc.rust-lang.org/book/ch17-00-async-await.html)
  —— 并行与并发的区分、blocking 与 I/O-bound 的动机说明。
