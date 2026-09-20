# Terraform 插件协议、发现与版本选择 (Demo 464)

> `terraform init` 那几行 "Installing ..." 背后其实做了三件事：**找得到**（发现位置）、**选得对**（版本选择三条规则）、**说得上话**（插件协议握手）。
> 本 demo 用 Python + Go 双实现复现这三层判定。

## 一、简介

官方（How Terraform works with plugins）把 Terraform 分为两部分：

- **Terraform Core**：静态编译的 Go 二进制，负责读配置与插值、**资源状态管理**、**构建资源图**、**计划执行**、**通过 RPC 与插件通信**
- **Terraform Plugins**：*"Plugins are written in Go and are executable binaries invoked by Terraform Core over RPC ... They are executed as a **separate process** and communicate with the main Terraform binary over an RPC interface."*

Provider 插件的四项职责（原文）：初始化做 API 调用的库、与基础设施厂商认证、定义 managed resources 与 data sources、定义 functions。

## 二、原理详解

### 2.1 插件协议是「版本化接口」

原文（Terraform plugin protocol）：*"The Terraform plugin protocol is a **versioned interface** between Terraform CLI and Terraform Plugins."*

三条元规则：

1. **主版本划分兼容性**：*"Major versions of the protocol delineate Terraform CLI and Terraform Plugin compatibility."*
2. **次版本是叠加的**：*"Minor versions of the protocol are additive."*
3. **实现载体**：*"The protocol is implemented in Protocol Buffers and gRPC, with the canonical source for protocol definitions located in the Terraform CLI repository."*

| 协议 | 兼容的 CLI | 相对上一版的新增 |
| --- | --- | --- |
| **v6** | CLI 1.0+ | 全部 v5 功能 + **Nested Attributes**（`SchemaAttribute` 的 `NestedType` 字段）、可用参数语法代替块语法、能对**单个嵌套属性**配置敏感度 |
| **v5** | CLI 0.12+ | — |

实现对应（原文列举）：v6 有 `terraform-plugin-framework` / `tf6server` / `tf5to6server` / `tf6muxserver`；v5 有 `terraform-plugin-framework` / `terraform-plugin-sdk/v2` / `tf5server` / `tf6to5server` / `tf5muxserver`。

> **本 demo 的口径（官方未给出具体算法，明确标注）**：「次版本叠加」在本模型里实现为 *有效次版本 = min(CLI 次版本, 插件次版本)* —— 双方都认识的能力才可用。自检 `C3`。

### 2.2 init 的发现流程

原文：*"When `terraform init` is run, Terraform reads configuration files in the working directory to determine which plugins are necessary, searches for installed plugins in several locations, sometimes downloads additional plugins, decides which plugin versions to use, and writes a **lock file** to ensure Terraform will use the same plugin versions in this directory until `terraform init` runs again."*

### 2.3 版本选择的四条规则（最容易被误解的一段）

原文逐条列举：

| 顺序 | 条件 | 结果 |
| --- | --- | --- |
| 0 | 有 lock file 且满足约束 | **一律遵守 lock**（"If a lock file is present ... will all obey it"） |
| 1 | 已安装里有可接受的 | 用**已安装里最新的**，*"even if the registry has a newer acceptable version"* |
| 2 | 已安装里没有、registry 里有 | 从 registry 下载**最新的可接受版本** |
| 3 | 都没有 | *"terraform init fails and the user must manually install an appropriate version"* |

**规则 1 是最反直觉的一条**：本地缓存里有个旧但合规的版本，即使远端 registry 已经有更新的合规版本，Terraform 也不会去下 —— 想升级必须 `-upgrade` 或删 lock。自检 `D1` 专门钉住这条。

另外原文还提到：*"During discovery, the Terraform Registry uses the **protocol version** as additional compatibility metadata when deciding which plugin versions Terraform CLI can select."* —— 协议版本也参与筛选，所以老 CLI 在 registry 里根本「看不见」只有 v6 协议的插件（`E1`/`E2`）。

### 2.4 版本约束与 `~>`

原文（Provider Requirements）：*"Each module should at least declare the minimum provider version it is known to work with, using the `>=` version constraint syntax."*

`~>` 的官方定义：*"a convenient shorthand for **allowing the rightmost component of a version to increment**"* —— `~> 1.0.4` 只允许 `1.0.x`，`~> 1.0` 允许 `1.x`。

