# GitLab CI rules 与父子流水线（首次匹配 + include 上限 + 下游层级约束）

## 简介

GitLab CI 的 `rules` 是整套配置里最"反直觉"的关键字：它既不是布尔开关（**首次匹配即生效，
后续 rule 根本不被求值**），也不是简单的包含/排除（命中后还携带 `when`/`allow_failure`/
`variables` 三组属性）。本 demo 把 `rules` 求值、`rules:if` 变量表达式、`changes`/`exists`
条件、`include` 展开与合并、父子/多项目流水线层级约束全部实现成可执行模型。

覆盖的五块：

| 模块 | 覆盖内容 |
| --- | --- |
| `rules` 求值 | 顺序首次匹配、`when: never`、默认属性 `on_success`/`false`、`rules:variables` |
| `rules:if` | `==`/`!=`/`=~`/`!~`、`&&`/`||` 与括号、正则字面量、**正则内变量不展开** |
| `rules:changes`/`exists` | 非推送类流水线恒真、`compare_to` 换基线、50 模式 / 50000 文件上限 |
| `include` | 先求值再合并、`.gitlab-ci.yml` 优先、150 个上限（含嵌套与重复）、30 秒预算 |
| 下游流水线 | 1000 条上限、父子两层、子流水线 3 个配置文件、`$CI_PIPELINE_SOURCE` 取值 |

关键概念：

| 概念 | 一句话解释 |
| --- | --- |
| 首次匹配 | 顺序求值，命中第一条就定论；**没有默认命中**，全不匹配 = job 不加入流水线 |
| rule 内 AND | 同一条 rule 的 `if`/`changes`/`exists` 必须**同时为真**才算命中 |
| 求值时点 | `rules` 在任何 job 运行**之前**求值，看不到 job 脚本产生的 dotenv 变量 |
| `changes` 恒真面 | 标签/计划/手动等**没有 Git push 事件**的流水线里，`changes` 一律为真 |

## 原理详解

### 1. 首次匹配与属性携带

官方原话：*"Rules are evaluated in order until the first match. When a match is found, the
job is either included or excluded from the pipeline."*

```yaml
job:
  script: echo "Hello, Rules!"
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      when: manual
      allow_failure: true
    - if: $CI_PIPELINE_SOURCE == "schedule"
```

| 流水线类型 | 结果 |
| --- | --- |
| 合并请求 | 命中第 1 条 → `when: manual` + `allow_failure: true` |
| 计划流水线 | 第 1 条不匹配 → 命中第 2 条 → 无属性 → `on_success` + `allow_failure: false` |
| 其它 | 无 rule 命中 → **job 不加入流水线** |

`allow_failure` 的默认值有一条官方明确的例外：**`rules` 里的 `when: manual` 默认为
`false`**，而 job 级 `when: manual`（不在 `rules` 里）默认为 `true`。两者相反，写错会
让"手动门"从"可失败"变成"阻塞流水线"。

### 2. `rules:if` 的正则语义

`=~` / `!~` 右侧是**正则**，且必须用 `/.../` 包裹；不包裹时按字面量处理（本 demo 用
`re.escape` 对齐这一行为）。最关键的一条官方说明：

> Variables in a regular expression are **not expanded**.

于是同一份配置里：

```yaml
variables:
  pattern: '/^ab.*/'
rules:
  - if: '$teststring =~ $pattern'      # 变量存正则 -> 生效
  - if: '$CI_JOB_NAME =~ /$pattern/'   # 正则内写变量 -> 不展开, 不生效
```

第二种写法里 `$pattern` 会被当作**正则文本** `$pattern` 去匹配（`$` 是行尾锚），几乎
永远匹配不上。本 demo 把两种写法都固化成断言。

### 3. `changes` 的"恒真"面与 `**` 通配

- 官方明确：`rules:changes` 在**没有 Git push 事件**的流水线（标签、计划、手动、API 等）
  里**总是求值为真** —— 这类流水线根本没有可比基线；新分支同理（无推送前对比点）。
