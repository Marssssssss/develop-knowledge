# Argo CD 自动同步与漂移检测（automated 三态 + 唯一尝试键 + prune 安全阀 + diff 去噪）

## 简介

Argo CD 是声明式 GitOps 控制器：它以 Git 里的 manifest 为"期望状态"，周期性对比集群
live 对象，算出 `OutOfSync` 后按 `syncPolicy` 决定是否自动修正。看似只是一句
`syncPolicy.automated: {}`，实际语义密度很高——**开关是三态的、尝试有唯一键、prune 默认
关着、diff 又默认忽略 status**，任何一条理解错了，行为都和预期差一个数量级。

本 demo 把这些语义落成**可执行模型**（Python + Go），用断言固定住每一个官方明确写出的
数字与分支，方便在排障时对照："为什么它没有自动同步？""为什么删掉的资源还在？"

覆盖点：

| 主题 | 官方要点 |
| --- | --- |
| `automated.enabled` | 显式 `false` 才关；缺失/null 等同开启 |
| 同步触发 | **只有 OutOfSync 才尝试自动同步** |
| 尝试唯一键 | `(commit SHA1, 应用参数)` 组合成功过就不再试；失败过也不再试 |
| `selfHeal` | 打开后 live 漂移可自愈，自愈前等 5 秒（默认） |
| rollback | 开了自动同步的应用**不能** rollback |
| 调和周期 | 默认 120s + 60s 抖动，**上限 3 分钟** |
| `prune` | **默认关闭**；`allowEmpty` 再叠一层保护 |
| diff 去噪 | `ignoreDifferences`（group/kind/name/namespace + jsonPointers/jq/managedFields） |
| status | `ignoreResourceStatusField` 默认 `all`，可设 `crd`/`none` |

## 原理详解

### 1. `syncPolicy.automated.enabled` 的三态

```yaml
syncPolicy:
  automated:            # ① 块缺失：不开自动同步
    enabled: null       # ② 块在、enabled 写 null：等同开启
    prune: true
    selfHeal: true
```

只有 `enabled: false` 才是显式关闭。于是"块缺失"与"块在但 enabled 为空"**不是**一回事：
前者整块策略不生效（`prune`/`selfHeal` 写了也白写），后者策略生效。本 demo 用
`dict | None`（Python）/ `*Automated` 指针（Go）来区分这两态。

### 2. 三条短路：什么时候**不**同步

按官方 auto_sync 页，自动同步要同时满足"在 OutOfSync"且"这个 `(commit, 参数)` 组合没试过"：

1. 应用状态是 `Synced` 或 `Unknown` → 不动（只有 `OutOfSync` 才尝试）。
2. 最近一次**成功**同步的 revision 与参数和当前完全相同 → 不再重复应用，
   **除非** `selfHeal: true`（此时允许再跑一次把 live 拉回来）。
3. 最近一次对同一组合**失败**过 → 不再重试；要有新 commit 或应用参数变化才会再试。

第 2 条最容易被误解成"设置完就不管了"，它其实是**幂等保护**：只要没有新提交，
`synced` 一次之后就安静下来，live 被外部改动（例如 HPA 调副本数）也不会被拉回——
这正是 `selfHeal` 存在的理由。

### 3. selfHeal 与调和周期

`selfHeal: true` 时，控制器在检测到漂移后会等待 **self-heal timeout（默认 5 秒）** 再尝试
同步，该值由 `argocd-application-controller` 的 `--self-heal-timeout-seconds` 控制。
调和周期由 `argocd-cm` 的 `timeout.reconciliation` 决定：**默认 120s，外加最多 60s 抖动，
合计上限 3 分钟**。所以"git push 完 5 秒内集群就变了"是不成立的，正常窗口是分钟级。

### 4. prune 与 allowEmpty：两个默认安全阀

Git 里删掉一个资源，live 里它并不会自动消失——`prune` **默认关闭**，控制器只把它标成
`OutOfSync`。即便 `prune: true`，当 Git 侧渲染出的资源集**为空**时（典型事故：路径写错、
chart 渲染失败），`allowEmpty` 未开则拒绝清空整个应用。两道闸门叠加，避免"一次错误提交
删光集群"。

### 5. diff 定制：让"假漂移"闭嘴

`ignoreDifferences` 的匹配四元组 `group / kind / name / namespace` 是**与**关系，且：

- **core 组的 `group` 是空串 `""`，不是 `v1`**（`apiVersion: v1` 里没有 `/`）。写成 `v1`
  规则会静默失效——本 demo 有专门断言钉住这一点。
- `jsonPointers` 走 RFC6902，转义符 `~1`→`/`、`~0`→`~`。
- `jqPathExpressions` 是真 jq（gojq）；**数组必须显式写 `[]` 才展开**，
  `.webhooks.clientConfig.x` 与 `.webhooks[].clientConfig.x` 语义完全不同。
- `managedFieldsManagers` 按字段所有权忽略（HPA 拥有 `/spec/replicas`），用来消掉
  "双方都在写同一字段"造成的噪声。
- `knownTypeFields` + `core/Quantity`：`100m` 与 `0.1` 是同一个量，不规整就是假漂移。

## 对比 / 选型

