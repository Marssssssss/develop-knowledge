# OPA/Rego 编译期的 safety 检查与 body 重排

## 一、简介

Rego 的 rule body 是**无序合取**：书写顺序不代表求值顺序。但求值器必须真的
按某个顺序跑，于是 OPA 在编译期做一次 safety 分析——把 body 重排成「从左到
右求值不会碰到未绑定变量」的顺序；如果怎么排都排不出来，就报
`rego_unsafe_var_error: var x is unsafe`。

本 demo 用 Python 与 Go 两个实现复刻这条链路：

* `v1/ast/compile.go` —— `reorderBodyForSafety` / `outputVarsForExpr` /
  `outputVarsForExprEq` / `outputVarsForExprCall` / `outputVarsForTerms`
* `v1/ast/unify.go` —— `Unify` / `isRefSafe` / `isCallSafe`

并用官方 `v1/ast/compile_test.go` 里 `TestOutputVarsForNode` 的全部 31 条
向量做逐条对拍（Python 侧 `selfcheck_rego.py` 的 V 段）。

## 二、原理

### 2.1 不动点重排

```
safe := bodyVars ∩ globals                 # globals = {data, input} ∪ 函数名 ∪ 规则头参数
unsafe[e] := e 中所有不属于 safe 的变量
repeat:
    for e in body 按原始顺序:
        ovs := outputVarsForExpr(e, safe)  # 这条表达式能产出什么
        把 unsafe[e] 中「属于 ovs 或 safe」的变量删掉
        if unsafe[e] 为空:
            追加 e 到重排结果;  safe ∪= ovs     # 立刻生效，同趟后面的表达式可见
until 某一趟一条都没加进去
剩余非空 unsafe 即编译错误
```

两个关键性质（源码注释原话）：

* 表达式**一变安全就被立刻加入**，而不是每趟只加一条
* 同一趟里变安全的多条按**原始顺序**加入 —— 这就是 "minimal re-ordering"，
  已经合法的顺序一个字节都不会动

### 2.2 outputVarsForExpr 各分支

| 表达式 | 产出 |
| --- | --- |
| `not ...`（`IsNegated`） | **空**——所以 `not` 里的变量必须在别处绑定 |
| `... with input as t` | `t` 未全安全则空，否则递归内层 |
| 裸项 | `outputVarsForTerms` |
| `every k, v in dom` | 只有 dom 的下标变量；`k`/`v` 是**声明变量**不是产出 |
| `and` / `or` | 空——不给外围 body 贡献绑定 |
| `a == b` / `a := b` | `outputVarsForTerms ∪ safe`（`:=` 时删掉 LHS）再 `Unify`，最后减 `safe` |
| `f(x, y)` | `arity` 之前是输入、之后是输出；输入没全安全则**整条空产出** |

### 2.3 Unify 的方向性

`Unify(safe, a, b)` 返回「这次求值新绑定了哪些变量」，按 `a` 的类型分派：

* `Var` vs `Var`：谁安全谁流向对方；都不安全 → 记为互相 unknown
* `Var` vs `Ref`：`isRefSafe(ref)` 才绑定这个 var —— **只看引用头，不看下标**
* `Var` vs 常量：无条件绑定
* `Array` vs `Array`：等长则逐元素递归
* `Object` vs `Object`：等长则按**键精确匹配**再递归值
* `Set`：不参与 unify（`SkipSets`）

unknown 之间有依赖传播：`markSafe(v)` 会连带解锁所有只依赖 `v` 的变量。

### 2.4 两套变量口径（最容易写反的地方）

| 口径 | 是否含引用头 | 用途 |
| --- | --- | --- |
| `vars()` | **含** | `bodyVars` / `unsafe` 判定（`SafetyCheckVisitorParams` 只在头是函数调用时才跳过） |
| `output_vars()` | **不含**，且所有层级都不含 | `outputVarsForTerms`、调用的输入/输出判定（`SkipRefHead: true`） |

含头这一点是 `input` / `data` 能进 `safe` 的唯一原因：如果两边都跳过头，
`safe` 初值就是空集，整个分析直接崩掉。

## 三、对比

| 系统 | 未绑定变量怎么处理 | 会不会重排 |
| --- | --- | --- |
| Rego（OPA） | 编译期报 `var x is unsafe` | **会**，不动点重排，且尽量少动 |
| Prolog / Datalog | 最左优先求值，运行时自由变量直接失败 | 不会 |
| SQL | 没有变量绑定概念；优化器重排的是 join 顺序 | 会，但语义完全不同 |
| Rego 的 ad-hoc query | 同一套 `reorderBodyForSafety`（`queryCompiler.checkSafety`） | 会 |

