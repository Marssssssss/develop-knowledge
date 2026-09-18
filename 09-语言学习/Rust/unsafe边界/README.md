# unsafe 边界（五项 superpower 与安全封装）

> Rust 第二批 · demo 371 · 依据 The Book ch20-01、std::cell::UnsafeCell、Rustonomicon
> Transmutes、std::pin 官方文档实读。
>
> 一句话：**`unsafe` 不是「关掉编译器」，而是「解锁五件特定的事」**——解引用裸指针、
> 调 unsafe 函数、访问可变静态、实现 unsafe trait、读 union 字段。除此之外借用检查
> 全部照常工作。`unsafe` 的正确用法不是写满全篇，而是**把它压成一小块，再用安全 API 包起来**。

## 一、原理详解

### 1.1 五项 superpower（官方清单）

The Book ch20-01 原文："You can take **five** actions in unsafe Rust that you can't in
safe Rust, which we call *unsafe superpowers*"：

1. 解引用裸指针（Dereference a raw pointer）
2. 调用 unsafe 函数或方法（Call an unsafe function or method）
3. 访问或修改可变静态变量（Access or modify a mutable static variable）
4. 实现 unsafe trait（Implement an unsafe trait）
5. 访问 union 字段（Access fields of `union`s）

本 demo 断言 1.x 把这张表固化成**恰好五项**，并逐项验证「安全代码里做 → E0133」。

### 1.2 `unsafe` 不关借用检查

官方原话："unsafe **doesn't turn off the borrow checker** or disable any of Rust's other
safety checks: **If you use a reference in unsafe code, it will still be checked.**"

这是最容易被误解的一点。`unsafe` 块的正确心智模型是：

```
安全 Rust  =  全套检查
unsafe Rust = 全套检查  +  五项额外能力（这五项本身不被检查）
```

本 demo 用两条断言把它钉死：
- 模型层面：`Ref.write()` 的签名里**根本没有** `in_unsafe` 参数（断言 2.3）——
  结构上就不存在「用 unsafe 绕过」这条路；
- 代码层面：`compile_fail/e0502_borrow_check_inside_unsafe.rs` 把 `v.push()` 包进
  `unsafe` 里，照样报 **E0502**。

### 1.3 裸指针：创建安全，解引用危险

`*const T` / `*mut T` 与引用的区别（官方四条）：

| 性质 | 引用 `&T` | 裸指针 `*const T` |
| --- | --- | --- |
| 遵守借用规则 | 是 | **可以忽略**（可同时有多个 `*mut`） |
| 保证指向有效内存 | 是 | **不保证** |
| 可以为 null | 否 | **可以** |
| 自动清理 | 是 | **没有** |

关键分工：**创建裸指针是安全的，解引用才是 unsafe**。所以 `E0133` 打在 `*r` 上，
不打在 `let r: *const i32 = &num;` 上。本 demo 断言 3.x 复现了这一点，
并把「悬垂 / 空指针解引用」显式建模为 UB（编译器**不保证**能发现）。

### 1.4 UnsafeCell：内部可变性的唯一原料

std 原文："`UnsafeCell<T>` is **the core primitive for interior mutability in Rust** …
All other types that allow internal mutability, such as `Cell<T>` and `RefCell<T>`,
internally use `UnsafeCell` to wrap their data."

它解除的是**共享引用的不可变保证**，仅此一条：

```
&T            → 编译器可以假设它指向的数据不变；改它 = UB
&UnsafeCell<T> → 允许通过共享引用修改（这就是「内部可变性」）
&mut T        → 唯一性保证**不受影响**：
                官方："There is no legal way to obtain aliasing `&mut`,
                      not even with `UnsafeCell<T>`"
```

本 demo 断言 4.x：无 `UnsafeCell` 的 `&T` 改写 → UB；有 `UnsafeCell` → 允许；
**无论有没有 UnsafeCell，交叠 `&mut` 永远是 UB**。

另有两条 std 明写的边界：`UnsafeCell` **不防止数据竞争**（多线程仍需 atomic / Mutex）；
`.get()` 返回的就是一个 `*mut T`——内部可变性的入口本身是裸指针。

### 1.5 transmute：唯一保护是尺寸检查

