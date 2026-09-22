# demo575 实现笔记

## 建模口径（与真实 OPA 的差异，逐条标注）

| 项 | 本 demo | 真实 OPA | 说明 |
| --- | --- | --- | --- |
| 引用头 | `vars()` 计入、`output_vars()` 不计 | 同（SkipRefCallHead 只在头是 Call 时跳过） | 一致 |
| 复合头 `{1,2}[1]` | `is_ref_safe` 走 head.vars() ⊆ safe | `isRefSafe` 的 default 分支，等价 | 一致（对拍 V24/V25/V26） |
| 调用头 `split(z,"")[y]` | `CallT` 项，`head.vars() ⊆ safe` | `isCallSafe` | 一致（对拍 V27/V28） |
| `every` 的 key/value | 声明变量，只在 `check_every` 里作为 safe 初值 | `rewriteEveryStatement` → `declaredVar` | **近似**：本 demo 不建模重命名，直接把 key/value 当已声明 |
| 闭包检查 | 未建模 | `unsafeVarsInClosures` + `newBodySafetyTransformer` | 明确缺口 |
| 推导式 / 模板字符串 | 未建模 | 有专门分支 | 明确缺口 |
| `with` 多修饰器 | 只看单个 target | 遍历 `expr.With` | 明确缺口 |
| 生成变量 `__localN__` | 未建模 | `IsGenerated()` 过滤 | 明确缺口 |

## 断言有效性自查（扰动探针）

首版 14 条失败，重写后 67 条一次全绿。按「通过 ≠ 验到」的规矩做了 4 组探针：

| 扰动 | 翻动的断言 |
| --- | --- |
| `is_ref_safe` 恒为真 | V21 `p[x]`、V26 `{x,2}[1] = y` |
| `Not` 照常产出变量 | V02 `not x = 1` |
| eq 里丢掉 `outputVarsForTerms` 的并集 | A9 `input.a[i] == 1` → `{i}` |
| `:=` 不清 LHS | A5 `input.a[i] := 1` → 空集 |

**教训**：最初挑的 A5（`x := y`）与 V30 都不具区分力 —— 去掉被扰动的代码
后它们照样通过。这两条已换成 `input.a[i] := 1` 与 `input.a[i] == 1` 这一对
「只差一个 `:=`」的用例，改动前必须能翻、改动后必须能翻回来。

`x == y` 与 `x := y` 在「两边都未绑定」时结果相同，所以拿 `:=` 去验
「方向性」是无效断言，必须让 LHS 出现在引用里。

## 读源码时踩到的三个坑

1. **`Args(terms)` 是类型转换不是切片**。`Args` 是 `type Args []*Term`，
   于是 `Args(terms[numInputTerms:])` 就是 `terms[arity+1:]` 原样。若误以为
   它会再切掉首元素，就会把 `count(input.a, n)` 的输出项 `n` 弄丢。
2. **`SafetyCheckVisitorParams` 只设了 `SkipRefCallHead`**，不是
   `SkipRefHead`。这一点决定了 `input` / `data` 能进入 `bodyVars`。
3. **`every` 的产出只有 domain 的下标变量**。官方测试
   `every k, v in [1, 2] { k < v }` → `set()`、`xs = []; every k, v in xs[i]`
   → `{xs, i}` 已经写死了这一点；`{k, v}` 是「想当然」的答案。

## 文件结构

```
python/
  terms.py      Term 模型（含两套变量口径）        177 行
  exprs.py      Expr 模型                          164 行
  safety.py     Unify + outputVarsFor*             219 行
  main.py       reorderBodyForSafety + 报错         107 行
  selfcheck_rego.py  67 条断言（V 段为官方对拍）    240 行
go/
  terms.go      VarSet 工具 + Term                 253 行
  exprs.go      Expr                               156 行
  safety.go     Unifier + outputVarsFor*           278 行
  main.go       重排 + 31 条官方对拍入口            262 行
```

Python 侧 `safety.py` 初版 535 行，按 `terms / exprs / safety` 三块拆开后
重跑自检仍是 67 条全绿；Go 侧 `ast.go` 初版 401 行，同样按 `terms / exprs`
拆开后三项静态检查仍全过。
