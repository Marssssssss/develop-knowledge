# trait 高级形态（关联类型 / supertrait / GAT / dyn 兼容）

> Rust 第三批 · demo 588 · 依据 The Book ch20-02、Reference `items/traits`（Dyn compatibility）
> 与 Reference `items/associated-items`（GAT）实读。
>
> 一句话：**关联类型与泛型参数的分水岭是「同一类型能实现几次」；
> supertrait 是「实现我之前必须先实现谁」；GAT 把「关联类型」从「一个类型」
> 升级成「一个类型族」；而 dyn 兼容（原 object safety）是一张**逐条否决**的清单——
> 关联常量要否决、GAT 要否决、`async fn` 要否决，但**不带泛型的关联类型不否决**。**

## 一、原理详解

### 1.1 关联类型 vs 泛型参数（The Book）

| | 泛型参数 `trait Iter<T>` | 关联类型 `trait Iter { type Item; }` |
| --- | --- | --- |
| 同一类型实现几次 | **可以**（换参数即可） | 只能一次（E0119） |
| 调用方是否要标注 | 要（`Iter<u32>::next` 与 `Iter<String>::next` 说不清） | 不要 |
| 语义 | 「这个 trait 对多种类型都成立」 | 「实现者**选定**一个类型」 |

官方原文：*"when a trait has a generic parameter, it can be implemented for a type
multiple times… With associated types, we don't need to annotate types, because
we can't implement a trait on a type multiple times."*

本 demo 断言 7.x 把「份数」这件事做成可执行判定：参数不同不算冲突、
参数完全相同才是 E0119；关联类型版的参数集合恒为空，所以第二次实现**必然**冲突。

### 1.2 supertrait（The Book + Reference）

```rust
trait OutlinePrint: Display { fn outline_print(&self) { /* 能用 self.to_string() */ } }
```

- 语义：**实现我之前必须先实现它**；写成 where 子句 `trait Circle where Self: Shape` 等价。
- 传递性：bound 一个 trait，就能访问它**全部祖先**的关联项（断言 4.5 / 4.6）。
- 官方还规定：**不能是自己的 supertrait**（断言 4.7 检测成环）。
- 对 dyn 兼容的传染性：supertrait 不兼容 ⇒ 子 trait 也不兼容（断言 4.4）。

### 1.3 GAT：关联类型带泛型

Reference 原文：关联类型**可以**带泛型参数和 where 子句，称为 *generic associated
types*，写法是 `<Thing as Trait>::Item<'x>`：

```rust
trait Lend {
    type Lender<'a> where Self: 'a;
    fn lend<'a>(&'a mut self) -> Self::Lender<'a>;
}
```

代价：**带泛型的关联类型会让 trait 失去 dyn 兼容**（见 1.4）。

### 1.4 dyn 兼容清单（原 object safety，Reference `items/traits`）

一个 trait 能做 trait object 的基 trait，当且仅当：

| # | 规则 | 本 demo 断言 |
| --- | --- | --- |
| 1 | 所有 supertrait 也必须是 dyn 兼容 | 4.4 |
| 2 | `Sized` **不能**是 supertrait | 4.2 |
| 3 | 不能有**关联常量** | 2.1 |
| 4 | 不能有**带泛型的关联类型**（GAT） | 3.2 |
| 5 | 每个关联函数要么可分派，要么**显式**不可分派 | 5.x |
| 6 | `AsyncFn` / `AsyncFnMut` / `AsyncFnOnce` 直接不算 | 6.x |

第 5 条细化：

- **可分派**要求：没有类型参数（生命周期可以）、`Self` 只出现在接收者类型里、
  接收者是 `&self` / `&mut self` / `Box<Self>` / `Rc<Self>` / `Arc<Self>` / `Pin<P>`、
  不是 `async fn`、没有返回位置 `impl Trait`、没有 `where Self: Sized`、没有 C 可变参数。
- **显式不可分派**要求：有 `where Self: Sized`（按值 `self` 接收者隐含这一点）。

两个最容易记反的点：

1. **不带泛型的关联类型不影响 dyn 兼容**——`dyn Iterator<Item = u32>` 完全合法（断言 3.1）；
   但构造时必须把泛型参数和关联类型**都写全**，否则 E0191（断言 8.x）。
2. **加一句 `where Self: Sized` 能把方法「救回来」**：`fn make(&self) -> Self` 会让 trait
   失去 dyn 兼容，加上后该方法变成「显式不可分派」，trait 重新兼容，代价是
   `dyn Trait` 上不能调它（断言 5.4 / 5.5 成对对照）。

## 二、对比

| 维度 | 关联类型 | 泛型参数 | GAT |
| --- | --- | --- | --- |
| 每次实现的取值 | 一个类型 | 一个类型（可有多份实现） | 一个**类型族**（按参数实例化） |
| dyn 兼容 | ✅ | ✅（写全参数） | ❌ |
| 典型用途 | `Iterator::Item` | `From<T>` / `PartialEq<Rhs>` | 借用型迭代器 `LendingIterator` |

