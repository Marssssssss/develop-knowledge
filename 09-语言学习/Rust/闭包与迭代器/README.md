# 闭包与迭代器（Fn 三 trait 推导 + 惰性求值）

> Rust 第二批 · demo 367 · 依据 The Book ch13-01 / ch13-02 / ch13-04 与 `std::iter` 文档实读。
>
> 一句话：**闭包不是「匿名函数」，是一个「捕获环境 + 携带 trait 身份」的值；
> 它的 trait 身份完全由「闭包体对捕获变量做了什么」决定。迭代器不是「更好的 for」，
> 是一个默认什么都不做、只有被消费时才运行的状态机。**

## 一、原理详解

### 1.1 三条捕获路径直接映射到三种函数传参方式

官方原文（ch13-01）："Closures can capture values from their environment in three ways,
which **directly map to the three ways a function can take a parameter**: borrowing
immutably, borrowing mutably, and taking ownership."

关键在于**由闭包体怎么用决定，而不是由变量类型决定**：

| 闭包体对变量做了什么 | 捕获方式 | 类比 |
| --- | --- | --- |
| 只读 | `&T` | `fn f(x: &T)` |
| 改动（如 `push` / `+=`） | `&mut T` | `fn f(x: &mut T)` |
| 交出所有权（如 `vec.push(v)` / `return v`） | 按值 move | `fn f(x: T)` |
| 没用到 | 不捕获 | — |

`move` 关键字**只改变第一条判定**：它把「只读 / 改动」也强制成按值捕获。
它**不改变** trait 身份 —— 本 demo 断言 1.7 固化了这一点（`move` 版闭包仍是 `Fn`）。
`move` 的真实用途是跨线程：主线程若先结束会 drop 掉被借用的值，子线程的引用就悬了
（官方 Listing 13-6 的说明）。

### 1.2 Fn 三 trait 是「加法式」的，不是三选一

官方原文："Closures will automatically implement one, two, or all three of these Fn
traits, **in an additive fashion**"。

```
FnOnce  ⊂—— 所有闭包都实现（"all closures can be called"）
  ↑ 超集
FnMut   —— 不移出捕获值，但可能改动它们
  ↑ 超集
Fn      —— 既不移出也不改动（含「什么都没捕获」）
```

推论（本 demo 断言 2.x）：约束写成 **`FnOnce` 的函数最宽容**，三种闭包都能进 ——
这正是 `Option::unwrap_or_else` 的签名用 `F: FnOnce() -> T` 的原因；
`slice::sort_by_key` 因为要对每个元素调用一次，只能写 `FnMut`，
于是一旦闭包移出了捕获值（想用 `push(String)` 计数）就报 **E0507**。

判别口诀：**看签名要求 `&self` / `&mut self` / `self`**。
`Fn` 的调用只借共享引用，`FnMut` 要独占借用，`FnOnce` 调用完闭包本身就没了。

### 1.3 闭包的坑：类型由第一次调用锁定

函数可以泛型，闭包不行。官方 Listing 13-3：`let f = |x| x;` 先用 `String` 调用，
参数/返回类型就被锁成 `String -> String`，再用整数调就 **E0308**。
（对比：泛型函数 `fn id<T>(x: T) -> T` 每次单态化出一份新实体。）

### 1.4 迭代器：只有 `next` 是必须实现的

```rust
pub trait Iterator {
    type Item;
    fn next(&mut self) -> Option<Self::Item>;
    // 其余几十个方法都有默认实现，且都建立在 next 之上
}
```

- **惰性（lazy）**：官方原文 "iterators are lazy, meaning they have no effect until
  you call methods that consume the iterator"。`v.iter().map(f)` 什么也不做，
  rustc 会直接给 `unused Map that must be used` 警告。
- **两类方法**：
  - *iterator adapters*（`map`/`filter`/`take`/`zip`…）：返回新迭代器，不消费、不运行。
  - *consuming adapters*（`collect`/`sum`/`count`/`for_each`…）：调 `next` 直到 `None`，
    **并拿走迭代器所有权** —— 之后再用就是 E0382。
- **`for val in iter` 是语法糖**：去糖后就是 `loop { match iter.next() { Some(v) => …, None => break } }`，
  本 demo 断言 7.2 据此算出 `next` 次数为「元素数 + 1」。

### 1.5 「零成本」到底零在哪

官方只给了**定性**说法加一组数字（ch13-04："Iterators are one of Rust's zero-cost
abstractions"；基准 `bench_search_for` 19,620,300 ns/iter vs `bench_search_iter`
19,234,900 ns/iter，差距落在噪声内）。

本 demo 不假装有汇编，而是把它翻译成**两个可计数的结构指标**：

| 写法 | 上游遍历趟数 | 物化中间容器次数 |
| --- | --- | --- |
| `iter().filter(..).map(..).collect()` | 1 | **1**（只有终态 `Vec`） |
| `iter().filter(..).collect()` 再 `.map(..).collect()` | 2 | **2**（中间 `Vec` 真实存在过） |

要点：**链式写法不产生中间集合**，因为 adapters 是层层包裹的状态机，
一趟 `next` 就把 `filter → map` 全部走完（官方在 async-book 里把同一手法称作
"allocation-free state machines"）。

## 二、对比

| 维度 | 闭包 | `fn` 函数指针 |
| --- | --- | --- |
| 捕获环境 | 可以 | 不行 |
| 类型 | 每个闭包一个**匿名唯一类型** | 具体类型 `fn(T) -> U` |
| 类型标注 | 通常可省略（上下文推断） | 必须写全 |
| 泛型 | 不行（类型首次调用即锁定） | 可以 |
| 不捕获时 | 可强转为 `fn` 指针 | — |

