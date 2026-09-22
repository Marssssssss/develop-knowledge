# 声明宏与卫生性（`macro_rules!` 的匹配、转录与作用域）

> Rust 第三批 · demo 589 · 依据 Rust Reference `macros-by-example` 实读。
>
> 一句话：**`macro_rules!` 不是字符串替换，而是一台「逐 token、不向前看」的
> 小解析器**：它先把输入按**片段分类符**切成 AST 片段，再按转录器拼回去；
> 片段后面能跟什么 token 由**跟随集**规定，而**局部变量与标签在定义处查找、
> 其它符号在调用处查找**（mixed-site hygiene）——
> 这三条就是「宏为什么既好使又爱报莫名其妙的错」的全部答案。**

## 一、原理详解

### 1.1 片段分类符（15 种）

`block` `expr` `expr_2021` `ident` `item` `lifetime` `literal` `meta` `pat`
`pat_param` `path` `stmt` `tt` `ty` `vis`

其中三条容易踩：

- `pat` 从 2021 起匹配**顶层 or-pattern**，`pat_param` 只匹配 `PatternNoTopAlt`
  （所以 `pat_param` 后面允许 `|`，`pat` 不允许 —— 断言 2.4 / 2.5）。
- 2024 之前 `expr` 不匹配顶层的 `_` 与 const block；`expr_2021` 是为兼容存在的。
- `tt` 是「单个 token 或一对定界符包起来的一串」，是唯一能**无损转发**的东西。

### 1.2 跟随集：为什么 `$i:expr [ , ]` 不合法

官方原文说得非常直白：这条限制**不是**因为今天有歧义（`[,]` 不可能是合法表达式的一部分），
而是因为 `[` 可以开启后缀表达式，**将来**一旦 `[,]` 有含义，这个 matcher 就会变得有歧义或解析错误，
从而破坏今天能用的代码。清单：

| 片段 | 后面只能跟 |
| --- | --- |
| `expr` / `stmt` | `=>`、`,`、`;` |
| `pat_param` | `=>`、`,`、`=`、竖线、`if`、`in` |
| `pat` | `=>`、`,`、`=`、`if`、`in` |
| `path` / `ty` | `=>`、`,`、`=`、竖线、`;`、`:`、`>`、`>>`、`[`、`{`、`as`、`where`、block 元变量 |
| `vis` | `,`、非 raw `priv` 的标识符、能开始一个类型的 token、`ident`/`ty`/`path` 元变量 |
| 其余（`tt`/`ident`/`literal`/...） | 任意 token |

本 demo 断言 2.x 把这张表做成可执行检查：注意判定的是**分组的左定界符**
（`$i:expr [` 里的 `[`），而不是分组里的内容。

### 1.3 不向前看（no lookahead）

官方例子 `($($i:ident)* $j:ident)` 遇到 `ambiguity!(error)` 直接报 local ambiguity：
编译器不会为了确认后面是不是 `)` 而多读一个 token。断言 5.1 建模了这条。

### 1.4 最外层定界符不限定种类

官方原文：matcher 的**最外层**定界符可以匹配任意一对定界符，所以 `(())` 能匹配 `{()}`；
但**内层**必须一致，所以匹配不了 `{{}}`。断言 6.1 / 6.2。

### 1.5 重复的三条硬规矩

1. `*` 任意多次、`+` 至少一次、`?` 零或一次；**`?` 不能带分隔符**（断言 4.4）。
2. 转录里的元变量必须与匹配里**层数、种类、嵌套顺序完全一致**（断言 4.5 / 4.9）。
3. 同一层重复里的多个元变量必须绑到**同样多**的片段：
   `($($i:ident),* ; $($j:ident),*)` 调用时两边数量不同就是错误（断言 4.6 / 4.7）。

另外，匹配到 **0 次**时元变量也算绑定（绑到空序列），转录出空内容而不是报错（断言 4.3）。

### 1.6 混合点卫生性（mixed-site hygiene）

| 符号类别 | 在哪里查找 |
| --- | --- |
| 局部变量、block/loop 标签 | **定义处** |
| 其它（item、函数、类型、宏名…） | **调用处** |
| `$crate` | 定义这个宏的 crate（特殊） |

官方例子（断言 8.x 逐条复现）：

```rust
let x = 1;
fn func() { unreachable!("this is never called") }

macro_rules! check {
    () => {
        assert_eq!(x, 1); // 定义处的 x == 1
        func();           // 调用处的 func（不打 panic 的那个）
    };
}
```

推论：**宏展开里定义的局部变量不在调用之间共享**——`m!(define); m!(refer);`
会报 E0425（断言 8.3）。

`$crate` 还有一条：它**不改变可见性**，私有项照样不可达（断言 9.3，官方 E0603 例子）。

### 1.7 两种作用域

- **textual scope**：按源码出现顺序，定义之后才可用，**可跨模块、跨文件**，
  也能在函数体内定义；后定义的**遮蔽**先定义的。
- **path-based scope**：与 item 作用域一致，靠 `use` 引入。
- 无修饰调用：**先** textual 再 path-based；带路径调用：只查 path-based。
- textual 绑定**遮蔽** path-based 绑定——官方那个「`use` 写在后面，
  但前面的调用点仍然解析到 path-based」的例子，本 demo 断言 10.6 / 10.7 成对覆盖。

