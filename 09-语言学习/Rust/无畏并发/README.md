# 无畏并发（Send / Sync 自动 trait）

> Rust 第二批 · demo 368 · 依据 The Book ch16-03 / ch16-04、std::marker::{Send, Sync}、
> std::sync::MutexGuard、std::cell::RefCell 官方文档实读。
>
> 一句话：**Rust 的并发安全不是靠标准库里某个锁写得好，而是靠两个「语言内置」的标记
> trait——`Send`（所有权可跨线程转移）与 `Sync`（引用可跨线程共享）。它们是 auto trait，
> 由编译器按字段/成分自动推导；能编译过就排除了数据竞争，但**排除不了死锁**。**

## 一、原理详解

### 1.1 两个 trait 的官方定义与精确关系

- `Send`："Types that can be **transferred** across thread boundaries."
- `Sync`："Types for which it is safe to **share references** between threads."
  **精确定义：`T: Sync` 当且仅当 `&T: Send`。**

std 文档给出四条关系（本 demo 断言 5.1–5.4 逐条固化）：

```
&T     是 Send  ⟺  T 是 Sync
&mut T 是 Send  ⟺  T 是 Send
&T     是 Sync  ⟺  T 是 Sync
&mut T 是 Sync  ⟺  T 是 Sync
```

第三条有个官方自己都称 "somewhat surprising" 的推论：**`&mut T` 是 `Sync`（只要 `T: Sync`）**。
直觉上「可变引用共享出去」听起来就能并发写，但关键在于 `& &mut T` 里的 `&mut T`
**退化成只读**，等价于 `& &T`，所以没有数据竞争。

### 1.2 自动推导规则（"composed entirely of"）

官方原文："Any type composed entirely of `Send` types is automatically marked as `Send` as
well." —— 结构体/元组的自动实现是**全员通过制**：只要有一个字段不是，整体就不是
（断言 6.2）。这也是为什么往结构体里塞一个 `Rc` 字段，整个类型突然就不能跨线程了。

本 demo 实现的推导表：

| 类型 | Send | Sync | 依据 |
| --- | --- | --- | --- |
| 基本类型（`i32`/`String`/…） | ✅ | ✅ | 官方：almost all primitive types |
| 裸指针 `*const T` / `*mut T` | ❌ | ❌ | 官方：raw pointers 是例外 |
| `Rc<T>` | ❌ | ❌ | 非原子计数，并发 clone → UB |
| `Arc<T>` | `T:Send+Sync` | 同左 | 原子计数（有性能代价） |
| `Cell<T>` / `RefCell<T>` | `T:Send` | ❌ | 内部可变性非线程安全 |
| `Mutex<T>` / `RwLock<T>` | `T:Send` | `T:Send` | 官方：Mutex<T> implements Sync |
| `MutexGuard<'_, T>` | **❌** | `T:Sync` | std `impl !Send` / `impl<T: Sync> Sync` |
| `&T` | `T:Sync` | `T:Sync` | std 四条关系 |
| `&mut T` | `T:Send` | `T:Sync` | std 四条关系 |

两张必须记住的非对称牌：

1. **`RefCell<T>` 是 Send 但不是 Sync** → 于是 `Arc<RefCell<i32>>` **连 Send 都不是**
   （因为 `Arc<T>: Send` 要求 `T: Send + Sync`）。这正是「想共享可变状态必须换 Mutex」的
   真正原因（断言 3.4）。
2. **`MutexGuard` 不能离开加锁的线程，但 `&MutexGuard` 可以**。std 给的 `!Send` 理由是
   "prevent it being dropped from a different thread than it was locked in"。

### 1.3 `Arc<Mutex<T>>` 是怎么拼出来的

官方 Listing 16-13 → 16-14 → 16-15 是一条完整的教学链：

| 版本 | 结果 | 说明 |
| --- | --- | --- |
| 直接 `move` 一个 `Mutex<i32>` 进 10 个线程 | E0382 | 所有权已被上一轮循环移走 |
| 包一层 `Rc<Mutex<i32>>` | **E0277** | `Rc` 不 Send（计数非原子） |
| 换成 `Arc<Mutex<i32>>` | 编译通过，`Result: 10` | `Arc` 原子计数 + `Mutex` 提供 Sync |

### 1.4 Mutex 的两条规则被类型系统接管