值得注意：OPA 的两种 safety 阶段（`checkSafetyRuleBodies` 与
`queryCompiler.checkSafety`）共用同一个 `reorderBodyForSafety`，所以规则
和 ad-hoc 查询看到的重排行为是一致的。

## 四、环境

* Python 3.13，纯标准库
* Go 1.21，纯标准库（本机**没有** Go 工具链，Go 侧只过静态检查）

## 五、运行

```bash
cd python && python selfcheck_rego.py        # PASS=67 / ALL OK
```

Go 侧（有工具链时）：

```bash
cd go && go run .                            # 31 条官方向量 + 重排演示
```

无工具链时的人工替代：

```bash
python _docs/tools/bracket_check.py go/terms.go go/exprs.go go/safety.go go/main.go
python _docs/tools/go_sanity.py     go/terms.go go/exprs.go go/safety.go go/main.go
python _docs/tools/go_crossref.py   go/terms.go go/exprs.go go/safety.go go/main.go
```

## 六、关键代码

`python/safety.py` 里 eq 的产出（顺序一步都不能错）：

```python
def output_vars_for_eq(e, safe):
    out = output_vars_for_terms(e, safe)   # ① 安全且非 ground 的引用 → 下标变量
    out |= set(safe)                       # ② 与 safe 取并集
    if e.from_assignment:
        out -= e.lhs.vars()                # ③ := 把 LHS 整体剔出安全基
    u = Unifier(out)
    u.unify(e.lhs, e.rhs)                  # ④ 用这个并集当 Unify 的安全基
    out |= u.unified
    return out - set(safe)                 # ⑤ 最后才减掉 safe
```

`python/main.py` 的不动点：

```python
while True:
    n = len(reordered)
    for i, e in enumerate(body):
        if i in done: continue
        ovs = output_vars_for_expr(e, safe)
        for v in list(unsafe[i]):
            if v in ovs or v in safe: unsafe[i].discard(v)
        if not unsafe[i]:
            done.add(i); reordered.append(e); safe |= ovs
    if len(reordered) == n: break          # 这一趟一条都没排进去
```

## 七、性能边界

* 最多 `|body|` 趟，每趟 `O(|body|)`，最坏 `O(n²)`；`n` 是单条规则的表达
  式个数，量级很小，不值得优化
* `Unify` 的递归深度 = 项的嵌套深度；`unknown` 传播是线性的
* 未建模：闭包检查（`unsafeVarsInClosures` / `newBodySafetyTransformer`）、
  推导式、模板字符串、`with` 的多修饰器、函数头（call-head）引用

## 八、坑

1. **`vars()` 与 `output_vars()` 混用** —— 用错一处 `input` 就进不了 `safe`
2. **`isRefSafe` 只看头** —— `input.a[i] == 1` 产出 `{i}`，`p[x]` 产出空
3. **`:=` 会整体剔除 LHS** —— 同样两项，`input.a[i] == 1` → `{i}`，
   `input.a[i] := 1` → `set()`（issue #3546）
4. **`outputVarsForTerms` 要取并集不是替代** —— 写成「直接用 Unify 结果」
   会把 `{i}` 弄丢（本 demo 用扰动探针验证过这条断言确实有约束力）
5. **调用无输出项时返回 `outputVarsForTerms` 的结果，不是空集**
   （`numInputTerms >= len(terms)` 分支）
6. **报错时 `checkBodySafety` 返回原始 body**，不是重排后的
7. **`safetyErrorSlice` 会压掉 `:=` 的 LHS** —— 只要同批里还有非 LHS 的
   unsafe 变量，`n := [x]` 只报 `x`
8. **`SkipSets`**：集合的变量计入 unsafe，但从不作为产出
9. **`SkipObjectKeys`**：对象键同理；且 unify 按键精确匹配，所以
   `{"foo": x} = {y: 1}` 一个变量都不绑定
10. **`every` 的 key/value 不是 output var** —— 它们是声明变量，作用域只在
    every 体内；体外使用会被判 unsafe

## 九、参考

实际读过的源码（均取自 `open-policy-agent/opa` 仓库 `main` 分支）：

* `v1/ast/compile.go` —— `reorderBodyForSafety`、`outputVarsForExpr`、
  `outputVarsForExprEq`、`outputVarsForExprCall`、`outputVarsForTerms`、
  `outputVarsForBody`、`checkBodySafety`、`safetyErrorSlice`、
  `assignmentLHSVars`、`SafetyCheckVisitorParams`
* `v1/ast/unify.go` —— `Unify`、`unifier.unify`、`isRefSafe`、`isCallSafe`
* `v1/ast/compile_test.go` —— `TestOutputVarsForNode`（31 条对拍向量）
* `v1/ast/visit.go` —— `VarVisitorParams`、`WalkArgs`
* `v1/ast/policy.go` —— `ReservedVars`（= `{data, input}`）
