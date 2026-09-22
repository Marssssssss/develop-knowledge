# 型变与 PhantomData（协变 / 逆变 / 不变的判定）

> Rust 第三批 · demo 587 · 依据 Rust Reference `subtyping` 与 Rustonomicon `phantom-data` 实读。
>
> 一句话：**子类型化只发生在生命周期上**（擦掉生命周期后只剩类型相等）；
> 复合类型的型变不看「你希望它是什么」，而是**由字段逐个推导**——
> 同一参数只要出现在两个型变不同的位置，就立刻降为 invariant；
> 而 `PhantomData` 是唯一能在「字段里根本没有这个参数」时，
> 替你把型变、Send/Sync、drop check 三件事都交代清楚的零大小标记。**

## 一、原理详解

### 1.1 型变的定义（官方原文）

| 型变 | 定义 |
| --- | --- |
| covariant | `T <: U` ⇒ `F<T> <: F<U>`（子类型关系「穿过」） |
| contravariant | `T <: U` ⇒ `F<U> <: F<T>`（方向翻转） |
| invariant | 推不出任何子类型关系 |

### 1.2 内置类型的型变表（Reference 表格，本 demo 断言 2.x 逐项比对）

| 类型 | 在 `'a` 上 | 在 `T` 上 |
| --- | --- | --- |
| `&'a T` | 协变 | 协变 |
| `&'a mut T` | 协变 | **不变** |
| `*const T` | | 协变 |
| `*mut T` | | **不变** |
| `[T]` / `[T; n]` | | 协变 |
| `fn() -> T` | | 协变 |
| `fn(T) -> ()` | | **逆变** |
| `UnsafeCell<T>` | | **不变** |
| `PhantomData<T>` | | 协变 |
| `dyn Trait<T> + 'a` | 协变 | **不变** |

### 1.3 struct 的型变 = 字段型变的合并

官方例子（本 demo 断言 3.x）：

```rust
struct Variance<'a, 'b, 'c, T, U: 'a> {
    x: &'a U,               // 协变 'a，也本该让 U 协变（但被下面推翻）
    y: *const T,            // 协变 T
    z: UnsafeCell<&'b f64>, // 不变 'b
    w: *mut U,              // 不变 U ⇒ 整个 struct 的 U 不变
    f: fn(&'c ()) -> &'c (),// 既协又逆 ⇒ 不变 'c
}
// 结论：'a 与 T 协变；'b、'c、U 不变
```

合并规则就是两条：**一致则保留，冲突则 invariant**；没出现过的参数是 bivariant（不受约束）。
本 demo 用两组成对对照钉死它（3.6 / 3.7）：只把 `w: *mut U` 删掉，`U` 就变回协变；
只把 `f` 改成纯协变用法，`'c` 也变回协变。

### 1.4 不在 struct 里时，每个位置**各自**计算

官方原文：*"When used outside of a `struct`, `enum`, or `union`, the variance for
parameters is checked at each location separately."*

```rust
fn generic_tuple<'short, 'long: 'short>(
    x: (&'long u32, UnsafeCell<&'long u32>),
) {
    // 协变位置单独收缩到 'short，不变位置保持 'long —— 整体是允许的
    let _: (&'short u32, UnsafeCell<&'long u32>) = x;
}

fn takes_fn_ptr<'short, 'middle: 'short>(f: fn(&'middle ()) -> &'middle ()) {
    // 参数位**延长**到 'static（逆变），返回位**收缩**到 'short（协变）
    let _: fn(&'static ()) -> &'short () = f;
}
```

关键是：同样两个字段若放进 struct，`'long` 会因为「既协又逆」而变成 invariant，
赋值也就失败了——本 demo 断言 4.2 / 4.3 正是这个对照。

### 1.5 `PhantomData` 的四件事

`PhantomData` 是零大小的，但它同时决定：

1. **型变**（九种写法各不相同，见下）；
2. **自动 trait**（Send/Sync 是被继承、被取消、还是附带条件）；
3. **drop check**（`PhantomData<T>` 表示「我拥有 T」，`#[may_dangle]` 下不允许悬垂）；
4. **未使用参数的合法性**（没有它，未使用的 `'a`/`T` 直接 E0392）。

| 写法 | `'a` | `T` | Send/Sync | drop 期允许悬垂 |
| --- | --- | --- | --- | --- |
| `PhantomData<T>` | — | 协变 | 继承 | **否（拥有 T）** |
| `PhantomData<&'a T>` | 协变 | 协变 | 需 `T: Sync` | 是 |
| `PhantomData<&'a mut T>` | 协变 | **不变** | 继承 | 是 |
| `PhantomData<*const T>` | — | 协变 | `!Send + !Sync` | 是 |
| `PhantomData<*mut T>` | — | **不变** | `!Send + !Sync` | 是 |
| `PhantomData<fn(T)>` | — | **逆变** | `Send + Sync` | 是 |
| `PhantomData<fn() -> T>` | — | 协变 | `Send + Sync` | 是 |
| `PhantomData<fn(T) -> T>` | — | **不变** | `Send + Sync` | 是 |
| `PhantomData<Cell<&'a ()>>` | **不变** | — | `Send + !Sync` | 是 |

本 demo 的断言 7.x 不是「照抄表格」，而是用**型变代数算出来**再去和 Nomicon 的表格比对——
两个官方来源在这里完全吻合。

### 1.6 高阶生命周期（HRTB）

`for<'a> F<'a>` 是「把 `'a` 换成任意具体生命周期后」的类型的子类型：

