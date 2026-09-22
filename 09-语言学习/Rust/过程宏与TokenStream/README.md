# 过程宏与 TokenStream（三类过程宏 + 两套 token 定义）

> Rust 第三批 · demo 590 · 依据 Rust Reference `procedural-macros` 实读。
>
> 一句话：**过程宏是「在编译期跑一段 Rust 程序，把 token 流变成另一段 token 流」；
> 它只有三种形态（函数式 / derive / 属性），必须住在独立的 `proc-macro` crate 里，
> 而且**完全不卫生**——生成的代码就像被就地写在调用处一样解析。
> 更反直觉的是：声明宏与过程宏用的是**两套不同的 token 定义**，跨界时要双向转换。**

## 一、原理详解

### 1.1 三类过程宏

| 形态 | 写法 | 输入 | 输出做什么 |
| --- | --- | --- | --- |
| Function-like | `custom!(...)` | 定界符里的内容 | **替换**整个宏调用 |
| Derive | `#[derive(CustomDerive)]` | struct / enum / union | **追加**若干 item |
| Attribute | `#[CustomAttribute]` | `(attr, item)` 两个流 | 用 0..n 个 item **替换**原 item |

三类都要求：`pub fn(TokenStream) -> TokenStream`、Rust ABI、没有其它限定符、
位于 **crate 根部**、且必须在 `proc-macro = true` 的 crate 里（断言 6.x 与
声明宏做了一一对照：声明宏没这些限制）。

### 1.2 属性宏的输入切分（官方例子，断言 5.x 逐项比对）

```rust
#[show_streams]
fn invoke1() {}          // attr: ""                 item: "fn invoke1() {}"
#[show_streams(bar)]
fn invoke2() {}          // attr: "bar"              item: "fn invoke2() {}"
#[show_streams(multiple => tokens)]
fn invoke3() {}          // attr: "multiple => tokens"
#[show_streams { delimiters }]
fn invoke4() {}          // attr: "delimiters"
```

要点：**attr 不含外层定界符**；item 是「属性之后的其余部分」，包含 item 上的其它属性。

### 1.3 两套 token 定义（本 demo 最反直觉的部分）

| | 声明宏 `macro_rules!` | 过程宏 |
| --- | --- | --- |
| 多字符运算符 | 一个 token（`+=`） | **多个** Punct（`+` `=`） |
| 单引号 `'` | 不在标点集里（`'a` 是一个 lifetime token） | **在**标点集里 |
| 负数 | `-1` 是 `-` 与 `1` 两个 token | `-1` 是**一个**字面量 |
| 生命周期 | `'ident` | 无（只有 `'` + ident） |
| 元变量替换 | 有 | 无 |

跨界转换（官方原文，断言 2.x / 3.x）：

- **传给过程宏**：多字符运算符拆成单字符；生命周期拆成 `'` + ident；
  `$crate` 作为**单个标识符**传入；其它元变量替换展开成底层 token stream，
  必要时用 `Delimiter::None` 的分组包起来；**`tt` 与 `ident` 从不被包**。
- **从过程宏输出**：标点粘回多字符运算符；`'` 与 ident 粘回生命周期；
  负数常量拆成 `-` 与常量两个 token。
- **文档注释两种宏都不支持**，一律先转成 `#[doc = r".."]`。

### 1.4 不卫生（unhygienic）

官方原文：*"Procedural macros are **unhygienic**. This means they behave as if
the output token stream was simply written inline to the code it's next to."*

对比声明宏的 mixed-site（断言 7.x 成对对照）：

| 符号 | 声明宏 | 过程宏 |
| --- | --- | --- |
| 局部变量 / 标签 | 定义处 | **调用处** |
| 其它符号（item、类型…） | 调用处 | 调用处 |

所以官方给了两条实操建议：写**绝对路径**（`::std::option::Option` 而不是 `Option`），
以及生成物用 `__internal_foo` 这类不太可能撞名的名字。

### 1.5 运行方式与报错

- 过程宏在编译期运行，与编译器共享 stdin/stdout/stderr 与文件权限，
  因此**与 build script 有同样的安全顾虑**（断言 8.5）。
- 官方：它们"must either return syntax, panic, or loop endlessly"。
  panic 会被编译器捕获成编译错误；**死循环不会被捕获，会挂住编译器**（断言 8.2 / 8.3）。
- 报错还有第二条路：emit 一个 `compile_error!` 调用，好处是指向调用点。

## 二、对比

| 维度 | 过程宏 | `macro_rules!` |
| --- | --- | --- |
| 输入 | `TokenStream`（≈ `Vec<TokenTree>`） | token 树 + 片段分类符 |
| 处理能力 | 任意 Rust 代码（可配 `syn`/`quote` 解析 AST） | 只能按规则拼 token |
| 卫生性 | **不卫生** | mixed-site |
| 所在 crate | 必须独立的 `proc-macro` crate + crate 根部 | 任意，还有 textual scope |
| 能否在定义处使用 | **不能** | 能 |
| 调试 | 可以 `eprintln!` / `println!` 打 token | 只能靠 `trace_macros!` |

