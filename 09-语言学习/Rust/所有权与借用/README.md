# 所有权与借用检查

> `09-语言学习/Rust/` 子类目首批 demo 1/5（2026-09-15）。
> 依据：The Rust Programming Language *ch04-01 What Is Ownership?* / *ch04-02 References and Borrowing*。

## 简介

Rust 不需要 GC 也能保证内存安全，靠的是把「谁拥有这块堆内存、谁在借用它」变成**编译期可判定的规则**——
本文把这 5 类报错的判定逻辑抽成可在运行期执行的**借用检查器模型**，并用 NLL 的对比场景说明
「词法作用域」与「最后一次使用」两种模型判定上的差别。

关键概念：

- **所有权三条规则**：每个值有唯一 owner；同一时刻只有一个 owner；owner 离开作用域时值被 drop。
- **move**：`let s2 = s1;` 只复制栈上的 ptr/len/cap，堆数据不复制，但 `s1` 立刻失效——
  因为两个指针都 release 就是 double free（The Book 原话："instead of being called a shallow copy, it's known as a *move*"）。
- **借用（borrowing）**：`&` / `&mut` 创建引用，引用不拥有数据，所以引用离开作用域**不会** drop 数据。
- **NLL（非词法生命周期）**：引用的作用域「从引入处到**最后一次使用**为止」，而不是到所在花括号结束。
- **数据竞争三条件**：① 两个及以上指针同时访问同一数据 ② 至少一个在写 ③ 没有任何同步机制。
  借用规则（一个可变引用 **或** 任意多个不可变引用）正是为了让编译器在编译期排除这三条同时成立。

历史背景：所有权模型是 Rust 在 1.0 前（2014 年放弃 GC runtime 之后）确立的核心设计；
NLL 让借用检查从「块级作用域」精确到「使用点」，消除了大量历史误报（详见下节对比）。

## 原理详解

### 1. 借用检查的四条判定规则（本 demo 实现的就是这四条）

| 错误码 | 触发规则 | The Book 的原始措辞 |
| --- | --- | --- |
| `E0382` | move 之后再使用原变量 | `borrow of moved value: s1` / `value borrowed here after move` |
| `E0499` | 同时存在两个 `&mut` | `cannot borrow s as mutable more than once at a time` |
| `E0502` | `&mut` 与存活的 `&` 共存 | `cannot borrow s as mutable because it is also borrowed as immutable` |
| `E0596` | 通过 `&` 改数据 | `cannot borrow *some_string as mutable, as it is behind a & reference` |
| `E0106` | 返回局部变量的引用 | `this function's return type contains a borrowed value, but there is no value for it to be borrowed from` |

### 2. 检查器模型的数据结构

```text
Step（抽象语句）                        借用的「活跃区间」
  Declare(v)        获得所有权          region(r) = [创建处, r 的最后一次使用]
  Borrow(r, v, kind)                    └─ 若 r 从未被使用 → 空区间（NLL 的关键）
  Use(v) / MoveOut(v) / Mutate(r)
  ReturnLocalRef(v)
```

判定顺序：先查 E0382（数据流：move 点 → 后续使用），再按**区间重叠**查 E0499/E0502，
最后是 E0596（透过共享引用写）与 E0106（无入参可借）。NLL 的作用全部体现在区间终点上。

### 3. 词法作用域 vs NLL（本 demo 的量化对比）

同一段代码（The Book Listing 4-10 的形状）：

```text
        0123456                      判定
词法    r1 &   &s |  [=====]         E0502
        r2 &   &s |   [====]
        r3 &mut &s|      [=]

NLL     r1 &   &s |  [==]            合法
        r2 &   &s |   [==]
        r3 &mut &s|      [=]
```

- 词法模型把 `r1/r2` 的生命周期拉到块尾 → 与 `r3` 重叠 → 误报 `E0502`。
- NLL 把终点收缩到 `println!` 那一行 → 区间不相交 → 合法。

### 4. 底层发生了什么

借用检查是 **MIR（mid-level IR）上的数据流分析**，在编译期完成，
因此"The Book" 能给出这句保证：*"None of the features of ownership will slow down your program while it's running."*
运行期没有任何引用计数或检查开销——这与 `Rc`/`RefCell`（把检查搬到运行期，见 demo 4）形成明确取舍。

## 环境准备

- 操作系统：任意（本 demo 无平台相关代码）
- Rust：1.63+（`let else`/NLL 之后的版本均可；`cargo` 1.63+ 支持 `[[bin]] path` 单文件布局）
- Python：3.9+（用到 `from __future__ import annotations` 与 dataclass）

## 运行方式

### Rust

```bash
cd rust
cargo run            # 或： rustc -O main.rs -o demo && ./demo
```

观察 5 个非法片段的**原文报错**（每个文件单独编译，必然失败）：

```bash
cd rust/compile_fail
for f in *.rs; do echo "== $f"; rustc "$f" 2>&1 | head -6; done
```

### Python

```bash
cd python
python3 run_cases.py     # 夹具与断言入口（检查器在 borrow_checker.py）
```

