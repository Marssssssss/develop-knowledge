# Helm Chart 模板渲染:值合并、管道函数与 InstallOrder

## 简介

`helm install` 到底做了什么?核心只有三步,而且每一步都有明确的、写在源码里的规则:

1. **合并 values**:chart 的 `values.yaml` → 父 chart 的 values → `-f` 用户文件 → `--set`,后者覆盖前者。
2. **渲染模板**:用 Go `text/template` + sprig 函数把 `templates/*.yaml` 求值成一批 manifest 文本。
3. **按 Kind 排序**:渲染结果按一张写死的 `InstallOrder` 表排序后才提交给 Kubernetes —— 因为 `Namespace` 必须比 `ConfigMap` 先建、`Service` 必须比 `Deployment` 先建。

本项目把这三步各自做成可判定的代码,并用官方文档的示例做回归。

## 原理详解

### 一、值优先级

> 原文:*"The list above is in order of specificity: `values.yaml` is the default, which can be overridden by a parent chart's `values.yaml`, which can in turn be overridden by a user-supplied values file, which can in turn be overridden by `--set` parameters."*

```
values.yaml  <  父 chart 的 values.yaml  <  -f myvals.yaml  <  --set
```

**一个非常重要的细节:合并是深合并,不是替换。** 覆盖 `livenessProbe.httpGet.path` 不会抹掉同层的 `port`。文档里专门讲了怎么绕过这一点:

> 原文:*"If you need to delete a key from the default values, you may override the value of the key to be `null`, in which case Helm will remove the key from the overridden values merge."*

所以 `--set livenessProbe.httpGet=null` 才有意义 —— 否则你会得到 `httpGet` 和 `exec` 两个 handler 并存,而 Kubernetes 只允许一个。

### 二、模板函数与管道

> 原文:*"Template functions follow the syntax `functionName arg1 arg2...`."*

两种写法等价:

```yaml
drink: {{ quote .Values.favorite.drink }}       # 函数式
drink: {{ .Values.favorite.drink | quote }}     # 管道式(推荐)
```

管道的语义是:

> 原文:*"the result of the first evaluation (`.Values.favorite.drink`) is sent as the **last argument to the function**."*

这一条决定了多参函数的参数顺序。例如 sprig 的 `repeat COUNT STRING`:

```yaml
drink: {{ .Values.favorite.drink | repeat 5 | quote }}    # => "coffeecoffeecoffeecoffeecoffee"
```

管道值 `"coffee"` 是**最后一个**参数,所以实际调用是 `repeat(5, "coffee")`。同理 `join ", "` 里分隔符是第一个参数、列表是第二个(管道值补在最后)。

`default` 也是这个顺序:`default DEFAULT_VALUE GIVEN_VALUE`,**默认值在前**。

> 原文:*"the `default` command is perfect for computed values, which cannot be declared inside `values.yaml`."*

另外:运算符 `eq` / `ne` / `lt` / `gt` / `and` / `or` **全是函数**,不是语法。

### 三、InstallOrder

`helm` 不是按文件名、也不是按字母序提交资源的,而是按 `pkg/releaseutil/kind_sorter.go` 里写死的一张表(完整 36 项,v3.19.0 原文顺序):

```
PriorityClass, Namespace, NetworkPolicy, ResourceQuota, LimitRange,
PodSecurityPolicy, PodDisruptionBudget, ServiceAccount, Secret,
SecretList, ConfigMap, StorageClass, PersistentVolume,
PersistentVolumeClaim, CustomResourceDefinition, ClusterRole,
ClusterRoleList, ClusterRoleBinding, ClusterRoleBindingList, Role,
RoleList, RoleBinding, RoleBindingList, Service, DaemonSet, Pod,
ReplicationController, ReplicaSet, Deployment, HorizontalPodAutoscaler,
StatefulSet, Job, CronJob, IngressClass, Ingress, APIService
```

排序比较函数 `lessByKind` 有三条规则(源码原文):

```go
if !aok && !bok {
    // if both are unknown then sort alphabetically by kind, keep original order if same kind
    if kindA != kindB { return kindA < kindB }
    return false
}
if !aok { return false }   // unknown kind is last
if !bok { return true }
return first < second      // sort different kinds, keep original order if same priority
```

加上外层用的是 `sort.SliceStable`,所以:

- **没见过的 Kind(CRD 实例)永远排在最后**,而且它们之间按字母序。
- **相同 Kind 的多个对象保持模板输出顺序** —— 想控制顺序只能靠模板里的书写顺序或 `helm.sh/hook-weight`。

`UninstallOrder` 是另一张表(基本是 InstallOrder 的逆序,但**不完全是**:v3.19.0 里 `StatefulSet` 排在 `Job` 之前),卸载时用它。

