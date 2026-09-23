# 628 · Backstage Scaffolder：软件模板（黄金路径）的求值与执行

> 归属：`06-DevOps/08-平台工程`。平台团队把「怎么正确地创建一个服务」固化成模板，开发者在
> 门户里填几个字段就能得到仓库 + CI + 目录实体——这条被官方称为 **golden path** 的路径，
> 技术实现就是 Scaffolder 的 Template 实体与它的步骤引擎。本 demo 按官方文档与后端源码实读，
> 把模板校验、两套表达式语法、步骤执行与 dry-run 落成可执行模型。

## 一、事实来源（本轮实读，非凭记忆）

| 来源 | 拿到什么 |
| --- | --- |
| `backstage.io/docs/features/software-templates/writing-templates` | `${{ }}` 与 `{{ }}` 的分工、`parameters.x` 与 `values.x` 的差别、`steps.$id.output.$prop`、output.links/text 的 `if`、`backstage.io/time-saved` 是 ISO 8601 时长、`spec.presentation` 改按钮文案 |
| `backstage.io/docs/features/software-templates/builtin-actions` | 动作以模块方式安装（github/gitlab/azure/…），`fetch:*` / `publish:*` / `catalog:*` 的分工 |
| `backstage.io/docs/features/software-catalog/descriptor-format`（Kind: Template 小节） | Template 的 `apiVersion`（文档示例里出现 `backstage.io/v1beta2` 与 `scaffolder.backstage.io/v1beta3` 两种）、`spec.type/parameters/steps` 必填 |
| `plugins/scaffolder-backend/src/scaffolder/tasks/NunjucksWorkflowRunner.ts` | 步骤按序执行；`each` 注入 `each.key`/`each.value`；dry-run 且 action 不支持时按 output schema **生成示例输出**（无 schema 则空对象）；结果写入 `context.steps[step.id].output` |

## 二、核心机制

### 1. 两套花括号，别混用

| 语法 | 出现位置 | 上下文 | 结果 |
| --- | --- | --- | --- |
| `${{ ... }}` | 模板 YAML 内部（step 的 input、output） | `parameters` / `steps` / `secrets` / `each` | **保留类型** |
| `{{ ... }}` | 被 `fetch:template` 抓取的**模板文件正文** | `values`（需由 `fetch:template` 显式传入） | 字符串插值 |

「保留类型」是实打实的行为差异：`${{ parameters.enabledDB }}` 求值是布尔 `False`，而
`{{ parameters.enabledDB }}` 得到字符串 `"True"`。前者能直接塞进 JSON schema 校验，后者不能。

同理 `parameters.x`（表单里来的）与 `values.x`（传给模板文件的）是两个命名空间：
模板 YAML 里写 `${{ values.name }}` 取不到东西，除非该值由 `fetch:template` 的 `input.values`
显式传下去。

### 2. 步骤串行、输出累积、可跳过

- 步骤按 `spec.steps` 顺序执行，每步结果写入 `steps[step.id].output`，后续步骤用
  `steps['publish'].output.repoContentsUrl` 消费（点号与下标两种写法等价）。
- `if` 求值为假则**跳过**：官方 runner 不会为该步写入真实输出，本 demo 按同一口径把它的
  output 置为空对象——后续引用该 output 会「取不到」而不是拿到上一步的残留值。
- `each` 把一个对象展开成多次执行，每次注入 `each.key` / `each.value`。
  **陷阱**：demo 首版先渲染 input 再判断 `each`，结果 `each.value` 在外层上下文里是 `None`
  而崩溃；正确顺序是先解析 `each`，命中时用它自己的局部上下文渲染 input。

### 3. dry-run 会伪造输出

模板编辑器执行的是 dry run。若动作声明不支持 dry run，runner 会按该动作的 **output schema
生成示例输出**，好让后续步骤与 output 卡片仍能渲染；没有 output schema 时给空对象。
这意味着 dry-run 看到的 URL/实体名是**占位值**，不能当真。

### 4. 时间节省是可度量的

`metadata.annotations["backstage.io/time-saved"]` 用 ISO 8601 时长表达（`PT4H` / `PT15M` /
`P1DT2H`），配合 `backstage.io/source-template` 注解或分析数据，就能算出模板累计节省了多少
工程师时间——这是平台团队向管理层证明价值的常用口径。

## 三、运行

```bash
cd 06-DevOps/08-平台工程/03-软件模板与黄金路径
python python/selfcheck_scaffold.py   # 45 条断言
python python/main.py                 # 建仓 → 注册实体 的完整链路
cd go && go run scaffold.go main.go
```

## 四、断言设计

- **类型断言成对**：`${{ }}` 得到 `True`/`3`，`{{ }}` 得到 `"True"`；不能只断言「能渲染出值」。
- **跳过语义**：`if` 为假 → status 是 `skipped` 且 output 为空；`if` 为真 → `completed`。
- **dry-run 成对**：不支持 dry run 的动作给示例输出并标记 `dry-run`；支持的动作仍走真实处理。
- **必填校验双向**：缺 `parameters` 报 `spec.parameters is required`，重复 step id 单独报错。
- 未定义变量按**报错**处理并断言抛出，避免「写错变量名静默变空串」这类漏网。

## 五、注意事项与口径（官方未给定量处，已标注）

1. **口径分歧**：descriptor-format 把 `spec.owner` 标为 `[optional]`，但同一段正文写
   「This field is required」。本 demo **不强制** owner，README 记录该分歧。
2. `generate_example_output` 的官方实现未读：本 demo 按 JSON Schema 的 `type` 生成占位值
   （`string → "<string>"`、`number → 0`、`boolean → false` …），仅用于演示 dry-run 的语义。
3. 未定义变量报错是本 demo 的策略选择（模板作者写错应立即失败），不代表官方 Nunjucks 子集
   的默认行为。
4. Go 侧简化：`Execute` 未实现 `each` 迭代（Python 侧完整实现），其余语义一致。

## 六、参考资料（实际读过）

- <https://backstage.io/docs/features/software-templates/writing-templates>
- <https://backstage.io/docs/features/software-templates/builtin-actions>
- <https://backstage.io/docs/features/software-catalog/descriptor-format>
- <https://github.com/backstage/backstage/blob/master/plugins/scaffolder-backend/src/scaffolder/tasks/NunjucksWorkflowRunner.ts>