| 维度 | 可分派方法 | 显式不可分派方法 |
| --- | --- | --- |
| 写法 | 普通方法 | 带 `where Self: Sized` 或按值 `self` |
| `dyn Trait` 上能调 | 能 | 不能（E0277） |
| 对 trait 的 dyn 兼容性 | 保持 | 保持 |

## 三、环境

- Rust 2021 edition，纯标准库；`cargo run` 即可。
- Python 3 模型（可运行、含 44 项断言）：`python selfcheck_traits.py`。

## 四、运行方式

```bash
cd rust && cargo run
cd python && python main.py              # dyn 兼容逐条否决 + 关联类型 vs 泛型参数
cd python && python selfcheck_traits.py  # 44 项断言
```

`rust/compile_fail/` 下 4 个文件**故意编译失败**：

| 文件 | 报错 | 想说明什么 |
| --- | --- | --- |
| `e0038_assoc_const.rs` | E0038 | 关联常量让 trait 失去 dyn 兼容 |
| `e0038_gat.rs` | E0038 | GAT 同样让它失去 dyn 兼容 |
| `e0191_assoc_type_unspecified.rs` | E0191 | `dyn Iter` 漏写 `Item` |
| `e0119_conflicting_impl.rs` | E0119 | 关联类型版不能有第二份实现 |

## 五、关键代码

**dyn 兼容判定（Python 模型，真跑）**

```python
for s in tr.supertraits:
    if s == "Sized":
        reasons.append("Sized 不能是 supertrait")
    elif not dyn_compatible(reg, s)[0]:
        reasons.append("supertrait %s 不是 dyn 兼容" % s)
for it in tr.items:
    if it.kind == "const":  reasons.append("有关联常量 " + it.name)
    if it.kind == "type" and (it.generics or it.lifetimes):
        reasons.append("关联类型 %s 带泛型参数（GAT）" % it.name)
```

**「显式不可分派」的豁免（Python 模型，真跑）**

```python
if it.where_sized or it.self_kind == "self":
    return None          # 按值 self 隐含 Sized，等价于 where Self: Sized
```

## 六、性能边界

- **关联类型与泛型参数在单态化下都是零成本**：编译器把具体类型直接填进去，
  没有 vtable、没有间接跳转。
- **`dyn` 才是那个有成本的选择**：胖指针两个字宽、每次调用查 vtable、
  无法内联。换来的是**异构容器**与更小的代码体积（同题见本目录 `trait与分发/`）。
- **GAT 的成本同样为零**，代价在表达力：它让签名变复杂，
  且放弃了 trait object 这条路。
- **supertrait 不改变运行期布局**，纯粹是约束：它让 `impl` 的先决条件显式化。

## 七、注意事项与常见坑

1. **别把「关联类型」和「GAT」混为一谈**：前者不破坏 dyn 兼容，后者破坏。
2. **`Sized` 不能当 supertrait**（断言 4.2）——想表达「只给固定大小类型用」，
   应该写在某些方法上的 `where Self: Sized`。
3. **`async fn` 在 trait 里会让 trait 失去 dyn 兼容**（隐藏了 `Future` 类型）；
   需要 `dyn` 时用 `async_trait` 这类方案或手动返回 `Pin<Box<dyn Future>>`。
4. **supertrait 是传递的**：给 trait 加一个 supertrait，等于把它整条祖先链
   的约束都加给你的实现者。
5. **`dyn Trait` 上只能调可分派方法**：`where Self: Sized` 的方法看不见（E0277）。
6. **关联类型版的 trait 无法为一个类型实现两次**：需要「一个类型既是
   `Iterator<Item=u32>` 又是 `Iterator<Item=i64>`」时，只能用泛型参数版。

## 八、参考资料（实际阅读）

- [The Book — ch20-02 Advanced Traits](https://doc.rust-lang.org/book/ch20-02-advanced-traits.html)
  —— 关联类型与泛型参数的对比（"can be implemented for a type multiple times"）、
  `Iterator::Item` 的例子、`OutlinePrint: Display` 的 supertrait 例子与
  「不写 supertrait 就找不到 `to_string`」的说明。
- [Rust Reference — Traits（Supertraits / Dyn compatibility）](https://doc.rust-lang.org/reference/items/traits.html)
  —— supertrait 的声明与传递、"It is an error for a trait to be its own supertrait"、
  dyn 兼容的六条规则清单、可分派接收者列表、`async fn` 与返回位置 `impl Trait`
  的否决、"This concept was formerly known as *object safety*"。
- [Rust Reference — Associated items](https://doc.rust-lang.org/reference/items/associated-items.html)
  —— "Associated types may include generic parameters and where clauses;
  these are often referred to as *generic associated types*, or *GATs*"、
  `<Thing as Trait>::Item<'x>` 的命名方式与 `Lend` / `ArrayLender` 完整例子。
