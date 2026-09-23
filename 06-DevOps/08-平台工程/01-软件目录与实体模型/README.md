# 626 · Backstage 软件目录：实体模型、引用与关系推导

> 归属：`06-DevOps/08-平台工程`。内部开发者平台（IDP）的地基是**软件目录**：平台团队把组织里的
> 组件、API、资源、系统、域与归属关系收进一个可查询的图，自助服务门户、模板、权限策略全都
> 建立在这张图上。本 demo 按官方规范与源码实读，把「一个 `catalog-info.yaml` 进到目录之后会
> 发生什么」落成可执行、可断言的模型。

## 一、事实来源（本轮实读，非凭记忆）

| 来源 | 位置 | 拿到了什么 |
| --- | --- | --- |
| Backstage 官方文档 | `backstage.io/docs/features/software-catalog/descriptor-format` | 信封四个根字段、`metadata` 各字段的长度与字符集、`relations`/`status` 只读、`uid`/`etag` 只读、各 kind 的「默认 kind + 生成的正向/反向关系」表格 |
| Backstage 源码 | `packages/catalog-model/src/kinds/relations.ts` | 8 组 `addRelationPair` 的 `fromKind`/`toKind` 约束（`ownedBy` 的 to 端只有 `Group`/`User`、`dependsOn` 只在 `Component`/`Resource` 之间……） |
| Backstage 源码 | `packages/catalog-model/src/schema/EntityMeta.schema.json` | `name` 必填、`namespace` 默认 `default`、`links[].url` 必填、`tags[]` 元素 `minLength: 1`——**注意 schema 只约束长度，不写字符集正则** |
| Backstage 官方文档 | `backstage.io/docs/features/software-catalog/system-model` | Component/API/System/Domain/Resource/Group/User 的分层语义 |

## 二、核心机制

### 1. 三套字符集各不相同（最容易写反的地方）

| 字段 | 长度 | 字符集 | 分隔符 |
| --- | --- | --- | --- |
| `metadata.name` | 1–63 | `[a-zA-Z0-9]` | `-` `_` `.` 之一（不能连续、不能首尾） |
| `metadata.namespace` | 1–63 | `[a-zA-Z0-9]` | **只有 `-`**（下划线与点号都非法） |
| `metadata.tags[]` | 1–63 | `[a-z0-9:+#]`（**强制小写**） | 只有 `-` |
| labels/annotations 键 | name 部分 1–63；前缀 ≤253 且必须是小写域名 | `[a-zA-Z0-9]` | `-` `_` `.` 之一 |

所以 `under_score` 作为 **name 合法、作为 namespace 非法**；`Java` 作为 tag 非法而作为 name 合法。
label 的**值**走 name 规则（含 63 上限），annotation 的**值**只要求是字符串、无长度上限——这是两者
用途差异的直接体现（label 用于筛选、annotation 用于外链任意数据）。

### 2. 实体引用 `[<kind>:][<namespace>/]<name>`

- kind 与 namespace 都可省略，省略时由**调用点的上下文**决定：本 demo 里 `spec.owner` 默认
  `Group`、`spec.system` 默认 `System`、`spec.providesApis` 默认 `API`、`spec.dependsOn` 默认
  `Component`。
- 省略 namespace 时**继承源实体的 namespace**（不是无条件 `default`）——把整个团队导入 `ghe`
  命名空间后，其中的组件引用会一起落到 `ghe`。
- kind 大小写不敏感，但规范化的字符串形式统一小写：`group:default/dev.infra`。
- `uid` 是数据库生成的**不稳定**标识：官方文档明确说「同一个文件注销再注册会得到新的 uid」，
  跨系统引用一律用字符串引用而不是 uid。

### 3. 关系是推导出来的，不是写出来的

`relations` 与 `status` 都是**只读根字段**：描述文件里写了会被拒。它们由 catalog 的处理循环
（processor）分析 spec 与周围环境后附加。官方还强调：插件应优先消费关系而不是 `spec.owner`，
因为 owner 可能来自同目录的 `CODEOWNERS` 而不是 YAML。

关系**成对**出现，且受 kind 约束。本 demo 直接按 `relations.ts` 的 7 组关系对校验：

| spec 字段 | 默认 kind | 实体侧关系 | 对端反向关系 |
| --- | --- | --- | --- |
| `owner` | Group | `ownedBy` | `ownerOf` |
| `system` / `subcomponentOf` / `domain` | System / Component / Domain | `partOf` | `hasPart` |
| `providesApis` / `consumesApis` | API | `providesApi` / `consumesApi` | `apiProvidedBy` / `apiConsumedBy` |
| `dependsOn` | Component | `dependsOn` | `dependencyOf` |
| `dependencyOf` | Component | **`dependencyOf`**（反向字段） | `dependsOn` |
| `memberOf` | Group | `memberOf` | `hasMember` |
| `parent` / `children` | Group | `childOf` / `parentOf` | `parentOf` / `childOf` |

关键陷阱：`spec.dependencyOf` 这个字段写出的是 `dependsOn` 关系对的**反向**那一半，所以
「实体侧关系类型」与「关系对主键」不是一回事——本 demo 用 `direction` 显式区分，避免把
`WELL_KNOWN_RELATIONS["dependencyOf"]` 当主键去查（会 KeyError，因为表里只有 `dependsOn`）。

kind 约束是硬校验：`dependsOn` 指向 `api:default/x` 会被拒（to 端只有 Component/Resource），
`Group` 上写 `spec.memberOf` 会被拒（`memberOf` 的 from 端只有 User）。

## 三、运行

```bash
cd 06-DevOps/08-平台工程/01-软件目录与实体模型
python python/selfcheck_catalog.py   # 70 条断言
python python/main.py                # 三实体目录的处理演示
cd go && go run catalog.go main.go   # Go 侧转写（无工具链时已通过 bracket/go_sanity/crossref）
```

## 四、断言设计（避免伪断言）

- 字符集断言**成对**：同一个串（如 `under_score`）同时断言在 name 下为 True、在 namespace 下为
  False，而不是只写「合法样本通过」。
- 边界断言带**上界与越界两侧**：`a*63` 通过、`a*64` 拒绝。
- 关系断言用**集合**比较整体结果（如 `dependencyOf` 字段产出的正是两条互为反向的三元组），
  而不是只查其中一条——只查一条会漏掉反向缺失。
- 反向对称性用**遍历校验**：对每条关系，在结果集里找它的配对，缺失计数必须为 0。

## 五、注意事项与口径

- 官方 JSON Schema 只有 `minLength` 没有字符集正则，字符集规则来自**文档正文**；两者冲突时以
  正文为准，本 demo 全部按正文实现（schema 的宽松通道不实现为「通过」）。
- `partOf` 的 to 端在源码里出现三次声明（Component→System、System→Domain、Domain→Domain），
  本 demo 合并成一组 `partOf` 关系对，to 端取并集 `{Component, System, Domain}`。
- 本 demo 不实现「孤儿检测 / 墓碑 / 处理循环重试」等生命周期语义，那属于
  `life-of-an-entity` 文档的范畴，另案。

## 六、参考资料（实际读过）

- <https://backstage.io/docs/features/software-catalog/descriptor-format>
- <https://backstage.io/docs/features/software-catalog/system-model>
- <https://backstage.io/docs/features/software-catalog/life-of-an-entity>
- <https://github.com/backstage/backstage/blob/master/packages/catalog-model/src/kinds/relations.ts>
- <https://github.com/backstage/backstage/blob/master/packages/catalog-model/src/schema/EntityMeta.schema.json>
