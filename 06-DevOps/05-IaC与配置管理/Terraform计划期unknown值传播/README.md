# Terraform 计划期 unknown 值传播 (Demo 466)

> `terraform plan` 输出里的 **"known after apply"** 不是一句客套话 —— 它是一整套「prior 状态 + 配置 → proposed new state」的合并规则跑出来的结果。
> 本 demo 依据 Terraform v1.9.8 源码 `internal/plans/objchange/objchange.go` 用 Python + Go 复现这套规则。

## 一、简介

Terraform 构造计划时先算出一个 **proposed new state**（提议的新状态），再交给 provider 的 `PlanResourceChange` 修正。源码注释：

> *"ProposedNew constructs a proposed new object value by combining the computed attribute values from `prior` with the configured attribute values from `config`. ... The prior value must be **wholly known**, but the config value may be unknown or have nested unknown values."*

控制台上看到的 `known after apply`，本质就是合并后**仍是 unknown** 的那些位置。

## 二、原理详解

### 2.1 属性级的三条分支

源码 `proposedNewAttributes` 的 switch 只有三个分支，注释写得很直白：

> *"required isn't considered when constructing the plan, so attributes are essentially either **computed or not computed**. In the case of optional+computed, they are only computed when there is no configuration."*

| 情形 | 结果 |
| --- | --- |
| `Computed && config 为 null` | 取 **prior** |
| 有 `NestedType` | 递归进嵌套结构 |
| 其它（非 computed） | **一律取 config**，即使 config 是 null |

前两条决定了：*provider 算出来的字段（id、arn 等）不会因为你在配置里没写就被抹掉*。自检 `B1`/`B3`。

### 2.2 唯一的例外：optionalValueNotComputable

```go
case attr.Computed && configV.IsNull():
    newV = priorV
    // the exception to the above is that if the config is optional and the
    // _prior_ value contains non-computed values, we can infer that the
    // config must have been non-null previously.
    if optionalValueNotComputable(attr, priorV) { newV = configV }
```

`optionalValueNotComputable` 成立需要三条同时满足：属性是 **Optional**、**有 NestedType**、且能在 prior 里走到一个**非 computed 且非 null** 的嵌套属性。此时判定「以前配置过、现在删掉了」，于是**取 config（null）而不是保留 prior**。自检 `I1`/`I2`。

### 2.3 嵌套块的四种关联方式（最容易出错的地方）

| nesting | 关联依据 | 对不上的元素 |
| --- | --- | --- |
| `single` | 就是它自己；config 为 null 直接取 config | — |
| `list` | **按下标**（*"Nested blocks are correlated by index"*） | 超出 prior 长度的原样取 config |
| `map` | **按键名** | 不在 prior 里的键原样取 config |
| `set` | **启发式**：按非 computed 属性值匹配 | 匹配不上原样取 config |

`set` 那一档源码自己打了预防针：

> *"the correlation for blocks backed by sets is a **heuristic** based on matching non-computed attribute values and so it may produce strange results with more 'extreme' cases, such as a nested set block where **all** attributes are computed."*

自检 `G1`/`G2`/`G3` 分别验证「同签名能匹配上」「不同签名原样取」「签名只含非 computed 属性」。

另外 `map` 有一条类型约束：*"The value must leave as the same type it came in as"* —— object 进 object 出、map 进 map 出。

### 2.4 整体 unknown 只有一种来源

源码注释：*"The only time we should encounter an entirely unknown block is from the use of `dynamic` with an unknown `for_each` expression."* 遇到时**直接原样返回 config**（`D1`）。

顶层也一样：`proposedNew` 开头 `if config.IsNull() || !config.IsKnown() { return prior }`（`C1`/`C2`）。

### 2.5 data source 的「unknown 短路」技巧

`PlannedDataResourceObject` 的实现很巧妙，源码注释原文：

> *"Our trick here is to run the proposedNewBlock logic with an **entirely-unknown prior value**. Because of cty's unknown short-circuit behavior, any operation on prior returns another unknown, and so unknown values propagate into all of the parts of the resulting value that would normally be filled in by preserving the prior state."*

也就是说：**不用写第二套逻辑**，只要把 prior 换成「整体 unknown」，所有本该保留 prior 的位置就自动变成 unknown。自检 `H1`~`H3`。这也解释了为什么 data source 在 plan 阶段几乎所有输出都是 `known after apply`。

### 2.6 「known after apply」是哪一步产生的

