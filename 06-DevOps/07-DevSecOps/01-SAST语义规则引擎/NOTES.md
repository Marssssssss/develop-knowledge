# NOTES — demo571 踩坑与口径说明

## 一、口径说明（模型自己选的读法，官方未给定量）

1. **`Node` 的相等**：OCaml 用的是「结构相等 + `id_info` 的引用比较」。本 demo 把 `IdInfo` 落成值语义（`resolved` + `sid`），
   `equal_ast_bound_code` 里 `Some i1, Some i2` 分支对应为 `resolved` 与 `sid` 同时相等。
2. **`$...ARGS` 绑的是什么**：源码绑的是「一段 AST 列表」，本 demo 用 `Lit(list)`（Python）/ `Node{Kind:"List"}`（Go）承载，
   只用于断言「绑到的元素序列」，不参与 AST 语义。
3. **`less_is_ok` 的取值**：源码按调用点传入（实参列表多为 `false`，语句列表多为 `true`）。本 demo 把它显式成参数，
   而不是替官方决定某个场景的默认值。

## 二、开发期修掉的问题

1. `match_any` 的 `cfg` 最初是必填参数，而列表匹配里回调签名是 `f(p, c, env)`，
   导致 `m_list_with_dots` 一进元素匹配就 `TypeError`。改成 `cfg=None` + `match_args` 里包一层闭包携带 `cfg`，
   **否则 C5b（严格模式）会一直用默认配置、断言恒绿**。
2. `match_args` 的 `env` 原为必填，B 组用例直接传两个列表就报错，已加默认值 `{}`。

## 三、几条容易写反的断言

- **B4b 的顺序**：`inits_and_rest_of_list_empty_ok` 先给 `([], xs)`，再给所有非空前缀，
  所以绑定顺序是 `[] / [1] / [1,2]`，不是反过来。
- **C6 的不对称是「顺序敏感」而不是「不对称 bug」**：把它写成两条断言（C6c / C6d）比只测 `equal_ast_bound_code` 更能说明后果。
- **C3b 不能写成 `len(env) == 0` 就完事**：`$X` 未出现时也是空环境，必须同时断言 `f($_,$_)` 对 `f(a,b)` **匹配成功**，
  否则「环境为空」这条断言对任何实现都成立（伪断言）。
