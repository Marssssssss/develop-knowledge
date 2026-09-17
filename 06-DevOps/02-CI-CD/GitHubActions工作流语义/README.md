# GitHub Actions 触发与表达式语义（`on:` 过滤器 + `matrix` 展开 + 表达式求值）

## 简介

CI 配置文件里最容易出错的部分不是 YAML 缩进，而是**求值语义**：为什么 `!releases/**-alpha`
写在 `releases/**` 前后结果相反？为什么 `branches-ignore` 和 `branches` 同时写会直接报错？
为什么两条看起来一样的 `include` 会产出两个矩阵组合？本 demo 把 GitHub Actions 的
**触发过滤、矩阵展开、表达式求值** 三块语义从零实现成可执行的模型，逐条与官方文档对齐。

覆盖的三件事：

| 模块 | 覆盖内容 |
| --- | --- |
| 触发过滤 | `on:` 三种写法、`types` 交集、`branches/tags` 顺序语义、只声明一半时的触发面、`paths`/`paths-ignore`、1000 提交与 3000 文件两条硬边界 |
| 矩阵展开 | 笛卡尔积、`exclude` 按原始值剔除、`include` 的"并入 or 新建"判据与处理顺序 |
| 表达式 | 字面量与假值集合、宽松相等的类型转换表、NaN 关系运算、`&&`/`||` 返回操作数、函数库 |

关键概念：

| 概念 | 一句话解释 |
| --- | --- |
| 过滤器模式 | `*` 不跨 `/`、`**` 跨 `/`、`?`/`+` 是**修饰前一个字符的量词**（不是 shell 的通配符） |
| 顺序语义 | 过滤器列表是"最后命中者胜出"，正负模式**互相覆盖**，因此顺序即语义 |
| 宽松相等 | 类型不同就转数字：`null→0`、`true→1`、字符串按 JSON 数字解析，失败即 NaN |
| 操作数返回 | `&&`/`||` 返回操作数本身而非布尔值，所以能写 `github.head_ref \|\| github.run_id` |
| matrix include | 能否并入取决于**原始矩阵值**，不能覆盖原始值；不能并入就新开一个组合 |

## 原理详解

### 1. 过滤器模式 → 正则

官方对 `branches`/`tags`/`paths` 的模式字符定义（**注意与 shell glob 不同**）：

```
*   -> 匹配零个或多个字符，但不匹配 /        -> [^/]*
**  -> 匹配零个或多个任意字符                -> .*
?   -> 匹配前一个字符零次或一次              -> 量词 ?
+   -> 匹配前一个字符一次或多次              -> 量词 +
\   -> 转义（模式里含上述字符又要字面匹配时）
```

因此 `feat*` **不**匹配 `feat/x`，`feat/**` 才匹配；`v1.?` 同时匹配 `v1` 与 `v1.`，但
不匹配 `v1..`（`?` 只放行 **0 或 1** 次）。

### 2. 顺序即语义

官方原话：*"The order that you define patterns matters. A matching negative pattern
(prefixed with `!`) after a positive match will exclude the Git ref. A matching positive
pattern after a negative match will include the Git ref again."*

```python
decision = False
for p in patterns:                     # 不是 any()/all()，而是"最后命中者胜出"
    neg = p.startswith("!")
    if pattern_to_regex(p[1:] if neg else p).match(value):
        decision = not neg
return decision
```

于是 `['releases/**', '!releases/**-alpha']` 排除 alpha 分支，而把两者对调后 alpha 分支
**又重新被包含**。断言里两条都覆盖了。

### 3. 触发面由"声明了哪些过滤器"决定

| 声明情况 | 分支推送 | 标签推送 |
| --- | --- | --- |
| 只写 `branches`/`branches-ignore` | 按模式判 | **不触发** |
| 只写 `tags`/`tags-ignore` | **不触发** | 按模式判 |
| 都不写 | 触发 | 触发 |
| 同时写 `branches` 与 `branches-ignore` | **工作流校验失败** | — |
| 同时写 `paths` 与 `paths-ignore` | **工作流校验失败** | — |

另外路径过滤**不适用于标签推送**；`paths-ignore` 只有在**全部**变更路径都命中时才拦，
只要有一条没命中就跑。

### 4. 两条被忽略的硬边界

- **提交数 > 1000** ⇒ 工作流**总是**运行，路径过滤被绕过（官方明说）。
- **变更文件 > 3000** ⇒ 若匹配项不在前 3000 个里，工作流**不会**运行；本 demo 因无法
  复现"前 3000 个"的截断顺序，对该分支取**保守判运行**（保守性已在断言里固定）。

### 5. `matrix.include` 的判据

官方规则：逐条处理 `include`，**若某条不会覆盖任何"原始矩阵值"**，就把它并入**全部原始
组合**；否则**新建**一个组合。原始矩阵值不可被覆盖，但 include 新增的键可以被后续
include 覆盖——这正是官方示例里 `color: green` 被 `color: pink` 覆盖的原因。

用官方示例验证（`fruit × animal` + 5 条 include）：

```
原始组合:  {apple,cat} {apple,dog} {pear,cat} {pear,dog}
include 1: color=green      -> 并入全部 4 个
include 2: color=pink,animal=cat -> 只并入 animal=cat 的 2 个(覆盖 green)
include 3: fruit=apple,shape=circle -> 只并入 fruit=apple 的 2 个
include 4: fruit=banana     -> 4 个原始组合都不匹配 -> 新建
include 5: fruit=banana,animal=cat -> 仍不匹配原始组合 -> 再新建(不是并入上一条!)

结果 6 个组合: {apple,cat,pink,circle} {apple,dog,green,circle}
              {pear,cat,pink} {pear,dog,green} {banana} {banana,cat}
```

