# GitOps 调和循环与漂移检测

## 简介

GitOps 不是"用 Git 存 YAML"，而是**四个可以被验收的原则**（CNCF OpenGitOps v1.0.0）。本 demo 实现这套原则里最容易被说糊的那部分：**一个持续跑的调和（reconciliation）循环**——它从 Git 拉期望态、和集群里的真实态做 diff、决定要不要动手、以及"什么时候**不该**动手"。

Argo CD 的几条硬规则是这里的重点：自动同步**每对 (commit SHA, 参数) 只做一次**；失败过的组合**不再重试**；**默认不剪枝**；**集群里手动改的东西默认不管**（要开 `selfHeal` 才回滚）。这些"不做"，恰恰是 GitOps 能在生产里跑得住的原因。

关键概念：

| 概念 | 一句话解释 |
| --- | --- |
| 期望态 / 实际态 | Git 里的声明 vs 集群里的真实对象 |
| 漂移（drift） | 两者不一致；来源既可能是 Git 变了，也可能是有人手动改了集群 |
| 调和（reconcile） | 一个循环：观察 → diff → 行动 → 记录，然后等下一个周期 |
| Pull 模型 | Agent 在集群内主动拉取，**集群凭证不出边界**（对比 push 的 CI/CD） |
| pruning | 删掉"Git 里已经没有了"的资源；**默认关闭** |
| `selfHeal` | 允许"集群被手动改动"触发一次纠正同步 |
| SSA / `managedFields` | 服务端记录**哪个字段归哪个管理者**，以此检测冲突 |

## 原理详解

### 1. OpenGitOps v1.0.0 的四条原则（官方原文）

| # | 原则 | 官方表述 |
| --- | --- | --- |
| 1 | Declarative | A system managed by GitOps must have its desired state expressed declaratively |
| 2 | Versioned and Immutable | Desired state is stored in a way that enforces immutability, versioning and retains a complete version history |
| 3 | Pulled Automatically | Software agents automatically pull the desired state declarations from the source |
| 4 | Continuously Reconciled | Software agents continuously observe actual system state and attempt to apply the desired state |

第 3 条特别值得停一下：社区文档明确指出 **"pull" 是刻意用来对比传统 CI/CD 的 "push"**——传统流水线要拿到集群凭证才能 `kubectl apply`，而 GitOps 的 agent 住在集群里往外拉，因此**凭证不需要离开集群边界**。第 4 条的措辞也很讲究：agent 要**持续**观察（不能只靠触发器），漏掉一次手动改动就等于放弃了这条原则。

### 2. 调和循环的形状

```text
   ┌────────────────────────── 每 120s + 0..60s jitter ──────────────────────────┐
   │                                                                            │
   ▼                                                                            │
 读取 Git HEAD ──▶ 读出期望态 ──▶ 与集群实际态 diff ──▶ 门控（该不该动手？）──▶ 动手/不动手
                                                                │
                          ┌─────────────────────────────────────┴──────────────┐
                          │ 门控规则（任一不满足就不动手）                      │
                          │  · automated 关了就只报告                          │
                          │  · 同一 (SHA, params) 已成功同步过 → 跳过           │
                          │  · 同一 (SHA, params) 失败过 → 不重试               │
                          │  · 只有"手动漂移"且 selfHeal 关 → 不动              │
                          │  · 只剩孤立对象且 prune 关 → 报告但删不掉            │
                          └────────────────────────────────────────────────────┘
```

周期来自 `argocd-cm` 的 `timeout.reconciliation`（默认 **120 s**），官方另加"**最多 60 s 抖动**"，所以最大周期是 **3 分钟**——抖动是为了避免成百上千个 Application 在同一秒齐刷刷去压 API Server。

### 3. 四种"该不动手"的情形（本 demo 的可执行断言）

