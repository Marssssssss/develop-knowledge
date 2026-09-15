# 智能指针与内部可变性

> `09-语言学习/Rust/` 子类目首批 demo 4/5（2026-09-15）。
> 依据：The Rust Programming Language *ch15-01 / ch15-04 / ch15-05 / ch15-06*、std 文档 `core::ops::Deref`。

## 简介

`Box` / `Rc` / `RefCell` / `Weak` 是所有权模型的四个「逃生舱」：分别解决**大小不确定**、
**多所有者**、**不可变类型也想改内部值**、**双向引用成环**这四类静态检查兜不住的问题。
本文把它们的运行期行为全部写成断言——包括 `Rc` 的计数轨迹、`RefCell` 的 panic、引用环导致的永久泄漏。

关键概念：

- **Box<T>**：堆分配 + 间接层；指针大小固定，因此递归类型有了确定大小（否则 E0072）。
- **智能指针**：实现 `Deref`（能像引用一样用）与 `Drop`（离开作用域时定制清理）的类型。
- **deref coercion**：`&T` 在 `T: Deref<Target=U>` 时自动转成 `&U`，转换在编译期解析、**无运行期代价**。
- **Rc<T>**：引用计数（单线程）；`Rc::clone` 只加计数不做深拷贝；计数归 0 才释放。
- **内部可变性**：在 `unsafe` 内部把借用规则搬到**运行期**检查（`RefCell` 违反即 panic）。
- **Weak<T>**：不表达所有权的弱引用，`upgrade()` 返回 `Option<Rc<T>>`；用来打断引用环。

## 原理详解

### 1. Box：为什么递归类型必须有间接层

`enum List { Cons(i32, List), Nil }` 报两个错：`E0072: recursive type` has infinite size
（附建议 "insert some indirection (e.g., a `Box`, `Rc`, or `&`) to break the cycle"）与
`E0391: cycle detected when computing when List needs drop`。
原因：编译器按「枚举取最大变体」算大小，`Cons` 需要 i32 + `List` 的大小，而 `List` 又依赖 `Cons`。
`Box<T>` 是指针，**指针大小不随指向数据量变化**，链条被打断。本 demo 的量化：

```text
无间接层  → InfiniteSize（模拟 E0072）
有间接层  → size_of::<List>() = 16 字节（i32(4) + 指针(8) = 12 → 对齐到 16）
```

另一个常见误解：**Box 不改变「move 会复制」这件事**（`let b2 = b1;` 是整体移走 Box 与堆所有权），
但 **move 不复制堆数据**——本 demo 用 `let big = Box::new([7u8; 65536]); let big2 = big;`
前后堆地址相等来证明。

### 2. deref coercion：三条规则

std `Deref` 文档给出的三种情形（The Book ch15-02 同款表述）：

```text
&T     → &U       当 T: Deref
&mut T → &mut U   当 T: DerefMut
&mut T → &U       当 T: Deref
```

第三条反向不成立：**不可变引用永远不会被强转成可变引用**——借用规则不能保证「只有一个共享引用」。
链式转换（本 demo 的 `hello(&m)`）：`&MyBox<String> → &String → &str`，插入 `Deref::deref` **2 次**，
且插入次数**在编译期确定**，因此 "there is no runtime penalty for taking advantage of deref coercion"。

### 3. Rc：计数轨迹（复现 The Book Listing 15-19）

```text
let a = Rc::new(..);                     strong = 1
let b = Rc::clone(&a);                   strong = 2
内层作用域建 c = Rc::clone(&a)            strong = 3
c 离开作用域                              strong = 2
```

`Rc::clone` 与 `a.clone()` 的语义差别是「计数 +1」vs「深拷贝」——所以**惯用写法是 `Rc::clone(&a)`**，
让代码里「便宜的克隆」一眼可辨。

### 4. RefCell：借用规则搬到运行期

| 检查时机 | 类型 | 违规后果 |
| --- | --- | --- |
| 编译期 | `&`/`&mut`、`Box<T>` | 编译错误（demo 1 的 5 个错误码） |
| 运行期 | `RefCell<T>` | **panic**（不是 UB） |

