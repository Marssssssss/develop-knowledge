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
| 错误处理 | ch09 | ✅ | `panic!` vs `Result` + `?` 传播 | 9 |
| 泛型 / trait / 生命周期 | ch10 | ✅ | trait bound、孤儿规则、`'a` | 3 / 5 |
| 智能指针 | ch15 | ✅ | `Box` / `Rc` / `RefCell` + `Deref`/`Drop` | 4 |
| 无畏并发 | ch16 | 进阶 | `Send`/`Sync` + `Arc<Mutex<T>>` | 7 |
| async/await | ch17 | 进阶 | Future / task / stream | 8 |
| 闭包与迭代器 | ch13 | ✅ | `Fn`/`FnMut`/`FnOnce`,零成本迭代链 | 6 |
| 模式匹配 | ch19 | 进阶 | 全量模式参考 | — |
| unsafe / 宏 / 高级 trait | ch20 | 进阶 | 裸指针、`macro_rules!`、GAT | 10 |

## 三、已完成 demo(第一批 5 个 2026-09-15 · 第二批 5 个 2026-09-19 · 第三批 5 个 2026-09-22)

| # | demo | 一句话核心机制 |
| --- | --- | --- |
| 1 | [`所有权与借用/`](./所有权与借用/) | 5 类报错(E0382/E0499/E0502/E0596/E0106)的判定规则被抽成可运行期执行的检查器;NLL 把借用区间终点从「块尾」收缩到「最后一次使用」 |
| 2 | [`Copy与Drop/`](./Copy与Drop/) | drop scope 四条顺序规则(逆声明序 / 由内向外 / 字段声明序 / 模式内逆序)全部断言;`Copy` + `Drop` 互斥 = E0184 |
| 3 | [`trait与分发/`](./trait与分发/) | 单态化(0 次间接跳转、按类型复制代码实体)↔ `dyn`(胖指针 2 字宽、每次查 vtable);孤儿规则四组合;`impl Trait` 返回位只能单一类型 |
| 4 | [`智能指针/`](./智能指针/) | `Rc` 计数轨迹 `[1,2,3,2]`;`RefCell` 运行期 panic 而非 UB;`Rc` 引用环 strong 计数永不归零(泄漏但内存安全);`Weak` 破环 |
| 5 | [`生命周期/`](./生命周期/) | 三条省略规则的判定引擎(7 签名里只有 2 个必须显式标注);返回值生命周期取两输入中较短者;E0597 与 E0106 的分工 |
| 6 | [`闭包与迭代器/`](./闭包与迭代器/) | Fn/FnMut/FnOnce 是「加法式」推导(移出捕获值→只剩 FnOnce)、`move` 只改捕获方式不改 trait 集;迭代器惰性(不消费 `calls == 0`)与零成本的两个可计数指标(单趟 / 1 次分配) |
| 7 | [`无畏并发/`](./无畏并发/) | Send/Sync 是语言内置的 auto trait;`&mut T` 竟是 Sync(因 `& &mut T` 退化成只读);`Arc<RefCell<T>>` 连 Send 都不是;类型系统拦得住数据竞争但**拦不住死锁**(wait-for 图判环) |
| 8 | [`async状态机/`](./async状态机/) | Future 是惰性的(std 原文 inert);Waker 驱动只需 2 次 poll vs 盲轮询 101 次;只有「最近一次 Context」的 Waker 该被唤醒;Pin 只为自引用 future 存在 |
| 9 | [`错误处理惯用法/`](./错误处理惯用法/) | `?` 能作用于 5 种类型,但**只有 Result 系会调 `From::from`**;`Box<dyn Error>` = "any kind of error";std 明令 source() 与 Display「二者只能选一个」(写成可执行 lint) |
| 10 | [`unsafe边界/`](./unsafe边界/) | unsafe 只解锁五件事,**借用检查照常生效**(E0502 反例);`UnsafeCell` 只解除 `&T` 不可变保证、交叠 `&mut` 永远非法;安全抽象靠一句 `assert!` 把 UB 变成 panic |
| 11 | [`方法解析与自动解引用/`](./方法解析与自动解引用/) | 候选接收者列表 = 反复解引用 + 每个 `T` 后紧跟 `&T`/`&mut T` + 末尾未定长强转;按列表顺序三级查找(固有 > 约束 trait > 其余),命中**之后**才查可变性与 unsafe |
| 12 | [`型变与PhantomData/`](./型变与PhantomData/) | 子类型化只在生命周期上;struct 型变 = 字段合并(冲突即不变),裸位置**逐位置各算**;`&mut T` 在 T 上不变是安全前提;PhantomData 九种写法的型变 / 自动 trait / drop check 各不相同 |
| 13 | [`trait高级形态/`](./trait高级形态/) | 关联类型 vs 泛型参数的分水岭是「同类型能实现几次」;supertrait 闭包与环检测;GAT 把关联类型升级成类型族;dyn 兼容是六条**逐条否决**清单(GAT 与关联常量被否、`where Self: Sized` 能救) |
| 14 | [`声明宏与卫生性/`](./声明宏与卫生性/) | `macro_rules!` 是逐 token、**不向前看**的小解析器;片段分类符 + 跟随集(组的左定界符算前一个 token);mixed-site hygiene:局部与标签走定义处、其余走调用处 |
| 15 | [`过程宏与TokenStream/`](./过程宏与TokenStream/) | 三类过程宏必须住独立 `proc-macro` crate 且**完全不卫生**;声明宏与过程宏是**两套 token 定义**需双向转换;doc 注释即 `#[doc]` 属性 |

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
6. ✅ **闭包与迭代器** — `Fn`/`FnMut`/`FnOnce` 推导、捕获方式、零成本迭代链(demo 367)
7. ✅ **无畏并发** — `Send`/`Sync` 的 auto trait 语义、`Arc<Mutex<T>>`、`MutexGuard` 与死锁(371 目录 `无畏并发/`,demo 368)
8. ✅ **async/await 状态机** — Future 是惰性的、`poll` 与 `Waker`、Pin 解决的问题、task 与 executor 的关系(demo 369)
9. ✅ **错误处理惯用法** — `Result` + `?` + `From` 自动转换、`thiserror`/`anyhow` 的分工、`Box<dyn Error>`(demo 370)
10. ✅ **unsafe Rust 边界** — 裸指针、`UnsafeCell` 与内部可变性的底层、`transmute` 的前提、`unsafe` 不取消借用检查(demo 371)
11. ✅ **方法解析与 auto-deref** — 候选接收者列表构造顺序、三级优先级、歧义 E0034、`Deref` 与自动引用的交互(demo 586)
12. ✅ **variance 与协变** — `&'a T`/`&'a mut T` 的协变与不变、struct 由字段推导、`PhantomData` 的九种写法(demo 587)
13. ✅ **trait 高级形态** — associated type vs 泛型参数、supertrait、GAT、dyn 兼容六条规则(demo 588)
14. ✅ **宏系统** — `macro_rules!` 的片段分类与跟随集 + 卫生性(demo 589);过程宏三类与两套 token 定义(demo 590)

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

