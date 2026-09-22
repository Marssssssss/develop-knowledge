# 方法解析与自动解引用（候选接收者列表 + 搜索顺序）

> Rust 第三批 · demo 586 · 依据 Rust Reference `expressions/method-call-expr` 实读。
>
> 一句话：**`a.f()` 不是「在 `a` 的类型上找方法」，而是先构造一条候选类型列表
> （反复解引用，每个 `T` 后面紧跟 `&T` 与 `&mut T`，末尾再补一次未定长强制转换），
> 然后**按列表顺序**依次问「这个类型有没有接收者正好是它的方法」——
> 于是「谁先被问到」比「谁的签名更合适」更能决定命中结果。**

## 一、原理详解

### 1.1 两步流程（官方原文）

第一步，构造候选接收者类型：

- 反复解引用接收者表达式的类型，把遇到的每个类型加入列表；
- 最后尝试一次数组未定长强制转换（unsized coercion），成功则把结果加入；
- **然后对每个候选 `T`，把 `&T` 与 `&mut T` 紧跟在 `T` 之后加入列表。**

官方给的完整例子（本 demo 断言 1.2 逐项比对）：

```text
接收者类型 Box<[i32;2]>
→ Box<[i32;2]>, &Box<[i32;2]>, &mut Box<[i32;2]>
  [i32;2],     &[i32;2],       &mut [i32;2]
  [i32],       &[i32],         &mut [i32]        // [i32;2] 未定长强制转换得到
```

第二步，按候选顺序搜索；对每个候选类型 `T`：

1. `T` 的**固有方法**（直接写在 `impl T` 里的）；
2. `T` 实现的**可见 trait** 提供的方法（若 `T` 是类型参数，**先查 bound 里的 trait**，
   再查作用域内其余 trait）。

关键结论：**候选类型的顺序优先于方法签名的匹配度**。

### 1.2 「出人意料」的官方例子

```rust
struct Foo {}
trait Bar { fn bar(&self); }

impl Foo { fn bar(&mut self) { println!("In struct impl!") } }
impl Bar for Foo { fn bar(&self) { println!("In trait impl!") } }

let mut f = Foo{};
f.bar();   // 打印 "In trait impl!"
```

原因是**接收者形态决定候选位置**：`&self` 方法的接收者类型是 `&Foo`（候选 #1），
`&mut self` 方法的是 `&mut Foo`（候选 #2）。遍历到 `&Foo` 时就命中了 trait 方法，
struct 自己的 `&mut self` 版本根本没机会被问到。

本 demo 用**成对对照**钉死这一点（断言 3.1 / 3.3）：只把 struct 的方法从
`&mut self` 改成 `&self`，命中方就立刻从 trait 回到固有方法。

### 1.3 查找不考虑可变性 / 生命周期 / unsafe

官方原文：*"This process does not take into account the mutability or lifetime of
the receiver, or whether a method is `unsafe`."*

也就是说这三件事**不参与选择**，只在选定之后做检查：

| 阶段 | 做什么 |
| --- | --- |
| 查找 | 只要接收者类型对得上就选中（断言 7.1：不可变绑定也照样选中 `&mut self`） |
| 调用 | 再判可变性 → E0596（7.2）、unsafe → E0133（7.4）、找不到 → E0599（7.5） |

### 1.4 三级优先，同级才歧义

`固有方法 > bound 里的 trait > 作用域内其余 trait`。跨级时高一级直接胜出、
**不算歧义**（断言 4.1）；只有**同一级里出现多个**才是 E0034（断言 6.1、8.1）。

### 1.5 数组的 `into_iter`：edition 差异的真实原因

官方 EDITION-2021 说明：2021 之前，**候选接收者类型若是数组类型，
`IntoIterator` 提供的方法被忽略**。于是 `[1,2,3].into_iter()` 会跳过
`[i32;3]` 上的按值实现，落到 `&[i32;3]` 上，迭代出**引用**；2021 起不再忽略，
直接按值迭代。本 demo 断言 10.x 两个 edition 各跑一遍。

## 二、对比

| 维度 | 方法调用 `a.f()` | 完全限定 `T::f(a)` / `<T as Tr>::f(a)` |
| --- | --- | --- |
| 是否走候选列表 | 是 | 否，直接指定 |
| 自动借用 / 解引用 | 会 | 不会（要自己写 `&`、`*`） |
| 同名冲突 | E0034 | 可绕过（`Foo::bar(&mut f)` 明确要固有版本） |
| trait object 同名 | 报错 | 只命中 trait 方法，固有方法**无法调用** |