| 场景 | 官方依据 | 本 demo 的观察结果 |
| --- | --- | --- |
| 同一 (SHA, params) 已成功同步 | "a second sync will not be attempted, unless selfHeal flag is set to true" | 第二次循环只打印 `already synced`，集群无写入 |
| 手动漂移 + `selfHeal` 关 | "changes that are made to the live cluster will not trigger automated sync" | `spec.replicas` 保持被手改的 **9**，状态停在 OutOfSync |
| 同一 (SHA, params) 失败过 | "Automatic sync will not reattempt a sync if the previous sync attempt ... had failed" | 该组合被记入 `failed_pairs`，后续循环直接跳过 |
| 只剩孤立对象 + `prune` 关 | "automated sync will not delete resources when Argo CD detects the resource is no longer defined in Git" | 孤儿对象留在集群里，应用**一直是 OutOfSync** |

最后一条特别容易踩：`prune=false` 时应用**永远不会变成 Synced**，因为 Argo CD 明确说"期望剪枝但被跳过"就是这个状态。这不是 bug，是设计。

### 4. 两种漂移的来源要分开

- **Git 变了**（新 commit）：这是正常发布，`automated` 开启就会同步，与 `selfHeal` 无关。
- **集群被手动改了**：这才是"漂移"意义上的 drift，默认**不管**。

区分方式很简单：**当前 SHA 是否等于上一次成功同步的 SHA**。如果相等却仍有差异，那差异只可能来自集群侧。本 demo 的 `live_drift = bool(stale) and not git_moved` 就是这个判据。

### 5. 字段所有权：SSA 让"谁改的"变成可查事实

Kubernetes 的 Server-Side Apply 把**字段所有权**记在对象的 `metadata.managedFields` 里：

| 情形 | 结果 |
| --- | --- |
| 两个管理者把同一字段设成**相同值** | 共享所有权 |
| 想设成**不同值**，但字段归别人 | **冲突**（apply 失败，对象完全不变） |
| 带 `force` 再试 | 成功，并从**其他所有管理者**的记录里移除该字段 |
| 从自己的清单里**删掉**某字段 | 释放该字段；没有任何人拥有时它会从对象上消失 |

这就是"声明式但不互相踩脚"的机制。字段级冲突只会让 **apply** 失败——文档明确说 `PUT`/update 遇到被托管的字段**不会**因此报错，所以控制器应当坚持用 SSA，并在管理自己的对象时 `force`。

### 6. 顺带一个易忽略的选项：`Replace=true`

默认 Argo CD 用 `kubectl apply` 同步，而 `apply` 会把整份清单塞进 `kubectl.kubernetes.io/last-applied-configuration` 注解——**清单太大就塞不进去**。这时才需要 `Replace=true` 改用 `replace/create`。官方对该选项加了警告："has the potential to be destructive and might lead to resources having to be recreated, which could cause an outage"。

## 环境准备

- 操作系统：Linux / macOS / Windows（只用标准库）
- Python：3.10+（本 demo 用 `3.13`）——拆为 `model.py`（数据模型）+ `gitops_reconcile.py`（控制器与演示）
- Go：1.21+——拆为 `model.go` + `gitops_reconcile.go`

## 运行方式

### Python

```bash
cd python
python3 model.py             # 只做 import 自检（无 main）
python3 gitops_reconcile.py  # 9 个场景的调和循环演示
```

### Go

```bash
cd go && go run model.go gitops_reconcile.go
```

## 关键代码片段

```python
def reconcile(self, params: str = "") -> tuple[str, int]:
    pair = (self.repo.sha, params)
    stale, extra = self.drift()                       # 期望态 vs 实际态
    git_moved = self.repo.sha != self.synced_revision  # Git 变了？
    live_drift = bool(stale) and not git_moved         # 否则就是集群被手动改了

    if not pending:                                   ...
    elif not self.policy.automated:                    ...   # 只报告
    elif live_drift and not self.policy.self_heal:      ...   # 默认不管手动改动
    elif pair == (self.synced_revision, params) and not self.policy.self_heal:
        ...                                           # 每对 (SHA, params) 只同步一次
    elif pair in self.failed_pairs:                    ...   # 失败过的不重试
    else:
        self.sync()                                   # 真正动手
    interval = self.policy.reconciliation + random.randint(0, self.policy.jitter)
    return self.status(), interval
```

## 性能与边界

