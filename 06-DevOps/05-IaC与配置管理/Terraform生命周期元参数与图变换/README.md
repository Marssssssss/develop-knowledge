# Terraform 生命周期元参数与依赖图变换 (Demo 463)

> `lifecycle` 块里的四个元参数（`create_before_destroy` / `prevent_destroy` / `ignore_changes` / `replace_triggered_by`）
> 并不是「给资源加个开关」这么简单 —— 它们会**改变依赖图上的边**，其中 `create_before_destroy` 还会沿依赖边传播。
> 本 demo 用 Python + Go 双实现复现这套判定。

## 一、简介

官方把这些参数统称 **meta-arguments**：*"Meta-arguments are built into the Terraform language and control how Terraform creates resources."*

默认行为（lifecycle reference 原文）：*"when Terraform must change a resource argument that cannot be updated in-place due to remote API limitations, Terraform destroys the existing object and then creates a new replacement object"* —— 即**先销毁、后新建**。

本 demo 建模的四条规则 + 一条前置校验（`precondition`）。

## 二、原理详解

### 2.1 create_before_destroy：唯一会「传染」的开关

原文：*"Terraform propagates and applies `create_before_destroy` behavior to all resource dependencies. For example: `create_before_destroy` is enabled on resource A but not on resource B. Because resource A is dependent on resource B, Terraform enables `create_before_destroy` for resource B implicitly by default and **stores it to the state file**. As a result, you **cannot** override `create_before_destroy` to `false` on resource B because that would imply **dependency cycles** in the graph."*

三个可验证的点：

1. 传播方向是 **依赖方 → 被依赖方**（A 依赖 B，则 B 被置位），并且是**传递**的（A→B→C 时 C 也被置位）。

   直觉：A 的新对象要先于 A 的旧对象销毁而建好，而 A 的新对象又依赖 B 的新对象 —— 于是 B 也只能先建后拆。
2. 隐式置位会**写进 state 文件**，不是每次 plan 重新推导就算了。
3. 被隐式置位的资源再显式写 `false` 会被拒绝，理由是**会成环**。本 demo 把它建模成 `conflicts` 列表并进 `errors`（`C5`/`C6`）。

替换次序：`create_before_destroy = true` 时 `(create, destroy)`，否则 `(destroy, create)`。

### 2.2 prevent_destroy：守得住替换，守不住「配置被删」

原文：*"When `prevent_destroy` is set to `true`, Terraform rejects plans that would destroy the infrastructure object associated with the resource and returns an error. **The argument must be present in the configuration.** This rule **doesn't prevent** Terraform from destroying a resource if you remove its configuration."*

- 触发条件：计划中出现 `replace` 或 `destroy`
- 不触发：纯 `create`、纯 `update`、`noop`
- **漏洞**：把整个 `resource` 块从配置里删掉，`prevent_destroy` 一起消失，对象照常销毁（这也是为什么它必须和代码评审/策略检查配合使用）

原文还提醒：*"Enabling `prevent_destroy`, however, makes certain configuration changes impossible to apply and prevents the `terraform destroy` command from operating once such objects are created. Use `prevent_destroy` sparingly."*

### 2.3 ignore_changes：create 看、update 不看

原文：*"Terraform considers the arguments corresponding to the given attribute names when planning a **create** operation, but are ignored when planning an **update** operation."*

- 支持索引写法：`tags["Name"]`、`list[0]`
- 可以用 `all` 关键字：*"As a result, Terraform can create and destroy the remote object but will never propose updates to it."*
- 限制：*"Terraform only ignores attributes defined by the resource type. You can't apply `ignore_changes` to itself or to any other meta-arguments."*

> **本 demo 的口径（官方未规定，明确标注）**：官方只写明「create 考虑、update 忽略」，没有规定「该属性同时是 ForceNew」时怎么办。
> 本模型让被忽略的属性**完全不进入 diff**，因此既不 update 也不 replace（自检 `E5`）。真实 Terraform 的行为请以实测为准。

### 2.4 replace_triggered_by：只能引用「有计划动作」的东西

原文列举三条触发条件：

| 引用对象 | 触发条件 |
| --- | --- |
| 有多个实例的资源 | 任一实例计划 update 或 replace |
| 单个资源实例 | 该实例计划 update 或 replace |
| 单个实例的某个属性 | 该属性值发生任何变化 |

