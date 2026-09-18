# 污点分析的精确性阶梯：为什么 SAST 不可能零误报

**SAST（静态应用安全测试）**的污点分析回答一个问题：**不可信输入（source）能不能流到危险操作（sink）**。
本 demo 用一条极小 IR 实现三档精度——**flow-insensitive / flow-sensitive / path-sensitive**——
并在同一批程序上把它们各自的**误报集与漏报集**量化出来，说明「误报率」不是一个可调参数，
而是**分析抽象层次的必然产物**。

判定语义对齐 [CodeQL 的 DataFlow / TaintTracking 两套库](https://codeql.github.com/docs/codeql-language-guides/analyzing-data-flow-in-python/)：

| CodeQL 概念 | 本 demo 对应 |
| --- | --- |
| `isSource` | `("source",)` 表达式 |
| `isSink` | `("sink", label, var)` 语句 |
| `isBarrier` | `("sanitize", v)` 表达式 |
| `DataFlow`（只跟保值步骤） | `mode="flow"` |
| `TaintTracking`（追加非保值步骤） | `mode="taint"` |

## 原理详解

### 1. 保值 vs 非保值：DataFlow 与 TaintTracking 的分界线

CodeQL 文档的原话是：local/global taint tracking 是 data flow 再加上
**non-value-preserving flow steps**。典型例子就是字符串拼接：

```python
y = "SELECT * FROM t WHERE id=" + x   # x 的值并不等于 y 的值，但 y 被 x 污染
```

`x` 的值并没有「原封不动」变成 `y`，所以**纯 DataFlow 看不见这条边**；
但站在攻击者视角，控制 `x` 就能控制 `y` 的结构，所以 **TaintTracking 必须看见**。
本 demo 的 P2 正是这条边：`mode="flow"` 漏报、`mode="taint"` 命中。

代价也写在 CodeQL 文档里：global data flow **比 local 精度低**，而且更耗时间内存。
精度与开销是同一个旋钮的两端。

### 2. 三档精度分别丢弃了什么信息

| 精度 | 保留 | 丢弃 | 典型误报 |
| --- | --- | --- | --- |
| flow-insensitive | 约束集合 | **语句顺序、分支结构** | 「先读后写」被当成有流（P1） |
| flow-sensitive / path-insensitive | 语句顺序 | **分支间的相关性** | 条件与状态被拆开合并（P4） |
| path-sensitive | 顺序 + 可行路径 | —— | 仍漏报隐式流（P5） |

**flow-insensitive 的并集语义是个坑**：它必须取「所有赋值的 OR」而不是「后写覆盖」。
用覆盖语义时，同一变量存在两个冲突赋值（P4 里 `x` 既是 source 又是 sanitize 结果）
会在不动点迭代里 **True/False 振荡、永不收敛** —— 本 demo 的 Python 版第一版就死在这里。

**path-insensitive 的合并丢失相关性**是 P4 的核心：

```
x = source()
ok = False
if cond:
    x = sanitize(x)      # 只有走这条路，ok 才会变成 True
    ok = True
if ok:
    sink(x)              # 可行路径上 x 必然已被净化
```

汇合点把两条分支的环境做并集，得到 `x` 可能脏、`ok` 不确定（True 与 False 合并成 ⊥）。
等到第二个 `if ok` 时，「`ok==True` 蕴含 `x` 已净化」这个**相关性已经不存在了**，
于是报出误报。path-sensitive 逐条路径枚举、只走可行分支，把它排除掉。
把 `else` 分支也改成置 `ok=True`（P4b），相关性被打掉，此时确实存在漏洞路径，三档都该报。

### 3. 隐式流：加精度也补不上的漏洞

P5 里数据不是通过赋值、而是通过**控制流**传递：

```
x = source()
if x: admin = True
else: admin = False
y = admin        # y 的取值完全由 x 决定，但没有任何赋值边
sink(y)
```

三档分析**全部漏报**，因为污点是沿 `def-use` 边传播的，控制依赖不在图里。
要覆盖它得做**控制依赖分析**，边上再叠一层，代价更高、误报更多。
所以「零误报零漏报」在**可判定性**上就不可达——这也是本目录 README「待研究」第一条。

### 4. 结果总表

```
program                    insensitive   sensitive     path          truth
P1_read_before_write       S             -             -             -      ← FI 误报
P2_concat                  S             S             S             S
P3_sanitizer               -             -             -             -
P4_correlated_flag         S             S             -             -      ← FI/FPI 误报
P4b_broken_correlation     S             S             S             S
P5_implicit_flow           -             -             -             S      ← 全部漏报
P6_true_positive           S             S             S             S
```

报告数单调递减（4 → 3 → 2），但**漏报数不变**（始终 P5 一个）。
这就是精度-召回的真实形状：加精度主要是在**砍误报**，砍不动漏报。

## 代码结构

三个语言实现同一套 IR 与同一批程序，输出必须一致：

| 文件 | 内容 |
| --- | --- |
| `taint_analysis.py` | IR、三档分析、测试程序集、`run_all()` |
| `taint_analysis_selftest.py` | 断言误报集 / 漏报集（23 项） |
| `taint_analysis.go` + `taint_programs.go` | Go 版（程序集单独成文件以满足 ≤300 行） |
| `taint_analysis.c` | C 版；分支用 **thenN/elseN 偏移**线性编码 |

C 版值得单独看一眼：没有嵌套数组，IF 语句只记两个块的语句条数，
执行时传 `[start, end)` 区间递归即可，`after` 位置由 `i+1+thenN+elseN` 算出。

## 运行

```bash
python taint_analysis.py            # 打印三档精度对照表
python taint_analysis_selftest.py   # 23 项断言
go run taint_analysis.go taint_programs.go
cc -o taint taint_analysis.c && ./taint
```

## 参考资料

- <https://codeql.github.com/docs/codeql-language-guides/analyzing-data-flow-in-python/> —— CodeQL 数据流/污点跟踪：local vs global data flow、`ConfigSig` 的 `isSource`/`isSink`/`isBarrier`、非保值步骤、预置 source（`RemoteFlowSource`）与 sink（`Concepts::SqlExecution` 等）