原文还给了使用建议：*"Do not use `~>` (or other maximum-version constraints) for modules you intend to reuse across many configurations ... it forces users of the module to update many modules simultaneously."* —— **可复用模块只写最小版本，上限交给根模块**。

### 2.5 go-plugin 握手

原文（go-plugin README）：

- *"A very basic 'protocol version' is supported that can be incremented to invalidate any previous plugins ... When a protocol version is incompatible, a **human friendly error message** is shown to the end user."*
- 架构是**启动子进程 + RPC**：*"Plugins can't crash your host process: A panic in a plugin doesn't panic the plugin user."*
- 多路复用：net/rpc 走 yamux，gRPC 走 HTTP/2
- 安全：*"Plugins can be verified with an expected checksum and RPC communications can be configured to use TLS."*
- 限制：*"it is currently only designed to work over a local [reliable] network. Plugins over a real network are not supported."*

本 demo 的 `handshake()` 按「先 magic cookie、后 protocol version」两步校验。cookie 的作用是**防止把普通二进制当插件启动**。

## 三、对比

| 维度 | Protocol v5 | Protocol v6 |
| --- | --- | --- |
| 最低 CLI | 0.12 | 1.0 |
| 嵌套属性 | 只能用 block 语法 | `NestedType` 属性，参数语法 |
| 敏感度 | 整个只读属性 | 单个嵌套属性 |
| 典型 SDK | plugin-sdk/v2 | plugin-framework |

| 手段 | 用途 |
| --- | --- |
| lock file | 锁定版本，跨机器一致 |
| `~>` | 限制上限，防意外升级 |
| `>=` | 声明最小可用版本（可复用模块推荐） |
| muxserver | 把多个 provider 合成一个 |

## 四、环境与运行

```bash
python selfcheck_tfplugin.py     # 33 项断言，纯标准库
go run .                          # Go 版同口径（本机无 Go 工具链，走人工审查 + 静态检查）
```

## 五、关键代码

```python
satisfies("1.0.9", "~> 1.0.4")            # True：只允许最右分量递增
negotiate((6, 3), [(5, 2), (6, 1)])       # (6, 1)：取共有最大主版本，次版本取小
select_version(">= 1.0", ["1.5.0"], [("2.1.0", 6)])   # ('1.5.0','installed')
select_version(">= 1.0", [], [("2.0.0", 6), ("2.1.0", 6)])  # ('2.1.0','registry')
handshake(out, "TF_PLUGIN_MAGIC_COOKIE", "...", 6, 6)       # (True,'ok')
```

## 六、性能边界

- 版本解析/比较是 O(分量数)；候选排序 O(n log n)，registry 候选通常几十条
- 真正的耗时在网络下载与校验和验证，不在选择算法
- 子进程 RPC 每次调用有序列化开销，go-plugin 只建**一条**连接（gRPC 走 HTTP/2 多路复用）

## 七、注意事项与常见坑

1. **本地已有合规版本时不会自动升级** —— 想拿新版要 `-upgrade`。
2. **`~>` 写在可复用模块里会造成「一次升级、处处升级」**，官方明确反对。
3. **老 CLI 看不见新协议插件**：协议版本是 registry 的筛选元数据，不只是握手时用。
4. **lock file 不满足约束时会失效**（例如把约束从 `>= 1.0` 收紧到 `>= 2.0`），此时退回正常选择流程（`D4`）。
5. **插件是独立进程**：插件 panic 不会带崩 CLI，但插件崩溃会导致本次操作失败。
6. **不要跨网络跑插件**：go-plugin 明确只支持本地可靠网络。
7. 本模型不实现真实的 protobuf 编解码与 gRPC 传输，只建模握手与协商的判定逻辑。

## 八、参考资料（均为本轮实际抓取并阅读）

- HashiCorp《Terraform plugin protocol》—— 版本化接口、v6/v5 差异与兼容范围
  https://developer.hashicorp.com/terraform/plugin/terraform-plugin-protocol
- HashiCorp《How Terraform Works With Plugins》—— Core/Plugin 分工、发现流程、版本选择三条规则
  https://developer.hashicorp.com/terraform/plugin/how-terraform-works
- HashiCorp《Provider Requirements》—— `>=` / `~>` 语义、lock file、可复用模块的版本写法建议
  https://developer.hashicorp.com/terraform/language/providers/requirements
- hashicorp/go-plugin README（v1.6.0）—— 握手、协议版本、子进程隔离、仅本地网络等限制
  https://cdn.jsdelivr.net/gh/hashicorp/go-plugin@v1.6.0/README.md