## 二、对比

| 维度 | `macro_rules!` | 过程宏（demo 590） | C 预处理器 |
| --- | --- | --- | --- |
| 输入 | token 树 + 片段分类符 | `TokenStream`（纯 token） | 纯文本 |
| 卫生性 | **mixed-site**（局部定义处 / 其余调用处） | **不卫生**（当作就地展开） | 完全不卫生 |
| 能力 | 只能按规则拼 token | 任意 Rust 代码，可解析 AST | 文本替换 |
| 报错 | 展开期/解析期 | panic 或 `compile_error!` | 通常要等编译后期 |

## 三、环境

- Rust 2021 edition，纯标准库；`cargo run` 即可。
- Python 3 模型（可运行、含 51 项断言）：`python selfcheck_macros.py`。
  其中 `macro_lex.py` 负责词法与跟随集，`macro_match.py` 负责匹配，
  `macro_trans.py` 负责转录，`hygiene.py` 负责卫生性与作用域。

## 四、运行方式

```bash
cd rust && cargo run
cd python && python main.py              # 跟随集 / 匹配 / 展开 / mixed-site hygiene
cd python && python selfcheck_macros.py  # 51 项断言
```

`rust/compile_fail/` 下 4 个文件**故意编译失败**：

| 文件 | 想说明什么 |
| --- | --- |
| `e0425_no_share_between_invocations.rs` | 宏里定义的局部变量不跨调用共享 |
| `follow_set_expr_bracket.rs` | `expr` 后面不能跟 `[` |
| `local_ambiguity.rs` | 匹配不向前看 |
| `forwarding_opaque_ast.rs` | 转发的片段是**不透明 AST**（`tt` 除外） |

## 五、关键代码

**分组的左定界符才是「紧跟在片段后面」的那个 token（Python 模型，真跑）**

```python
if nxt[0] == "grp":
    tok = nxt[1]          # 分组的**左定界符**
elif nxt[0] == "lit":
    tok = nxt[1][1]
if tok not in FOLLOW[frag]:
    bad.append("元变量 $%s:%s 后面不能跟 %r" % (p[1], frag, tok))
```

**混合点卫生性（Python 模型，真跑）**

```python
if kind in ("local", "label"):     # 局部变量与标签 → 定义处
    ...def_site...
else:                              # 其余符号 → 调用处
    ...call_site...
```

## 六、性能边界

- 展开**全部发生在编译期**，运行期零成本；代价是编译时间（递归宏尤其明显，
  官方有 `recursion_limit`，默认 128）。
- 递归宏会把编译器的工作量变成**指数级**：`count_exprs!(1,2,3)` 要展开 4 层。
  能用重复 `$(...)*` 表达的就别写递归。
- `stringify!` / `concat!` 这类宏把内容变成字面量，会让**代码体积**增大，
  但运行期仍是静态字符串。
- 卫生性是有代价的：每次调用都会生成新的 syntax context，
  这是「两个调用互不干扰」的实现基础，也让宏的调试信息更难读。

## 七、注意事项与常见坑

1. **`expr` 后面不能跟 `[`、`(`、`{`** —— 想匹配「表达式后面还有东西」就先收成 `tt`。
2. **转发时一律用 `tt`**：`$l:expr` 交给下游宏就变成不透明 AST，下游无法用字面 token 匹配。
3. **`$crate` 不绕过可见性**：私有项在别的 crate 里照样不可达。
4. **宏里定义的局部变量逃不出这次展开**（E0425），想共享就让它成为宏的**参数**。
5. **`?` 不能带分隔符**：`$( $x:expr ),?` 是错的，写成 `$(, $x:expr)?` 或直接 `$(,)?`。
6. **后定义的宏会遮蔽先定义的**：跨文件引用时尤其容易误伤。
7. **匹配到 0 次不是错误**：转录会产出空内容，别指望它报「至少需要一个」。
8. **递归宏会撞 `recursion_limit`**：报错时加 `#![recursion_limit = "256"]` 只是缓兵之计，
   更好的做法是把递归改写成重复。

## 八、参考资料（实际阅读）

- [Rust Reference — Macros By Example](https://doc.rust-lang.org/reference/macros-by-example.html)
  —— 15 个片段分类符与各 edition 差异（`pat` 2021、`expr`/`expr_2021` 2024）、
  转录与「逐规则尝试、首个成功即止」、no lookahead 的 `ambiguity!` 例子、
  最外层定界符的原话、元变量与 `$crate`、重复的 `*`/`+`/`?` 与「`?` 不能用分隔符」、
  转录的三条限制（层数一致、至少一个元变量、同一层数量相同）、
  转发不透明与 `ident`/`lifetime`/`tt` 例外、
  mixed-site hygiene 的两个官方例子（定义处 `x` 与调用处 `func`、`m!(define)/m!(refer)` 的 E0425）、
  `$crate` 不影响可见性的 E0603 例子、textual / path-based 两种作用域、
  遮蔽与「textual 遮蔽 path-based」的完整例子。
- [Rust Reference — Procedural macros](https://doc.rust-lang.org/reference/procedural-macros.html)
  —— 本批 demo 590 的主源；这里用于对照「过程宏不卫生」。
