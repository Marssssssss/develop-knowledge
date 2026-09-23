# 08-平台工程（Platform Engineering / IDP）

> 平台工程 = 把「怎么正确地交付软件」变成**产品**：平台团队构建内部开发者平台（IDP），
> 通过软件目录、黄金路径模板、自助服务 API 与统一授权，把认知负荷从应用团队身上收走。
> 本子类目的 demo 全部按**官方源码 / 官方文档实读**后转写，重点在那些「读文档看不出来、
> 只有读源码才知道」的语义细节。

## 一、核心研究主题

| 主题 | 关键问题 | 状态 |
| --- | --- | --- |
| 软件目录（Backstage Catalog） | 实体信封与元数据的字符集限制、实体引用解析、关系由谁推导 | ✅ demo 626 |
| 权限与授权（Permission Framework） | 决策与执行如何分离、条件树怎么求值、空 claims 为什么不能放行 | ✅ demo 627 |
| 黄金路径模板（Scaffolder） | `${{ }}` 与 `{{ }}` 的差别、步骤输出如何被消费、dry-run 的假输出 | ✅ demo 628 |
| 组合式平台资源（Crossplane） | XRD/Composition/Claim 的契约、组合选择优先级、连接密钥白名单 | ✅ demo 629 |
| 应用交付抽象（KubeVela OAM） | 能力定义与修订、traits 为什么是数组、有 workflow 就不下发 | ✅ demo 630 |

## 二、已完成 demo

| ID | 目录 | 一句话 |
| --- | --- | --- |
| 626 | [01-软件目录与实体模型/](./01-软件目录与实体模型/) | name/namespace/tag 三套字符集各不相同；关系由处理循环推导且受 kind 约束，正向与反向成对 |
| 627 | [02-权限框架与条件策略/](./02-权限框架与条件策略/) | ALLOW/DENY/CONDITIONAL 三种决策字段互斥；条件树 allOf/anyOf/not 必须非空；空 claims 不是 owner |
| 628 | [03-软件模板与黄金路径/](./03-软件模板与黄金路径/) | `${{ }}` 保留类型而 `{{ }}` 只做字符串插值；`each` 先于 input 渲染；dry-run 按 schema 伪造输出 |
| 629 | [04-组合式平台资源Crossplane/](./04-组合式平台资源Crossplane/) | 只有 LegacyCluster 能开 claim；GVK 取**最后一个** referenceable；组合选择 enforced > selector > ref > default |
| 630 | [05-OAM应用交付抽象KubeVela/](./05-OAM应用交付抽象KubeVela/) | 有 workflow 时只渲染进 AppRevision；traits 是数组保序；ReplicaKey 的 json 标签是 `-` |

## 三、待研究

- [x] 软件目录实体模型与关系推导 → demo 626
- [x] 权限框架与条件策略 → demo 627
- [x] 软件模板（Scaffolder）与黄金路径 → demo 628
- [x] Crossplane XRD / Composition / Claim → demo 629
- [x] KubeVela OAM 应用模型与定义修订 → demo 630
- [ ] Backstage TechDocs 与 docs-like-code
- [ ] Backstage 插件后端与模块发现（backend system wiring）
- [ ] 平台工程度量：DORA 四指标 / SPACE / DevEx 三维度
- [ ] Team Topologies 的四种团队类型与认知负荷分配
- [ ] CNCF Platforms White Paper 的能力模型与采用阶段
- [ ] Crossplane composition functions（gRPC 协议与 function 链）
- [ ] KubeVela CUE 模板求值与 patch 策略
- [ ] 门户与自助服务 API 的版本演进（Backstage API 兼容策略）

## 四、本批参考资料（本轮实际读过）

- Backstage 官方文档：`descriptor-format` / `system-model` / `life-of-an-entity` /
  `writing-templates` / `builtin-actions` / `permissions/overview` / `permissions/concepts` /
  `permissions/writing-a-policy`
- Backstage 源码：`packages/catalog-model/src/kinds/relations.ts`、
  `packages/catalog-model/src/schema/EntityMeta.schema.json`、
  `plugins/scaffolder-backend/src/scaffolder/tasks/NunjucksWorkflowRunner.ts`
- `@backstage/plugin-permission-common` 的 `dist/index.d.ts`（npm 包，经 jsDelivr）
- Crossplane 源码（main 分支）：`apis/apiextensions/v1/{xrd,composition,composition_revision,conditions}_types.go`
- KubeVela 源码（master 分支）：`apis/core.oam.dev/v1beta1/{application,componentdefinition,definitionrevision}_types.go`、
  `apis/core.oam.dev/common/types.go`
