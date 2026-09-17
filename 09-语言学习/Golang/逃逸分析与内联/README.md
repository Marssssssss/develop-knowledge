# Go 编译器的逃逸分析与函数内联

## 简介

Go 没有手动内存管理，但**栈分配还是堆分配**决定了 GC 压力与分配开销。这件事由编译期的
**逃逸分析（escape analysis）** 决定：一个变量只要「有可能」被外部看到，就必须上堆。

**函数内联（inlining）** 是逃逸分析的放大器——被调函数的函数体一旦在调用点展开，
更多事实（尤其常量传播）变得可见，原本只能保守判「逃逸」的分配就可能留在栈上。

关键概念：

| 概念 | 一句话 |
| --- | --- |
| location（位置） | 每个分配语句/表达式在分析图里是一个顶点 |
| 边权 derefs | `解引用次数 − 取地址次数`：`p = &q` 为 −1、`p = q` 为 0、`p = *q` 为 1 |
| 不变量 (a) | 指向栈对象的指针**不得存入堆** |
| 不变量 (b) | 指向栈对象的指针**不得比该对象活得更久** |
| parameter tag | 把「参数 → 堆 / 参数 → 返回值」的信息总结在函数上，供静态调用点复用 |
| 内联预算 | 默认 80，一次调用记 57，调用参数额外 17，panic 记 1，throw 记满 |

历史背景：`gc` 自 1.0 起就做全局（跨函数、跨包）逃逸分析；内联预算 80 与"调用记 57"
是 2018 年（Go 1.11 前后）benchmark 调出来的定值，注释里写明了理由。

## 原理详解

### 1. 位置图与边权

`escape.go` 头部注释给的构造方式：

```text
p = &q        // -1   （取地址一次）
p = q         //  0
p = *q        //  1
p = **q       //  2
p = **&**&q   //  2   （取地址与解引用抵消）
```

`&x` 本身**不可寻址**，所以权重不会低于 −1。图是**流不敏感、路径不敏感、上下文不敏感**的：
`x.f = u[0]` 被建模成 `x = *u`，不区分 `x.f` 与 `x.g`，也不区分 `u[0]` 与 `u[1]`。

### 2. 两条不变量与判定

从 `heapLoc`（属性为 `escapes|persists|mutates|calls`）与 `calleeLoc`（返回值）**反向传播**：

```text
        dst 逃逸  且  derefs <= 0
        ───────────────────────────
              src 也会逃逸
```

关键在 `derefs <= 0`：`heap = q`（权重 0）让 `q` 逃逸，但 `heap = *q`（权重 +1）
只把 `q` 指向的**内容**拷出去，`q` 这个指针自身不上堆。本 demo 用一个断言把这条差异钉住。

### 3. 过程间：parameter tag

跨函数时看不到函数体，于是每个函数先算出一张"参数标签"：

```go
func sink(p *int)  { global = p }   // tag: param 0 leaks
func ident(p *int) *int { return p } // tag: param 0 → result
func pure(p *int)  { _ = *p }        // tag: 干净
```

静态调用点上，`sink(&x)` 让 `x` 上堆，`pure(&x)` 不会。**这就是"在 Go 里传递指针不一定逃逸"
的机制来源**；`-gcflags=-m` 能直接看到编译器给出的结论。

### 4. 内联预算

`inl.go` 的常量块（原文注释：*Inlining budget parameters, gathered in one place*）：

| 常量 | 值 | 含义 |
| --- | --- | --- |
| `inlineMaxBudget` | 80 | 默认预算（AST 节点数近似） |
| `inlineExtraCallCost` | 57 | 一次调用；57 是 benchmark 出来的（issue 19348） |
| `inlineParamCallCost` | 17 | 调用一个"参数"，可能暴露常量函数 |
| `inlineExtraPanicCost` | 1 | panic 几乎不惩罚 |
| `inlineExtraThrowCost` | 80 | 内联 `runtime.throw` 无收益，直接吃满预算 |
| `inlineBigFunctionNodes` / `inlineBigFunctionMaxCost` | 5000 / 20 | 大函数里只允许很便宜的被调方 |
| `inlineClosureCalledOnceCost` | 800 | 闭包只被调用一次 → 放宽 10 倍 |
| `inlineHotMaxBudget` | 2000 | PGO 热函数预算 |

`-l` 档位（原文）：`0: disabled`、`1: 80-nodes leaf functions, oneliners, panic ... (default)`、
`4: allow non-leaf functions`。

### 5. 结构闸：与预算无关的硬阻断

源码列出的"太毛糙、连预算都不看"的构造：**闭包、defer、recover、select、go 语句**，
以及 `//go:noinline`、没有函数体。特别值得注意的是
**`//go:uintptrescapes` 也阻断内联** —— 理由是原文那句：*since the escape information will be lost during inlining*。
也就是说，内联**会改变逃逸结论**，编译器必须对这类函数保守处理。

## 对比 / 选型

| 维度 | Go（静态、跨函数） | C/C++（无） | Java JIT（分层） | Rust（借用检查） |
| --- | --- | --- | --- | --- |
| 决策时机 | 编译期，`-gcflags=-m` 可查 | 靠人 | 运行期 + 逃逸分析 | 编译期，类型系统强制 |
| 栈分配判定 | 图反向传播 + tag | 人写 `malloc`/栈变量 | JIT 标量替换 | 所有权/生命周期 |
| 不确定性 | 有（内联一改就变） | — | 有（分层编译） | 无 |
| 典型建议 | 别过度关注，先写对 | 手动优化 | 预热后再看 | 无需关注 |

