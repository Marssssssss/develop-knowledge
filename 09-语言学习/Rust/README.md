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

| 特性 | 章节 | 必学? | 核心机制 | demo |
| --- | --- | --- | --- | --- |
| `cargo` 工程 | ch01/ch14 | ✅ | 依赖 / feature / workspace / 发布 | — |
| 所有权 Ownership | ch04 | ✅ | 三条规则 + Move/Copy + `drop` | 1 |
| 借用与引用 | ch04 | ✅ | `&` / `&mut`,同时只能一个可变引用 | 1 |
| struct / enum / `match` | ch05/ch06 | ✅ | 代数数据类型 + 穷尽匹配 | — |
| 模块与可见性 | ch07 | ✅ | `mod` / `pub` / `use` 路径 | — |
| 集合(Vec/String/HashMap) | ch08 | ✅ | UTF-8 与索引/切片的关系 | — |
| 错误处理 | ch09 | ✅ | `panic!` vs `Result` + `?` 传播 | — |
| 泛型 / trait / 生命周期 | ch10 | ✅ | trait bound、孤儿规则、`'a` | 3 / 5 |
| 智能指针 | ch15 | ✅ | `Box` / `Rc` / `RefCell` + `Deref`/`Drop` | 4 |
| 无畏并发 | ch16 | 进阶 | `Send`/`Sync` + `Arc<Mutex<T>>` | — |
| async/await | ch17 | 进阶 | Future / task / stream | — |
| 闭包与迭代器 | ch13 | ✅ | `Fn`/`FnMut`/`FnOnce`,零成本迭代链 | — |
| 模式匹配 | ch19 | 进阶 | 全量模式参考 | — |
| unsafe / 宏 / 高级 trait | ch20 | 进阶 | 裸指针、`macro_rules!`、GAT | — |

## 三、已完成 demo(第一批 5 个,2026-09-15)

| # | demo | 一句话核心机制 |
| --- | --- | --- |
| 1 | [`所有权与借用/`](./所有权与借用/) | 5 类报错(E0382/E0499/E0502/E0596/E0106)的判定规则被抽成可运行期执行的检查器;NLL 把借用区间终点从「块尾」收缩到「最后一次使用」 |
| 2 | [`Copy与Drop/`](./Copy与Drop/) | drop scope 四条顺序规则(逆声明序 / 由内向外 / 字段声明序 / 模式内逆序)全部断言;`Copy` + `Drop` 互斥 = E0184 |
| 3 | [`trait与分发/`](./trait与分发/) | 单态化(0 次间接跳转、按类型复制代码实体)↔ `dyn`(胖指针 2 字宽、每次查 vtable);孤儿规则四组合;`impl Trait` 返回位只能单一类型 |
| 4 | [`智能指针/`](./智能指针/) | `Rc` 计数轨迹 `[1,2,3,2]`;`RefCell` 运行期 panic 而非 UB;`Rc` 引用环 strong 计数永不归零(泄漏但内存安全);`Weak` 破环 |
| 5 | [`生命周期/`](./生命周期/) | 三条省略规则的判定引擎(7 签名里只有 2 个必须显式标注);返回值生命周期取两输入中较短者;E0597 与 E0106 的分工 |

**原理速览**(细节已下沉到各 demo 的 README):

- **所有权三规则** = 每个值有唯一 owner / 同时只有一个 owner / owner 离开作用域即 drop;
  检查全在编译期,运行期零开销("None of the features of ownership will slow down your program while it's running")。
- **move vs copy** = `String` 赋值只复制栈上 ptr/len/cap 并让原变量失效(避免 double free);
  只有 `Copy` 类型(标量、只含 `Copy` 的元组)才隐式复制。
- **借用两规则** = 同时「一个 `&mut`」**或**「任意多个 `&`」;引用必须始终有效。
  目的是在编译期排除数据竞争三条件(同时访问 / 至少一个写 / 无同步)。
- **trait 三身份** = 接口 + 约束 + 分发开关;泛型单态化零运行期成本,`dyn` 换来异构容器与更小代码体积。
- **内部可变性** = 用 `unsafe` 把借用规则从编译期搬到运行期(违反即 panic),代价是错误后置 + 计数开销;
  逃生舱四件套 `Box`/`Rc`/`RefCell`/`Weak` 分别解决大小不定 / 多所有者 / 不可变想改 / 引用成环。
- **生命周期标注** 不改变任何值的存活时间,只把引用之间的关系写进签名成为契约。