| 维度 | 迭代器链 | 手写 `for` 循环 |
| --- | --- | --- |
| 中间分配 | 0 | 0（若直接 push 到终态 Vec） |
| 可组合性 | 高（`.filter().map().take()`） | 低（要自己嵌 if） |
| 提前退出 | `take` / `find` 等天然短路 | 手写 `break` |
| 官方结论 | 二者编译产物**大致等价**，可放心用迭代器 | 同左 |

## 三、环境

- Rust 2021 edition，纯标准库，无第三方依赖；`cargo run` 即可。
- Python 3 模型（可运行、含 30 项断言）：`python closure_check.py`。

## 四、运行方式

```bash
cd rust && cargo run
cd python && python closure_check.py
```

`rust/compile_fail/` 下 4 个文件**故意编译失败**，用于对照四个报错：

| 文件 | 报错 | 想说明什么 |
| --- | --- | --- |
| `e0507_move_out_of_fnmut.rs` | E0507 | 移出捕获值 → 只剩 FnOnce → 进不了 `sort_by_key` |
| `e0308_two_types.rs` | E0308 | 闭包类型由首次调用锁定 |
| `e0382_iter_after_sum.rs` | E0382 | 消费适配器拿走所有权 |
| `e0502_borrow_conflict.rs` | E0502 | 捕获发生在**定义时**，不是调用时 |

## 五、关键代码

**trait 推导（Python 模型，真跑）**

```python
def traits(self):
    if any(k == "consume" for k, _ in self.ops):
        return {"FnOnce"}                    # 只能调一次
    if any(k == "mutate" for k, _ in self.ops):
        return {"FnOnce", "FnMut"}
    return {"FnOnce", "FnMut", "Fn"}         # 含「什么都没捕获」
```

**trait 约束的可调用形态（Rust，真代码）**

```rust
fn call_via_mut_ref<F: FnMut(i32) -> i32>(f: &mut F) -> i32 { f(1) + f(2) }
fn call_via_shared_ref<F: Fn(i32) -> i32>(f: &F) -> i32 { f(1) + f(2) }
```

**惰性度量**：`m.calls` 在 `collect()` 之前为 0，之后等于元素个数。

## 六、性能边界

- **闭包的成本**：不捕获 → 零大小类型（ZST），可被完全内联；按引用捕获 → 只多一个指针宽度的环境；
  按值捕获大对象 → 环境变大，且 `move` 会真的拷贝/移动一次。
- **迭代器的成本**：单次 `next` 的开销等价于手写索引访问的边界检查，
  优化器常能消掉（官方提到 "eliminating bounds checking on array access"）。
- **不适合用迭代器的场合**：需要**索引回看**（`v[i-1]` 与 `v[i+1]` 同时用）、
  需要在遍历中**原地改结构**、或者循环体本身远重于遍历逻辑时 —— 可读性收益趋近于 0。
- **`collect::<Vec<_>>()` 会分配**：能直接在链尾 `sum()`/`count()`/`any()` 就别先 collect。

## 七、注意事项与常见坑

1. **把 `FnMut` 当 `Fn` 传** → E0277。若函数把闭包存起来反复调用，约束就得写 `FnMut`。
2. **在 `FnMut` 闭包里移出捕获值** → E0507（想计数就改用 `+= 1`，见官方 Listing 13-9）。
3. **`sort_by_key` 的 key 会被反复调用**，key 函数里做重活会被放大 N 倍；需要缓存就先 `sort_by_cached_key`。
4. **忘了消费**：`v.iter().map(f);` 编译过、运行没效果，只给一条 `unused_must_use` 警告 —— 看到就补 `collect()`。
5. **`collect()` 的类型必须能从上下文推出**：`let v: Vec<_> = …` 或 turbofish `collect::<Vec<i32>>()`，
   否则 E0282。
6. **`move` 不等于「闭包变成 FnOnce」** —— 它只改捕获方式（断言 1.7）。反过来，
   **没有 `move` 也可能只剩 FnOnce**（只要闭包体移出了捕获值）。
7. **迭代器不能二次消费**：需要两遍就重新 `.iter()`，或者先 `collect()` 成 `Vec`。

## 八、参考资料（实际阅读）

- [The Book — ch13-01 Closures: Anonymous Functions that Capture Their Environment](https://doc.rust-lang.org/book/ch13-01-closures.html)
  —— 三种捕获方式 "directly map to the three ways a function can take a parameter"、
  Fn/FnMut/FnOnce 的 "additive fashion"、`unwrap_or_else` 的 `FnOnce` 签名与
  `sort_by_key` 的 `FnMut` 签名、Listing 13-3 的 E0308、Listing 13-8 的 E0507。
- [The Book — ch13-02 Processing a Series of Items with Iterators](https://doc.rust-lang.org/book/ch13-02-iterators.html)
  —— "iterators are lazy"、`Iterator` 的 `type Item` 与唯一必实现方法 `next`、
  consuming adapters 与 iterator adapters 的划分、`sum` 拿走所有权。
- [The Book — ch13-04 Comparing Performance: Loops vs. Iterators](https://doc.rust-lang.org/book/ch13-04-performance.html)
  —— 基准数字（19,620,300 vs 19,234,900 ns/iter）、"zero-cost abstractions" 的官方定义
  与 Stroustrup 的 zero-overhead 引述。
- [Rustonomicon / async-book — Futures](https://rust-lang.github.io/async-book/02_execution/02_future.html)
  —— "allocation-free state machines" 这一说法的原始出处（用于类比迭代器的无中间分配）。