官方说 Mutex 难用是因为要记住两条：**用之前先拿锁**、**用完要解锁**。Rust 的接管方式：

- 类型是 `Mutex<i32>` 而**不是** `i32` —— 不调 `lock()` 根本碰不到里面的值，规则一由类型保证。
- `lock()` 返回 `MutexGuard`，它 `impl Deref`（能当内部值用）、`impl Drop`（离开作用域自动解锁）——
  规则二由 RAII 保证。

### 1.5 拦得住数据竞争，拦不住死锁

官方原文："Rust **can't protect you from all kinds of logic errors** when you use `Mutex<T>…
Mutex<T> comes with the risk of creating **deadlocks**."

本 demo 用 wait-for 图判环把它变成可断言的：两线程以**相反顺序**锁 A/B → 图里出现环 → 死锁；
**相同顺序** → 无环 → 正常完成（断言 9.1 / 9.2）。关键在于：**四次加锁动作单独看全都合法、
全都通过 borrow check** —— 死锁是逻辑错误，不是内存安全问题。

顺带两个运行时细节：
- **中毒（poisoning）**：持锁线程 panic 后，锁被标记 poison，之后 `lock()` 返回 `Err`，
  直接 `unwrap()` 会跟着 panic（官方原文）。可用 `is_poisoned()` 探测、`clear_poison()` 恢复。
- **不可重入**：同一线程重复 `lock()` 同一个 `Mutex` 会直接卡死（std 的 Mutex 不是递归锁）。

## 二、对比

| 维度 | `Rc<T>` | `Arc<T>` |
| --- | --- | --- |
| 计数操作 | 普通加减 | **原子**指令 |
| Send / Sync | 都不是 | 取决于 `T` |
| 代价 | 无 | 官方："thread safety comes with a performance penalty" |
| 适用 | 单线程多所有者 | 跨线程多所有者 |

| 维度 | `RefCell<T>` | `Mutex<T>` |
| --- | --- | --- |
| 借用检查时机 | 运行期（违反 → panic） | 运行期（争用 → 阻塞） |
| Sync | ❌ | ✅（只要 `T: Send`） |
| 失败形态 | `BorrowMutError` panic | 阻塞 / 死锁 / poison |
| 官方定位 | 单线程内部可变性 | 共享状态并发 |

| 维度 | 消息传递（channel） | 共享状态（`Arc<Mutex<T>>`） |
| --- | --- | --- |
| 官方类比 | 单一所有权（传出去就别再用） | 多重所有权 |
| 适用 | 任务分发、流水线 | 共享计数器/缓存 |

## 三、环境

- Rust 2021 edition，纯标准库。`thread::scope` 需 1.63+，`Mutex::clear_poison` 需 1.77+。
- Python 3 模型（含 40 项断言）：`python concurrency_check.py`。

## 四、运行方式

```bash
cd rust && cargo run
cd python && python concurrency_check.py
```

`rust/compile_fail/` 三个文件故意编译失败：

| 文件 | 报错 | 说明 |
| --- | --- | --- |
| `e0277_rc_not_send.rs` | E0277 | `Rc<Mutex<i32>>` cannot be sent（官方 Listing 16-14） |
| `e0277_refcell_not_sync.rs` | E0277 | `Arc<RefCell<i32>>` 连 Send 都不是 |
| `e0277_guard_not_send.rs` | E0277 | `MutexGuard` 不能离开加锁线程 |

## 五、关键代码

**自动推导（Python，真跑）**

```python
if t[0] == "ref":
    _kind, inner, mut = t
    i_send, i_sync = traits(inner)
    return (i_sync if not mut else i_send, i_sync)   # &T→Sync, &mut T→Send；Sync 都看 T: Sync
```

**共享计数器（Rust，真代码）**

```rust
let counter = Arc::new(Mutex::new(0));
for _ in 0..10 {
    let counter = Arc::clone(&counter);
    handles.push(thread::spawn(move || {
        *counter.lock().unwrap() += 1;   // guard 在本行结束即 drop → 自动解锁
    }));
}
```

**死锁判据**：wait-for 图（`thread → 它等待的 thread`）存在环。

## 六、性能边界

- **`Arc` 的代价**：每次 `clone` / `drop` 都是一次原子 RMW，比 `Rc` 的普通加减慢一个量级；
  官方明确说这是"只在真正需要时才付"的代价。