## 环境准备

- 操作系统：任意（模型与平台无关）
- Python：3.8+（实测 3.13.12）
- Go：仅 `go/` 目录需要；想亲手复现 `-m` 输出需要 1.24+

## 运行方式

### Python（含全部断言，推荐先跑这个）

```bash
cd python
python3 main.py     # 55 项断言，退出码 0 表示全绿
```

### Go

```bash
cd go
go run .            # main.go + escape_graph.go，同一批断言
```

### 亲手看真实编译器的结论（需要 Go 工具链）

```bash
go build -gcflags='-m' ./...         # 逃逸分析结论
go build -gcflags='-m -l' ./...      # 关掉内联，对比逃逸结论的变化
go build -gcflags='-m -l=4' ./...    # 放开非叶子内联
```

## 关键代码片段

`python/escape_graph.py` —— 反向传播的不动点：

```python
def solve(self):
    changed = True
    while changed:
        changed = False
        for dst, src, derefs in self.edges:
            if dst.escapes and derefs <= 0 and not src.escapes:
                src.escapes = True     # 权重 <= 0 才传播「指针本身」的逃逸
                changed = True
    return self
```

`python/inline_budget.py` —— 两道闸（预算闸 + 结构闸）：

```python
def can_inline(nodes=1, calls=0, param_calls=0, panics=0, throws=0, blockers=(),
               is_leaf=True, debug_l=1, caller_nodes=0, closure_called_once=False):
    cost, reason = inline_cost(nodes, calls, param_calls, panics, throws, blockers)
    if reason:
        return False, reason                       # 结构闸优先，不看预算
    if debug_l == 0:
        return False, "-l=0 完全关闭内联"
    if debug_l == 1 and not is_leaf:
        return False, "-l=1（默认）只内联叶子函数"
    limit = 20 if caller_nodes >= BIG_FUNC_NODES else BUDGET
    if closure_called_once:
        limit = 800
    if cost > limit:
        return False, "function too complex: cost %d exceeds budget %d" % (cost, limit)
    return True, "cost %d <= budget %d" % (cost, limit)
```

## 性能与边界

- **预算量纲**：80 是 AST 节点数级别的近似，不是字节数也不是指令数；因此**升级 Go 版本、
  改一行代码都可能改变内联结果**。不要把 demo 里的具体数字当成跨版本不变的结论。
- **一次调用就 57**：意味着默认档位下「函数体 + 一次调用」的可用节点数只剩 23，
  这是官方"默认最多内联一个调用"的来源。
- **内联的收益**：省掉调用开销 + 打开常量传播/逃逸改善；**代价**：二进制膨胀、
  编译时间、`-m` 输出更难读。
- **本 demo 未覆盖**：PGO 的 `hotBudget = 2000` 具体触发条件、`inlheur` 的启发式评分、
  真正的 SSA 层重写；这些只列出了常量与出处。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 「我传的是指针，为什么没逃逸」 | 权重 ≥ 1（`= *p`）或 tag 显示参数干净 | 用 `-gcflags=-m` 看结论，别凭直觉 |
| 加了 `//go:noinline` 后逃逸结论变了 | 内联会暴露更多事实；关掉内联就退回保守结论 | 需要稳定结论时同时固定 `-l` |
| 循环里 `&x` 被多个 goroutine 共享 | 不逃逸时每轮复用**同一槽位** | 显式在循环体内 `x := x` 或改用切片 |
| 用 `zip()` 比较两张表 | `zip` 静默截断 | 先比长度再逐项比 |
| 断言里写「权重表查字典」 | 查表等于把答案抄进断言 | 改为从表达式文本**重算**权重（本 demo 的 `derefs_of`） |

## 参考资料（实际阅读过的权威来源）

- [cmd/compile/internal/escape/escape.go（Go 1.24.0）](https://raw.githubusercontent.com/golang/go/go1.24.0/src/cmd/compile/internal/escape/escape.go) — 位置图、`derefs` 权重表（含 `p = **&**&q` 为 2）、两条不变量、"flow/path/context 不敏感"的说明、parameter tag。
- [cmd/compile/internal/inline/inl.go（Go 1.24.0）](https://raw.githubusercontent.com/golang/go/go1.24.0/src/cmd/compile/internal/inline/inl.go) — 文件头 `-l` 档位说明、`inlineMaxBudget=80` / `inlineExtraCallCost=57` / `inlineParamCallCost=17` / `inlineExtraPanicCost=1` / `inlineExtraThrowCost` / `inlineBigFunctionNodes=5000` / `inlineBigFunctionMaxCost=20` / `inlineClosureCalledOnceCost=800` / `inlineHotMaxBudget=2000`，以及 `tooHairy` 的报错文案。
- [Go Wiki: Compiler And Runtime Optimizations](https://go.dev/wiki/CompilerOptimizations) — 逃逸分析"跨函数跨包但常常放弃"的说明、`-gcflags -m` 用法、内联规则清单（节点数 < 预算 80、不含闭包/defer/recover/select、未被 `go:noinline` 或 `go:uintptrescapes` 标记、必须有函数体）。