## 对比

| 环节 | 规则 | 出错症状 |
| --- | --- | --- |
| 值合并 | 深合并,null 删键 | 两个 probe handler 并存 → `apply` 失败 |
| 覆盖顺序 | `--set` 最高 | 改了 values 文件不生效(被 --set 盖住) |
| 管道 | 管道值是**最后一个**参数 | `repeat 5` 写反 → 输出把数字重复 |
| default | `default 默认值 给定值` | 参数写反 → 永远取到给定值 |
| InstallOrder | 未知 Kind 最后 | CRD 实例先于 CRD 创建 → not found |
| 同 Kind 顺序 | 保持模板输出顺序 | 无法用排序手段改变 |

## 环境

- Python 3.8+(仅标准库)
- Go 1.18+(仅标准库)
- C99 编译器(实现体在 `helm_render_impl.h`,用 `#include` 引入)

## 运行方式

```bash
python python/helm_render.py        # 55 项断言
go run go/helm_render.go
gcc -std=c99 -o /tmp/hr c/helm_render.c && /tmp/hr
```

## 关键代码

```python
def eval_action(body, ctx):
    parts = body.split("|")
    head = parts[0].strip()
    head_toks = _tokenize(head)          # 引号感知:join ", " 不能被空格切开
    if head_toks and head_toks[0] in FUNCS:
        val = FUNCS[head_toks[0]](*[resolve(a) for a in head_toks[1:]])
    else:
        val = resolve(head)
    for seg in parts[1:]:
        toks = _tokenize(seg.strip())
        val = FUNCS[toks[0]](*[parse_arg(a) for a in toks[1:]], val)  # 管道值补最后
    return "" if val is None else str(val)
```

```go
func lessByKind(a, b string) bool {
    // 未知 kind 最后;双未知按字母序;相同 kind 保持原顺序
    if !aok && !bok { if a != b { return a < b }; return false }
    if !aok { return false }
    if !bok { return true }
    return ia < ib
}
```

## 性能边界

- 值合并是 O(键数),但**深合并要递归**:values 树很深时递归栈是瓶颈;文档明确建议 **"keep your values trees shallow, favoring flatness"**。
- 模板渲染是 O(模板总长 × 函数个数),真实 Helm 还会做 `lookup`(查集群)—— 那会让渲染从纯函数变成有 I/O 的操作,`helm template` 默认不允许(`--dry-run=server` 才行)。
- `lessByKind` 每次比较都重建一次 `map[string]int`(源码里 `ordering := make(map[string]int, len(o))` 写在函数体内),排序 N 个 manifest 就是 O(N log N × 36) 的 map 构造。这是真实 upstream 实现的一个可优化点,本项目照抄了它的语义。
- InstallOrder 表只有 36 项,**自定义资源(Kind 不在表里)全部落到「未知」分支**,大 chart 里未知 Kind 数量多时字母序排序是唯一秩序来源。

## 注意事项与常见坑

- **`--set` 的优先级最高**,排障 "改了 values 没生效" 先看 CI 里有没有 `--set`。
- **深合并会留下不想要的键**,必须用 `=null` 显式删除,不是覆盖成空对象。
- **`default` 的参数顺序是「默认值, 给定值」**,写反了不会报错,只会永远拿不到默认值。
- **管道值补在参数列表最后**,所以 `repeat 5`、`join ", "` 这种「第一个参数是配置」的函数,管道写法看起来像参数倒过来了。
- **模板里的引号要能被分词器正确识别**:`join ", "` 里的逗号+空格必须整体作为一个参数,朴素 `split()` 会把它切成两半。
- **未知 Kind 排最后**意味着:用 CRD 创建的自定义资源实例,一定在 CRD 之后创建 —— 这既是保护也是约束,想提前只能加 hook。
- **相同 Kind 的顺序不可控**(保持模板输出顺序),多个同 Kind manifest 的先后顺序要靠模板书写顺序保证。
- 本项目只建模了模板语言的**一个子集**(变量引用、管道、6 个函数、注释);控制结构(`if` / `range` / `with`)、命名模板 `define`/`include`、`.Files`、capabilities 均未覆盖。

## 参考资料

已实际阅读:

1. Helm 官方文档 — Values Files,https://helm.sh/docs/chart_template_guide/values_files/
2. Helm 官方文档 — Template Functions and Pipelines,https://helm.sh/docs/chart_template_guide/functions_and_pipelines/
3. `helm/helm@v3.19.0` — `pkg/releaseutil/kind_sorter.go`,https://cdn.jsdelivr.net/gh/helm/helm@v3.19.0/pkg/releaseutil/kind_sorter.go
