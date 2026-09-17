# Jenkins 声明式 Pipeline 语义（agent/timeout 区间 + post 顺序 + when 时点 + matrix 两段式）

## 简介

声明式 Pipeline 把 Groovy 的任意性换成了"严格结构"，代价是**大量语义写在文档里、却不在
语法里**：`timeout` 到底算不算 agent 分配时间？`post` 块按什么顺序执行？`when` 在 `agent`
之前还是之后求值？`matrix` 的 cell 是静态还是动态生成的？本 demo 把这几处"光看 YAML 看不
出来"的规则实现成可执行模型，逐条对齐官方 Pipeline Syntax 文档。

覆盖的五块：

| 模块 | 覆盖内容 |
| --- | --- |
| `post` | 10 个条件的触发判据 + **固定执行顺序**（与声明顺序无关） |
| `agent`/`timeout` | 顶层与 stage 级的**计数区间差异**、agent 类型与 `none` 的位置约束 |
| stage 时点 | `options → input → agent → when` 基线顺序、三个 `before*` 开关优先级、四选一与不可嵌套 |
| `parallel`/`matrix` | `failFast` 中止语义、`axis`/`exclude` 静态展开与 `notValues` |

关键概念：

| 概念 | 一句话解释 |
| --- | --- |
| timeout 区间 | 顶层 `agent` 分配时间**不计入** timeout，stage 级 **计入** |
| post 顺序 | 固定九步条件 + 恒在最后的 `cleanup`，声明顺序不影响执行顺序 |
| when 提前 / matrix | `beforeOptions > beforeInput > beforeAgent`；`axis`/`exclude` 运行前定 cell，per-cell 指令运行时求值 |

## 原理详解

### 1. agent 与 timeout 的计数区间不同

官方明确区分了两种作用域，且这处差异会直接导致"同样 1 秒的 timeout 行为不同"：

```groovy
pipeline { agent any                       // 顶层: 先分配 agent, 再应用 timeout
    options { timeout(time: 1, unit: 'SECONDS') }
    stages { stage('Example') { steps { echo 'Hi' } } } }
```

```groovy
pipeline { agent none                      // 顶层不分配
    stages { stage('Example') {
        agent any                          // stage 级: options 在分配 agent **之前**调用
        options { timeout(time: 1, unit: 'SECONDS') }
        steps { echo 'Hi' } } } }
```

- **顶层**：分配 agent 的时间**不计入** timeout，因此 agent 供应再慢也不会吃掉 timeout
  预算。
- **stage 级**：`options` 在分配 `agent` **之前**被调用，timeout 从那一刻开始计时，
  所以**分配 agent 的时间计入** timeout —— agent 供应延迟会让 Pipeline 直接失败。

本 demo 用 `(是否超时, 计入秒数)` 把它固化成断言：同样 `alloc=600s, work=100s, limit=300s`，
顶层算出 `100s → 不超时`，stage 级算出 `700s → 超时`。

### 2. post 条件的固定执行顺序

官方给出的顺序（**与 YAML 里的声明顺序无关**）：

```
always -> changed -> fixed -> regression -> aborted -> failure -> success -> unstable
       -> unsuccessful -> cleanup
```

`cleanup` 特别之处在于：它**在所有其它 post 条件评估完毕之后**运行，无论 Pipeline 或
stage 是什么状态 —— 所以它永远是最后一条。

各条件的判据（本 demo 全部实现并断言）：

| 条件 | 触发判据 |
| --- | --- |
| `always` / `cleanup` | 无条件 |
| `changed` | 本次状态 ≠ 上次状态 |
| `fixed` | 本次 `success` 且上次 `failure`/`unstable` |
| `regression` | 本次 `failure`/`unstable`/`aborted` 且上次 `success` |
| `success`/`failure`/`unstable`/`aborted` | 本次状态精确等于该值 |
| `unsuccessful` | 本次状态**不是** `success`（一个"或"的聚合） |

注意 `fixed` 与 `regression` 是**互斥**的：前者要求本次成功、后者要求本次失败，所以
`["fixed","regression"]` 声明下每次只会跑其中一个。

