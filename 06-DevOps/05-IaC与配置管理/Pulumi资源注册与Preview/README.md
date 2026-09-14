# Pulumi 资源注册与 Preview 引擎

## 简介

Pulumi 与 Terraform 最大的分歧点是"用什么语言描述基础设施"：Terraform 用自研 DSL（HCL），**Pulumi 用通用编程语言**（TypeScript/Python/Go/C#/Java）。这个选择带来一个清爽的后果——注册资源就是**调用构造函数**，条件、循环、函数、类型系统全都可用；但也带来一个新问题：**程序一边跑，引擎一边收到资源注册请求**，那么"已经建好了"和"只是声明了"必须严格区分。

本 demo 用两种语言复刻 Pulumi 的引擎语义：注册请求（registration request）、URN 生成、Output 的依赖与 unknown 传播、依赖分层调度、preview 与 up 的差别、create/update/replace/delete 的分类，以及官方示例"改名 = 建新的 + 删旧的"。

关键概念：

| 概念 | 一句话解释 |
| --- | --- |
| 语言宿主（language host） | 执行器 + SDK，跑程序并把注册请求转给引擎 |
| 部署引擎（deployment engine） | 内嵌在 CLI 里，算出"当前态 → 期望态"的最小变更集 |
| 资源提供者（resource provider） | 插件 + SDK；**引擎自己不调云 API，而是让 provider 去调** |
| 注册请求 | 构造资源对象时发出；**函数返回不代表资源已创建** |
| URN | 全局唯一的资源标识，由栈/项目/类型（含父类型链）/逻辑名拼出 |
| Output | 即是"未来会有值的 Promise"，又携带依赖集合与 known/unknown 标记 |

## 原理详解

### 1. 三个组件与数据流

```text
  程序 (language host)                引擎 (deployment engine)            资源提供者
  ─────────────────────               ────────────────────────            ──────────
  s3.Bucket("media-bucket")  ──注册请求──▶  读 state：存在吗？
                                            ├─ 不存在 → 让 provider 创建
                                            └─ 存在   → 让 provider diff，
                                                        决定原地更新 or 替换
  程序继续往下跑（并发）─────────────────────▶  （无依赖者可并行处理）
  程序结束                ─────────────▶  没收到注册请求的 state 条目 → 删除
```

原文特别强调：`new aws.s3.Bucket(...)` 返回时"**并不意味着 S3 bucket 已经在 AWS 里创建了**"，只是语言宿主表达了"它属于期望状态"。因此程序可以继续并发往下执行。

### 2. URN 的正式文法

官方给出的 EBNF：

```text
urn            = "urn:pulumi:" stack "::" project "::" qualified-type "::" name
qualified-type = [ parent-type "$" ] type
type           = package ":" [ module ":" ] type-name
```

实例（本 demo 输出）：

```text
urn:pulumi:production::acmecorp-website::aws:s3/bucket:Bucket::my-bucket
urn:pulumi:production::acmecorp-website::custom:resources:Resource$aws:s3/bucket:Bucket::my-bucket
```

父类型链用 `$` 拼接、**不含根 Stack 资源**。URN 必须全局唯一，重复会直接报错（本 demo 的 `[6]` 复现了原文那句话）。

### 3. Output：值 + 依赖 + 是否已知

Output 不只是"异步值"，它带着三件元数据：**依赖哪些资源**、**是否 secret**、**是否已知（known/unknown）**。这决定了 preview 的能力：

```text
preview（只算不执行）                 up（执行并写 state）
  media-bucket.id     → unknown        media-bucket.id     → "aws-3f9a..."
  content-bucket.tags → unknown        content-bucket.tags → "aws-3f9a...:arn"
                        （由前者派生）                       （由前者派生）
```

因为 `content-bucket.tags` 是用 `media-bucket.id` 算出来的，**它在 preview 阶段必然未知**——这段依赖关系既是调度顺序的依据，也是"preview 只能展示将要做什么"的根本原因。

### 4. 依赖分层调度

引擎把"Output 当 Input"识别为一条依赖边，然后用 Kahn 分层排出波次：

```text
wave 1: [media-bucket]
wave 2: [content-bucket]
```

同波次的资源**没有相互依赖，可以并行**。这与 Terraform 的图并行是同一思路，但边是**程序执行时自动产生的**，不需要在配置里显式写 `depends_on`。

### 5. 六种操作与替换顺序

| 符号 | 操作 | 含义 |
| --- | --- | --- |
| （无） | same | 当前态 == 期望态 |
| `+` | create | 新建 |
| `~` | update | 原地修改 |
| `-` | delete | 移除（**没收到注册请求的 state 条目**） |
| `+-` | replace | 必须替换 |
| `++` / `--` | create-replacement / delete-replaced | 替换的两个阶段 |

替换**默认先建后删**（create-replacement → delete-replaced），把停机时间压到最小；`deleteBeforeReplace` 可以翻转顺序，而且在**关闭自动命名**（或显式指定了名字）时会被自动视为开启——否则新资源会因为物理名被旧资源占用而创建失败。

还有一组只在显式模式下出现的操作：`read` / `import` / `refresh`。**`refresh` 只在显式 `pulumi refresh` 或 `up/preview --refresh` 时发生**，Pulumi 不会每次操作前自动刷新——所以"程序里的期望态"和"云上的真实态"可能长期不一致，这正是 drift 的入口。

### 6. 自动命名与"逻辑名 ≠ 物理名"

