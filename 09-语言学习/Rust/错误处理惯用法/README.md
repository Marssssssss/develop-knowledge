# 错误处理惯用法（Result + `?` + From + `Box<dyn Error>`）

> Rust 第二批 · demo 370 · 依据 The Book ch09-02、Rust Reference（try propagation operator）、
> std::error::Error、thiserror 与 anyhow 官方文档实读。
>
> 一句话：**`?` 不是一个「把错误原样往上扔」的操作符，它是「提前 return +
> 调一次 `From::from`」的语法糖。** 错误处理的全部设计都围绕这条展开：
> 下层错误靠 `From` 实现自动升层，跨抽象边界的信息靠 `Error::source()` 串成链。

## 一、原理详解

### 1.1 `?` 的真实语义（Reference 原文）

`?` 作用于**五种**类型，去糖后等价于 `Try::branch` + `FromResidual::from_residual`：

| 类型 | Continue 情形 | Break 情形（提前 return） | **经过 `From::from`** |
| --- | --- | --- | --- |
| `Result<T, E>` | `Ok(val)` → `val` | `Err(e)` → `Err(From::from(e))` | ✅ |
| `Option<T>` | `Some(val)` → `val` | `None` → `None` | ❌ |
| `ControlFlow<B, C>` | `Continue(c)` → `c` | `Break(b)` → `Break(b)` | ❌ |
| `Poll<Result<T,E>>` | `Ready(Ok(v))` → `v` | `Ready(Err(e))` → `Ready(Err(From::from(e)))`；`Pending` 原样 | ✅（仅 Err） |
| `Poll<Option<Result<T,E>>>` | `Ready(Some(Ok(v)))` → `v` | `Ready(Some(Err(e)))` → `Ready(Some(Err(From::from(e))))` | ✅（仅 Err） |

「**只有 `Result` 系才调 `From`**」是本 demo 最核心的可断言事实（断言 2.3 / 3.3 / 4.3）——
直接用计数器验证 `From::from` 的调用次数，`Option` 与 `ControlFlow` 路径上**一次都不调**。

Reference 还说明：`Try` trait 目前 **unstable**，不能给自己类型实现，
所以这张表就是当前能用 `?` 的全部场合。

### 1.2 `From` 是跨层传播的全部秘密

The Book 原文："Error values that have the `?` operator called on them go through the
`from` function … which is used to convert values from one type into another."

于是「一个函数返回一种错误类型，内部却可能因为很多不同原因失败」这件事，
只需要补若干条 `impl From<X> for MyError` 就能成立，**函数体一行都不用改**。
缺哪条 impl，编译器就在那一行报 **E0277**（本 demo 断言 5.1 复现）。

### 1.3 `Box<dyn Error>` = "any kind of error"

官方原文："`Box<dyn Error>` … you can read `Box<dyn Error>` to mean 'any kind of error'"。
它的可行性来自标准库的 blanket impl：任何实现了 `Error` 的类型都能转成 `Box<dyn Error>`
（本 demo 断言 6.x：两个**不同**的下层错误装箱后是**同一个静态类型**，且仍能 downcast 回具体类型）。

代价是**丢失静态类型**：调用方无法 `match` 出具体变体，只能靠 `downcast_ref` 或字符串判断。
官方给的使用场景是 `main` / 二进制 crate 的顶层。

### 1.4 source 链与「不要两边都渲染」

`std::error::Error` 的定义：

```rust
pub trait Error: Debug + Display {
    fn source(&self) -> Option<&(dyn Error + 'static)> { ... }
    ...
}
```

- **必须有 `Debug` + `Display`**（缺一即 E0277）。
- `source()` 用于「错误跨越抽象边界」时保留下层原因。
- 官方明确规则："the underlying error should be **either** returned by … `source()`,
  **or** rendered by … `Display` …, **but not both**."
  —— 否则用户会看到同一句话印两遍（anyhow 的 `Caused by:` 段就是靠 `source()` 展开的）。