## 三、环境

- Rust 2021 edition；本 demo 是**workspace**：`rust/` 是使用侧 bin crate，
  `rust/mymacro/` 是 `proc-macro = true` 的库 crate。
- Python 3 模型（可运行、含 50 项断言）：`python selfcheck_procmacro.py`。

## 四、运行方式

```bash
cd rust && cargo run          # 编译时会打印属性宏看到的 attr / item
cd python && python main.py   # 两套 token 定义的转换 + 属性宏四例切分
cd python && python selfcheck_procmacro.py  # 50 项断言
```

`rust/compile_fail/` 下 3 个文件**故意编译失败**：

| 文件 | 想说明什么 |
| --- | --- |
| `use_in_defining_crate.rs` | 过程宏不能在定义它的 crate 里用 |
| `inner_attribute.rs` | 属性宏不能用作 inner attribute |
| `unhygienic_name_clash.rs` | 不卫生导致生成物与调用方同名类型相撞 |

## 五、关键代码

**两套 token 定义的转换（Python 模型，真跑）**

```python
def to_proc_macro(toks):            # 声明宏 → 过程宏
    if t[0] == "punct" and len(t[1]) > 1:
        out.extend(("punct", ch) for ch in t[1])     # 拆成单字符
    elif t[0] == "lifetime":
        out.append(("punct", "'")); out.append(("ident", t[1][1:]))
    elif t[0] == "crate_meta":
        out.append(("ident", "$crate"))              # 单个标识符
```

**不卫生的解析顺序（Python 模型，真跑）**

```python
def resolve_in_proc_macro_output(name, def_env, call_env):
    # 过程宏：一律先在调用处解析
    return ("call", call_env[name]) if name in call_env else ...
```

## 六、性能边界

- 过程宏**显著拖慢编译**：要把 crate 编译成动态库、在编译期加载执行，
  还要把 token 流来回序列化。`syn` 的完整 AST 解析尤其贵。
- 运行期**零成本**：产出就是普通 Rust 代码，`#[derive(Debug)]` 和手写 `impl Debug`
  编译产物一样。
- 代价还包括**错误信息质量**：宏内部的 panic 与 span 处理不当，
  会让报错指向看不懂的位置——`compile_error!` 与正确的 span 是唯一解药。
- 依赖 `proc-macro2` / `syn` / `quote` 时，建议把生成逻辑拆到**普通 crate** 里，
  让 `proc-macro` crate 只做 token 转换，这样那部分代码可以被单元测试。

## 七、注意事项与常见坑

1. **过程宏不能和它的使用者在同一个 crate**——必须拆成两个 crate。
2. **必须写在 crate 根部**（`src/lib.rs` 顶层），写在子模块里会报错。
3. **生成物一律写绝对路径**：`::std::option::Option`、`::core::fmt::Result`。
4. **属性宏不能当 inner attribute**（`#![...]`）用。
5. **derive 的辅助属性是 inert 的**，且只在「该 item 上、写在 derive 之后」
   与「字段 / 变体上」可见——写在 derive 之前属于废弃行为。
6. **outline 模块（`mod foo;`）传给属性宏时只有声明 token**，文件内容不会被加载。
7. **死循环会挂住编译器**，不是超时报错——写递归生成逻辑时必须有终止条件。
8. **`tt` / `ident` 的替换从不被 `Delimiter::None` 包裹**，其它片段可能被包裹；
   依赖「片段在下游长什么样」的代码会踩这个坑（对照 demo 589 的转发不透明）。

## 八、参考资料（实际阅读）

- [Rust Reference — Procedural macros](https://doc.rust-lang.org/reference/procedural-macros.html)
  —— 三类形态与 "must be defined in the root of a crate with the crate type of
  `proc-macro`"、"may not be used from the crate where they are defined"、
  "must either return syntax, panic, or loop endlessly"、两种报错途径、
  `TokenStream ≈ Vec<TokenTree>` 与 `Span`、"Procedural macros are unhygienic"
  及绝对路径/`__internal_foo` 两条建议、`#[proc_macro]` / `#[proc_macro_derive]`
  / `#[proc_macro_attribute]` 的形态与位置清单、derive helper attribute 的作用域规则、
  属性宏两个参数的定义与 outline 模块的例外、
  **以及两份 token 定义对照与双向转换规则**、`///` 转成 `#[doc = r".."]`。
- [Rust Reference — Macros By Example](https://doc.rust-lang.org/reference/macros-by-example.html)
  —— 本批 demo 589 的主源；这里用于对照「声明宏是 mixed-site 卫生」。
- [The Book — ch20-05 Macros](https://doc.rust-lang.org/book/ch20-05-macros.html)
  —— 三类过程宏的入门描述与 `proc-macro = true` 的 Cargo 配置。