Rustonomicon 原文："The only restriction is that the `T` and `U` are verified to have
**the same size**." 尺寸一旦相同，下面这些全是 UB 且 rustc 一声不响：

| 情形 | 官方措辞 |
| --- | --- |
| `&` → `&mut` | "is **always** Undefined Behavior. No you can't do it. No you're not special." |
| 造出非法值（如 `3 → bool`） | "Do not transmute 3 to bool. Even if you never do anything with the bool." |
| 依赖 `repr(Rust)` 的布局 | "for your run-of-the-mill `repr(Rust)`, it is **not** [precisely defined]" |
| 转出未标生命周期的引用 | "produces an **unbounded lifetime**" |

`repr(C)` 与 `repr(transparent)` 的布局**是**有定义的，所以这两类可以。

### 1.6 安全抽象：把 UB 变成 panic

官方："To isolate unsafe code as much as possible, it's best to enclose such code within
a **safe abstraction** and provide a safe API."

教科书例子就是 `split_at_mut`：两个 `&mut` 指向同一块切片，安全 Rust 写不出来
（借用检查不允许），只能用裸指针；但**加一句 `assert!(mid <= len)`**，越界就从
「UB」降级成「panic」。本 demo 断言 6.x 直接对比两条路径：
- 裸实现 `unsafe_split_impl(data, 99)` → **UB**
- 安全 API `split_at_mut(data, 99)` → **panic**（可捕获、可恢复）

这就是「unsafe 代码的价值不在于 unsafe 本身，而在于它外面那层安全契约」。

## 二、对比

| 能力 | 需要 unsafe？ | 失败后果 |
| --- | --- | --- |
| 创建裸指针 | 否 | — |
| 解引用裸指针 | **是** | 悬垂/空 → UB |
| 通过 `&T` 改数据 | 编译错误（E0596） | 用裸指针绕过 → UB |
| 通过 `&UnsafeCell<T>` 改数据 | **是** | 只要不并发就安全 |
| 拿到两个交叠 `&mut` | 永远不可能 | UB（且无任何合法写法） |
| `transmute` 尺寸不同 | 编译错误（E0512） | — |
| `transmute` 尺寸相同但语义错 | 编译通过 | **UB，编译器不报** |

| 用法 | 推荐度 | 理由 |
| --- | --- | --- |
| 大段 `unsafe { ... }` | ❌ | 官方："Keep `unsafe` blocks small" |
| 小 unsafe + 安全封装 | ✅ | 官方推荐做法；标准库就是这么写的 |
| 为省一次检查而上 unsafe | ❌ | 检查的代价通常远小于 UB 的代价 |

## 三、环境

- Rust 2021 edition，纯标准库。
- Python 3 模型（含 40 余项断言）：`python unsafe_check.py`。

## 四、运行方式

```bash
cd rust && cargo run
cd python && python unsafe_check.py
```

`rust/compile_fail/` 四个文件故意编译失败：

| 文件 | 报错 | 说明 |
| --- | --- | --- |
| `e0133_deref_raw_in_safe.rs` | E0133 | 创建安全、解引用不安全 |
| `e0502_borrow_check_inside_unsafe.rs` | E0502 | **unsafe 里借用检查照样生效** |
| `e0512_transmute_size.rs` | E0512 | `transmute` 唯一的编译期检查是尺寸 |
| `e0133_static_mut_in_safe.rs` | E0133 | 可变静态是五项 superpower 之一 |

## 五、关键代码

**UnsafeCell 的最小实现（Rust，真代码）**

```rust
struct MyCell<T> { value: UnsafeCell<T> }

impl<T: Copy> MyCell<T> {
    fn set(&self, v: T) { unsafe { *self.value.get() = v; } }   // &self 也能改
    fn get(&self) -> T  { unsafe { *self.value.get() } }
}
```

**安全抽象的全部秘密就是那句 assert**

```rust
fn split_at_mut(values: &mut [i32], mid: usize) -> (&mut [i32], &mut [i32]) {
    let len = values.len();
    let ptr = values.as_mut_ptr();
    assert!(mid <= len);                       // ← UB 与 panic 的分界线
    unsafe {
        (std::slice::from_raw_parts_mut(ptr, mid),
         std::slice::from_raw_parts_mut(ptr.add(mid), len - mid))
    }
}
```