第二批(2026-09-19,demo 367-371)新增——逐节依据见各 demo 的 README §八,这里只列主源:

- [The Book — ch13-01 Closures](https://doc.rust-lang.org/book/ch13-01-closures.html)
  —— 三种捕获方式直接映射三种传参、"additive fashion"、`unwrap_or_else` 的 `FnOnce` 与
  `sort_by_key` 的 `FnMut`、E0308 / E0507。
- [The Book — ch13-02 Iterators](https://doc.rust-lang.org/book/ch13-02-iterators.html)
  —— "iterators are lazy"、`Iterator::next` 是唯一必实现方法、consuming vs iterator adapters。
- [The Book — ch13-04 Performance](https://doc.rust-lang.org/book/ch13-04-performance.html)
  —— 基准数字 19,620,300 vs 19,234,900 ns/iter 与 "zero-cost abstractions" 官方定义。
- [std — `marker::Send`](https://doc.rust-lang.org/std/marker/trait.Send.html)
  —— "transferred across thread boundaries"、`Rc` 非 Send 与 `Arc` 的对照。
- [std — `marker::Sync`](https://doc.rust-lang.org/std/marker/trait.Sync.html)
  —— 精确定义 "`&T` is `Send`"、四条引用关系、`&mut T is Sync` 的 surprising consequence。
- [std — `sync::MutexGuard`](https://doc.rust-lang.org/std/sync/struct.MutexGuard.html)
  —— `!Send` 及其理由、`impl<T: Sync> Sync`。
- [The Book — ch16-03 / ch16-04](https://doc.rust-lang.org/book/ch16-04-extensible-concurrency-sync-and-send.html)
  —— Mutex 两条规则被类型系统接管、`Rc`→`Arc` 的报错链、Send/Sync 是语言内置、手动实现是 unsafe。
- [std — `future::Future`](https://doc.rust-lang.org/std/future/trait.Future.html)
  —— "Futures alone are inert"、只保留最近一次 Waker、完成后不得再 poll。
- [async-book — The Future Trait](https://rust-lang.github.io/async-book/02_execution/02_future.html)
  —— 真实签名两处改动的理由、Join / AndThenFut 的 allocation-free state machines。
- [std — `pin` module](https://doc.rust-lang.org/std/pin/index.html)
  —— moving / pinning 定义、自引用类型、`AddrTracker` 里 `PhantomPinned` 摘掉自动 `Unpin`。
- [The Book — ch09-02 Result](https://doc.rust-lang.org/book/ch09-02-recoverable-errors-with-result.html)
  —— `?` 走 `From::from`、`Box<dyn Error>` = "any kind of error"、`main` 退出码约定。
- [Rust Reference — try propagation operator](https://doc.rust-lang.org/reference/expressions/operator-expr.html)
  —— 五种可作用类型的完整行为表、`Try`/`FromResidual` 去糖。
- [std — `error::Error`](https://doc.rust-lang.org/std/error/trait.Error.html)
  —— `Error: Debug + Display`、`source()`、"but not both" 规则。
- [thiserror](https://docs.rs/thiserror/latest/thiserror/) / [anyhow](https://docs.rs/anyhow/latest/anyhow/)
  —— derive 生成 Display、`#[from]` 隐含 `#[source]`;trait object 错误、`context()`、downcast。
- [The Book — ch20-01 Unsafe Rust](https://doc.rust-lang.org/book/ch20-01-unsafe-rust.html)
  —— 五项 superpower、"doesn't turn off the borrow checker"、安全抽象与 `split_at_mut`。
- [std — `cell::UnsafeCell`](https://doc.rust-lang.org/std/cell/struct.UnsafeCell.html)
  —— 内部可变性的核心原语、交叠 `&mut` 无合法途径、不防数据竞争。
- [Rustonomicon — Transmutes](https://doc.rust-lang.org/nomicon/transmutes.html)
  —— 尺寸是唯一限制、`&`→`&mut` 永远 UB、`repr(Rust)` 布局无保证。

第三批(2026-09-22,demo 586-590)主源(逐节依据见各 demo 的 README §八):

- [Rust Reference — Method call expressions](https://doc.rust-lang.org/reference/expressions/method-call-expr.html) —— 候选接收者列表的构造与查找顺序、官方 9 项 `Box<[i32;2]>` 例子。
- [Rust Reference — Subtyping and variance](https://doc.rust-lang.org/reference/subtyping.html) —— 型变三定义、内置类型型变表、struct 由字段推导、"outside of a struct … separately"。
- [Rust Reference — Traits](https://doc.rust-lang.org/reference/items/traits.html) —— supertrait、GAT、Dyn compatibility 六条否决规则;来源 `items/traits.md` + `items/associated-items.md`。
- [Rust Reference — Macros By Example](https://doc.rust-lang.org/reference/macros-by-example.html) —— 片段分类符、跟随集、重复匹配、混合位点卫生性(mixed-site hygiene)。
- [Rust Reference — Procedural Macros](https://doc.rust-lang.org/reference/procedural-macros.html) —— 三类过程宏、两套 token 定义、属性宏的四种输入拆分、不卫生性。

## 六、与已有 demo 的边界

- **领域 demo 里的 Rust 实现**(如 `08-安全/01-密码学/`、`06-DevOps/01-容器化/` 后续若补 Rust):
  聚焦该知识点的 Rust 写法,**不展开**语言其他机制
- **本目录的 demo**:只讲 Rust 本身——所有权、借用、trait、生命周期、`unsafe` 边界

例:
- `09-语言学习/Python/装饰器/` 讲的是"函数是一等对象 + 闭包 + 语法糖"
- `09-语言学习/Rust/trait与分发/` 要讲的是"trait 如何同时充当接口、约束与单态化开关",跟具体业务场景无关

## 七、进度

由 [`_docs/STATE.md`](../../_docs/STATE.md)(主,≤ 5 KB)+ [`archive/`](../../_docs/archive/)(历史回溯)统一追踪。
本子类目当前状态:**README 已建 + 第一批 5 个 demo(2026-09-15,demo 207-211)+ 第二批 5 个(2026-09-19,demo 367-371)+ 第三批 5 个(2026-09-22,demo 586-590),共 15 个**;
§四 清单 1–14 已全部完成;第三批 Python 自检共 245 项断言(39+61+44+51+50),实跑全通过。
