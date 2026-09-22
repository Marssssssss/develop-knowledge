# Semgrep 通用匹配引擎：`...`、`$X` 与 `$...ARGS`

## 简介

- Semgrep 的「模式」不是正则，而是**用目标语言写的源码片段**；匹配在 **AST_generic（通用 AST）** 上进行，因此同一条规则能跨语言复用语义。
- 本 demo 把 semgrep 匹配引擎里最容易写错的四处机制抽出来做成可执行模型：列表中的 `...`、`$...ARGS`（元变量省略号）、**元变量绑定的一致性判据**、以及 import 归一化。
- 为什么值得单独研究：这四处决定了「一条规则为什么匹配/为什么漏报」。规则作者以为 `f($X, $X)` 是「任意两参数」，实际它是「两个**相等**的实参」，而「相等」的判据里藏着一处**源码显式的不对称**。

## 原理详解

### 1. 匹配结果是「一串环境」，不是布尔值

源码里匹配函数的类型是 `tin -> tout`，其中 `tout = tin list`。空列表即 `fail`，非空即成功，**每个环境代表一种元变量绑定方式**。
两个组合子：

| 组合子 | 含义 | 本 demo 的实现 |
| --- | --- | --- |
| `>>=` | 在前一个环境上继续匹配 | `for e2 in f(...) : out += aux(..., e2)` |
| `>||>` | 两条分支的环境取并集 | Python 里就是 `list + list` |

因此「`[..., $X, ...]` 匹配 `f(1,2,3,4)`」会产生 **4 个环境**（`X` 分别为 1/2/3/4），而不是「找到第一个就停」。

### 2. `...` 的列表匹配（`m_list_with_dots`）

```
| [a], [] when is_dots a                    -> 匹配（`...` 可以吃 0 个）
| a :: xsa, xb :: xsb when is_dots a        -> 吃掉 0 个  >||>  继续吃
| xa :: aas, xb :: bbs                      -> 逐元素匹配
| [], _  |  _ :: _, _                       -> 由 less_is_ok 决定 / fail
```

外层还有一个**优化分支**：模式形如 `[..., PAT, ...]` 时（首、尾都是 `...`），不需要做指数级回溯，只要把 `PAT` 对目标的每个元素试一遍再取并集即可。

`less_is_ok` 是「空模式列表能否匹配非空目标」的开关：语句列表场景会给 `True`，实参列表场景给 `False`。

### 3. `$...ARGS`（`m_list_with_dots_and_metavar_ellipsis`）

`$...ARGS` 不是「`...` 加个名字」那么简单——它会**枚举目标列表的所有切分**：

```
inits_and_rest_of_list_empty_ok []      = [([], [])]
inits_and_rest_of_list_empty_ok xs      = [([], xs)] @ inits_and_rest_of_list xs
```

即长度 `n` 的列表有 `n+1` 个切分（含空前缀与空剩余）。对每个切分：把 `ARGS` 绑到前缀，再用剩余继续匹配。

**关键结论**：`f($...ARGS)` 匹配 `f(1,2)` 时——

- `less_is_ok = false`（实参场景）：只有「剩余为空」的那一个切分能通过 → **1 个绑定**，`ARGS = [1,2]`。
- `less_is_ok = true`（语句场景）：所有切分都能通过 → **3 个绑定**，`ARGS` 依次为 `[]`、`[1]`、`[1,2]`。

源码里针对 `not less_is_ok` 的短路优化（`[a], xs when is_metavar_ellipsis a <> None && not less_is_ok`）目前**是被注释掉的**，所以实际仍走全枚举。

### 4. 元变量绑定一致性（`check_and_add_metavar_binding`）

第二次遇到同一个 `$X` 时，不重新绑定，而是用 `equal_ast_bound_code` 比对；**见证值保持第一次绑定的那个**（源码注释：`valu remains the metavar witness`）。

`equal_ast_bound_code` 对两个 `Id` 的判据分两段：

```ocaml
(* 名字段 *) 两侧都是 case-insensitive 的 id 才忽略大小写，否则精确比较
(* 作用域段 *)
| Some {id_resolved=None}, _ | _, Some {id_resolved=None} | None, _ -> true
| Some i1, Some i2 -> (not unify_ids_strictly) || equal_id_info i1 i2
| Some _, None -> false        (* ← 不对称！ *)
```

三个容易踩的点：

1. **未解析的 id 一律放行** —— 所以 `self.$FOO = $FOO` 能匹配 `self.foo = foo`（源码注释明说 "Maybe we should not ... but let's try"）。
2. **默认不是严格模式** —— `unify_ids_strictly` 默认 `false`，于是「同名但不同作用域（不同 sid）」的两个 id 也算相等。开启严格模式后会被拒绝。
3. **`Some _, None` 是 false，反过来却是 true** —— 已解析的 id 与「完全没有 id_info 的 id」比较会失败，反向却成功。**这意味着匹配结果与实参顺序有关**：`f($X, $X)` 对 `(已解析, 无info)` 不匹配，对 `(无info, 已解析)` 匹配。

另外：**匿名元变量 `$_` 不进环境**（`add_mv_capture` 里 `if Mvar.is_anonymous_metavar key then env`），因此 `f($_, $_)` 可以匹配 `f(a, b)`——它根本不参与合一。