- `compare_to` 可显式指定基线 ref，改变参与比较的变更文件集合。
- 单条 rule 的 `changes` 模式上限 **50**；`exists` 模式上限同为 50，最多检查 **50000** 个文件。

**`**` 的语义坑**：`cmd/**/*.go` 需要匹配 `cmd/main.go` —— 即 `**/` 必须能匹配**零个**
目录段。Python 的 `fnmatch` 把 `**` 翻成 `.*.*`，会强制要求多一层 `/`；Go 的 `path.Match`
干脆不支持 `**`。所以两侧都自己实现了 glob → 正则：

```
**/  ->  (?:.*/)?     # 零个或多个目录段
**   ->  .*
*    ->  [^/]*        # 不跨 /
?    ->  [^/]         # GitLab 是标准 glob: 单个字符
```

注意最后一行：**GitLab 的 `?` 是"单个字符"**，而 GitHub Actions 的 `?` 是"前一个字符
出现 0 或 1 次"的量词 —— 两家同名不同义，本 demo 与 292 号 demo 各测一种。

### 4. `include` 的合并顺序与硬上限

官方规则：`include` **总是先求值，再与 `.gitlab-ci.yml` 合并**，与 `include` 关键字在
文件里的**位置无关**；同名 job / 全局关键字两者合并，且 **`.gitlab-ci.yml` 里的配置优先**；
默认**每条流水线最多 150 个 include**（含嵌套），**重复的 include 也计数**；解析全部文件
的时限 **30 秒**；**嵌套 include 以公开用户无上下文执行**，因此嵌套段里**没有变量可用**。

本 demo 用"深度优先展开 + root 最后覆盖"的顺序复现合并结果，并用
`150 × 200ms = 30s` 的假设预算把"数量上限"与"时限"两条约束都做成断言。

### 5. 下游流水线的层级约束

| 约束 | 值 | 适用 |
| --- | --- | --- |
| 层级中下游流水线总数 | **1000**（默认） | 两者都适用 |
| 父子流水线嵌套深度 | 最多 **2 层**子流水线 | 仅父子 |
| 单个子流水线的配置文件数 | 最多 **3 个** | 仅父子 |
| `$CI_PIPELINE_SOURCE` | `parent_pipeline` / `pipeline` | 子 / 多项目下游 |

子流水线里 `$CI_PIPELINE_SOURCE` **恒为** `parent_pipeline`，所以
`if: $CI_PIPELINE_SOURCE == "merge_request_event"` 在子流水线里**永远不会命中**，官方建议
改用 `$CI_MERGE_REQUEST_ID`。另外 `strategy: depend` 官方**不推荐**（触发 job 状态与下游
状态不总一致），但生成产物报告的父子流水线**必须**用它或 `strategy: mirror`，否则父流水线
会在子流水线完成前结束、报告不出现在 MR 里。

## 对比 / 选型

| 维度 | `rules` | 旧 `only`/`except` |
| --- | --- | --- |
| 结构 | 顺序列表，每条可携带 `when`/`allow_failure`/`variables` | 关键词并列，无属性 |
| 表达力 | `&&`/`\|\|`/括号组合变量与正则 | 仅分支名/正则/特殊关键字 |
| 默认行为 | 无命中 → **不创建 job** | 无 `only` → 全部触发 |
| 状态 | 推荐 | 已废弃 |

## 环境准备

- 操作系统：任意（纯计算，无网络与文件系统依赖）
- Python 3.8+；Go 1.18+（无第三方依赖）

## 运行方式

```bash
python3 python/gitlab_check.py      # 67 条断言
cd go && go run .                   # 50 条断言
```

## 关键代码片段（Python）