## 四、待研究清单(按"教学价值"排序)

1. **所有权与借用检查** ✅ `09-语言学习/Rust/所有权与借用/` — E0382/E0499/E0502/E0596/E0106 五大报错的最小复现 + 词法作用域 vs NLL 双模型对比
2. **Move/Copy/Clone 与 drop 时机** ✅ `09-语言学习/Rust/Copy与Drop/` — drop scope 嵌套与四条顺序规则、覆盖赋值即 drop、部分 move、`mem::forget`、Copy 与 Drop 互斥(E0184)
3. **trait 与静态分发** ✅ `09-语言学习/Rust/trait与分发/` — 单态化 vs vtable、胖指针宽度、孤儿规则/coherence 四组合、blanket impl、条件实现、`impl Trait` 返回位限制
4. **智能指针与内部可变性** ✅ `09-语言学习/Rust/智能指针/` — Box 递归类型与地址不变性、deref coercion 三情形、Rc 计数轨迹、RefCell 运行期借用、引用环泄漏与 Weak 破环
5. **生命周期标注** ✅ `09-语言学习/Rust/生命周期/` — 三条省略规则判定引擎、结构体持引用、`'static` 与常量提升、E0597 区间包含检查
6. [ ] **闭包与迭代器** → `Rust/Fn三兄弟与迭代器/` — `Fn`/`FnMut`/`FnOnce` 推导、捕获方式、零成本迭代链与手写循环的汇编级对比
7. [ ] **无畏并发** — `Send`/`Sync` 的 auto trait 语义、`Arc<Mutex<T>>`、`MutexGuard` 与死锁
8. [ ] **async/await 状态机** — Future 是惰性的、`poll` 与 `Waker`、Pin 解决的问题、task 与 executor 的关系
9. [ ] **错误处理惯用法** — `Result` + `?` + `From` 自动转换、`thiserror`/`anyhow` 的分工、`Box<dyn Error>`
10. [ ] **unsafe Rust 边界** — 裸指针、`UnsafeCell` 与内部可变性的底层、`transmute` 的前提、`unsafe` 不取消借用检查
11. [ ] **方法解析与 auto-deref** — 方法调用的候选集顺序、`&self`/`&mut self`/`self` 的自动引用、与 `Deref` 的交互
12. [ ] **variance 与协变** — `&'a T`/`&'a mut T` 的协变/逆变、`PhantomData` 的作用、闭包捕获对 variance 的影响
13. [ ] **trait 高级形态** — associated type vs 泛型参数、supertrait、GAT、trait object 上的关联类型
14. [ ] **宏系统** — `macro_rules!` 的卫生性与片段分类、过程宏三类(derive/attribute/function-like)、与 C 预处理器的本质差异

## 五、权威来源(本 README 实际阅读过的来源)