### 3. stage 内各段的求值时点

官方对 `input` 的描述给出了关键的相对位置：

> 该 stage 将在**应用完 `options` 后**、且在**进入该 stage 的 `agent` 块或评估 `when`
> 条件之前**暂停。

于是基线顺序是 `options → input → agent → when`（`options` 最先、`when` 最后）。三个
开关把 `when` 往前提，优先级由官方明确定义：

| 开关 | 效果 | 优先级 |
| --- | --- | --- |
| `beforeOptions true` | `when → options → input → agent` | 最高 |
| `beforeInput true` | `options → when → input → agent` | 中（**优先于** `beforeAgent`） |
| `beforeAgent true` | `options → input → when → agent` | 最低 |
| 都不开 | `options → input → agent → when` | — |

官方原话是 `beforeInput true` **优先于** `beforeAgent true`，而 `beforeOptions true`
**优先于** `beforeInput true` 与 `beforeAgent true` —— 所以当多个开关同时置位时，等价于
只开优先级最高的那个。本 demo 的 `when_priority_rank` 把它压成一个 0~3 的权重。

### 4. 结构约束：四选一与不可嵌套

> 一个 stage 必须有且仅有一个 `steps`、`stages`、`parallel` 或 `matrix`。如果该 stage
> 本身嵌套在 `parallel` 或 `matrix` 块内，则**不能**在其中嵌套 `parallel` 或 `matrix` 块。

但 `parallel`/`matrix` 块**内**的 stage 依然可以用 stage 的全部其它能力：`agent`、
`tools`、`when`、`environment` 等。`options` 有作用域白名单：stage 级只允许
`retry`/`timeout`/`timestamps`/`skipDefaultCheckout`；`disableRestartFromStage` 干脆
**不能在 stage 内使用**；`buildDiscarder`、`parallelsAlwaysFailFast` 等只能在
`pipeline` 块里。

### 5. matrix 的两段式求值

> `axis` 与 `exclude` 指令在 pipeline 运行**开始之前**生成静态 cell 集合；而 per-cell
> 指令在**运行时**求值。

per-cell 指令包括 `agent`、`environment`、`input`、`options`、`post`、`tools`、`when`。
`exclude` 支持两种子句：`values`（命中即排除）与 `notValues`（**不在**该列表里才排除）；
一个 `exclude` 条目命中当且仅当它列出的**每个轴子句都匹配**。官方示例里
`{PLATFORM: notValues: ["windows"], BROWSER: values: ["edge"]}` 会排掉 linux/mac × edge，
只保留 windows × edge。

`parallel` 的中止语义：`failFast true`（写在含 `parallel` 的 stage 上）或
`options { parallelsAlwaysFailFast() }`（pipeline 级开关）下，任一并行 stage 失败即中止
其余未完成的 stage。注意 `unstable` **不等于** `failure`，不会触发 `failFast`。

## 对比 / 选型

| 维度 | 声明式（Declarative） | 脚本式（Scripted） |
| --- | --- | --- |
| 结构 | 预定义、受约束（`pipeline {}` 必须存在） | 无约束，从 `Jenkinsfile` 顶部向下串行执行 |
| 编程模型 | 声明式；流程控制走 `when`/`parallel`/`matrix`/`post` | 命令式（Groovy `if/else`、`try/catch/finally`） |
| 逃生舱 | `script { }` 块（复杂时应移入 Shared Libraries） | 本身就是脚本 |
| 已知限制 | `pipeline {}` 内代码有最大长度限制（issue 37984） | 无此限制；但**持久性**要求使部分 Groovy 惯用法不可用 |

两者底层同属一个 Pipeline 子系统，都能使用内置/插件步骤与 Shared Libraries。

## 环境准备

操作系统任意（纯计算，无网络与文件系统依赖）；Python 3.8+ 与 Go 1.18+，无第三方依赖。

