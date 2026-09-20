# Terraform 重构块与声明式状态搬迁 (Demo 462)

> 用 `moved` / `removed` / `import` 三个块把「改地址」这件事从命令行 (`terraform state mv`) 搬进版本化的配置里。
> 本 demo 用 Python + Go 双实现复现它们在 **plan 阶段**对 state 地址的作用，并给出可判定的计划结果。

## 一、简介

Terraform 默认把「资源地址变了」理解为 **销毁旧的 + 建新的**。三个块用于覆盖这个默认解释：

| 块 | 作用 | 关键参数 |
| --- | --- | --- |
| `moved` | 改地址但不销毁对象 | `from` / `to`（**都是必需**） |
| `removed` | 把对象移出 state（可连带销毁真实资源） | `from`（必需）、`lifecycle { destroy = <bool> }` |
| `import` | 把已存在的真实资源纳入管理 | `to`（必需）、`id` 或 `identity`（**互斥**）、`for_each` |

官方原文（Refactor modules）：*"By default, Terraform interprets a change as an instruction to destroy the existing resource and create a new resource at the new address. You can use a `moved` block to update a resource address without destroying it."*

`moved` 的工作时机（moved block reference）：*"Before creating a new plan for the resource specified in the `to` field, Terraform checks the state for an existing object at the address specified in the `from` field. Terraform renames existing objects to the string specified in the `to` field and then creates a plan."* —— **先改 state 地址，再造计划**。

## 二、原理详解

### 2.1 整资源级 vs 实例级寻址（最核心的一条判据）

官方原文：*"When at least one of the two addresses includes an instance key ... Terraform understands both addresses as referring to specific instances of a resource rather than the resource as a whole."*

也就是说：**只要 `from`、`to` 任意一侧带实例键，这一对地址就按「具体实例」匹配，否则按「整资源」匹配（覆盖全部实例）**。

| 写法 | 级别 | 效果 |
| --- | --- | --- |
| `aws_instance.a` → `aws_instance.b`（资源原本 `count = 2`） | 整资源 | `a[0]→b[0]`、`a[1]→b[1]`，**实例键被保留** |
| `aws_instance.a` → `aws_instance.a["small"]` | 实例 | 单实例对象变成键 `"small"` |
| `aws_instance.d[2]` → `aws_instance.d` | 实例 | 键被**丢掉**，变回单实例 |
| `aws_instance.c[0]` → `aws_instance.c["small"]` | 实例 | count 索引改写成 for_each 键 |
| `module.a` → `module.b` | 整资源 | 该模块下**所有**地址换前缀 |

本 demo 的 `_rewrite` 就把这条判据写成代码：整资源级用 `addr.key`，实例级用 `to.key`。

注意 **模块路径上的键也算键**：官方要求引用带 `count`/`for_each` 的模块内资源时必须给出实例键，例如
`moved { from = aws_instance.example to = module.new[2].aws_instance.example }`。

### 2.2 新实例会忽略 moved

官方原文：*"New instances of the module, which **never** had an `aws_instance.a`, will ignore the `moved` block and propose to create `aws_instance.b[0]` and `aws_instance.b[1]` as normal."*

`moved` 只是一条**改写规则**：state 里没有 `from` 就不产生任何效果，不会凭空造对象。

### 2.3 move 链

官方建议：*"If you need to rename or move the same object twice, we recommend chaining `moved` blocks ... Recording a sequence of moves in this way allows for successful upgrades for both configurations with objects at `aws_instance.a` and configurations with objects at `aws_instance.b`."*

即 `a→b` 与 `b→c` 同时存在时，**两个起点都能一步到 `c`**。本 demo 用「按配置顺序依次应用到当前 state」来复现：第一轮 `a` 变 `b`，第二轮把 `b`（含刚改名来的）变 `c`。

### 2.4 加 count 时的自动搬迁（一条容易踩的隐式规则）

官方原文：*"When you add `count` to an existing resource that didn't previously have the argument, Terraform automatically proposes moving the original object to instance `0` **unless** you write a `moved` block that explicitly mentions that resource."*

要点：① 只对 `count` 生效，`for_each` 没有这条兜底（必须显式写 `moved` 指定键）；② 只要**任意** `moved` 块的 `from`/`to` 提及该资源，自动搬迁就被抑制。

自检 `G1/G2/G3` 三条分别验证：加 count 自动搬 0 号、显式 moved 后不再自动搬、换 for_each 则旧对象被判销毁。

### 2.5 removed 的默认行为与其反面

`removed` block reference：*"By default, Terraform removes the resource from state and destroys the actual resource. Set `destroy` to `false` to remove the resource from state without destroying the actual resource."*

所以 `removed` **默认是 `destroy = true`**（销毁真实资源），`destroy = false` 才是「交接给别的工具管」。本 demo 对应动作名 `destroy` / `forget`；没有任何 `removed` 覆盖的残留 state 对象同样走 `destroy`。

### 2.6 import 的两条约束