本 demo 把这条规则写成**可执行的 lint**：`obeys_not_both_rule(e)` 检查
`e.source().display() not in e.display()`，并构造一个反例 `BadAppError` 证明它**确实抓得住**
（断言 7.4 / 7.5）。

### 1.5 退出码

`main() -> Result<(), E>`：官方原文 "the executable will exit with a value of 0 if `main`
returns `Ok(())` and will exit with a **nonzero** value if `main` returns an `Err` value"，
理由是与 C 的约定兼容。真正的机制是 `std::process::Termination` trait 的 `report() -> ExitCode`。

### 1.6 thiserror 与 anyhow 的分工

| | thiserror | anyhow |
| --- | --- | --- |
| 官方定位 | "a convenient **derive macro** for the standard library's `std::error::Error` trait" | "`anyhow::Error`, a **trait object based** error type" |
| 用在哪 | 库（library）—— 定义精确的错误类型 | 应用（application）—— 只管往上抛 |
| 关键机制 | `#[error("…")]` 生成 `Display`；`#[from]` 生成 `From` 且**隐含 `#[source]`** | `?` 传播任何 `impl Error`；`.context()` 追加上下文；支持 downcast |
| 官方强调 | "deliberately does not appear in your public API"；手写 impl 与 derive **互相切换不是破坏性变更** | Rust ≥1.65 且下层未自带时会自动捕获 backtrace |

一句话分工：**库用 thiserror 定义「发生了什么」，应用用 anyhow 记录「当时在做什么」。**

## 二、对比

| 维度 | `panic!` / `unwrap` | `Result` |
| --- | --- | --- |
| 官方定位 | bug（不可恢复） | 可预期的失败 |
| 传播 | 沿栈展开，难拦截 | `?` 显式传播 |
| 信息 | panic 位置 + message | 类型化的错误 + source 链 |
| `expect` vs `unwrap` | 官方："most Rustaceans choose **expect** … and give more context" | — |

| 维度 | 自定义 enum 错误 | `Box<dyn Error>` |
| --- | --- | --- |
| 类型信息 | 完整，可 `match` | 擦除，只能 downcast |
| 新增错误来源 | 要加变体 + From | 什么都不用改 |
| 适合 | 库的公开 API | 二进制 / main / 快速原型 |

## 三、环境

- Rust 2021 edition，纯标准库。**不引入 thiserror / anyhow**（它们是第三方 crate，
  本节只是引用其官方文档来说明分工）。
- Python 3 模型（含 40 项断言）：`python error_check.py`。

## 四、运行方式

```bash
cd rust && cargo run          # 退出码 0
cd rust && cargo run -- fail  # 走 Err 路径，退出码非 0
cd python && python error_check.py
```

`rust/compile_fail/` 三个文件故意编译失败：

| 文件 | 报错 | 说明 |
| --- | --- | --- |
| `e0277_no_from_impl.rs` | E0277 | 缺 `From<ParseIntError>` → `?` 转不动 |
| `e0277_question_mark_in_unit_fn.rs` | E0277 | `?` 只能用在返回 Result/Option/… 的函数里 |
| `e0277_error_needs_display.rs` | E0277 | `Error` 要求 `Debug + Display` 两者兼具 |

## 五、关键代码

**`?` 走不走 From，一眼可见（Python，真跑）**

```python
def q_result(r, convert):                 # Result：Err 要转换
    if r[0] == "Ok":  return (YIELD, r[1])
    return (RETURN, ("Err", convert(r[1])))

def q_option(o, convert):                 # Option：None 直接返回，convert 根本不被调用
    if o[0] == "Some": return (YIELD, o[1])
    return (RETURN, NONE)
```

**「不要两边都渲染」的可执行判据**

```python
def obeys_not_both_rule(e):
    s = e.source()
    if s is None: return True
    return s.display() not in e.display()
```

**Rust 侧：一条 `?` 链、三种来源、一个出口类型**