`borrow()` 增加共享计数，`borrow_mut()` 要求计数为 0；违反时 panic 信息是 `already borrowed: BorrowMutError`。
本 demo 用 `try_borrow`/`try_borrow_mut` 取出判定结果断言，再用 `catch_unwind` 证明 panic 可控：

```text
已有 2 个活动 Ref 时 try_borrow_mut() → 失败：already borrowed
持有 RefMut 期间 try_borrow() 失败 = true，try_borrow_mut() 失败 = true
双重 borrow_mut() 触发 panic 并被 catch_unwind 拦下 = true
```

`Rc<RefCell<T>>` 同时拿到「多所有者」+「可改」："you can get a value that can have multiple owners
*and* that you can mutate"。

### 5. 引用环 → 泄漏（量化）

```text
成环后                       strong: x=2 y=2
变量 x / y 都离开作用域后     strong: x=1 y=1   ← 永不归零
Weak::upgrade() 仍成功 = true → 内存永不回收
```

The Book 的原话："**Preventing memory leaks entirely is not one of Rust's guarantees,
meaning memory leaks are memory safe in Rust.**"

### 6. Weak：把反向边变弱

```text
leaf 建立后          leaf: strong=1 weak=0
branch 挂上后        branch: strong=1 weak=1 / leaf: strong=2 weak=0
branch 离开作用域     branch 被释放（weak 计数不阻止释放）→ leaf.parent.upgrade() = None
```

`Rc::downgrade` 增加 `weak_count`；**`weak_count` 不需要归零，值就能被释放**——
这正是「弱引用不表达所有权」的含义。

## 对比 / 选型

| 类型 | 所有者数量 | 借用检查时机 | 可改性 | 线程安全 |
| --- | --- | --- | --- | --- |
| `&T` / `&mut T` | 不拥有 | 编译期 | 视 `mut` 而定 | `Sync` 视 `T` 而定 |
| `Box<T>` | 单一 | 编译期 | 需 `&mut` 访问 | `Send` 视 `T` 而定 |
| `Rc<T>` | 多个 | 编译期（只读共享） | 不可（只读） | ❌ 单线程 |
| `RefCell<T>` | 单一 | **运行期** | ✅（内部可变性） | ❌ 单线程（`Mutex` 才是线程安全版） |
| `Rc<RefCell<T>>` | 多个 | 运行期 | ✅ | ❌ 单线程 |

运行期检查的取舍：代价是 ①错误可能晚到生产环境 ②运行期有计数开销；
收益是**允许静态分析判不了的合法程序**（"Static analysis, like the Rust compiler, is inherently conservative"）。

## 环境准备

- 操作系统：任意
- Rust：1.63+（`Weak::strong_count` 自 1.41 稳定）
- Python：3.9+

## 运行方式

```bash
cd rust && cargo run                 # 或： rustc -O main.rs -o demo && ./demo
cd rust/compile_fail && rustc box_pitfalls.rs   # 观察 Box 的两种误用（文件内注释说明）
cd python && python3 smart_ptr.py
```

## 关键代码片段

Weak 破环的关键两行：

```rust
// 父持子：强引用（父被 drop 时子也释放）
children: RefCell::new(vec![Rc::clone(&leaf)]),
// 子持父：弱引用（不构成环，不阻止父释放）
*leaf.parent.borrow_mut() = Rc::downgrade(&branch);
// 访问前必须 upgrade()，父已释放时得到 None
assert!(leaf.parent.borrow().upgrade().is_none());
```

## 运行结果（本机实测）

```text
=== Rc 计数轨迹 ===          strong_count 轨迹 = [1, 2, 3, 2]（Book Listing 15-19 输出一致）
=== RefCell ===             双重 borrow_mut() 触发 panic 并被 catch_unwind 拦下 = true
=== 引用环 → 泄漏 ===        成环后 strong: x=2 y=2 → 变量离开作用域后 x=1 y=1，已释放对象 = 无
=== Weak 破环 ===            branch 离开作用域后 leaf: strong=1 weak=0，upgrade() = None
```

