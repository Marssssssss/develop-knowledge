# Move / Copy / Clone 与 drop 时机

> `09-语言学习/Rust/` 子类目首批 demo 2/5（2026-09-15）。
> 依据：The Rust Programming Language *ch04-01* + Rust Reference *Destructors*。

## 简介

Rust 里「值什么时候被销毁」不是运行时 GC 决定的，而是由**作用域嵌套 + 声明顺序**在编译期确定：
本文用 `PrintOnDrop` 把每一次析构变成可断言的日志，逐条验证 Rust Reference 里写死的 drop 顺序规则，
并说清 `Copy` / `Clone` / `move` 三者在「会不会隐式复制」「会不会立即析构」上的区别。

关键概念：

- **drop scope**：每个变量/临时值都关联一个作用域；控制流离开作用域时，变量按**声明顺序的逆序**、
  临时值按**创建顺序的逆序**析构。
- **由内向外**：一次性离开多层作用域（如函数返回）时，内层作用域先析构。
- **drop glue**：类型的析构 = ①若 `T: Drop` 调用 `<T as Drop>::drop` ②**递归**析构所有字段。
- **Copy**：可以用 `memcpy` 平拷贝的类型；赋值不 move、原变量继续可用。
- **Copy 与 Drop 互斥**：实现 `Drop` 的类型不能实现 `Copy`（E0184）——否则平拷贝出两份、析构两次 = double free。
- **部分 move**：只移出结构的一部分字段后，其余字段照常析构，被移出的字段不再析构。

## 原理详解

### 1. drop 顺序的四条规则（逐个断言）

| 规则 | 内容（Rust Reference 原文要点） | 本 demo 场景 | 实测顺序 |
| --- | --- | --- | --- |
| 变量 | 同一作用域内「drop in reverse order of declaration」 | `a, b, c` | `c, b, a` |
| 作用域 | 同时离开多个作用域「dropped from the inside outwards」 | 外层1 / 内层 / 外层2 | `inner, outer-2, outer-1` |
| 类型内部 | struct/枚举字段「in declaration order」，数组「first element to the last」 | `Triple{a,b,c}` / `[0,1,2]` | `a,b,c` / `0,1,2` |
| 模式 | 「Variables in patterns are dropped in reverse order of declaration」 | `let (p0, p1) = (..)` | `p1, p0` |

### 2. 赋值与部分 move

- **赋值即析构旧值**："Assignment also runs the destructor of its left-hand operand, if it's initialized."
  因此 `v = PrintOnDrop("new")` 会在这一行立刻析构旧值，而不是等到作用域结束。
- **对未初始化变量赋值不触发析构**：`let v; v = PrintOnDrop("x");` 无析构发生。
- **部分 move**："If a variable has been partially initialized, only its initialized fields are dropped."
  `mem::forget(partial.1)` 之后，作用域结束时只剩 `partial.0` 被析构。

### 3. 函数参数为什么最后析构

Reference 的 drop scope 层级里，`entire function` 是最外层作用域，而**所有函数参数属于该作用域**，
函数体块嵌在它内部 → 由内向外析构时参数最后走。同一参数内，模式引入的绑定又先于参数自身析构，
于是 Reference 给出的示例顺序是 `3 2 0 1`。

### 4. Copy 判定的完整规则

The Book 给的判据："any group of simple scalar values can implement `Copy`, and nothing that
requires allocation or is some form of resource can implement `Copy`"：

```text
✅ 所有整数 / bool / 所有浮点 / char / 只含 Copy 类型的元组（(i32,i32) ✅，(i32,String) ❌）
❌ String / Vec / Box 等需要分配的类型；任何实现 Drop 的类型
```

`Copy` 与 `Drop` 互斥的根因（E0184 报错原文："`Copy` may not be implemented for this type;
the type has a destructor"）：`Copy` 语义 = 平拷贝，两份都析构就会 double free / 重复 `close(fd)`。

## 环境准备

- 操作系统：任意
- Rust：1.63+（用到 `thread_local!` + `RefCell`，均为长期稳定 API）
- Python：3.9+

## 运行方式

### Rust

```bash
cd rust
cargo run            # 或： rustc -O main.rs -o demo && ./demo
```

观察 `Copy + Drop` 互斥的报错：

```bash
cd rust/compile_fail && rustc e0184_copy_with_drop.rs
```

### Python

```bash
cd python
python3 drop_sim.py
```

## 关键代码片段

Rust：把析构写进 thread_local 日志，才能「在作用域之外」读到完整顺序。

```rust
struct PrintOnDrop(&'static str);

impl Drop for PrintOnDrop {
    fn drop(&mut self) {
        LOG.with(|log| log.borrow_mut().push(self.0));
    }
}

fn sc_reverse_declaration() -> Vec<&'static str> {
    {
        let _a = PrintOnDrop("a");
        let _b = PrintOnDrop("b");
        let _c = PrintOnDrop("c");
    } // 离开块作用域 → 逆序：c, b, a
    take_log()   // 必须在块结束之后调用，否则日志还是空的
}
```