```rust
let f: for<'a> fn(&'a i32) -> &'a i32 = |x| x;
let g: fn(&'static i32) -> &'static i32 = f;   // 'a := 'static
```

反方向**不成立**（断言 6.4）：具体的函数指针不是 `for<'a>` 函数的子类型。

## 二、对比

| 维度 | 协变 | 逆变 | 不变 |
| --- | --- | --- | --- |
| 能不能把 `F<A>` 当 `F<B>` 用（`A <: B`） | 能 | 只能反过来 | 都不能 |
| 典型来源 | 只读引用、返回值 | 函数参数 | 可变引用、`UnsafeCell` |
| 直觉 | 「读」可以放宽 | 「喂进去的东西」可以更宽 | 读写都要，必须精确匹配 |

| 维度 | struct 里的参数 | 裸位置（元组 / 函数指针） |
| --- | --- | --- |
| 型变怎么算 | 全部字段合并，冲突即不变 | **每个位置各算各的** |
| 后果 | 一次冲突拖累整个参数 | 可以一部分收缩、一部分保持 |

## 三、环境

- Rust 2021 edition，纯标准库；`cargo run` 即可。
- Python 3 模型（可运行、含 61 项断言）：`python selfcheck_variance.py`。

## 四、运行方式

```bash
cd rust && cargo run
cd python && python selfcheck_variance.py
```

`rust/compile_fail/` 下 3 个文件**故意编译失败**：

| 文件 | 报错 | 想说明什么 |
| --- | --- | --- |
| `e0392_unused_param.rs` | E0392 | 裸指针不带生命周期也不表达所有权，参数「从未被使用」 |
| `e0597_mut_invariance.rs` | E0597 | `&mut T` 在 T 上不变，正是为了挡住这类悬垂写入 |
| `e0308_cell_invariance.rs` | E0308 | `Cell<T>` 在 T 上不变，共享引用之间不能互相放宽 |

## 五、关键代码

**型变合并与复合（Python 模型，真跑）**

```python
def join(a, b):                 # 同一参数在多个位置出现
    if a == BIV: return b
    if b == BIV: return a
    return a if a == b else INV

def compose(shape_v, arg_v):    # 外层型变 ∘ 内层型变
    if arg_v == BIV: return BIV
    return shape_v * arg_v      # cov=1 / contra=-1 / inv=0
```

**用代数算 `PhantomData` 的型变，再和 Nomicon 表格比对（真跑）**

```python
phantom_variance("PhantomData<fn(T)>", "T")     # → contravariant
PHANTOM_TABLE["PhantomData<fn(T)>"][1]          # → CONTRA（一致）
```

## 六、性能边界

- 型变**完全是编译期的**：`PhantomData` 是零大小类型（ZST），
  不占空间也不产生运行期代码；`size_of::<PhantomData<u8>>() == 0`。
- 真要说代价，是**表达力**：
  - 写成 `PhantomData<T>` 会让类型在生命周期上变成 bivariant（`'a` 那一列是 `-`），
    也就放弃了「用生命周期约束它」的可能；
  - 写成 `PhantomData<&'a T>` 换来协变，但要求 `T: Sync` 才能 Send+Sync；
  - 写成 `PhantomData<&'a mut T>` 得到不变 + 继承自动 trait，代价是最严格。

## 七、注意事项与常见坑

1. **`&mut T` 在 `T` 上不变**——这不是实现细节，是安全前提：
   若允许，就能把短命引用写进只应装长命引用的位置（见 `e0597` 例子）。
2. **`Cell<T>` / `UnsafeCell<T>` 在 `T` 上不变**：内部可变性取消共享引用的协变。
3. **`PhantomData` 只解决型变与自动 trait，不解决 `Unpin`**——
   想去掉 `Unpin` 要用 `PhantomPinned`（Nomicon 原文的 Note）。
4. **同一参数在两个冲突位置出现就整体不变**：排查「为什么这里不能赋值」时，
   先把该参数在每个字段里的型变列出来。
5. **元组 / 函数指针类型不受这条约束**：它们是逐位置计算的，
   所以会出现「同一个 `'a` 一部分收缩、一部分保持」的合法赋值。
6. **别用 `PhantomData<T>` 只是为了让 `T` 能被用上**：它同时声明了「我拥有 `T`」，
   会影响 drop check；不拥有就写 `PhantomData<&'a T>` 或 `fn() -> T`。
7. **逆变只出现在函数参数位**：`fn(T) -> ()` 在 `T` 上逆变，
   所以「能吃更宽输入的函数」可以当「吃窄输入的函数」用。

## 八、参考资料（实际阅读）

- [Rust Reference — Subtyping and variance](https://doc.rust-lang.org/reference/subtyping.html)
  —— 子类型化只限于生命周期与高阶生命周期、型变三定义、内置类型型变表、
  struct 由字段推导且冲突即不变、官方 `Variance` 结构体例子、
  "outside of a struct … checked at each location separately" 及两个例子
  （元组与函数指针）、`for<'a>` 的替换规则。
- [Rustonomicon — Subtyping and Variance](https://doc.rust-lang.org/nomicon/subtyping.html)
  —— 型变的动机与「为什么 `&mut T` 必须不变」的推导。
- [Rustonomicon — PhantomData](https://doc.rust-lang.org/nomicon/phantom-data.html)
  —— 九种 `PhantomData` 写法的型变 / Send+Sync / drop glue 对照表、
  「拥有 `T`」与 `#[may_dangle]` 的相互作用（含 std `Vec` 的真实写法）、
  `PhantomPinned` 的 Note。