原文限制：*"You can only reference **managed resources** in `replace_triggered_by` expressions. This lets you modify these expressions without forcing replacement."* —— `local` / `var` 这类普通值**没有自己的计划动作**，所以不能引用；确实需要时官方建议用 `terraform_data` 资源包一层。

在 `count` / `for_each` 的资源里可以用 `count.index`、`each.key` 来引用对应实例。

### 2.5 precondition：在配置参数求值之前

原文：*"Terraform evaluates `precondition` blocks **before** evaluating the resource's configuration arguments."* 本 demo 在算 diff 之前先跑条件（`G1`）。

### 2.6 图变换：为什么要关心边

默认销毁顺序：X 依赖 Y ⇒ **先销毁 X 再销毁 Y**（被依赖者要活到最后）。
X 开了 `create_before_destroy` 后，X 的旧对象要等新对象建好，而新对象依赖 Y 的新对象 ⇒ 约束**翻转**成先销毁 Y 再销毁 X。

如果同一个图里既有 `X→Y`（翻转后 `Y→X`）又有 `Y→X`，就成环 —— 这正是 §2.1 第 3 点「不能显式写 false」的根因。自检 `H1`~`H5` 覆盖。

## 三、对比

| 元参数 | 解决的问题 | 副作用 |
| --- | --- | --- |
| `create_before_destroy` | 替换时不能中断服务 | 沿依赖边传播；可能要求被依赖资源也支持并存 |
| `prevent_destroy` | 防误删数据库等有状态对象 | 挡不住「删配置」；会让某些变更无法 apply |
| `ignore_changes` | 与外部系统共管同一对象的字段 | 真的漂移也不会被纠正 |
| `replace_triggered_by` | 依赖对象变了要跟着重建 | 只能引用托管资源 |
| `precondition` | apply 前拦下非法输入 | 失败即中止，不进入属性求值 |

## 四、环境与运行

```bash
python selfcheck_tflifecycle.py     # 34 项断言，纯标准库
go run .                             # Go 版同口径（本机无 Go 工具链，走人工审查 + 静态检查）
```

## 五、关键代码

```python
eff, conflicts = propagate_cbd(config, deps)   # 沿依赖边传播，返回冲突列表
actions, errors, cbd, order, triggers = plan(state, config, deps)
order[name]  # ("create","destroy") 或 ("destroy","create")
edges = destroy_graph(deps, actions, eff)      # CBD 时销毁边翻转
```

## 六、性能边界

- CBD 传播是不动点迭代，最坏 O(V·E)；实际模块规模下 V、E 都是百级，可忽略
- `destroy_graph` 是 O(V+E)；环检测用三色 DFS，避免递归爆栈要看图深度
- 真实 Terraform 的瓶颈在图构建阶段的 provider `ReadResource`，与本模型无关

## 七、注意事项与常见坑

1. **`create_before_destroy` 不是局部开关**：开了它，它的全部（传递）依赖都被隐式置位并写进 state，事后想关掉会报环。
2. `prevent_destroy` **必须字面出现在配置里**才有效（原文 *"The argument must be present in the configuration"*），从 `moved` 或继承里拿不到。
3. `ignore_changes = all` 的对象**永远不会被 update**，只能 create/destroy —— 想要改配置只能靠替换。
4. `replace_triggered_by` 引用 `local`/`var` 会报错；官方文档明确推荐用 `terraform_data` 兜底。
5. 带 `count`/`for_each` 的资源里引用别的资源，要用 `count.index` / `each.key` 指到具体实例，否则会按「多个实例」语义被任一实例的变化触发。
6. 本模型不模拟 provider 的 `PlanResourceChange`（那才是「哪些属性 ForceNew」的真正来源），`requires_replace` 是手工声明的。

## 八、参考资料（均为本轮实际抓取并阅读）

- HashiCorp《lifecycle: Meta-argument》—— 四个元参数与 precondition 的官方原文
  https://developer.hashicorp.com/terraform/language/meta-arguments/lifecycle
- HashiCorp《Resource behavior》—— 资源生命周期与替换语义
  https://developer.hashicorp.com/terraform/language/resources/behavior
