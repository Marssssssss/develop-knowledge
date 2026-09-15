# 生命周期标注与省略规则

> `09-语言学习/Rust/` 子类目首批 demo 5/5（2026-09-15）。
> 依据：The Rust Programming Language *ch10-03 Validating References with Lifetimes*、Rust Reference *Destructors*。

## 简介

生命周期标注是 Rust 里最容易被误解的语法：它**不改变任何值的存活时间**，
只是把「多个引用之间的存活关系」写进函数签名，成为借用检查器要遵守的契约。
本文把三条省略规则做成可判定的引擎，并把「被引用数据活得比引用短」这一违反情形量化为 E0597。

关键概念：

- **生命周期参数**："Lifetime annotations don't change how long any of the references live.
  Rather, they describe the relationships of the lifetimes of multiple references to each other."
- **输入 / 输出生命周期**：参数上的生命周期叫 input lifetime，返回值上的叫 output lifetime。
- **省略规则（elision rules）**：编译器内置的三条特例；命中则无须显式标注。
- **`'static`**：表示引用**可以**存活整个程序；所有字符串字面量都是 `'static`。
- **常量提升**：可常量求值且被借用的表达式会被提升到 `'static` 槽位（如 `&None`）。
- **E0597**：被引用对象在引用的某个使用点已离开作用域（`does not live long enough`）。

## 原理详解

### 1. 三条省略规则（The Book 原文）

```text
规则 1  每个引用参数各自获得一个生命周期参数
        fn foo<'a>(x: &'a i32) / fn foo<'a, 'b>(x: &'a i32, y: &'b i32)
规则 2  恰好一个输入生命周期 → 该生命周期赋给所有输出
        fn foo<'a>(x: &'a i32) -> &'a i32
规则 3  多个输入，但其中一个是 &self / &mut self（仅方法）→ 输出的生命周期取 self 的
```

性质："These aren't rules for programmers to follow; they're a set of particular cases that the
compiler will consider, and if your code fits these cases, you don't need to write the lifetimes explicitly."
三条都不适用时（**自由函数 + 多个引用入参 + 返回引用**）必须显式标注，否则 E0106。

### 2. 本 demo 的 elision 引擎判定结果

| 签名 | 命中的规则 | 结论 |
| --- | --- | --- |
| `fn first_word(s: &str) -> &str` | 规则 2（唯一输入 `'a` 赋给输出） | ✅ 可省略 |
| `fn describe(x: &str, y: &str) -> usize` | 输出不含引用 | ✅ 无需标注 |
| `impl<'a> ImportantExcerpt<'a> { fn level(&self) -> i32 }` | 输出不含引用 | ✅ 无需标注 |
| `fn announce_and_return_part(&self, ann: &str) -> &str` | **规则 3**（输出取 `&self` 的 `'a`） | ✅ 可省略 |
| `fn longest(x: &str, y: &str) -> &str` | 规则 1/2/3 均不适用 | ❌ 必须显式标注（E0106） |
| `fn make_static() -> &str`（无引用入参） | 无入参可借 | ❌ 除非返回 `'static` |

统计：7 个签名中 **2 个**必须显式标注——正好对应「自由函数 + 多引用入参」与「无入参却返回引用」两种情形。

### 3. 返回值的有效期 = 两输入中较短者

```rust
fn longest<'a>(x: &'a str, y: &'a str) -> &'a str
```

同一生命周期 `'a` 施加在三个位置，语义是「返回的引用在 x 与 y **都还活着**的区间内有效」，即取两者交集。
本 demo 的合法用法（在 `short` 仍存活时使用返回值）生存区间为 `r: [==]` ⊂ `x: [===]` → 合法；
把 `println!` 挪到 `short` 的作用域之外则得到：

```text
E0597: `x` does not live long enough —— 在 `r` 的使用点（step 5）已离开作用域
```

### 4. 底层发生了什么：区间包含关系

Reference 规定控制流离开 drop scope 时数据被销毁，而借用检查器比较的是「引用的区间」与
「被引用数据的区间」——**引用区间必须被数据区间包含**。本 demo 用每个绑定的存活区间
`[声明点, 所在块的结束]` 直接做包含判断，这也是 E0597 报错里
`binding x declared here` / `x dropped here while still borrowed` / `borrow later used here` 三条标注的由来。

### 5. `'static` 与常量提升

```rust
let s: &'static str = "I have a static lifetime.";   // 字面量编进二进制
let promoted: &'static Option<i32> = &None;          // 常量提升到 'static 槽位
```

The Book 的提醒很实用：**错误信息建议 `'static` 时，通常真正的问题是要修悬垂引用或生命周期不匹配**，
而不是直接标 `'static` —— "the solution is to fix those problems, not to specify the `'static` lifetime."

## 对比 / 选型

| 场景 | 写法 | 是否需要标注 |
| --- | --- | --- |
| 单引用入参、返回引用 | `fn f(s: &str) -> &str` | 否（规则 2） |
| 多引用入参、输出不借用 | `fn f(x: &str, y: &str) -> usize` | 否 |
| 多引用入参、返回引用（自由函数） | `fn f<'a>(x: &'a str, y: &'a str) -> &'a str` | **是** |
| 方法返回 `&self` 的字段 | `fn f(&self, a: &str) -> &str` | 否（规则 3） |
| 结构体持有引用 | `struct S<'a> { p: &'a str }` | **是**（字段必须标注） |
| `impl` 块 | `impl<'a> S<'a> { .. }` | **是**（类型名后要复述参数） |
| 返回编译期常量引用 | `fn f() -> &'static str { "x" }` | 是（写 `'static`） |

## 环境准备

