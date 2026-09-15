# trait 与静态/动态分发

> `09-语言学习/Rust/` 子类目首批 demo 3/5（2026-09-15）。
> 依据：The Rust Programming Language *ch10-01* / *ch10-02* / *ch18-02*、std 文档 `core::keyword::dyn`。

## 简介

Rust 的 `trait` 同时扮演三个角色：**接口**（定义可调用的方法集合）、**约束**（trait bound 限定泛型可用类型）、
**分发开关**（选择编译期单态化还是运行期查 vtable）。本文把三者拆开做可观测实验：
单态化能直接打印出每种具体类型生成的实体名，trait object 的**胖指针宽度**也能被 `size_of` 量出来。

关键概念：

- **trait**："defines the functionality a particular type has and can share with other types"；
  默认方法可以调用同 trait 里没有默认实现的方法，于是实现者只需补最小一部分。
- **单态化（monomorphization）**：编译期按用到的具体类型把泛型代码各展开一份，
  因此 "we pay no runtime cost for using generics"。
- **trait object（`dyn Trait`）**：类型被擦除，引用里存**两个指针**——一个指向数据，一个指向 vtable。
- **孤儿规则 / coherence**：只有当 trait 或类型至少有一个本地，才能 `impl`；否则报 E0117。
- **blanket implementation**：`impl<T: Display> ToString for T` —— 实现 `Display` 即白拿 `to_string()`。
- **返回位 `impl Trait` 的限制**：只能返回**单一具体类型**；要多类型必须用 trait object。

## 原理详解

### 1. 静态分发：单态化到底做了什么

The Book 用 `Option<T>` 举例：`Some(5)` 与 `Some(5.0)` 会被展开成 `Option_i32` / `Option_f64` 两份定义。
本 demo 用 `std::any::type_name::<T>()` 把「展开成哪几份」打印出来：
`notify_static(&article)` → `...::NewsArticle`，`notify_static(&post)` → `...::SocialPost`。
推论：**调用点无间接跳转、可以内联**，代价是**代码体积**随具体类型数量线性增长。

### 2. 动态分发：胖指针与 vtable

`&dyn Trait` = (data pointer, vtable pointer)，因此宽度是**两个字**。本 demo 的断言：

```text
size_of::<&NewsArticle>()     = 8  字节（瘦指针）
size_of::<&dyn Summary>()     = 16 字节（= 数据指针 + vtable 指针）
size_of::<Box<dyn Summary>>() = 16 字节          assert_eq!(fat, 2 * thin);
```

调用流程：查 vtable 取函数指针 → 间接调用。该方法**通常不能被内联**，但代码只保留一份（与实现者数量无关）。

### 3. `impl Trait` 与泛型参数的差别

| 写法 | 允许不同类型 | 强制同类型 |
| --- | --- | --- |
| `fn notify(a: &impl Summary, b: &impl Summary)` | ✅ | ❌ |
| `fn notify<T: Summary>(a: &T, b: &T)` | ❌ | ✅ |

`impl Trait` 在参数位置是 trait bound 的语法糖；**要约束「两个参数同类型」必须写泛型 `T`**。
返回位置则相反地受限：

```rust
fn make_single() -> impl Summary { SocialPost { .. } }   // ✅ 单一具体类型
fn make_any(flag: bool) -> Box<dyn Summary> { .. }       // ✅ 运行期决定（多类型）
// fn f(flag: bool) -> impl Summary { if flag { NewsArticle } else { SocialPost } }
//                                                              ↑ E0308：两分支类型不兼容
```

The Book 的解释："you can only use `impl Trait` if you're returning a single type."

### 4. 孤儿规则 / coherence 的四种组合

| trait | 类型 | 能否在本 crate 实现 | 例 |
| --- | --- | --- | --- |
| 本地 | 本地 | ✅ | 自定义 trait for 自定义类型 |
| 本地 | 外部 | ✅ | 自定义 trait for `Vec<T>` |
| 外部 | 本地 | ✅ | `Display` for 自定义类型 |
| 外部 | 外部 | ❌ E0117 | `Display` for `Vec<T>` |

理由："Without the rule, two crates could implement the same trait for the same type,
and Rust wouldn't know which implementation to use."

### 5. 运行期 vs 编译期：错误被提前

The Book 收尾段值得记："In dynamically typed languages, we would get an error at runtime if we called
a method on a type that didn't define the method. But Rust moves these errors to compile time...
we don't have to write code that checks for behavior at runtime, because we've already checked at compile time."

## 对比 / 选型

| 维度 | 静态 `<T: Summary>` | 动态 `&dyn Summary` |
| --- | --- | --- |
| 代码实体数量 | 每一具体类型一份 | 一份（与实现者数量无关） |
| 调用点间接跳转 | 0（可内联） | 每次调用 1 次查表 + 1 次间接调用 |
| 异构集合 `Vec<..>` | ❌（`Vec<T>` 只能一种 `T`） | ✅ `Vec<Box<dyn Trait>>` |
| 值大小 | 无额外开销 | 引用/Box 变成两字宽 |
| 新增实现者的编译成本 | 每次新类型都重新单态化 | 无（只多一个 impl 实体） |
| 适用 | 热路径、类型集合已知、追求内联 | 插件式扩展、异构容器、编译时间敏感 |

## 环境准备

- 操作系统：任意
- Rust：1.63+（`dyn` 关键字自 2018 edition 起为标准写法）
- Python：3.9+

## 运行方式