- `to` **必须匹配一个已存在的 `resource` 块地址**（import block reference: *"The `to` argument must match the address of an existing `resource` block"*），否则报错；
- `id` 与 `identity` **互斥**（`identity` 用于 AWS S3 这类由多属性共同标识的资源，如 `account_id` / `bucket` / `region`）。

计划里：命中 import 且 state 无对象 → 动作是 `import` 而不是 `create`。

### 2.7 边界

- `moved` 可以把资源改名，跨资源类型要看 provider 是否支持；**不能**把托管资源 (`resource`) 变成数据源 (`data`) —— 本 demo 直接抛错。
- 删除 `moved` 块是**破坏性变更**：官方原文 *"Removing a `moved` block is a breaking change because any configurations that refer to the old address will plan to delete the existing object instead of move it."*
- 模块只能对自己及**子模块**的对象做 `moved` 声明。

## 三、对比

| 手段 | 是否进版本控制 | 是否可评审 | 生效时机 |
| --- | --- | --- | --- |
| `terraform state mv` | 否（一次性命令） | 否 | 立即改 state |
| `moved` 块 | 是 | 是 | 下次 plan/apply |
| 直接改资源名（无 moved） | 是 | 是 | **销毁 + 新建** |
| `removed` + `destroy=false` | 是 | 是 | 只移出 state，真实资源保留 |
| `import` 块 | 是 | 是 | 下次 plan/apply（可 `-generate-config-out` 生成配置） |

## 四、环境

- Python 3.8+（纯标准库）/ Go 1.20+（纯标准库）
- 无需云账号、无需 terraform 二进制：本 demo 是**语义模型**，不是 provider 调用

## 五、运行方式

```bash
python selfcheck_tfrefactor.py     # 35 项断言
go run .                            # Go 版同口径自检（需 Go 工具链；本机无，走人工审查 + 静态检查）
```

## 六、关键代码

```python
mv = Move("aws_instance.a", "aws_instance.b")       # 整资源级：保留实例键
plan(state, cfg, moves=[mv])                        # a[0]/a[1] → b[0]/b[1]，无 destroy

Move("aws_instance.d[2]", "aws_instance.d")         # 实例级：目标无键 → 丢掉键
Move("aws_instance.a", 'aws_instance.a["small"]')   # 单实例 → for_each 键
```

判据本体（Python 版 `_rewrite`）：

```python
def _rewrite(addr, frm, to, instance_level):
    if frm.is_module_only:
        return Addr(to.modules + addr.modules[len(frm.modules):],
                    addr.rtype, addr.rname,
                    to.key if instance_level else addr.key, addr.is_data)
    return Addr(to.modules, to.rtype, to.rname,
                to.key if instance_level else addr.key, to.is_data)
```

## 七、性能边界

- 解析是 O(地址长度)；apply_moves 是 O(moves × state 条目)，state 上千条、moved 上百块时仍在毫秒级
- 真正的开销在真实 Terraform 里是 provider 的 `ReadResource` 刷新，与本模型无关
- 地址解析用「按顶层 `.` 切分 + 括号深度 + 引号状态机」，键内含 `.`（如 `["a.b"]`）不会被误切

## 八、注意事项与常见坑

1. **别把「整资源级」和「实例级」混用**：`moved { from = aws_instance.a to = aws_instance.b }` 会连带搬走 `a[0]`、`a[1]`；只想搬某一个就必须给键。
2. **`for_each` 没有自动搬 0 号**的兜底，加 `for_each` 前必须先写 `moved` 指定键，否则旧对象被销毁。
3. **`removed` 默认是销毁**，想交接给别人一定要写 `lifecycle { destroy = false }`。
4. **模块拆分时 moved 要写在「还能看见旧地址」的那一层**：官方示例是把三个 `moved` 写在保留下来的 shim 模块里，且地址相对于所在模块实例解析。
5. **删除历史 `moved` 会砸掉老版本的升级路径**，官方明确建议保留全部历史 `moved`。
6. 本模型只覆盖地址搬迁语义，**不**模拟 provider 的 `PlanResourceChange` 与 `import` 时的真实属性读取。

## 九、参考资料（均为本轮实际抓取并阅读）

- HashiCorp《Refactor modules》—— `moved` 语义、整资源/实例级寻址、模块拆分、move 链、删除 moved 是破坏性变更
  https://developer.hashicorp.com/terraform/language/modules/develop/refactoring
- HashiCorp《moved block reference》—— `from`/`to` 均为必需，plan 前先查 state
  https://developer.hashicorp.com/terraform/language/block/moved
- HashiCorp《removed block reference》—— 默认销毁、`lifecycle { destroy = false }`
  https://developer.hashicorp.com/terraform/language/block/removed
- HashiCorp《import block reference》—— `to`/`id`/`identity` 互斥与 `for_each`
  https://developer.hashicorp.com/terraform/language/block/import
- HashiCorp《Import existing resources》—— `id` 与 `identity` 两种资源标识方式
  https://developer.hashicorp.com/terraform/language/import