```bash
python3 python/jenkins_check.py     # 69 条断言
cd go && go run .                   # 62 条断言（本机无 Go 工具链，走人工审查）
```

## 关键代码片段（Python）

```python
def run_post(declared: list, status: str, prev_status: str | None) -> list:
    """按**官方固定顺序**返回实际执行的 post 条件(与声明顺序无关)。"""
    if status not in STATUSES:
        raise JenkinsError("未知构建状态: %s" % status)
    return [c for c in POST_ORDER
            if c in set(declared) and post_condition_runs(c, status, prev_status)]


def stage_eval_order(before_options=False, before_input=False, before_agent=False) -> list:
    if before_options:  return ["when", "options", "input", "agent"]
    if before_input:    return ["options", "when", "input", "agent"]
    if before_agent:    return ["options", "input", "when", "agent"]
    return ["options", "input", "agent", "when"]
```

## 性能与边界

- 复杂度：`post` 求值 O(10)；`matrix` 展开 O(各轴值乘积 × exclude 数)；`parallel` 中止
  判定 O(stage 数)。真实规模下都不构成瓶颈（matrix 的 cell 数是轴值乘积）。
- **未覆盖**：`agent` 各类型的具体参数（`docker` 的 `registryUrl`/`registryCredentialsId`、
  `dockerfile` 的 `additionalBuildArgs`/`dir`、`kubernetes` 的 Pod 模板）、`triggers` 三种
  触发器在调度器里的细节（**`H` 哈希符号依赖 Jenkins 内部作业名哈希，本 demo 不还原**该
  算法，只记录其"打散负载尖峰"的用途）、`environment { credentials(...) }` 展开出的
  `_USR`/`_PSW`、`tools` 自动安装、Shared Libraries。

## 注意事项与常见坑

- **以为 `post` 按声明顺序执行**：完全按官方固定顺序，`cleanup` 永远最后；把清理逻辑写在
  `cleanup` 之外的块里，可能在别的条件之后才跑。
- **stage 级 timeout 吃掉 agent 分配时间**：agent 供应慢时 Pipeline 会莫名失败、重试即通过。
- **`input` 的位置**：它在 `options` 之后、`agent` 与 `when` 之前暂停，故 `input` 里无法依赖
  `agent` 上的工作区。
- **同时开多个 `before*`**：不是"都生效"，而是按优先级只认最高的那个；`beforeInput` 明确
  优先于 `beforeAgent`。
- **一个 stage 既写 `steps` 又写 `parallel`**：硬校验失败，必须二选一；`unstable` 也不等于
  `failure` —— 它不触发 `failFast`、不满足 `failure` 条件，但满足 `unsuccessful`。
- **`fixed` / `regression` 同时声明**：二者互斥（一个要求本次成功、一个要求本次失败），只跑其一。
- **`matrix` 的 cell 数被误当成动态**：`axis`/`exclude` 在运行前就定了；想按运行期条件
  裁剪 cell，只能靠 per-cell 的 `when`（那时 cell 已经存在，只是不执行）。
- 断言优先：`python/jenkins_check.py` 直接跑即可回归全部 69 条结论。

## 参考资料（实际阅读过的权威来源）

- [Pipeline Syntax（doc/book/pipeline/syntax/）— Jenkins Docs](https://www.jenkins.io/doc/book/pipeline/syntax/)
  — `agent` 各类型与顶层/stage 级 timeout 计数区间差异、`post` 十条件定义与**固定执行顺序**
  （含 `cleanup` 在所有条件之后）、`options` 全量列表与 stage 级白名单、`parameters`/`triggers`
  的"仅一次"约束、`input` 暂停位置、`when` 内置条件全集与 `beforeAgent`/`beforeInput`/
  `beforeOptions` 优先级、`stage` 四选一与 parallel/matrix 不可嵌套、`matrix` 的
  `axes`/`excludes`/`notValues` 与"静态 cell + 运行时 per-cell 指令"、声明式 vs 脚本式对比
  （含 issue 37984 长度限制与 JENKINS-27421/JENKINS-26481 的 Groovy 惯用法限制）
