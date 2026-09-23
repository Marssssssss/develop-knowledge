# 629 · Crossplane：XRD / Composition / Claim 与组合选择

> 归属：`06-DevOps/08-平台工程`。「平台即产品」的落地方式是**给应用团队一个更小的 API**：
> 他们申请 `PostgreSQLInstance`，平台在背后组合出 RDS 实例、安全组、子网、Secret。
> Crossplane 用三个对象表达这件事：XRD 定义 API、Composition 定义实现、Claim 让应用团队消费。
> 本 demo 全部按官方源码实读转写，重点在**校验规则**与**选择顺序**这两处最容易写错的地方。

## 一、事实来源（本轮实读，全部是官方源码）

| 文件 | 关键事实 |
| --- | --- |
| `crossplane/crossplane` `apis/apiextensions/v1/xrd_types.go` | scope 三值与默认值、两条 CEL 规则、`group` 与 XRD 名字的绑定、plural/singular 小写约束、两个策略的默认值、`GetCompositeGroupVersionKind` 的**循环无 break**、`OffersClaim` / `GetClaimGroupVersionKind` 的空值语义 |
| `crossplane/crossplane` `apis/apiextensions/v1/composition_types.go` | `mode` 枚举只有 `Pipeline` 且默认 `Pipeline`；`pipeline` 的 `MinItems=1` / `MaxItems=99` / `listMapKey=step`；`compositeTypeRef` 不可变 |
| `crossplane/crossplane` `apis/apiextensions/v1/composition_revision_types.go`、`conditions.go` | 组合版本与就绪条件的存在（本 demo 不展开） |

## 二、核心机制

### 1. XRD：给应用团队的 API 契约

```text
apiVersion: apiextensions.crossplane.io/v1
kind: CompositeResourceDefinition
metadata: { name: xpostgresqlinstances.database.example.org }   ← 必须 = <plural>.<group>
spec:
  group: database.example.org
  names: { kind: XPostgreSQLInstance, plural: xpostgresqlinstances }
  scope: LegacyCluster          # Namespaced | Cluster | LegacyCluster（默认）
  claimNames: { kind: PostgreSQLInstance, plural: postgresqlinstances }
  connectionSecretKeys: [endpoint, username]
  defaultCompositionRef: postgres-aws
```

三条最容易踩的校验：

1. **名字即契约**：`metadata.name` 必须等于 `<names.plural>.<group>`，且 `group` 不可变。
2. **scope 决定能不能开 claim**。`LegacyCluster`（默认）是「集群级但可被 claim」；
   改成 `Namespaced` 或 `Cluster` 之后，`claimNames` 与 `connectionSecretKeys` 都变成非法：
   官方 CEL 规则是 `self.scope == 'LegacyCluster' || !has(self.claimNames)`。
   **注意是「不能提供 claim」，不是「claim 会被忽略」**——配置会被拒绝。
3. **GVK 版本取最后一个 referenceable**。官方实现是个不带 `break` 的循环，`v` 会被后面的
   referenceable 版本覆盖，所以即使 `v1alpha1` 在列表里排前面且 referenceable，最终 GVK
   仍是最后一个 referenceable 版本。这是「读代码才知道」的细节，凭直觉容易写成「取第一个」。

### 2. Composition：把 API 落到真实资源

`mode` 目前只有 `Pipeline` 一种取值，`pipeline` 长度必须在 **[1, 99]** 之间，且 step 名唯一
（`listMapKey=step`）。`compositeTypeRef` 带 `XValidation: self == oldSelf`，即**不可变**——
想换目标类型只能新建 Composition。

### 3. 组合选择：四种来源，优先级固定

```text
enforcedCompositionRef（XRD 上，压过一切）
  > Composite 的 compositionSelector（按 label 匹配，必须唯一命中）
    > Composite 的 compositionRef（显式指定）
      > XRD 的 defaultCompositionRef
        > 都为空 → 报错
```

选择器的**唯一性**很关键：命中 0 个要报错，命中多个也**要报错**。「静默取第一个」在真实平台
里意味着应用团队以为走了 A 实现，实际跑在 B 上。

### 4. 连接密钥是白名单，策略有默认值

- `connectionSecretKeys` 为空 → 全部发布；非空 → **过滤掉**名单外的键（如 `password` 不出网）。
- `defaultCompositeDeletePolicy` 默认 `Background`；`defaultCompositionUpdatePolicy` 默认
  `Automatic`。实例上写了就覆盖，没写才回落——这决定了删除 Claim 时底层云资源是级联删还是
  保留。

## 三、运行

```bash
cd 06-DevOps/08-平台工程/04-组合式平台资源Crossplane
python python/selfcheck_crossplane.py   # 43 条断言
python python/main.py                   # XRD → 选择 → 连接密钥 → 策略回落
cd go && go run xrd.go main.go
```

## 四、断言设计

- **CEL 规则双向**：LegacyCluster + claim 合法；Namespaced + claim 与 Cluster + claim 都断言报错
  且报错文案与官方 CEL message 一致。
- **循环无 break 的实证**：不只断言「取最后一个 referenceable」，还构造
  `[referenceable, referenceable]` 与 `[referenceable(先), referenceable(后)]` 两种排列，
  确认结果随**列表顺序**而变化——单条断言无法区分「取最后一个」与「取第一个」的实现差异。
- **选择器唯一性**：命中 0 个、命中 2 个分别断言抛出，而不是只测成功路径。
- **白名单三态**：`None`、`[]`、`["endpoint"]` 三种输入分别断言，覆盖「空即全发」这一特殊语义。

## 五、注意事项与口径

- 本 demo 不实现真实 reconciliation：没有 managed resource、没有 function 的 CUE/Go 求值，
  `Pipeline` 只作为名字序列参与校验。
- `compositionRevisionSelector` 字段在源码中存在（默认版本选择器），本 demo 记录但未实现其
  选择算法（官方在 revision 控制器里处理）。
- 官方 `composition_types.go` 里还留有 `resources` 模式的注释（"One of resources and pipeline
  must be specified"），但 `Mode` 枚举当前只允许 `Pipeline`，本 demo 以**枚举**为准。

## 六、参考资料（实际读过）

- <https://github.com/crossplane/crossplane/blob/main/apis/apiextensions/v1/xrd_types.go>
- <https://github.com/crossplane/crossplane/blob/main/apis/apiextensions/v1/composition_types.go>
- <https://github.com/crossplane/crossplane/blob/main/apis/apiextensions/v1/composition_revision_types.go>
- <https://github.com/crossplane/crossplane/blob/main/apis/apiextensions/v1/conditions.go>