逻辑名（程序里写的 `media-bucket`）决定 URN 与默认物理名前缀；物理名默认是 `media-bucket-d7c2fa0` 这种**加了随机后缀**的形式。原文给了两个理由：让同一项目的多个栈不撞名；以及在需要替换时支持"先建后删"的零停机更新。

## 环境准备

- 操作系统：Linux / macOS / Windows（只用标准库）
- Python：3.10+（本 demo 用 `3.13`，仅用 `dataclasses`/`random`）
- Go：1.21+（仅用 `fmt`/`sort`/`strings`）

## 运行方式

### Python

```bash
cd python && python3 pulumi_engine.py
```

### Go（引擎逻辑拆为三个文件，每个 ≤ 300 行）

```bash
cd go
go run types.go engine.go pulumi_engine.go
```

## 关键代码片段

```python
def register(self, type_, name, inputs, parent=None):
    u = urn(self.stack, self.project, type_, name,
            parent.type if parent else None)      # (1) URN 由栈/项目/类型/名拼出
    if u in self.registered:
        raise DuplicateURN(f"error: Duplicate resource URN '{u}'")
    ...
    res.outputs["id"] = Output(                   # (2) 状态决定 known / unknown
        urn=u, known=old is not None,
        value=UNKNOWN if old is None else old["id"],
    )
    return res                                    # (3) 立刻返回，程序继续跑
```

## 性能与边界

- 引擎侧复杂度：`k` 个资源、`e` 条依赖边时，分层调度是 O(k + e)；provider diff 的次数等于既有资源数。
- preview 与 up **计算变更集的路径完全一致**，差别只在"是否把操作发给 provider 并写 state"——所以 preview 的准确性取决于 unknown 的传播是否被正确处理。
- 边界：state 里的 `id` 是 provider 视角的标识（AWS ARN、GCP self-link），引擎把它当不透明字符串。
- 若同一份 state 被两个进程并发 `up`，结果不可预期——Pulumi 用它自己的后端锁来避免这种情况（参见同目录的 `Terraform状态锁与远程后端/`）。

## 注意事项与常见坑

1. **"构造完成"不等于"资源存在"**。把 `new Bucket()` 返回值的属性当真实值用（而不是走 Output 管道），在首次部署时会拿到 unknown。
2. **改名等于重建**。逻辑名参与 URN 计算，改掉名字就是新 URN → 新建 + 删除旧的。想改名而不重建，要用 `aliases` 资源选项。
3. **变量名不影响任何东西**。`var foo = new aws.Thing("my-thing")` 里的 `foo` 与基础设施完全无关，改成别的名字 `pulumi up` 照样是 no changes。
4. **显式命名会把自己钉死**。指定了 `name` / `bucket` 就失去了自动命名带来的"多栈共存"与"零停机替换"能力；此时必须记得 `deleteBeforeReplace`，官方文档明确提醒了这一点。
5. **不注册就删除**。删掉一行资源声明，下一次 `up` 会**真的删掉云上资源**——想保命就加 `protect` 或在策略里加保护。
6. **别依赖自动 refresh**。官方明确说 Pulumi 不会自动刷新状态，`pulumi refresh` 必须显式调用；否则拿到的 diff 可能是基于过期状态算出来的。
7. **同 URN 的资源在一个栈里可以出现两次**。官方类型系统文档特别指出：正在被删除的旧副本与将要创建的新副本会共用同一个 URN，所以"URN 唯一"指的是"正常的程序里唯一"。
8. **注册的并行性需要显式表达**：只有"没有依赖关系"的注册才会被并行处理；如果一个资源读取了另一个资源的 Output，就自动串行了。

## 参考资料（实际阅读过的权威来源）

- [How Pulumi works — Pulumi Docs](https://www.pulumi.com/docs/iac/concepts/how-pulumi-works/) — 语言宿主/部署引擎/资源提供者三组件与职责、"the call returning does not mean the bucket was created"、注册请求与并行处理、state 记录（checkpoint）、期望状态模型与最小变更集、六种操作符号表（`+`/`~`/`-`/`+-`/`++`/`--`）、`refresh` 仅在显式调用时发生、替换默认先建后删与 `deleteBeforeReplace`（全文阅读）
- [Resource names and identity — Pulumi Docs](https://www.pulumi.com/docs/iac/concepts/resources/names/) — 逻辑名/物理名/物理 ID/URN 四种身份、自动命名随机后缀的两个理由、显式命名必须配 `deleteBeforeReplace`、改名即重建与 `aliases`、变量名不影响基础设施（全文阅读）
- [Resource names and identity（URN 细节）— Pulumi Docs](https://pulumi.com/docs/concepts/resources/names/) — URN 由项目名/栈名/资源名/资源类型/父资源类型链构造、`$` 分隔的父类型链、重复 URN 报错原文、类型 token 的简化形式规则（全文阅读）
- [Type system — Pulumi Developer Docs](https://pulumi-developer-docs.readthedocs.io/latest/docs/architecture/types/README.html) — URN 的 EBNF 文法（`urn`/`qualified type`/`type`/`identifier`）、Output 的依赖跟踪与 known/unknown/secret 元数据、custom vs component resource、同一 URN 在栈内可因"待删除旧副本 + 待创建新副本"而重复（全文阅读）
- [urn — Pulumi Go SDK 包文档](https://pkg.go.dev/github.com/pulumi/pulumi/sdk/v3/go/common/resource/urn) — URN 的四个组成元素与 `Prefix`/`NameDelimiter`/`TypeDelimiter` 常量、`Parse`/`Rename`/`Type`/`QualifiedType` 等 API（阅读用于交叉验证文法）
