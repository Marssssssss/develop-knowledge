# 630 · KubeVela OAM：应用模型、能力定义与定义修订

> 归属：`06-DevOps/08-平台工程`。Crossplane 解决「基础设施 API」，KubeVela 解决
> 「应用交付 API」：平台团队用 CUE 把 K8s 资源封装成 `webservice` / `scaler` / `gateway`
> 这类能力（Definition），应用团队只写 Application。本 demo 按官方源码实读，把 Application
> 的约束、DefinitionRevision 的修订语义、以及几处「字段长得很像但语义完全不同」的坑落成代码。

## 一、事实来源（本轮实读，全部是官方源码）

| 文件 | 关键事实 |
| --- | --- |
| `kubevela/kubevela` `apis/core.oam.dev/v1beta1/application_types.go` | `spec.components` 必填；**「If workflow is specified, Vela won't apply any resource, but provide rendered output in AppRevision」**；`AppPolicy{Name?, Type, Properties?}`；`ApplicationSource.AutoUpdate` 与 `publishVersion` pin 的双向压制；`ApplicationSourceStatusPolicy.ExposeConsumedValues` 注释「Unset means expose」 |
| `kubevela/kubevela` `apis/core.oam.dev/common/types.go` | `Schematic.CUE.Template` **必填**；`Terraform.Type` 枚举 `hcl;json;remote` 默认 `hcl`；`DefinitionReference.Version` 注释「by default it will use the first one if not specified」；`ApplicationComponent.Traits` 是**数组**（注释：type must be array to keep the order）；`ReplicaKey` 的 json 标签是 `"-"`；`Revision{Name, Revision int64, RevisionHash?}`；`DefinitionType` 五值；`ParameterValueType` 三值 |
| `kubevela/kubevela` `apis/core.oam.dev/v1beta1/definitionrevision_types.go` | `DefinitionRevisionSpec` 有 5 个快照字段，实际只应填与 `definitionType` 对应的那一个 |
| `kubevela/kubevela` `apis/core.oam.dev/v1beta1/componentdefinition_types.go` | `ComponentDefinitionSpec.Workload` 必填；`Restrictions` 非空时覆盖 `definition.oam.dev/restrict-namespaces` 注解 |

## 二、核心机制

### 1. 有 workflow 就不下发——渲染与应用是两个通道

`ApplicationSpec.Workflow` 的注释是硬约束：一旦指定 workflow，Vela **不会**把渲染出的资源直接
apply 到集群，而是把渲染结果放进 `AppRevision`，由 workflow 的步骤决定何时、以什么方式下发
（比如先灰度、审批后再全量）。这跟「workflow 只是额外的后置步骤」的直觉相反。

本 demo 用 `render()` 同时返回 `(applied, revision)` 两个值：有 workflow 时 `applied` 恒为空、
`revision["workflow"]` 有值；无 workflow 时反过来——两条路径都断言，避免只测一边。

### 2. Traits 必须是数组，ReplicaKey 根本不进 YAML

- `Traits []ApplicationTrait` 的源码注释写明「the type must be array to keep the order」：
  运维特征之间有顺序（先注入 sidecar 再扩副本，与反过来结果不同），用 map 会丢顺序。
- `ReplicaKey` 的 json 标签是 `"-"`，即使代码里赋了值也不会出现在清单里——它是复制策略
  （replication policy）内部填充的字段，应用方**无法**直接指定。

### 3. DefinitionRevision：一次修订 = 一个不可变快照

`DefinitionRevisionSpec` 里 `revision`（int64，从 1 起）、`revisionHash`、`definitionType`
（枚举 5 值）+ 5 个快照字段（`componentDefinition` / `traitDefinition` / `policyDefinition` /
`workflowStepDefinition` / `sourceDefinition`）。语义上**只有与 type 对应的那一个应被填充**：
Component 修订填 `componentDefinition`，Trait 修订填 `traitDefinition`，填错就是配置错误。

「spec 变了才递增」是平台治理的关键：应用可以靠 `externalRevision` 钉在某个修订上，避免
平台改一次模板就把所有应用一起改了。

### 4. 三处「默认值」来自注释而不是文档

- `DefinitionReference.Version` 未指定 → 用**第一个**可用版本（不是最新）。
- `Schematic.Terraform.Type` 未指定 → `hcl`。
- `ExposeConsumedValues` 未设置 → **暴露**（隐藏必须显式声明，且 `maskPaths` 可再按点号路径
  打码，两条机制互相独立）。

## 三、运行

```bash
cd 06-DevOps/08-平台工程/05-OAM应用交付抽象KubeVela
python python/selfcheck_oam.py   # 52 条断言
python python/main.py
cd go && go run oam.go main.go
```

## 四、断言设计

- **workflow 双通道**：有/无 workflow 分别断言 `applied` 与 `revision`，而不是只查其一。
- **顺序断言用列表而非集合**：`[t["type"] for t in traits]` 必须等于声明顺序。
- **ReplicaKey 负控**：赋值后仍断言清单里不含该键（验证「不序列化」而不是「默认空」）。
- **修订递增成对**：spec 未变 → revision 不变；spec 变了 → +1。只测「变了会加一」无法发现
  「每次都加一」的实现错误。
- **快照字段与类型一致性**：Component/Trait 各一个正例，外加一个「填错字段」的报错断言。
- **可见性三态**：`None` / `{}` / `{"exposeConsumedValues": False}` 三种策略分别断言，
  覆盖「未设置即暴露」这一反直觉默认。

## 五、注意事项与口径（官方未给定量处，已标注）

1. **`revisionHash` 的官方算法本轮未读**：Python 侧用「规范化 JSON 的 sha256 前 16 位」、
   Go 侧用「排序键值拼接的 sha256 前 16 位」，两者都只用于「spec 变了 hash 就变」这类
   **判等**断言，不与线上真实值比对，也不跨实现比较。
2. 本 demo 不实现 CUE 求值：`Schematic.CUE.Template` 只做「非空」校验，模板渲染属于
   `vela` 的 CUE 引擎职责。
3. `Policies` 在真实 Application 上会在**组件渲染之后、workflow 步骤之前**生效；本 demo 只
   记录顺序语义，不做策略执行。

## 六、参考资料（实际读过）

- <https://github.com/kubevela/kubevela/blob/master/apis/core.oam.dev/v1beta1/application_types.go>
- <https://github.com/kubevela/kubevela/blob/master/apis/core.oam.dev/common/types.go>
- <https://github.com/kubevela/kubevela/blob/master/apis/core.oam.dev/v1beta1/definitionrevision_types.go>
- <https://github.com/kubevela/kubevela/blob/master/apis/core.oam.dev/v1beta1/componentdefinition_types.go>
- <https://kubevela.io/docs/platform-engineers/workflow/workflow>