```bash
cd rust && cargo run                              # 或： rustc -O main.rs -o demo && ./demo
cd rust/compile_fail && rustc orphan_rule_violation.rs    # E0117：外部 trait + 外部类型
cd rust/compile_fail && rustc impl_trait_two_types.rs     # E0308：opaque type 不兼容
cd python && python3 dispatch.py
```

## 关键代码片段

trait 定义与「默认方法调用必需方法」：

```rust
pub trait Summary {
    fn summarize_author(&self) -> String;     // 必需
    fn summarize(&self) -> String {           // 默认实现，可以调用上面这个
        format!("(Read more from {}...)", self.summarize_author())
    }
}
impl Summary for NewsArticle {
    fn summarize_author(&self) -> String { format!("@{}", self.author) }   // summarize 白拿
}
impl Summary for SocialPost {
    fn summarize_author(&self) -> String { format!("@{}", self.username) }
    fn summarize(&self) -> String { format!("{}: {}", self.username, self.content) }  // 覆盖
}
```

blanket impl 的效果（无需为 `NewsArticle` 手写 `to_string`）：
`impl Display for NewsArticle {..}` 之后 `let s: String = article.to_string();`
即来自标准库的 `impl<T: Display> ToString for T`，断言 `s == "Rust 单态化 (doc)"`。

## 运行结果（本机实测）

```text
[静态] 单态化实体 = trait_and_dispatch::NewsArticle / ...::SocialPost
size_of::<&NewsArticle>() = 8 字节 / size_of::<&dyn Summary>() = 16 字节
异构 Vec<Box<dyn Summary>> 长度 = 2 → [0] 默认实现路径；[1] 覆盖实现路径
原子断言：mono[0] == mono[1]、fat == 2 * thin、article.to_string() == "Rust 单态化 (doc)"
```

Python 侧对照（把「实体数量 ↔ 间接跳转」做成计数表）：
`静态分发 调用 2 次 → 实体 2 份，间接跳转 0 次`；
`动态分发 &dyn Summary 实现者 3 个 → 实体 1 份，间接跳转 3 次`；`trait object 尺寸 = 16 字节`；
coherence 四组合全部符合预期（仅「外部 trait + 外部类型」被拒绝）。

## 性能与边界

- **泛型的运行期成本为 0**："Because Rust compiles generic code into code that specifies the type in
  each instance, we pay no runtime cost for using generics."
- **动态分发成本**：每次调用多一次间接跳转，且通常**阻断内联**——ch18-02 的表述是
  "requiring slower virtual function calls, and effectively inhibiting any chance of inlining"。
- **代码体积**：单态化体积 ≈ 具体类型数量 × 函数体大小；`dyn` 侧固定为「一份实现 + 一份调用点」。
- **`dyn` 兼容性（旧称 object safety）**：方法不能带类型参数、不能按值返回 `Self`、不能有
  associated constant 等，否则 `dyn Trait` 无法构造。官方文档已把该概念改称 **dyn compatibility**
  （`core::keyword.dyn` 脚注：*"Formerly known as object safe."*）。
- **边界**：本 demo 不构造自定义 vtable、不做汇编级验证（本机无 Rust 工具链，
  采用人工代码审查 + `_docs/tools/syntax_lint.py` 结构校验）。

## 注意事项与常见坑

1. **`impl Trait` 不是「任意实现者」的同义词**：返回位只能一个具体类型；
   想返回不同分支的不同类型必须 `Box<dyn Trait>`。
2. **`dyn Trait` 会丢掉具体类型信息**：需要找回时用 `std::any::Any::downcast_ref`。
3. **`&dyn Trait` 是胖指针**（2 字宽），塞进 `Option` 或结构体时改变布局与对齐。
4. **默认方法不能被覆盖实现调用**："it isn't possible to call the default implementation from an
   overriding implementation of that same method."
5. **泛型过度使用会拖慢编译**：单态化在编译期为每种类型生成代码，类型组合爆炸时编译时间显著上升；
   孤儿规则阻碍「外部 trait for 外部类型」时，可用 newtype 包装绕过。

## 参考资料（实际阅读过的权威来源）

- [The Book — ch10-01 Generic Data Types](https://doc.rust-lang.org/book/ch10-01-syntax.html)
  —— 泛型函数/结构体/枚举语法、`Point<T,U>` 多参数、`E0369` 与 `PartialOrd` 约束、
  "Performance of Code Using Generics" 一节（单态化定义、"we pay no runtime cost"、`Option<i32>`/`Option<f64>` 展开）。
- [The Book — ch10-02 Traits](https://doc.rust-lang.org/book/ch10-02-traits.html)
  —— trait 定义与 `Listing 10-12/10-14` 默认实现、默认方法调用无默认实现的方法、
  `impl Trait` 与 trait bound 的等价、`+` 多约束与 `where` 子句、返回位只能单一类型的原文、
  孤儿规则与 coherence、blanket impl、`Listing 10-15` 条件实现、收尾「把错误提前到编译期」的论述。
- [The Book — ch18-02 Using Trait Objects to Abstract over Shared Behavior](https://doc.rust-lang.org/book/ch18-02-trait-objects.html)
  —— trait object 定义（"points to both an instance of a type implementing our specified trait and a table
  used to look up trait methods on that type at runtime"）、`Box<dyn Draw>` 用法、泛型版与 dyn 版的取舍。
- [std 文档 — `core::keyword.dyn`](https://dev-doc.rust-lang.org/core/keyword.dyn.html)
  —— "a `dyn Trait` reference contains two pointers"、vtable 查询流程、
  动态分发不能内联但代码体积更小、以及 "Formerly known as object safe." 的命名变更。