```rust
fn read_and_parse(path: &str) -> Result<u32, AppError> {
    let mut s = String::new();
    File::open(path)?.read_to_string(&mut s)?;   // io::Error      → AppError
    let n: u32 = s.trim().parse()?;              // ParseIntError  → AppError
    Ok(n)
}
```

## 六、性能边界

- **`Result<T, E>` 本身零开销**：返回时按 ABI 传寄存器（或 ` niche 优化` 让
  `Result<&T, E>` 与 `&T` 等宽，空指针表示 Err）。只有 `E` 很大时才会明显。
- **`Box<dyn Error>` 有一次堆分配**：发生在**错误路径**上，通常无所谓；
  但若在热循环里频繁构造错误，就值得换回 enum。
- **`anyhow::Error` 是胖指针 + 可能的一次分配**：官方说它内部是 trait object，
  并会在 Rust ≥1.65 时捕获 backtrace —— **backtrace 捕获本身不便宜**，
  高频错误路径要权衡。
- **`Display` 格式化不便宜**：只在真正要打印时才格式化；不要在错误构造时就把字符串拼好
  （这也是 `with_context(|| format!(...))` 用闭包的原因 —— 只在出错时才调用）。

## 七、注意事项与常见坑

1. **在库里用 `Box<dyn Error>`** —— 调用方拿不到类型信息，等于把 API 退化成字符串。库应当给精确 enum。
2. **`Display` 里重复渲染 source** —— 官方明令禁止二者都做，否则 anyhow 的 `Caused by:` 段会重复。
3. **忘了实现 `From`** —— 编译器会在 `?` 那一行报 E0277，而不是在调用点，新手常找不到原因。
4. **`unwrap()` 在生产代码里** —— 官方倾向 `expect("为什么这里一定成功")`，出问题时信息量完全不同。
5. **把错误吞成 `String`** —— 丢掉 `source()` 链；要临时过渡至少用 `Box<dyn Error>` 或 anyhow。
6. **以为 `?` 只能用于 `Result`** —— 它同样用于 `Option`/`ControlFlow`/`Poll`，但**只有 Result 系会调 `From`**。
7. **错误类型没有 `Debug`** —— `Error: Debug` 是硬要求，`unwrap()` / `expect()` 要打 `{:?}`。
8. **`#[from]` 变体不能带别的字段** —— thiserror 文档原文："The variant using `#[from]`
   must not contain any other fields beyond the source error (and possibly a backtrace)"。

## 八、参考资料（实际阅读）

- [The Book — ch09-02 Recoverable Errors with `Result`](https://doc.rust-lang.org/book/ch09-02-recoverable-errors-with-result.html)
  —— `unwrap` / `expect` 的取舍与官方偏好、`?` 走 `From::from` 的原文、
  `Box<dyn Error>` = "any kind of error"、`main` 返回 `Result` 的退出码约定与
  `Termination` trait。
- [Rust Reference — The try propagation operator](https://doc.rust-lang.org/reference/expressions/operator-expr.html)
  —— 五种可作用类型的完整行为表、`Try::branch` + `FromResidual::from_residual` 去糖、
  "`Try` trait is currently unstable" 的说明。
- [std — `error::Error`](https://doc.rust-lang.org/std/error/trait.Error.html)
  —— `pub trait Error: Debug + Display`、`source()` 的用途、
  "either returned by … `source()`, or rendered by … `Display` …, but not both"、
  错误文案「小写短句不带尾部标点」的惯例。
- [thiserror 文档](https://docs.rs/thiserror/latest/thiserror/)
  —— derive 的定位与 "does not appear in your public API"、`#[error("…")]` 生成 Display、
  `#[from]` 生成 From 且隐含 `#[source]`、`#[from]` 变体的字段限制、`#[error(transparent)]` 的用途。
- [anyhow 文档](https://docs.rs/anyhow/latest/anyhow/)
  —— `anyhow::Error` 是 trait object、`Result<T, anyhow::Error>` 的写法、
  `.context()` / `.with_context()` 与 `Caused by:` 输出、downcasting 三种形态、
  Rust ≥1.65 的 backtrace 行为。
