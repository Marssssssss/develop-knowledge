# Rust

> 2026-09-15 类目自动拓展新增(S2)。补齐 `09-语言学习` 里"无 GC 的内存安全"这条主线——
> 目前只有 Python(动态 + 引用计数)与 Golang(GC + CSP),缺一门**编译期就把内存与并发错误挡掉**的语言。
>
> Rust 官方对自身定位的原文(*The Book*, Introduction):
> "High-level ergonomics and low-level control are often at odds in programming language design;
> Rust challenges that conflict." 并明确目标是 "providing safety *and* productivity,
> speed *and* ergonomics",手段是 **zero-cost abstractions**——
> "higher-level features that compile to lower-level code as fast as code written manually"。

## 一、Rust 关键差异(对比已收录语言)

| 维度 | Rust | C / C++ | Go | Java / Kotlin | Python |
| --- | --- | --- | --- | --- | --- |
| 类型系统 | 静态 + 推断 + trait | 静态(模板/trait 类)| 静态(接口)| 静态(名义类)| 动态 |
| 内存管理 | **所有权 + 借用检查(编译期)**,无 GC | 手动 / RAII | GC(三色标记)| GC | 引用计数 + GC |
| 安全边界 | `unsafe` 显式圈定 | 无边界 | 无边界 | JNI/Unsafe | C 扩展 |
| 并发模型 | `thread` + `Send`/`Sync` 编译期校验 + async/await | pthread / std::thread | goroutine + channel | thread + Future | GIL + asyncio |
| 错误处理 | `Result<T, E>` + `?` | 返回码 / 异常 | error 返回值 | checked Exception | try/except |
| 空值 | `Option<T>`,无 null | 裸指针可为 null | nil | null | None |
| 抽象成本 | 泛型单态化 + trait 静态分发,零开销 | 虚函数表 / 模板膨胀 | 接口 itab 动态派发 | 虚方法 + 逃逸分析 | 全动态 |
| 元编程 | 声明宏 `macro_rules!` + 过程宏 | 预处理器 / 模板 | 无(有代码生成)| 注解处理器 | 装饰器 / 元类 |
| 编译速度 | 慢(借用检查 + 单态化)| 中 | 快 | 慢 | 解释 |

## 二、标志性语言特性(按 *The Book* 章节序)

| 特性 | 章节 | 必学? | 核心机制 |
| --- | --- | --- | --- |
| `cargo` 工程 | ch01/ch14 | ✅ | 依赖 / feature / workspace / 发布 |
| 所有权 Ownership | ch04 | ✅ | 三条规则 + Move/Copy + `drop` |
| 借用与引用 | ch04 | ✅ | `&` / `&mut`,同时只能一个可变引用 |
| struct / enum / `match` | ch05/ch06 | ✅ | 代数数据类型 + 穷尽匹配 |
| 模块与可见性 | ch07 | ✅ | `mod` / `pub` / `use` 路径 |
| 集合(Vec/String/HashMap) | ch08 | ✅ | UTF-8 与索引/切片的关系 |
| 错误处理 | ch09 | ✅ | `panic!` vs `Result` + `?` 传播 |
| 泛型 / trait / 生命周期 | ch10 | ✅ | trait bound、孤儿规则、`'a` |
| 智能指针 | ch15 | ✅ | `Box` / `Rc` / `RefCell` + `Deref`/`Drop` |
| 无畏并发 | ch16 | 进阶 | `Send`/`Sync` + `Arc<Mutex<T>>` |
| async/await | ch17 | 进阶 | Future / task / stream |
| 闭包与迭代器 | ch13 | ✅ | `Fn`/`FnMut`/`FnOnce`,零成本迭代链 |
| 模式匹配 | ch19 | 进阶 | 全量模式参考 |
| unsafe / 宏 / 高级 trait | ch20 | 进阶 | 裸指针、`macro_rules!`、GAT |

## 三、原理详解:所有权与借用

### 1. 三条规则(原文 "Ownership Rules")

> - Each value in Rust has an *owner*.
> - There can only be one owner at a time.
> - When the owner goes out of scope, the value will be dropped.

配套约束原文:"Memory is managed through a system of ownership with a set of rules the compiler checks.
If any of the rules are violated, the program won't compile. None of the features of ownership will
slow down your program while it's running." —— 这就是"无 GC 也安全"的全部秘密:**检查发生在编译期**。

### 2. 栈 vs 堆(为什么需要所有权)

- 栈:"stores values in the order it gets them and removes the values in the opposite order"(LIFO),
  且 "All data stored on the stack must have a known, fixed size"。入栈比堆分配快,因为分配器不必搜索位置。
- 堆:分配器找一个足够大的空位、标记占用、返回**指针**;指针本身大小固定可放栈上,
  "but when you want the actual data, you must follow the pointer"。访问堆更慢,因为要多跳一次指针。
- 结论:"the main purpose of ownership is to manage heap data"。

### 3. `String` vs `&str` 字面量