### 5. AC（交换律/结合律）匹配

`m_list_in_any_order` 用于 `a + b` 这类可交换结构：逐元素从目标里挑一个，剩下的递归。它同样受 `less_is_ok` 控制。

### 6. import 归一化（`Normalize_generic`）

匹配前先把 import 展开成「全限定模块名」并**丢掉本地别名**：

| 源码 | 归一化结果 |
| --- | --- |
| `from foo import bar` | `foo.bar` |
| `from foo.bar import baz` | `foo.bar.baz` |
| `from foo import bar, baz` | `foo.bar` 与 `foo.baz`（两条） |
| `import x as y` | `x`（别名被丢掉） |

`FileName + 有 imports` 的组合（形如 `from "path" import x`）在**模式侧返回 `None`**（不参与匹配）；非模式侧按源码里的 bugfix 返回 `FileName(path)`——注释明说 JS 的 `import x from "path"` 不应退化成 `"path"`。

## 与 SQL 注入类规则的对比

| 机制 | 语义 | 典型误用 |
| --- | --- | --- |
| `...` | 吃掉 0 个或多个元素 | 以为它「至少吃一个」 |
| `$...ARGS` | 枚举全部切分并绑定前缀 | 以为它只绑定完整列表 |
| `$X`（重复出现） | 要求两处**相等** | 以为它表示「任意」 |
| `$_` | 不参与合一 | 与 `$X` 混用导致规则静默放宽 |

## 环境准备与运行

```bash
# Python（无需第三方依赖，标准库即可）
cd python && python selfcheck_rule.py        # 49 条断言，全绿时打印 ALL OK

# Go（需要 Go 1.22+）
cd go && go run .
```

## 关键代码

| 文件 | 作用 |
| --- | --- |
| `python/main.py` | 引擎转写：`m_list_with_dots`、`$...ARGS` 切分枚举、`check_and_add_metavar_binding`、`equal_ast_bound_code`、import 归一化 |
| `python/selfcheck_rule.py` | 49 条断言，逐条对应源码分支 |
| `go/matching.go` | 同算法的 Go 转写，与 Python 侧对拍 |
| `go/main.go` | 打印关键结论便于对照 |

## 性能边界与注意事项

- `[..., P, ...]` 优化只在模式**恰好三个元素且首尾都是 `...`** 时生效；写成 `[..., P, ..., Q]` 就退回指数回溯。
- `$...ARGS` 的切分是 `O(n+1)` 个分支，且每个分支还要继续匹配——规则里同时出现多个 `$...` 会使分支数相乘。
- 元变量比较用的是「已绑定代码的等价」，**不是**重新跑一遍模式匹配（源码注释解释了为什么不递归调用 `generic_vs_generic`：会循环依赖）。
- 严格模式 `unify_ids_strictly` 会让「同名不同作用域」的规则失效，写通用规则时通常不开。
- 完整注意事项见 [`NOTES.md`](./NOTES.md)。

## 参考与展望

- 未完成：`<... ...>`（deep expression matching）依赖 `SubAST_generic` 的子表达式枚举，本 demo 未建模；`pattern-not` / `pattern-inside` 的组合发生在 Python 侧（`Match_patterns.ml` 之上），也未建模。
- 可继续：把常量传播（`constant_propagation` 让「已知常量值的变量」等价于该字面量）接进 `equal_ast_bound_code`。

## 参考资料（实际阅读过的权威来源）

- [semgrep/semgrep — `src/matching/Matching_generic.ml`](https://github.com/semgrep/semgrep/blob/develop/src/matching/Matching_generic.ml) — `m_list_with_dots`、`m_list_with_dots_and_metavar_ellipsis`、`m_list_in_any_order`、`check_and_add_metavar_binding`、`equal_ast_bound_code`、`inits_and_rest_of_list_empty_ok`、`all_elem_and_rest_of_list`
- [semgrep/semgrep — `src/matching/Normalize_generic.ml`](https://github.com/semgrep/semgrep/blob/develop/src/matching/Normalize_generic.ml) — `full_module_names`、`normalize_import_opt`、JS `import x from "path"` 的 bugfix 注释
- [semgrep/semgrep — `src/matching/SubAST_generic.ml`](https://github.com/semgrep/semgrep/blob/develop/src/matching/SubAST_generic.ml) — deep matching 的子表达式/子语句枚举（本 demo 未建模，仅作延伸）
- [semgrep/semgrep — `src/matching/Match_patterns.ml`](https://github.com/semgrep/semgrep/blob/develop/src/matching/Match_patterns.ml) — 规则组合层入口
- [semgrep/semgrep — `src/matching/Pattern_vs_code.ml`](https://github.com/semgrep/semgrep/blob/develop/src/matching/Pattern_vs_code.ml) — 模式到代码的逐节点匹配
- [semgrep/semgrep-interfaces — `rule_schema_v1.yaml`](https://github.com/semgrep/semgrep-interfaces/blob/main/rule_schema_v1.yaml) — `pattern` / `pattern-either` / `pattern-not` / `metavariable-regex` 等字段的官方 schema