## 关键代码片段

NLL 的判定只改了一行——区间终点从「块尾」换成「最后一次使用」：

```python
def regions(self, loan: Loan) -> Tuple[int, int]:
    if self.lexical:
        return loan.start, loan.lexical_end        # 词法模型：块结束
    if loan.nll_end is None:
        return loan.start, loan.start - 1          # 从未使用 → 空区间
    return loan.start, loan.nll_end                # NLL：最后一次使用
```

冲突判定（两两比较区间是否相交）：

```python
for ai, a in enumerate(loans):
    a_lo, a_hi = self.regions(a)
    for b in loans[ai + 1:]:
        if a.target != b.target:
            continue
        b_lo, _ = self.regions(b)
        if a_lo <= b_lo <= a_hi:                   # b 创建时 a 仍活跃
            if a.kind == MUT and b.kind == MUT:
                add("E0499", b_lo, ...)
            elif a.kind != b.kind:
                add("E0502", b_lo, ...)
```

Rust 侧额外用**真实编译通过的代码**证明 NLL 生效（这段能跑起来就是证据）：

```rust
fn nll_live_example() {
    let mut s = String::from("hello");
    let r1 = &s;
    let r2 = &s;
    println!("      r1={r1} r2={r2}    ← 共享引用最后一次使用");
    let r3 = &mut s;               // 若 NLL 不生效，这里会报 E0502
    r3.push_str(", world");
}
```

## 运行结果（本机实测）

```text
[PASS] Listing 4-2: let s2 = s1; println!("{s1}")      期望=('E0382',) 实得=('E0382',)
[PASS] 两个同时存在的 &mut                             期望=('E0499',) 实得=('E0499',)
[PASS] 共享借用存活期间创建可变借用（Listing 4-8）        期望=('E0502',) 实得=('E0502',)
[PASS] 通过 & 改数据（Listing 4-6）                    期望=('E0596',) 实得=('E0596',)
[PASS] fn dangle() -> &String                        期望=('E0106',) 实得=('E0106',)
[PASS] NLL 合法：共享借用最后一次使用早于可变借用          期望=() 实得=()
[PASS] NLL 合法：引用创建后从未使用 → 空区间              期望=() 实得=()

夹具 7 个（7 通过 / 0 失败），抽象语句 31 条，借用 11 次
覆盖错误码：['E0106', 'E0382', 'E0499', 'E0502', 'E0596']
NLL 收益：词法模型下 E0502 → NLL 下合法
```

## 性能与边界

- **运行期开销**：0。所有判定都在编译期完成（The Book ch04-01 原文："None of the features of
  ownership will slow down your program while it's running."）。
- **自动拷贝的成本**：只有 `Copy` 类型会隐式复制，而 `Copy` 排除一切需要分配或属于资源的类型，
  因此 "any *automatic* copying can be assumed to be inexpensive"。
- **本模型的边界**：只覆盖 5 个错误码的**规则**，不做真实的过程间/跨函数分析，
  也不实现 NLL 的完整 place-based（字段级）分析；真实借用检查还包含 `E0503/E0505/E0515/E0621` 等更多情形。

## 注意事项与常见坑

1. **`&String` 借的是 `String`，不是它的内容**——`fn f(s: &String)` 里拿到的是指向 String 结构体的引用；
   多数场景应写 `&str`（deref coercion 能把 `&String` 转成 `&str`，见 demo 4）。
2. **想同时持有两份数据，必须 `.clone()`**：move 是所有权转移而非拷贝，原变量用不了会报 E0382。
3. **`_` 与 `_name` 不同**：`let _ = v;` 不绑定变量，值立即 drop；`let _x = v;` 仍绑定到作用域末尾（见 demo 2）。
4. **NLL 不是「生命周期延长」**：它只是把现有引用的有效区间收窄；`let r = &temp();` 仍然不可以。
5. **多个不可变引用可以共存**，这与「不可变」的直觉一致；只有 `&mut` 是独占的。
6. 常见误区：把 E0502 理解为「不能边读边写」。正确理解是「不能在**共享引用还会被使用**期间写」。

## 参考资料（实际阅读过的权威来源）

- [The Book — ch04-01 What Is Ownership?](https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html)
  —— 栈/堆对比与性能原因（push 比 allocate 快、访问堆要 follow pointer）、三条所有权规则原文、
  Move 与 double free、`E0382` 完整报错、"Rust will never automatically create 'deep' copies" 的设计推论、
  `Copy` 的完整类型清单、`Copy` 与 `Drop` 互斥、函数传参 move/copy 与返回值转移所有权。
- [The Book — ch04-02 References and Borrowing](https://doc.rust-lang.org/book/ch04-02-references-and-borrowing.html)
  —— 引用即借用（不拥有、不 drop 被指数据）、`E0596`/`E0499`/`E0502` 三段完整报错、
  数据竞争三条件原文、引用作用域到「最后一次使用」为止、
  `E0106` 悬垂引用与 `dangle()` 的逐行分析、收尾的两条借用规则原文。