| 策略 | 触发条件 | 适用 | 风险 |
| --- | --- | --- | --- |
| 纯手动 sync | 人点按钮 | 生产首批、变更窗口严格 | 忘记点，长期漂移 |
| `automated: {}` | OutOfSync + 新组合 | 常规环境 | 提交即生效（分钟级） |
| `+ selfHeal` | 上述 + live 漂移 | 防止人为热修漂移 | 会覆盖应急手改 |
| `+ prune` | 上述 + Git 删了资源 | 资源生命周期完全由 Git 管 | 误删；务必配合 `allowEmpty` |

## 环境准备

- Python 3.11+（本 demo 用 `3.13`），**零第三方依赖**，仅标准库。
- Go 1.18+（仅用于阅读/编译 Go 对照实现，非必需）。
- 本机**没有 Go 工具链**：Go 版走人工审查 + 结构自检（括号配平、未用 import、CRLF）。

## 运行方式

```bash
cd python && python argocd_check.py     # Python：77 条断言全绿
# cd go && go run .                      # 需本机有 Go 工具链（本机未装，见下）
```

文件分工（每个源文件都 ≤ 300 行）：

```
python/argocd_sync.py        同步决策 / prune / 调和周期 / 退避
python/argocd_diff.py        ignoreDifferences / JSON Pointer / jq 子集 / managedFields / 规整
python/argocd_harness.py     极简断言 harness（零依赖，两个断言模块共用）
python/argocd_check.py       断言 1-4 组 + 入口
python/argocd_check_diff.py  断言第 5 组（diff 去噪）
go/argocd_sync.go            Go 对照：同步 / prune / 时序
go/argocd_diff.go            Go 对照：diff 匹配与去噪
go/argocd_canon.go           Go 对照：Quantity 规整与内容摘要
go/argocd_check_diff.go      Go 对照：diff 侧断言
go/main.go                   Go 入口 + 断言 1-4 组
```

## 关键代码片段（Python）

```python
def automated_enabled(sync_policy: dict) -> bool:
    """``syncPolicy.automated.enabled`` 的三态语义: 缺失或 null 都视为开启。"""
    auto = sync_policy.get("automated")
    if auto is None:
        return False                      # 整个 automated 块缺失 = 未开自动同步
    flag = auto.get("enabled", None)
    return flag is None or flag is True   # null 等同开启, 只有显式 false 才关


def sync_decision(state: dict) -> tuple:
    """返回 (是否发起同步, 原因)。三条短路全部来自官方 auto_sync 原文。"""
    if state["sync_status"] in (STATUS_SYNCED, STATUS_UNKNOWN):
        return False, "只有 OutOfSync 才尝试自动同步"
    key = (state["revision"], state["param_hash"])
    if state.get("last_failed") == key:
        return False, "上一轮对同一 (commit, 参数) 已失败 -> 不再重试"
    if state.get("last_success") == key and not self_heal:
        return False, "同一 (commit, 参数) 已成功同步过, 且未开 selfHeal"
```

## 性能与边界

- 模型是**语义模型**，不含真实控制器循环：不做集群访问、不做 manifest 渲染、不做 health 判定。
- 未覆盖：ApplicationSet、多源应用、sync windows、Server-Side Apply、orphaned resources 监控、
  health 的 Lua 脚本、SSO/RBAC、集群注册。
- `jqPathExpressions` 只实现了可解释子集（多级路径、`[]`/`[]?`、尾部 `select(.k == "v")`），
  不是完整 jq；真实 Argo CD 用 gojq。
- Quantity 规整只处理十进制写法（`100m`/`0.1`），`1Gi`、`1e3` 这类后缀原样返回。
- Go 版的 JSON Pointer 删除只支持对象路径（数组场景统一走 jq 的 `[]`），Python 版支持
  数组下标/按 name 删除，这是**有意的口径差异**。

## 注意事项与常见坑

- **别用 `enabled: null` 表达"关闭"**：它等同开启。要关就写 `false`。
- **`prune` 关了不等于安全**：Git 里删了资源，live 里留着，`OutOfSync` 会一直亮；要么开
  `prune` 并接受风险，要么走手动 sync。
- **开了 `selfHeal` 就等于允许控制器覆盖应急手改**：抢修时先临时关掉。
- **同一 commit 的反复漂移不会触发同步**（除非 selfHeal），这不是 bug 而是幂等保护。
- **core 资源忽略规则 group 写 `v1` = 静默失效**；写 `""` 或 `*`。
- **`jq` 路径忘了 `[]` 会静默落空**，看起来"规则没生效"，其实是路径根本没匹配。
- **失败重试有上限**：`retry.limit` 限制次数，`backoff.maxDuration` 封顶单次等待。
- 本 demo 的 Go 版**未经编译器验证**（本机无 Go 工具链），已人工复核签名与实参个数，
  并用脚本统计 71 个 `check()` 调用的实参个数；Python 版 77 条断言全部通过。

## 参考资料（实际阅读过的权威来源）

- Argo CD — Automated Sync Policy：<https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/>
  （automated 三态与 `enabled`、只在 OutOfSync 时同步、`(revision, 参数)` 唯一尝试键、
  `prune`/`allowEmpty` 的默认值、self-heal timeout 5 秒、最大调和周期 3 分钟、
  开启自动同步后不可 rollback）
- Argo CD — Diff Customization：<https://argo-cd.readthedocs.io/en/stable/user-guide/diffing/>
  （`ignoreDifferences` 四元组匹配、`jsonPointers`、`jqPathExpressions`、
  `managedFieldsManagers`、`ignoreResourceStatusField` 的 `crd`/`all`/`none`、
  `knownTypeFields` 与 `core/Quantity` 规整）