```python
def select_job(rules: list, ctx: dict):
    """返回 (是否加入流水线, 生效属性)。无 rule 命中 -> **不加入**(不是默认加入)。"""
    for idx, rule in enumerate(rules):
        if "if" in rule and not eval_if(rule["if"], ctx.get("variables", {})):
            continue                      # 同一条 rule 内 if/changes/exists 是 AND
        if "changes" in rule and not _changes_hit(rule["changes"], ctx):
            continue
        if "exists" in rule and not _exists_hit(rule["exists"], ctx):
            continue
        when = rule.get("when", "on_success")
        if when == "never":
            return False, {"when": "never", "rule_index": idx}
        allow = rule.get("allow_failure")
        if allow is None:
            allow = False       # 官方: rules 里的 when: manual 默认 allow_failure=false
        return True, {"when": when, "allow_failure": allow, "rule_index": idx}
    return False, {"when": "never", "rule_index": None}
```

## 性能与边界

- 复杂度：`rules` 求值 O(rule 数)；`changes`/`exists` 是 O(模式数 × 文件数)，真实仓库
  规模下需要注意 `exists` 的 50000 文件检查上限。
- **口径说明**：`150 × 200ms = 30s` 是本 demo 为"解析时限"设定的**演示代价模型**，官方
  只给出 30 秒的时限值、未公开单个 include 的解析耗时，该假设已在 README 与代码常量
  注释双处标注。

## 注意事项与常见坑

- **把 `rules` 当 `if-elif-else` 写**：不是"最后一个兜底"而是**顺序首次匹配**，兜底 rule 写在前面会让后面所有 rule 失效。
- **以为"没命中就默认加入"**：相反 —— 没有任何 rule 匹配时 job **不加入流水线**；
  且 `rules` 内 `when: manual` 的 `allow_failure` 默认 `false`，job 级关键字默认 `true`。
- **正则字面量被当变量**：实现里若先把 token 拿去做变量查表，`/^ab.*/` 会变成空串，
  于是 `=~` 恒真、`!~` 恒假 —— 本 demo 的实现阶段真实踩到过这个 bug（4 条断言同时报错
  才暴露）。
- **用 `fnmatch` / `path.Match` 处理 `**`**：前者让 `cmd/**/*.go` 匹配不上
  `cmd/main.go`，后者完全不支持 `**`。
- **同时定义 `merged results` 与 `merge request` 流水线**：官方警告会产生**重复流水线**，需用 `workflow:rules` 收敛。
- **`include` 里塞变量**：只有**部分** CI/CD 变量可用于 `include`，且**嵌套 include 段
  完全没有变量**（以公开用户身份求值）。
- **父流水线早早结束**：生成产物报告的父子流水线必须配 `strategy: depend` 或
  `mirror`，否则报告不会出现在 MR 中。

## 参考资料（实际阅读过的权威来源）

- [Specify when jobs run with `rules`（ci/jobs/job_rules/）— GitLab Docs](https://docs.gitlab.com/ci/jobs/job_rules/)
  — 首次匹配即生效、`when` 属性默认值、`rules:changes:compare_to`、"dotenv 变量不能用于 rules"、
  `rules:if` 的正则与"正则内变量不展开"两个官方示例、`only`/`except` 迁移对照
- [CI/CD YAML syntax reference（ci/yaml/）— GitLab Docs](https://docs.gitlab.com/ci/yaml/)
  — `include` 的求值顺序与"`.gitlab-ci.yml` 优先"、**150 个 include 上限（含嵌套、重复计入）**、
  30 秒解析时限、嵌套 include 无变量、`workflow:rules` 与 `rules` 的关系及重复流水线警告
- [Downstream pipelines（ci/pipelines/downstream_pipelines/）— GitLab Docs](https://docs.gitlab.com/ci/pipelines/downstream_pipelines/)
  — 父子 vs 多项目、**1000 条下游流水线**、**父子最多两层**、多项目无嵌套限制、**单个子流水线最多 3 个配置文件**、
  `strategy: depend` 官方不推荐、`$CI_PIPELINE_SOURCE` 在子流水线恒为 `parent_pipeline`
  （故不能靠 `merge_request_event` 选 job）、`needs:pipeline:job` 取产物及 job token allowlist