| 维度 | 泛型参数 | 具体类型 |
| --- | --- | --- |
| 方法来源 | bound 里的 trait 先查 | 固有 → 其余 trait |
| 单态化 | 每个实例一份实体 | — |

## 三、环境

- Rust 2021 edition，纯标准库；`cargo run` 即可。
- Python 3 模型（可运行、含 39 项断言）：`python selfcheck_resolve.py`。

## 四、运行方式

```bash
cd rust && cargo run
cd python && python main.py               # 5 个反直觉场景的演示输出
cd python && python selfcheck_resolve.py  # 39 项断言
```

`rust/compile_fail/` 下 4 个文件**故意编译失败**：

| 文件 | 报错 | 想说明什么 |
| --- | --- | --- |
| `e0034_two_traits.rs` | E0034 | 同一级两个 trait 提供同名方法 |
| `e0596_immutable_receiver.rs` | E0596 | 查找不看可变性，选中后才报错 |
| `e0599_no_method.rs` | E0599 | 候选链走完仍无同名方法 |
| `e0034_trait_object_clash.rs` | E0034 | trait object 上固有与 trait 同名 |

## 五、关键代码

**候选列表构造（Python 模型，真跑）**

```python
for t in candidate_types(ty, env):     # 反复解引用 + 末尾未定长强制转换
    out.append(t)
    out.append("&" + t)                # 紧跟 &T
    out.append("&mut " + t)            # 紧跟 &mut T
```

**三级优先（Python 模型，真跑）**

```python
inherent = [m for m in hits if m.is_inherent]
if inherent: return inherent
in_bound = [m for m in hits if m.owner in bound_traits]
if in_bound: return in_bound
return hits                            # 只有这一级里 >1 个才 E0034
```

## 六、性能边界

- 解析**全部发生在编译期**，运行期零开销：候选列表、搜索顺序、自动借用
  都只在类型检查阶段存在。
- `Box<T>` / `Rc<T>` / `String` 的自动解引用**不产生运行期成本**，
  `(*b).len()` 与 `b.len()` 编译产物一致。
- 代价体现在**可读性**：候选列表可能很长（`Box<[i32;2]>` 就有 9 个），
  长链会让「到底调了谁」变得不直观——这正是 1.2 那个例子让多数人意外的原因。

## 七、注意事项与常见坑

1. **`&self` 方法会抢在 `&mut self` 方法前面被找到**（官方例子）——
   想调 struct 自己的可变版本就写 `Foo::bar(&mut f)`。
2. **可变性错误是 E0596 不是「找不到方法」**：说明方法其实找到了，
   只是接收者不可变。
3. **别在 trait object 上定义与 trait 同名的固有方法**：调用方
   没有任何语法能调到它。
4. **`Deref` 链会一直走到底**：`Box<String>` 能直接调 `str` 的方法，
   但这也意味着给类型加 `Deref` 等于**扩大了它的方法面**，是 API 设计决策。
5. **数组 `into_iter` 的行为随 edition 变化**：升级到 2021 后
   `array.into_iter()` 的产出从引用变成值，是真实的语义变更。
6. **两个 trait 同名方法**：把其中一个写进 bound 可以消歧（bound 优先），
   否则必须用完全限定语法。

## 八、参考资料（实际阅读）

- [Rust Reference — Method-call expressions](https://doc.rust-lang.org/reference/expressions/method-call-expr.html)
  —— 候选接收者列表的两步构造、`Box<[i32;2]>` 九候选原文、固有先于 trait、
  类型参数时 bound 先查、"does not take into account the mutability or lifetime
  of the receiver, or whether a method is unsafe"、`&self` 抢先的 NOTE 与完整例子、
  trait object 同名的 WARNING、EDITION-2021 下数组忽略 `IntoIterator` 的特例。
- [Rust Reference — Subtyping and variance](https://doc.rust-lang.org/reference/subtyping.html)
  —— 本批 demo 587 的主源；这里用于确认「候选类型」是**类型**而非值语义。
- [Rust Reference — Traits（Dyn compatibility）](https://doc.rust-lang.org/reference/items/traits.html)
  —— trait object 能被构造的前提（本批 demo 588 详述）。