- **`Mutex` 的代价**：无争用时是一次原子 CAS（约几十纳秒）；有争用时线程会陷入内核等待，
  开销跳到微秒级。**锁的粒度比锁的数量更重要** —— 一个粗粒度大锁常常比十个细锁更快
  （少了获取次数与死锁面），但也更容易成为瓶颈。
- **能用 `AtomicUsize` 就别用 `Mutex<i32>`**：官方原话是"there are types simpler than
  `Mutex<T>`"。`fetch_add` 是单条指令，不会阻塞、不会死锁、不会 poison。
- **`Arc<Mutex<T>>` 的双层代价**：外层原子计数 + 内层锁争用。读多写少换 `RwLock`；
  纯读可以把数据做成不可变直接共享（只需 `T: Sync`，`&T` 就能跨线程，零同步成本）。

## 七、注意事项与常见坑

1. **`Arc<RefCell<T>>` 编译不过** —— 最常见的新手困惑。要内部可变性 + 跨线程，就 `Arc<Mutex<T>>`。
2. **别把 `MutexGuard` 传进 `thread::spawn`** —— 它是 `!Send`（std 明写）。
   要么在跨线程之前就把值取出来，要么重新组织成「传 Arc，各自 lock」。
3. **`std::sync::Mutex` 不可重入** —— 同一线程二次 `lock()` 直接死锁。需要递归锁得换
   `parking_lot::ReentrantMutex`（第三方）。
4. **锁顺序必须全局一致** —— 类型系统不帮这个忙。约定「按 mutex 的某个全序加锁」是唯一可靠做法。
5. **`unwrap()` 遇 poison 会连锁 panic** —— 一个线程 panic 可能让整片代码跟着 panic。
   不希望传播时用 `lock().unwrap_or_else(|e| e.into_inner())` 取回被污染的数据。
6. **持锁期间不要调用户回调** —— 回调里再锁同一把就死锁，也极大拉长临界区。
7. **手动 `impl Send`/`Sync` 是 unsafe** —— 官方原文。只有当你确知某个含裸指针的类型
   在语义上确实线程安全时才可以，写错就是把 UB 编译进库里。
8. **`Rc` 换成 `Arc` 时 `Cell`/`RefCell` 要一起换** —— 只换外层会撞上 `Arc<T>: Send` 的
   `T: Send + Sync` 要求。

## 八、参考资料（实际阅读）

- [std — `marker::Send`](https://doc.rust-lang.org/std/marker/trait.Send.html)
  —— "Types that can be transferred across thread boundaries"、"automatically implemented when
  the compiler determines it's appropriate"、`Rc` 非 Send 的理由与 `Arc` 的对照。
- [std — `marker::Sync`](https://doc.rust-lang.org/std/marker/trait.Sync.html)
  —— 精确定义 "`&T` is `Send`"、四条引用关系原文、`&mut T is Sync` 的 "somewhat surprising
  consequence" 及其解释（`& &mut T` 退化成只读）。
- [std — `sync::MutexGuard`](https://doc.rust-lang.org/std/sync/struct.MutexGuard.html)
  —— `impl<T: ?Sized> !Send for MutexGuard<'_, T>` 与其理由、
  `impl<T: ?Sized + Sync> Sync for MutexGuard<'_, T>`。
- [std — `cell::RefCell`](https://doc.rust-lang.org/std/cell/struct.RefCell.html)
  —— `impl<T> Send for RefCell<T> where T: Send`、`impl<T> !Sync for RefCell<T>`。
- [The Book — ch16-03 Shared-State Concurrency](https://doc.rust-lang.org/book/ch16-03-shared-state.html)
  —— Mutex 两条规则、`MutexGuard` 的 Deref/Drop、Listing 16-13/14/15 的完整报错链、
  `Arc` 的 atomic 含义与性能代价说明、`RefCell`/`Rc` 与 `Mutex`/`Arc` 的对比、死锁警告。
- [The Book — ch16-04 Extensible Concurrency with Send and Sync](https://doc.rust-lang.org/book/ch16-04-extensible-concurrency-sync-and-send.html)
  —— Send/Sync 是「语言内置」而非标准库、composed entirely 自动推导、手动实现是 unsafe。