## 性能与边界

- **Box**：除了「数据放堆上」之外没有额外开销，也没有额外能力（"Boxes don't have performance
  overhead, other than storing their data on the heap"）。
- **deref coercion**：编译期解析、运行期 0 成本；但会隐式插入 `Deref::deref`，因此实现必须廉价且
  **不会意外失败**（std 文档："this trait should never fail"）。
- **Rc/RefCell**：计数是运行期读写（非原子，故单线程）；`Rc<T>` 的循环引用会泄漏内存。
- **尺寸**：`&dyn Trait` = 2 字宽（见 demo 3）；`Rc<T>` 额外保存 strong/weak 两个计数。
- **边界**：本 demo 不做多线程验证（`Rc`/`RefCell` 均 `!Send`/`!Sync`，线程版本是 `Arc`/`Mutex`）。

## 注意事项与常见坑

1. **`RefCell` 的违规是运行期 panic，不是编译错误**——错误可能推迟到生产环境才暴露。
2. **`borrow()` 的临时值作用域**：`x.borrow_mut().push(..)` 一行式安全；
   但 `let a = cell.borrow(); let b = cell.borrow_mut();` 会 panic。
3. **`Rc` 的环必须自己防**："you can't rely on Rust to catch them"；反向边尽量用 `Weak` 表达。
4. **`Rc::clone(&a)` ≠ `a.clone()`**：前者只加计数，后者是深拷贝；命名习惯用于区分「便宜/昂贵克隆」。
5. **`Box` 不解决生命周期问题**：`fn dangle() -> Box<&String>` 依然报 E0106
   （见 `rust/compile_fail/box_pitfalls.rs`）。

## 参考资料（实际阅读过的权威来源）

- [The Book — ch15-01 Using `Box<T>` to Point to Data on the Heap](https://doc.rust-lang.org/book/ch15-01-box.html)
  —— Box 的三类使用场景、递归类型与 cons list、`E0072`/`E0391` 完整报错与 "insert some indirection" 建议、
  "指针大小不随数据量变化" 的论证、Box 无额外开销、`Deref`/`Drop` 是智能指针的两个必备 trait。
- [std 文档 — `core::ops::Deref`](https://doc.rust-lang.org/std/ops/trait.Deref.html)
  —— deref coercion 的三条形式、`Box` 为何几乎没有方法、"should be careful about implementing Deref" 的警告、
  `AsRef`/`Borrow` 的替代建议、"this trait's method should never unexpectedly fail"。
- [The Book — ch15-04 `Rc<T>`, the Reference Counted Smart Pointer](https://doc.rust-lang.org/book/ch15-04-rc.html)
  —— 多所有权动机（图结构中多条边指向同一节点）、"can't determine at compile time which part will
  finish using the data last"、`Listing 15-18/15-19` 与 `[1, 2, 3, 2]` 实测输出、
  `Rc::clone` vs `a.clone()` 的约定、`strong_count`/`weak_count` 命名由来、单线程限制。
- [The Book — ch15-05 `RefCell<T>` and the Interior Mutability Pattern](https://doc.rust-lang.org/book/ch15-05-interior-mutability.html)
  —— 内部可变性的定义（用 `unsafe` 在数据结构内部弯折规则、外层类型仍不可变）、
  编译期 vs 运行期检查的取舍、"inherently conservative" 原文、`Ref<T>`/`RefMut<T>` 与借用计数、
  `already borrowed: BorrowMutError`、`Box`/`Rc`/`RefCell` 对比 recap、`Listing 15-24` 输出、`Mutex` 替代。
- [The Book — ch15-06 Reference Cycles Can Leak Memory](https://doc.rust-lang.org/book/ch15-06-reference-cycles.html)
  —— 引用环永不释放（"the reference count of each item in the cycle will never reach 0"）、
  "memory leaks are memory safe in Rust" 原文、`Rc::downgrade`/`Weak::upgrade` 的 `Some`/`None` 语义、
  `Listing 15-28/15-29` 父子 `Node` 与计数变化、drop 行为与「weak 计数不阻止释放」。