**「unsafe 不关借用检查」的结构性判据（Python）**

```python
check("借用检查不认 in_unsafe",
      "in_unsafe" not in Ref.write.__code__.co_varnames)
```

## 六、性能边界

- **`unsafe` 本身不产生任何代码**：它只是关掉若干**编译期**检查，生成的代码与
  手写 C 形态相同。性能收益来自「少一次边界检查」「少一次引用计数」，而不是来自 `unsafe` 关键字。
- **`UnsafeCell` 的代价是优化机会**：编译器不能再假定 `&T` 不变，
  于是少了常量传播、重排序、寄存器缓存等优化（这也是为什么 `Cell<T>` 不是免费的）。
- **`transmute` 是零指令的**（只是重解释位模式），正因如此它才危险 ——
  没有任何运行期信息可供校验。
- **可变静态变量会引入隐式的全局同步问题**：即便用 unsafe 访问，数据竞争仍是 UB；
  真要共享计数请用 `AtomicU32`（零 unsafe，且通常编译成单条指令）。
- **union 的 `#[repr(C)]` 是必须的**：`repr(Rust)` 下 union 的布局同样没有保证。

## 七、注意事项与常见坑

1. **以为 `unsafe` 里借用检查失效** —— 完全相反，它照常工作（E0502 反例可证）。
2. **以为「编译过了就安全」** —— 尺寸相同的 `transmute`、悬垂裸指针、非法值构造
   全都能编译通过。unsafe 代码的安全性**只能靠人审**。
3. **`&` → `&mut` 的 transmute** —— nomicon 用了三句话强调它永远是 UB，
   因为优化器会假定共享引用在其生命周期内不变。
4. **给含 `UnsafeCell` 的类型手写 `unsafe impl Sync`** —— 等于向编译器承诺「我自己
   保证了线程安全」，而 std 明说 `UnsafeCell` 本身不防数据竞争。要并发就换 `Mutex` / 原子。
5. **把 unsafe 块开得很大** —— 官方建议小而集中，否则出问题时无法定位。
   每个 unsafe 块都应在注释里写清 **SAFETY:** 它依赖什么前提。
6. **忘了给 unsafe fn 写 SAFETY 契约** —— 调用者无从判断前提，等于把 UB 外包出去。
   正确做法是像本 demo 的 `read_u32_le_checked` 那样提供安全包装。
7. **可变静态变量跨线程** —— 即使全都包在 unsafe 里也是数据竞争。用 `static` +
   `AtomicU32`，或 `OnceLock`。
8. **union 读错字段** —— 读的是同一段内存的另一种解释；只有 `#[repr(C)]` 下布局才确定。

## 八、参考资料（实际阅读）

- [The Book — ch20-01 Unsafe Rust](https://doc.rust-lang.org/book/ch20-01-unsafe-rust.html)
  —— 五项 unsafe superpower 的完整清单、"unsafe doesn't turn off the borrow checker"、
  "Keep `unsafe` blocks small"、安全抽象的建议与 `split_at_mut` 的完整推导、
  裸指针与引用的四条区别、`&raw const` / `&raw mut` 的创建方式。
- [std — `cell::UnsafeCell`](https://doc.rust-lang.org/std/cell/struct.UnsafeCell.html)
  —— "The core primitive for interior mutability in Rust"、只解除 `&T` 不可变保证、
  "There is no legal way to obtain aliasing `&mut`, not even with `UnsafeCell<T>`"、
  "UnsafeCell does nothing to avoid data races"、`.get()` 返回 `*mut T`、别名规则。
- [Rustonomicon — Transmutes](https://doc.rust-lang.org/nomicon/transmutes.html)
  —— "The only restriction is that the T and U are verified to have the same size"、
  `&`→`&mut` 永远是 UB 的三句强调、"Do not transmute 3 to bool"、
  `repr(C)` / `repr(transparent)` 布局有定义而 `repr(Rust)` 没有、unbounded lifetime。
- [std — `pin` module](https://doc.rust-lang.org/std/pin/index.html)
  —— unsafe 代码的又一类正当用途：为自引用类型与侵入式数据结构建立安全接口。