**include 新建的组合不再被后续 include 合并** —— 这是最容易实现错的一点（若按
"对所有当前组合都试一遍"写，5 号 include 会把 4 号新建的 `{banana}` 升级成
`{banana,cat}`，最终只有 5 个组合，与官方输出不符）。

### 6. 宽松相等的转换表

| 值的类型 | 转数字结果 |
| --- | --- |
| `null` | `0` |
| `true` / `false` | `1` / `0` |
| 数字 | 自身 |
| 字符串 | 按**合法 JSON 数字格式**解析，失败为 `NaN`；**空串返回 0** |
| 数组 / 对象 | `NaN` |

配套三条：字符串比较**忽略大小写**；关系运算（`< <= > >=`）只要有 `NaN` 参与就**恒为
`false`**；对象与数组**只有同一实例**才相等（故 `fromJSON('[1]') == fromJSON('[1]')` 是假）。

## 对比 / 选型

| 维度 | Python 版 | Go 版 |
| --- | --- | --- |
| 覆盖范围 | 全量（含 `*` 对象过滤器、`hashFiles`） | 触发 + 矩阵 + 表达式主体 |
| 矩阵轴顺序 | 依赖 dict 插入序（YAML 声明序） | 显式 `[]Axis` 声明，不依赖 map 遍历序 |
| 断言数 | 110 条，全部实跑 | 55 条，本机无工具链故走人工审查 |
| 适用场景 | 语义参照实现 / 回归基线 | 工业语言的类型安全对照 |

## 环境准备

- 操作系统：任意（纯计算，无网络与文件系统依赖）
- Python 3.8+（`hashlib` / `json` 标准库）；Go 1.18+
- 无第三方依赖

## 运行方式

```bash
# Python（含全部 110 条断言，必须全绿）
python3 python/gha_semantics.py

# Go（含 55 条断言）
cd go && go run .
```

## 关键代码片段（Python）

```python
def match_ordered(patterns, value: str) -> bool:
    """正向列表的顺序语义: 最后命中的模式胜出; 从未命中则为 False。"""
    decision = False
    for p in patterns:
        neg = p.startswith("!")
        body = p[1:] if neg else p
        if pattern_to_regex(body).match(value):
            decision = not neg
    return decision


def p_or(self):
    left = self.p_and()
    while self.peek() == ("op", "||"):
        self.eat("op", "||")
        right = self.p_and()
        left = left if truthy(left) else right      # 返回操作数本身
    return left
```

## 性能与边界

- 复杂度：模式匹配 O(模式数 × 长度)；矩阵展开 O(轴值乘积 × include 数)，`include` 的
  逐组合判据是 O(组合数 × include 数)，真实工作流规模下可忽略。
- **本 demo 未覆盖**：`concurrency`（组名大小写不敏感、`queue: single/max`、`queue: max`
  与 `cancel-in-progress` 组合非法）、`permissions`（一旦指定任一权限，未指定的全部变
  `none`）、`schedule` 的 POSIX cron 与时区语义 —— 这些已在官方文档中确认，留作后续批次。
- `hashFiles` 的口径：官方**未公开字节级算法**，本 demo 用
  `sha256(逐文件 sha256 十六进制按路径升序拼接)` 复现其形状（幂等 / 与遍历顺序无关 /
  对内容敏感），**不保证与 GitHub 逐位一致** —— 已在代码注释与断言名中双重标注。

## 注意事项与常见坑

- **把过滤器模式当 shell glob**：`?` 在 GitHub 里是"前一个字符 0 或 1 次"而不是"任意一个
  字符"，`feat?.yml` 想表达"任意一个字符"会得到完全不同的结果。
- **`branches` + `branches-ignore` 不是"包含并排除"**：想两者兼得必须写进同一个
  `branches` 列表并用 `!` 前缀，且**至少要有一个不带 `!` 的模式**。
- **`!` 开头的 `if:` 表达式必须用 `${{ }}` 包裹**：裸写会被 YAML 当成标签记法解析失败。
- **`if:` 可以省略 `${{ }}`，但并非处处适用**：`run:`、`env:` 等处的表达式仍需要包裹。
- **`include` 的实现陷阱**：把所有当前组合（含 include 新建的）都拿去试并入，会让两条
  "同前缀"的 include 合并成一条，组合数少一个 —— 必须只对**原始**组合做并入判据。
- **`exclude` 用原始值匹配**：`exclude` 在 `include` **之前**执行，且按原始矩阵值判定，
  不能靠 include 补进来的键去排除。
- **`'' == 0` 为真、`'abc' == 0` 为假**：空串转数字是 0（官方明确），非 JSON 数字串是 NaN；
  用 `==` 判"非空字符串"时务必小心。
- 断言优先：本 demo 全部结论都以断言固化，`python/gha_semantics.py` 直接跑即可回归。
## 参考资料（实际阅读过的权威来源）

- [Workflow syntax for GitHub Actions — GitHub Docs](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
  — `on` 三种写法、`on.<event>.types`、`branches`/`tags`/`paths` 过滤与顺序语义、
  `workflow_dispatch` 输入类型与上限（25 个 / 65535 字符）、`schedule` 的 UTC 与最短 5 分钟、`concurrency`、`permissions`
- [Evaluate expressions in workflows and actions — GitHub Docs](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions)
  — 运算符表、宽松相等的类型转换表（含空串→0）、NaN 关系运算恒假、假值集合、
  `contains`/`startsWith`/`endsWith`/`format`/`join`/`toJSON`/`fromJSON` 定义、状态检查函数
