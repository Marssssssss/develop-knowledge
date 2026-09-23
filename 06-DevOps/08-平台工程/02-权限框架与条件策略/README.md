# 627 · Backstage 权限框架：策略决策与条件（criteria）求值

> 归属：`06-DevOps/08-平台工程`。自助服务门户一旦开放「注册实体 / 执行模板 / 删除组件」这类
> 写操作，就必须有统一的授权层。Backstage 的权限框架把**决策**与**执行**分开：策略只做决定，
> 插件负责落地。本 demo 按官方类型声明实读，把决策形状校验、条件树求值与最终授权落成代码。

## 一、事实来源（本轮实读，非凭记忆）

| 来源 | 拿到什么 |
| --- | --- |
| `@backstage/plugin-permission-common` 的 `dist/index.d.ts`（jsDelivr 取 npm 包） | `AuthorizeResult` 的三个字面量、**两种决策的字段差异**、`PermissionCondition` 三字段、`AllOf/AnyOf` 的 `NonEmptyArray` 约束、`NotCriteria` |
| `backstage.io/docs/permissions/concepts` | Permission / Policy / 决策与执行的分工 / 资源与规则 / 条件决策的语义 |
| `backstage.io/docs/permissions/writing-a-policy` | 脚手架默认策略是 allow-all；`isPermission` 做类型收窄；`createCatalogConditionalDecision` + `catalogConditions.isEntityOwner` 的写法 |
| `backstage.io/docs/permissions/overview` | 默认端点不受保护；框架面向 RBAC/ABAC/外部授权等多种模型 |

## 二、核心机制

### 1. 两种决策，字段互斥

```text
DefinitivePolicyDecision = { result: "ALLOW" | "DENY" }
ConditionalPolicyDecision = { result: "CONDITIONAL", pluginId, resourceType, conditions }
```

- 结果字面量是**大写字符串** `"ALLOW" / "DENY" / "CONDITIONAL"`（不是小写的枚举键名）。
- 确定决策**不能**携带 `pluginId` / `resourceType` / `conditions`；条件决策**必须**三个都齐。
  本 demo 对两个方向都做了断言（只查「缺字段」会漏掉「多字段」这类误报）。

### 2. 条件树：叶子是「规则 + 参数」，组合子是 allOf / anyOf / not

```text
PermissionCriteria = AllOfCriteria | AnyOfCriteria | NotCriteria | PermissionCondition
PermissionCondition = { resourceType?, rule, params? }
AllOfCriteria  = { allOf: [T, ...T[]] }     ← NonEmptyArray，空数组非法
AnyOfCriteria  = { anyOf: [T, ...T[]] }
NotCriteria    = { not: PermissionCriteria }
```

`NonEmptyArray` 是类型层约束，**运行时不会自动成立**——`{anyOf: []}` 这种来自配置文件/外部
授权服务的数据必须显式校验，否则「空 anyOf」会被朴素实现当成 `false`、「空 allOf」被当成
`true`，两者都是静默的授权错误。

求值的官方口径：**条件全部为真才允许**。本 demo 直接按此实现 `eval_criteria`。

### 3. 决策与执行分离

策略说CONDITIONAL 时，它**没有**资源数据（比如它还不知道实体 owner 是谁），于是把判定包成
条件交回拥有资源的插件；插件在数据库/内存里求值后才真正放行。这也是官方文档强调的
「避免策略与资源 schema 耦合」——插件可以把条件编译成一次数据库查询，而不是全量加载后过滤。

本 demo 用三条 catalog 规则演示：`IS_ENTITY_OWNER`（claims 与 owners 求交）、
`HAS_ANNOTATION`（只给 key 判存在、给了 value 才比相等）、`IS_ENTITY_KIND`。

关键负控：**空 claims 一定不是 owner**。匿名/身份未解析时 `ownershipEntityRefs` 为空数组，
此时 `isEntityOwner` 必须返回 false——如果实现成「空交集也算通过」，就等于对所有人开后门。

### 4. 资源类型收窄

`isResourcePermission(permission, 'catalog-entity')` 是条件策略的守门条件：只有资源权限才能
返回对应该资源类型的条件决策。基础权限（`createPermission` 不带 `resourceType`）不匹配时，
策略退化为确定决策。demo 里 `catalog.entity.create` 这类基础权限走 ALLOW 分支。

## 三、运行

```bash
cd 06-DevOps/08-平台工程/02-权限框架与条件策略
python python/selfcheck_perms.py   # 46 条断言
python python/main.py              # 六种「用户 × 策略」组合的授权结果
cd go && go run perm.go main.go
```

## 四、断言设计

- **形状断言双向**：条件决策缺 `pluginId` 报错，确定决策多带 `conditions` 也报错。
- **组合语义成对**：`allOf` 里放一个假条件为假、全真才真；`not` 翻转与 `not not` 还原分别断言。
- **负控不可省**：空 claims 不是 owner、`hasAnnotation` 的 key 不存在为假、值不匹配为假。
- **非法决策在授权阶段被拦**：策略返回 `{anyOf: []}` 时 `authorize` 直接抛错，而不是静默放行。
- 未使用的「未知规则」用 `raises` 断言真实抛出 `PermissionError`（不用恒真的 `isinstance` 兜底）。

## 五、注意事项与口径

- 本 demo 的 `conditions` 求值发生在**进程内**，真实 Backstage 里由插件后端执行（可能下推到
  SQL）；语义一致，性能语义不同。
- 官方 `PermissionCondition.resourceType` 在类型上是**可选**的（泛型默认 `string`），demo 里
  叶子条件允许省略 `resourceType`，只校验它一旦出现必须是字符串。
- `deny` 与 `allow` 的优先级由策略自己决定（框架不做合并）；本 demo 的 `deny_delete` 是显式
  if 分支，不隐含「DENY 优先」语义。

## 六、参考资料（实际读过）

- <https://backstage.io/docs/permissions/overview>
- <https://backstage.io/docs/permissions/concepts>
- <https://backstage.io/docs/permissions/writing-a-policy>
- <https://backstage.io/docs/permissions/plugin-authors/03-adding-a-resource-permission-check>
- `@backstage/plugin-permission-common` 类型声明（npm 包 `dist/index.d.ts`）