- [*The Rust Programming Language*(The Book) — Introduction](https://doc.rust-lang.org/book/ch00-00-introduction.html)
  —— 全部 21 章 + 附录 A–G 清单;零成本抽象与"安全 *and* 效率"的官方定位原文;project chapter = ch02/12/21。
- [The Book — ch10-02 Traits](https://doc.rust-lang.org/book/ch10-02-traits.html)
  —— trait 定义/默认方法、`impl Trait` 与 trait bound 的等价、孤儿规则与 *coherence* 原文、
  blanket implementation(`impl<T: Display> ToString for T`)、返回位 `impl Trait` 只能单一类型。
- [The Book — ch10-03 Lifetime Syntax](https://doc.rust-lang.org/book/ch10-03-lifetime-syntax.html)
  —— 三条省略规则、结构体持引用、`'static` 的措辞与"不要用它掩盖问题"的建议。
- [The Book — ch15-00 Smart Pointers](https://doc.rust-lang.org/book/ch15-00-smart-pointers.html)
  —— 智能指针定义、`Deref`/`Drop` 的必备性、`Box<T>`/`Rc<T>`/`RefCell<T>` 四件套、内部可变性与循环引用泄漏。

第一批(2026-09-15,demo 207-211)新增——即 5 个 demo 的逐节依据:

- [ch04-00 Understanding Ownership](https://doc.rust-lang.org/book/ch04-00-understanding-ownership.html)
  —— 所有权使 Rust "make memory safety guarantees without needing a garbage collector"。
- [ch04-01 What Is Ownership?](https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html)
  —— 栈/堆对比与性能原因、三条所有权规则原文、Move 与 double free、"never automatically create deep copies"、
  `E0382`、`Copy` 完整类型清单、`Copy`/`Drop` 互斥、传参 move/copy 与返回值转移所有权。
- [ch04-02 References and Borrowing](https://doc.rust-lang.org/book/ch04-02-references-and-borrowing.html)
  —— 引用即借用、`E0596`/`E0499`/`E0502` 三段完整报错、数据竞争三条件、
  引用作用域到"最后一次使用"为止、`E0106` 悬垂引用、两条借用规则。
- [ch10-01 Generic Data Types](https://doc.rust-lang.org/book/ch10-01-syntax.html)
  —— 泛型语法、`E0369` 与 `PartialOrd`、单态化定义与 "we pay no runtime cost"、`Option<i32>`/`Option<f64>` 展开示例。
- [ch15-01 Using `Box<T>`](https://doc.rust-lang.org/book/ch15-01-box.html)
  —— 三类使用场景、`E0072`/`E0391` 与 "insert some indirection" 建议、指针大小与数据量无关。
- [ch15-04 `Rc<T>`](https://doc.rust-lang.org/book/ch15-04-rc.html)
  —— 多所有权动机、`Rc::clone` vs `a.clone()`、`Listing 15-19` 的 `[1, 2, 3, 2]` 计数轨迹、单线程限制。
- [ch15-05 `RefCell<T>` and Interior Mutability](https://doc.rust-lang.org/book/ch15-05-interior-mutability.html)
  —— 内部可变性定义、编译期 vs 运行期检查取舍、`already borrowed: BorrowMutError`、
  `Rc<RefCell<i32>>` 的 `Listing 15-24`、`Box`/`Rc`/`RefCell` 对比 recap。
- [ch15-06 Reference Cycles Can Leak Memory](https://doc.rust-lang.org/book/ch15-06-reference-cycles.html)
  —— "memory leaks are memory safe in Rust"、`Rc::downgrade`/`Weak::upgrade` 语义、
  `Listing 15-28/15-29` 父子 `Node` 的 strong/weak 计数变化。
- [ch18-02 Using Trait Objects](https://doc.rust-lang.org/book/ch18-02-trait-objects.html)
  —— trait object 定义与 `Box<dyn Draw>`、泛型版与 dyn 版的取舍。
- [Rust Reference — Destructors](https://doc.rust-lang.org/reference/destructors.html)
  —— drop scope 列表与嵌套、"reverse order of declaration"、字段声明序 / 数组首→尾 / 模式内逆序、
  参数最后 drop 与 `3 2 0 1` 示例、赋值即 drop、部分 move、常量提升。
- [std — `core::ops::Deref`](https://doc.rust-lang.org/std/ops/trait.Deref.html)
  —— deref coercion 三条形式、"should never unexpectedly fail"、"Be careful about implementing Deref"。
- [std — `core::keyword.dyn`](https://dev-doc.rust-lang.org/core/keyword.dyn.html)
  —— "a `dyn Trait` reference contains two pointers"、vtable 查询、"Formerly known as object safe."。
- [rustc error codes — E0184](https://doc.rust-lang.org/1.74.0/error_codes/E0184.html)
  —— `Copy` 与 `Drop` 互斥的官方错误说明与历史原因(issue #20126)。

## 六、与已有 demo 的边界

- **领域 demo 里的 Rust 实现**(如 `08-安全/01-密码学/`、`06-DevOps/01-容器化/` 后续若补 Rust):
  聚焦该知识点的 Rust 写法,**不展开**语言其他机制
- **本目录的 demo**:只讲 Rust 本身——所有权、借用、trait、生命周期、`unsafe` 边界

例:
- `09-语言学习/Python/装饰器/` 讲的是"函数是一等对象 + 闭包 + 语法糖"
- `09-语言学习/Rust/trait与分发/` 要讲的是"trait 如何同时充当接口、约束与单态化开关",跟具体业务场景无关

## 七、进度

由 [`_docs/STATE.md`](../../_docs/STATE.md)(主,≤ 5 KB)+ [`archive/`](../../_docs/archive/)(历史回溯)统一追踪。
本子类目当前状态:**README 已建 + 第一批 5 个 demo 完成(2026-09-15,demo 207-211)**;
剩余 §四 清单 6–14(闭包迭代器 / 并发 / async / 错误处理 / unsafe / 方法解析 / variance / 高级 trait / 宏)。