字面量在编译期已知,直接硬编码进可执行文件,因此不可变;`String` 管理堆内存,
"is able to store an amount of text that is unknown to us at compile time",需要
①运行期向分配器申请 ②用完归还。Rust 的归还方式是 `drop`——变量离开作用域时自动调用
(与 C++ RAII 同源)。

### 4. Move vs Copy

```rust
let s1 = String::from("hello");
let s2 = s1;   // 只复制栈上的 ptr/len/cap,堆数据不复制;s1 之后失效
```

原文强调这叫 **move** 而非 shallow copy:"because Rust also invalidates the first variable,
instead of being called a shallow copy, it's known as a *move*"。若两个指针都可释放就是
double free,所以编译器直接让 `s1` 失效(用 `s1` 报 `E0382: borrow of moved value`)。
设计推论:"Rust will never automatically create 'deep' copies of your data. Therefore,
any *automatic* copying can be assumed to be inexpensive in terms of runtime performance."

实现 `Copy` 的类型赋值不 move 而是平凡复制——原文列举:所有整数类型、`bool`、所有浮点、`char`、
以及"只含实现 `Copy` 的类型的元组"(故 `(i32, i32)` 可以、`(i32, String)` 不行)。
一般规则:"any group of simple scalar values can implement `Copy`, and nothing that requires
allocation or is some form of resource can implement `Copy`"。**`Copy` 与 `Drop` 互斥**,
同时标注会编译失败。要深拷贝堆数据须显式 `.clone()`。

函数传参/返回与赋值同构:"The mechanics of passing a value to a function are similar to those
when assigning a value to a variable. Passing a variable to a function will move or copy,
just as assignment does." —— 这正是"为了拿回所有权要返回元组"的痛点,也是引入引用的动机。

### 5. 借用规则(The Rules of References)

> - At any given time, you can have *either* one mutable reference *or* any number of immutable references.
> - References must always be valid.

- 不可变引用改不了数据,报 `E0596: cannot borrow ... as mutable`。
- 两个可变引用同时存在报 `E0499: cannot borrow ... as mutable more than once at a time`。
- 可变与不可变混用报 `E0502: cannot borrow ... as mutable because it is also borrowed as immutable`。
- 好处原文说得很直白:"The benefit of having this restriction is that Rust can prevent data races
  at compile time." 数据竞争三条件:①两个及以上指针同时访问同一数据 ②至少一个在写
  ③没有任何同步机制。三者全中才违规,穷尽检查由编译器完成。
- **作用域按最后一次使用算**(NLL,non-lexical lifetimes):"a reference's scope starts from where
  it is introduced and continues through the last time that reference is used",所以
  "先借两个不可变、用完后立刻借可变"是合法的,而词法作用域模型会误报。

### 6. 悬垂引用被编译期消灭

`fn dangle() -> &String { let s = String::from("hello"); &s }` 报
`E0106: missing lifetime specifier`,理由原文:

```text
this function's return type contains a borrowed value, but there is no value
for it to be borrowed from
```

语义保证原文:"If you have a reference to some data, the compiler will ensure that the data
will not go out of scope before the reference to the data does."

### 7. trait:静态分发与孤儿规则

- trait "defines the functionality a particular type has and can share with other types"。
- `impl Trait` 是 trait bound 的语法糖:`fn notify(item: &impl Summary)` 允许各参数类型不同;
  要强制同类型必须写 `fn notify<T: Summary>(item1: &T, item2: &T)`。
- 返回位 `impl Trait` 只能返回**单一具体类型**;要运行时多类型需 trait object(动态分发)。
- **孤儿规则**:只有当 trait 或类型至少有一个是本地 crate 的,才能 `impl`。
  缘由原文:"This rule ensures that other people's code can't break your code and vice versa.
  Without the rule, two crates could implement the same trait for the same type,
  and Rust wouldn't know which implementation to use."(该性质称 *coherence*)。
- **blanket implementation**:`impl<T: Display> ToString for T` 即为标准库写法,
  故任何实现 `Display` 的类型都自带 `to_string()`。
- 反向影响:动态语言的"方法不存在"错误在 Rust 里被提到编译期——
  "Rust moves these errors to compile time so that we're forced to fix the problems before
  our code is even able to run."

### 8. 智能指针:所有权模型的逃生舱

原文对智能指针的界定:"data structures that act like a pointer but also have additional metadata
and capabilities",与引用的关键差别是**智能指针通常拥有它所指向的数据**,
且都实现 `Deref`(使其能像引用一样用)与 `Drop`(定制离开作用域时的行为)。
标准库四件套:`Box<T>`(堆分配)、`Rc<T>`(引用计数,多所有权)、
`Ref<T>`/`RefMut<T>`(经 `RefCell<T>` 访问,**把借用规则从编译期挪到运行期**)、
以及内部可变性(interior mutability)模式 = 不可变类型暴露修改内部值的 API。
`Rc<RefCell<T>>` 组合可绕开编译期借用检查的静态不可判定部分,代价是运行期 panic,
并且 `Rc` 的循环引用会**泄漏内存**(故需 `Weak<T>`)。