- 操作系统：任意
- Rust：1.63+（常量提升与 `&'static Option<_>` 行为见 Rust Reference）
- Python：3.9+

## 运行方式

```bash
cd rust && cargo run                                       # 或： rustc -O main.rs -o demo && ./demo
cd rust/compile_fail && rustc e0597_does_not_live_long_enough.rs   # 被引用数据先 drop
cd rust/compile_fail && rustc e0106_two_inputs_no_annotation.rs    # 省略规则不适用
cd python && python3 lifetime_solver.py
```

## 关键代码片段

结构体持引用 + 省略规则 3（方法返回 `&self` 的一部分）：

```rust
struct ImportantExcerpt<'a> {
    part: &'a str,
}

impl<'a> ImportantExcerpt<'a> {
    fn level(&self) -> i32 { 3 }                       // 不返回引用 → 无需 'a

    // 省略规则 3：等价于 fn announce_and_return_part<'b>(&'b self, ..) -> &'b str
    fn announce_and_return_part(&self, announcement: &str) -> &str {
        println!("      注意！{announcement}");
        self.part                                       // 借的是 self，不是 announcement
    }
}
```

Python 侧的 elision 引擎核心（规则 3 的判定优先于规则 2）：

```python
ref_params = [p for p in sig.params if p.kind in ("ref", "ref_mut", "self", "self_mut")]
self_param = next((p for p in ref_params if p.kind in ("self", "self_mut")), None)
if len(ref_params) > 1 and self_param is not None and sig.is_method:
    return input_lt[self_param.name], "规则 3：输出取 self 的"
if len(ref_params) == 1:
    return input_lt[ref_params[0].name], "规则 2：唯一输入的生命周期赋给输出"
return None, "规则 1/2/3 均不适用 → E0106"
```

## 运行结果（本机实测）

```text
=== A. elision 引擎 ===
✅ 可省略     first_word(s: ref) -> 引用=True        规则 2：唯一输入 'a 赋给输出
❌ 需显式标注  longest(x: ref, y: ref) -> 引用=True   规则 1/2/3 均不适用 → E0106
✅ 可省略     describe(x: ref, y: ref) -> 引用=False  输出不含引用 → 无需任何标注
✅ 可省略     ImportantExcerpt::announce_and_return_part → 规则 3：输出取 self 的 'a
❌ 需显式标注  make_static() -> 引用=True            无引用参数却有引用输出 → E0106
=== B. 生存区间与 E0597 ===
✅ 在 x 作用域内使用 r → 合法；❌ 在 x 作用域外使用 r → E0597
=== C. 'static 与常量提升 ===   LITERAL / PROMOTED_AND_NONE 生存区间均为 [======] 'static
签名 7 个，其中 2 个必须显式标注；省略规则命中：规则 2 ×2、规则 3 ×1、无需标注 ×2
```

## 性能与边界

- **零运行期成本**：生命周期是纯编译期概念，不产生任何代码；The Book 的总结是
  "all of this analysis happens at compile time, which doesn't affect runtime performance!"
- **标注不改变语义**：只把约束写进签名（"The lifetime annotations become part of the contract of
  the function, much like the types in the signature"）。
- **边界**：本 demo 不覆盖 lifetime bound（`T: 'a`）、HRTB（`for<'a>`）、`'_` 匿名生命周期、
  协变/逆变（variance）与 GAT 等进阶主题，这些需要专门的 demo。
- **实际影响**：过度收紧生命周期会让 API 难用（给函数强加不必要的关系），
  因此「能不写就不写、必须写才写」是官方推荐的心智模型。

## 注意事项与常见坑

1. **生命周期标注不是「延长寿命」**：想让引用活更久，正确做法是移动所有权或提高数据的作用域。
2. **`E0106` 与 `E0597` 是两回事**：前者是「签名里没法表达」，后者是「调用点上的区间不满足」。
3. **方法返回值默认借 `&self`**（规则 3）：若实际借的是别的参数，必须显式标注，
   否则会得到一个看似能编译、但所有权契约错误的签名。
4. **结构体字段的 `'a` 必须写在 `impl` 上**："Lifetime names for struct fields always need to be declared
   after the `impl` keyword and then used after the struct's name"。
5. **别乱加 `'static`**：它是「能活满整个程序」，绝大多数报错场景加它只会把问题推得更远；
   字面量与常量提升值天然是 `'static`。

## 参考资料（实际阅读过的权威来源）

- [The Book — ch10-03 Validating References with Lifetimes](https://doc.rust-lang.org/book/ch10-03-lifetime-syntax.html)
  —— 「标注不改变生命周期、只描述关系」与 "become part of the contract" 原文、输入/输出生命周期术语、
  三条省略规则完整表述与 "these aren't rules for programmers to follow"、
  `Listing 10-17` 的 `'a`/`'b` 区间对比与 `E0597` 报错、
  `Listing 10-21/10-22/10-23` 的 `longest` 与「返回生命周期取较小者」的解释、
  结构体持引用的 `Listing 10-24` 与 `impl<'a>` 写法、`'static` 的精确措辞与「不要用它掩盖问题」的建议、
  `longest_with_an_announcement<'a, T>` 的 `where` 子句示例、章节收尾（编译期分析不影响运行期性能）。
- [Rust Reference — Destructors](https://doc.rust-lang.org/reference/destructors.html)
  —— drop scope 列表与嵌套关系、局部变量与块的关联、参数的 `entire function` 作用域、
  **常量提升**（"Promotion of a value expression to a `'static` slot..."，及其「结果值不含内部可变性或析构函数」的约束）、
  临时值生命周期延长（基于模式 / 基于表达式的扩展规则）。