- 一个调和周期是 O(对象数 × 字段数) 的 diff；真正的成本在**每次都要读 Git 与查询集群**，所以默认 120 s（不是 1 s）是有意的取舍。
- `ApplyOutOfSyncOnly` 同步选项可以把"每次都 apply 全部对象"改成"只 apply 不同步的"——官方说明这在对象上千时能显著减轻 API Server 压力，并避免 `status.operationState.syncResult.resources` 撑爆 etcd。
- 边界：本 demo 的 diff 是字段级浅比较，不还原 Kubernetes 的默认值填充、序列化差异等真实噪声——生产里这些噪声是"假漂移"的主要来源。

## 注意事项与常见坑

1. **`prune=false` 会让应用永远 OutOfSync**。这不是故障；但如果你的告警规则是"OutOfSync 就报警"，就会一直响。要么开 prune，要么在告警里排除"待剪枝"这一项。
2. **手动改动默认不会被回滚**。这正是 `selfHeal` 存在的意义。开着它意味着**任何人都别指望手动改动能留住**——紧急热修时先关 `selfHeal`。
3. **同 SHA 只同步一次**。如果你在同步后再改一次 Git 但**没产生新 commit**（比如改了 Application 的参数而 SHA 不变），别指望它会自动再跑一遍。
4. **失败组合不重试，但别以为没有重试**。Argo CD 有独立的 `retry` 策略（`limit` / `backoff.duration` / `factor` / `maxDuration`），按**指数退避**重试——"不重试"指的是"同一 SHA+参数不重复尝试"。
5. **`allowEmpty` 是保命开关**。目标路径解析为空时会拒绝同步；关掉它意味着"Git 里空了"就等于"把集群清空"。这与 `prune=true` 组合起来是最高危的配置。
6. **`managedFields` 不要手改**。官方明确警告该字段由 API Server 管理；用 `kubectl get --show-managed-fields` 只读查看即可。
7. **`Replace=true` 是破坏性的**。它绕过 apply 的注解机制，可能导致资源被重建与中断；只在清单过大等确实需要时使用。
8. **抖动不是可选项**。去掉 jitter 后大量 Application 会在同一时刻集中调和，API Server 上的尖峰往往就是"GitOps 拖慢集群"的真因。

## 参考资料（实际阅读过的权威来源）

- [OpenGitOps — GitOps Principles v1.0.0](https://opengitops.dev/) — 四条原则的官方原文（Declarative / Versioned and Immutable / Pulled Automatically / Continuously Reconciled）（全文阅读）
- [OpenGitOps 1.0 is finally here and why you should care](https://opengitops.dev/blog/1.0-announcement/) — "pull" 与 "push" 的刻意对比、"触发器不能替代持续观察"、"latest tag 破坏 versioned 原则"等措辞解释（全文阅读）
- [Automated Sync Policy — Argo CD Documentation](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/) — 自动同步只在 OutOfSync 时发生、每对 (commit SHA, 参数) 只同步一次、失败不重试、`selfHeal` 与 5 s self-heal timeout、`prune` 默认关闭与 `allowEmpty`、`retry` 指数退避、`timeout.reconciliation` 默认 120 s 加 60 s 抖动（全文阅读）
- [Sync Options — Argo CD Documentation](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/) — `Prune=false` 与 `Prune=confirm`、`ApplyOutOfSyncOnly`、`PrunePropagationPolicy`、`PruneLast`、`Replace=true` 的破坏性警告与"清单过大放不进 last-applied-configuration 注解"的成因、`argocd.argoproj.io/tracking-id` 跟踪注解、`managedNamespaceMetadata`（全文阅读）
- [Server-Side Apply — Kubernetes Documentation](https://kubernetes.io/docs/reference/using-api/server-side-apply/) — `metadata.managedFields` 托管字段、field manager 概念、相同值共享所有权、冲突三种解法（force 覆盖 / 放弃该字段 / 共享）、`force` 会从其他管理者记录中移除字段、删除清单字段导致字段被移除或重置为默认值、`listType`/`mapType` 合并策略标记、在控制器中使用 SSA 的建议（全文阅读）