## 四、待研究清单(按教学价值排序)

1. [ ] **所有权与借用检查** → `Rust/所有权与借用/` — E0382/E0499/E0502/E0596/E0106 五大报错的最小复现 + NLL 前后对比
2. [ ] **Move/Copy/Clone 与 drop 时机** → `Rust/Copy与Drop/` — 作用域末尾的 drop 顺序、重赋值立即 drop、`Copy`+`Drop` 互斥
3. [ ] **trait 与静态分发** → `Rust/trait与分发/` — 单态化 vs vtable、孤儿规则、blanket impl、`impl Trait` 不能返回多类型
4. [ ] **智能指针与内部可变性** → `Rust/智能指针/` — Box 递归类型、Deref coercion、Rc 计数、RefCell 运行期借用、Rc 循环泄漏与 Weak
5. [ ] **生命周期标注** → `Rust/生命周期/` — 省略规则(elision)、结构体持引用、`'static`、生命周期与 trait bound 的交互
6. [ ] **闭包与迭代器** → `Rust/Fn三兄弟与迭代器/` — `Fn`/`FnMut`/`FnOnce` 推导、捕获方式、零成本迭代链与手写循环的汇编级对比
7. [ ] **无畏并发** — `Send`/`Sync` 的 auto trait 语义、`Arc<Mutex<T>>`、`MutexGuard` 与死锁
8. [ ] **async/await 状态机** — Future 是惰性的、`poll` 与 `Waker`、Pin 解决的问题、task 与 executor 的关系
9. [ ] **错误处理惯用法** — `Result` + `?` + `From` 自动转换、`thiserror`/`anyhow` 的分工、`Box<dyn Error>`
10. [ ] **unsafe Rust 边界** — 裸指针、`UnsafeCell` 与内部可变性的底层、`transmute` 的前提、`unsafe` 不取消借用检查

## 五、权威来源(本 README 实际阅读过的来源)

- [*The Rust Programming Language*(The Book) — Introduction](https://doc.rust-lang.org/book/ch00-00-introduction.html)
  —— 全部 21 章 + 附录 A–G 清单;零成本抽象与"安全 *and* 效率"的官方定位原文;project chapter = ch02/12/21。
- [The Book — ch04-00 Understanding Ownership](https://doc.rust-lang.org/book/ch04-00-understanding-ownership.html)
  —— "enables Rust to make memory safety guarantees without needing a garbage collector"。
- [The Book — ch04-01 What Is Ownership?](https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html)
  —— 所有权三条规则原文、栈/堆对比与性能原文、作用域、`String` vs 字面量、Move 与 double free、
  `E0382`、`Copy` 的完整类型列表与 `Copy`/`Drop` 互斥、`clone`、Rust 的 `drop` 与 C++ RAII、
  函数传参 move/copy 与返回值转移所有权。
- [The Book — ch04-02 References and Borrowing](https://doc.rust-lang.org/book/ch04-02-references-and-borrowing.html)
  —— 引用即借用、`E0596`/`E0499`/`E0502` 三段完整报错、数据竞争三条件原文、
  引用作用域到"最后一次使用"为止(NLL)、`E0106` 悬垂引用、借用两条规则原文。
- [The Book — ch10-02 Traits](https://doc.rust-lang.org/book/ch10-02-traits.html)
  —— trait 定义/实现/默认方法、`impl Trait` 与 trait bound 的等价关系、
  `where` 子句、返回位 `impl Trait` 只能单一类型、条件化 `impl`、
  孤儿规则与 *coherence* 原文、blanket implementation(`impl<T: Display> ToString for T`)。
- [The Book — ch15-00 Smart Pointers](https://doc.rust-lang.org/book/ch15-00-smart-pointers.html)
  —— 智能指针定义、`Deref`/`Drop` 的必备性、`Box<T>`/`Rc<T>`/`Ref<T>`+`RefCell<T>` 四件套、
  内部可变性模式与循环引用泄漏。

## 六、与已有 demo 的边界

- **领域 demo 里的 Rust 实现**(如 `08-安全/01-密码学/`、`06-DevOps/01-容器化/` 后续若补 Rust):
  聚焦该知识点的 Rust 写法,**不展开**语言其他机制
- **本目录的 demo**:只讲 Rust 本身——所有权、借用、trait、生命周期、`unsafe` 边界

例:
- `09-语言学习/Python/装饰器/` 讲的是"函数是一等对象 + 闭包 + 语法糖"
- `09-语言学习/Rust/trait与分发/` 要讲的是"trait 如何同时充当接口、约束与单态化开关",跟具体业务场景无关

## 七、进度

由 [`_docs/STATE.md`](../../_docs/STATE.md)(主,≤ 5 KB)+ [`archive/`](../../_docs/archive/)(历史回溯)统一追踪。
本子类目当前状态:**README 已建(2026-09-15 S2),demo 待排期**(§四 清单 1–10)。