Python：同一套规则的作用域机——「逆序」只是 `reversed(bindings)`，「由内向外」只是作用域栈。

```python
def scope_out(self) -> None:
    scope = self.scopes.pop()
    for b in reversed(scope.bindings):      # 规则 1：逆声明序
        self._drop_binding(b)

def _drop_binding(self, b: Binding) -> None:
    if b.forgotten:
        return                              # mem::forget：抑制析构（泄漏但内存安全）
    if isinstance(b.value, Scalar):
        self.log.append(b.value.label)
        return
    for i, label in enumerate(b.value.items):   # 规则 3：字段声明序 / 数组首→尾
        if i in b.moved_out:
            continue
        self.log.append(label)
```

## 运行结果（本机实测）

```text
[PASS] 同一作用域：逆声明序              实际 ['c', 'b', 'a']
[PASS] 离开多层作用域：由内向外           实际 ['inner', 'outer-2', 'outer-1']
[PASS] 覆盖赋值：旧值立即 drop            实际 ['旧值-old（覆盖赋值 → 立即 drop）', '新值-new']
[PASS] move 不触发析构（只 drop 一次）     实际 ['move 不触发析构']
[PASS] struct 字段：声明顺序             实际 ['field-a', 'field-b', 'field-c']
[PASS] 数组元素：首 → 尾                 实际 ['elem-0', 'elem-1', 'elem-2']
[PASS] 模式内绑定：逆声明序               实际 ['pat-last', 'pat-first']
[PASS] forget + 部分 move               实际 ['partial-0']
        被 forget 抑制的析构 = ['forgotten：永不 drop', 'partial-1']

场景 8 个（8 通过 / 0 失败）
```

## 性能与边界

- **零运行期成本**：drop 时机完全由编译期决定，不引入任何跟踪结构；析构函数本身的执行成本 = 你写在
  `Drop::drop` 里的代码 + 字段递归析构。
- **`Rc`/`RefCell` 的 drop 计数**属于运行期成本（见 demo 4 的 `strong_count` 轨迹）。
- **`mem::forget` 是安全的**：Reference 明确「阻止析构函数运行也是安全的」，
  但 Reference 同时规定「类型不得为内存安全而依赖析构一定运行」。
- **边界**：本 demo 不覆盖临时值（temporary scope）与 2024 edition 的两条临时作用域收窄规则。

## 注意事项与常见坑

1. **`let _ = v;` 会立即 drop**，想要「绑定但不使用」必须写 `let _x = v;`——
   前者不构成绑定，后者仍活到作用域末尾。
2. **逆声明序不是为了省事**：后声明的变量往往依赖先声明的（如 `MutexGuard` 依赖 `Mutex`），
   逆序保证依赖方先销毁。
3. **`Drop` 里不要 `panic!`**：unwinding 期间再 panic 会直接 abort。
4. **`Copy` 不是「便宜」的同义词**：`[u8; 1<<20]` 是 `Copy`，赋值会真的复制 1 MiB；此时应考虑 `Box`/`&`。
5. **部分 move 后原变量不可整体使用**，但剩余字段仍可用，也仍会被析构。
6. **`std::process::exit` 不跑析构**：进程直接终止，析构（含 flush）都不会发生——
   日志/缓冲写入必须显式 flush。

## 参考资料（实际阅读过的权威来源）

- [Rust Reference — Destructors](https://doc.rust-lang.org/reference/destructors.html)
  —— 析构定义与递归析构组成、字段/元组/数组的 drop 顺序、`drop_in_place`、
  drop scope 列表与嵌套关系、「reverse order of declaration」原文、
  参数最后析构 + `patterns_in_parameters` 的 `3 2 0 1` 示例、局部变量与模式内逆序、or-pattern 规则、
  临时作用域清单、赋值即析构、部分初始化、`mem::forget`/`ManuallyDrop`、常量提升与临时值延长。
- [The Book — ch04-01 What Is Ownership?](https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html)
  —— Move 与 double free、`Copy` 类型清单（整数 / `bool` / 浮点 / `char` / 只含 `Copy` 的元组）、
  `Copy` 与 `Drop` 互斥的原文表述、`clone` 的语义、Rust 的 `drop` 与 C++ RAII 的对照、
  「赋值新值后旧值立即 drop」的 Figure 4-5 场景。
- [rustc error code index — E0184](https://doc.rust-lang.org/1.74.0/error_codes/E0184.html)
  —— "The `Copy` trait was implemented on a type with a `Drop` implementation"，
  以及「显式同时实现 Drop 与 Copy 当前被禁止，旧实现可能导致内存不安全（issue #20126）」的说明。