`ProposedNew` 本身**不会**凭空造 unknown：新建资源时 prior 被替换成 `EmptyValue()`（全 null），computed 属性取到的就是 **null 而不是 unknown**（`A1`/`A3`）。

真正的 unknown 来自下一步 —— provider 的 `PlanResourceChange`。源码在 `PlannedDataResourceObject` 注释里把这描述为：

> *"assuming a fixed implementation of PlanResourceChange that **just fills in unknown values as needed**."*

本 demo 用 `provider_fill_unknown()` 单列这一步（`A4`），并明确标注：这是**按源码注释口径建模**，不是 `ProposedNew` 自身的行为。

### 2.7 类型转换（官方文档口径）

官方《Types and Values》：*"Where possible, Terraform automatically converts values from one type to another ... Automatic type conversion **does not occur** when using the equality operator."* 并给出三组：`true` ↔ `"true"`、`false` ↔ `"false"`、`15` ↔ `"15"`。

## 三、对比

| 阶段 | 输入 | 输出 | 谁负责 |
| --- | --- | --- | --- |
| `ProposedNew` | prior + config | proposed new state | **Terraform Core** |
| `PlanResourceChange` | proposed new state | planned new state（补 unknown） | **provider** |
| `ApplyResourceChange` | planned new state | 真实新状态 | provider |

| 值 | 出现位置 | 含义 |
| --- | --- | --- |
| `null` | config 未设置 | 该属性不参与管理 |
| `unknown` | plan 输出 | `known after apply` |
| prior 的值 | computed 且 config 为 null | 沿用 provider 上次算出的结果 |

## 四、环境与运行

```bash
python selfcheck_tfunknown.py     # 30 项断言，纯标准库
go run .                           # Go 版同口径（本机无 Go 工具链，走人工审查 + 静态检查）
```

## 五、关键代码

```python
UNKNOWN = sentinel          # 对应 cty 的 unknown
p = proposed_new(SCHEMA, prior, config)       # Core 阶段：合并
p = provider_fill_unknown(SCHEMA, p)          # provider 阶段：补 unknown
p = planned_data_resource_object(SCHEMA, cfg) # data source：prior 整体 unknown
```

set 关联签名（只比非 computed 属性）：

```python
tuple(sorted((n, repr(value.get(n))) for n, s in schema.items() if not s.computed))
```

## 六、性能边界

- `ProposedNew` 是 O(属性数 × 嵌套深度)；真实瓶颈在 provider 的 API 调用
- set 关联是 O(n²)（对每个 config 元素扫一遍 prior），n 通常是个位数；n 很大时这个启发式既慢又不准
- 本模型用纯字典/列表表示值，不做 cty 的类型检查与路径遍历，复杂度低于真实实现

## 七、注意事项与常见坑

1. **`known after apply` 不等于 `null`**：前者是 unknown，后者是「配置没写」。新建时 computed 属性在 Core 阶段是 **null**，是 provider 才把它变 unknown。
2. **list 按下标关联**，所以在列表中间插入一个元素会让后面所有元素与 prior 错位 —— 这正是 set/map 更适合作嵌套块的原因之一。
3. **set 关联是启发式**：全 computed 的 set 块可能匹配出奇怪结果，源码已明确警告。
4. **整体 unknown 只来自 `dynamic` + unknown `for_each`**，不要在别处假设会出现。
5. **自动类型转换不适用于相等运算符**（官方明确点名），写 `==` 比较时类型必须一致。
6. `required` 在构造计划时**不参与判定**，别指望它影响合并结果 —— 该报错的在更早的校验阶段就报了。
7. 本模型不实现 cty 的类型系统与 `cty.Path`，嵌套 schema 用简化结构表达。

## 八、参考资料（均为本轮实际抓取并阅读）

- Terraform v1.9.8 源码 `internal/plans/objchange/objchange.go` —— `ProposedNew` / `proposedNewAttributes` /
  `proposedNewNestingList|Map|Set` / `optionalValueNotComputable` / `PlannedDataResourceObject` 全部逐段实读
  https://cdn.jsdelivr.net/gh/hashicorp/terraform@v1.9.8/internal/plans/objchange/objchange.go
- HashiCorp《Types and Values》—— 类型转换规则与三组转换示例
  https://developer.hashicorp.com/terraform/language/expressions/types
- HashiCorp《Conditional Expressions》
  https://developer.hashicorp.com/terraform/language/expressions/conditionals
