# 控制流图恢复与结构化反编译

## 简介

反编译器的第二步（第一步是反汇编）是**控制流分析**：把线性指令流切成基本块，连成控制流图（CFG），再从这张图里恢复出 `if` / `while` / `for` / `break` 这些**原始源码里根本不存在的**高层结构——二进制里只有跳转。本 demo 用一个玩具指令集走完整条链路：**切基本块 → 连边 → 算支配树 → 找回边与自然循环 → 结构化成 C 伪码**。

关键概念：

- **基本块（basic block）**：只能从第一条指令进入、只能从最后一条指令离开的直线代码段
- **CFG**：节点 = 基本块，边 = 跳转；另有额外的 entry / exit 节点
- **支配（dominate）**：从 entry 到 N 的**每一条**路径都经过 M，则 M 支配 N
- **立即支配者 idom**：最近的严格支配者；每个可达节点有唯一的 idom（entry 除外）
- **回边（back edge）**：指向 DFS 祖先的边，是循环的标志；**循环头 = 被回边指向的支配者**
- **可归约（reducible）**：所有"回退边"都是回边 → 等价于"循环只有唯一入口"

历史背景：1960 年代结构化程序设计（Böhm–Jacopini）证明了任意流程图都可用顺序/选择/循环表达，但**编译后的二进制丢掉了这个结构**——只剩跳转。1994 年 Cristina Cifuentes 的 dcc 首次把"任意 CFG 结构化"做成可用算法，奠定了今天 Hex-Rays、Ghidra 反编译器的控制流恢复基础。

## 原理详解

### 1. 基本块划分（leader 算法）

```
leaders = { 第一条指令 } ∪ { 所有跳转的目标 } ∪ { 跳转指令的下一条指令 }
每个 leader 到下一个 leader 之前 构成一个基本块
```

判定性质等价形式：**跳转目标开始一个块，跳转指令结束一个块**。块内没有分支、没有跳转目标。

```
指令流                          基本块
0x00 loadi 3 / 0x02 muli 4 / 0x04 jnz 0x0C   ┐ B0（leader 0x00）
0x06 addi 5  / 0x08 out 0  / 0x0A jmp 0x10   ┤ B1（leader 0x06 = jnz 的下一条）
0x0C subi 1  / 0x0E jmp 0x04                 ┤ B2（leader 0x0C = jnz 的目标）
0x10 halt                                    ┘ B3（leader 0x10）
```

### 2. 连边与 entry / exit

每条分支产生一条边；无分支的块产生一条到"下一条指令所在块"的边。另外补两个虚拟节点：**entry**（指向程序入口块）与 **exit**（所有可能结束的块指向它）。若某块从 entry 不可达 → 死代码，可安全删除；若 exit 从 entry 不可达 → 存在死循环。

### 3. 支配关系与支配树

```
M dominates N  ⟺  所有 entry→N 的路径都经过 M
M idom N       ⟺  M 是 N 的严格支配者中最接近的那个
```

支配关系自反、传递、反对称。**每个可达节点有唯一 idom**，因此 `idom` 关系构成一棵以 entry 为根的树 —— **支配树**。

计算：朴素迭代数据流法是 O(V²)，工程实现用 **Lengauer–Tarjan 1979**，复杂度 **O(E·α(E,V))**（α 是 Ackermann 反函数，实际近似常数）。迭代版本的核心是"**相交即上溯**"：

```python
def intersect(a, b, idom, rpo_index):
    while a != b:
        while rpo_index[a] > rpo_index[b]:
            a = idom[a]
        while rpo_index[b] > rpo_index[a]:
            b = idom[b]
    return a
```

即：两个节点沿支配树向上走，遇到的第一个公共祖先就是它们的 idom 候选。

### 4. 回边与自然循环

- **回边**：边 `(n → h)` 且 `h` 支配 `n`（注意：DFS 回边只是"指向祖先"，支配性定义更严格）
- **自然循环**：由回边 `(n → h)` 定义，节点集合 = `{h} ∪ {所有能不经过 h 到达 n 的节点}`

```
    ┌──────────────┐
    ↓              │
 [B0] → [B1] → [B2]┘   ← (B2 → B0) 是回边 ⟺ B0 dominates B2
```

**循环头（loop header）** = 被回边指向的那个支配者，它支配循环体内所有块。同一个块可以是多个循环的头（嵌套）；若循环有多个入口则**没有唯一的循环头**，这是不可归约的标志。

在循环外再套一个空的 **pre-header** 块（由循环头的非回边前驱指向它，它再指向循环头）是编译器的常规做法，方便做循环不变量外提（LICM）。

### 5. 可归约性

**CFG 可归约 ⟺ 所有回退边（retreating edge）都是回边**，其余边称为前向边。前向边构成 DAG，回边总是回到已经走过的块。

- 结构化语言（只用 `if/for/while/break/continue`，不用 `goto`）产生的 CFG **一定可归约**；`goto` 可能产生不可归约图，但**并非所有 goto 都如此**；优化器也可能造出不可归约图（如 jump threading）
- 大量循环优化只对可归约图成立，因此这是编译/反编译里的关键分界线

### 6. 结构化：从 CFG 恢复高层结构

Cifuentes 的算法先定义一组**通用结构**，再自顶向下把 CFG 折叠掉：

| 通用结构 | 对应源码 | 识别要点 |
| --- | --- | --- |
| 2 路条件 | `if` / `if-else` | 头节点两个分支，取**follow 节点** = 从两条分支出发都能到达的第一个汇合点 |
| n 路条件 | `switch` / `case` | 多个并列分支共享一个 follow 节点 |
| 前测循环 / 后测循环 | `while` / `do-while` | 退出判断在循环体**之前** / **之后** |
| 无限循环 | `for(;;)` | 无退出边，只有 `break` |
| 多出口循环 | 带 `break` 的循环 | 一个真实出口 + 若干 `goto` 出口 |

两条工程经验（来自原论文）：① **结构化失败时优先选"多出口循环"而不是"多入口循环"**——多入口循环更难理解，且会把图变成不可归约的；② 只有在无法用上述结构表达时才退化为 `goto`。

**follow 节点的计算**：对 2 路条件，头节点 H 的两个后继出发，各自做 DFS，**第一个共同到达的节点**即 follow 节点；随后把 H..follow 之间的子图折叠成一个结构节点，递归处理。区间分析法（interval analysis）是另一条路线：不断找出"单入口且最多含一个循环"的区间并折叠成单节点，直到图不可再折叠；折叠顺序**天然决定循环的嵌套层级**。

## 对比 / 选型

| 维度 | 线性扫描 | 递归下降 |
| --- | --- | --- |
| 原理 / 优点 | 从入口顺序解码，不跟随跳转；快、实现简单、覆盖全部字节 | 沿跳转边跟随，遇已访问即停；不把数据当代码，可处理间接跳转后的区域 |
| 缺点 | **数据会被误当代码**；内联数据错位后续全崩 | 遗漏未被引用的代码（如仅经跳转表到达的函数） |
| 组合用法 | 现代反汇编器默认两者结合：主路径递归下降 + 跳表/异常表补全 | — |

| 结构恢复路线 | 代表 | 特点 |
| --- | --- | --- |
| 自顶向下模式匹配 | Cifuentes dcc | 通用结构集 + follow 节点；实现直接，靠 goto 兜底 |
| 区间分析 / 高级 IR 重建 | Cifuentes 1999 / Stitt & Vahid 2007；Hex-Rays Microcode / Ghidra P-code | 折叠顺序自然给出嵌套层级；后者先在 IR 上做 SSA、类型推断再结构化，质量最高、复杂度也最高 |

## 环境准备

- 操作系统任意；Python 3.8+（纯标准库）；Go 1.18+

## 运行方式

```bash
python3 cfg_builder.py            # 全流程：切块 → 连边 → 支配树 → 自然循环 → 结构化
cd go && go run dominator.go cfg_graph.go   # 支配关系求解器与循环识别
```

## 关键代码片段

```python
# cfg_core.py —— 迭代式支配者求解（Cooper-Harvey-Kennedy 风格）
def compute_idom(blocks, entry):
    rpo = reverse_postorder(blocks, entry)   # 逆后序
    order = {n: i for i, n in enumerate(rpo)}
    idom = {entry: entry}
    changed = True
    while changed:
        changed = False
        for n in rpo:
            if n == entry:
                continue
            preds = [p for p in blocks[n].preds if p in idom]
            if not preds:
                continue
            new = preds[0]
            for p in preds[1:]:
                new = dom_intersect(new, p, idom, order)  # core：沿支配树上溯找交点
            if idom.get(n) != new:
                idom[n] = new
                changed = True
    return idom
```

```python
# cfg_struct.py —— 2 路条件结构化的核心：求 follow 节点（两分支的第一个汇合点），
# 再把 H..follow 之间的子图折叠成一个结构节点，递归处理剩余部分。
def find_follow(blocks, a, b, depth):
    common = arm_reachable(blocks, a) & arm_reachable(blocks, b)
    common.discard(a); common.discard(b)
    if not common:
        return None                       # 无汇合点 = 至少一侧是出口
    return min(common, key=lambda n: (depth.get(n, 1 << 30), n))
```

## 性能与边界

- 基本块划分：O(n)，n 为指令数；自然循环：每个回边一次 DFS，总计 O(E)
- 支配树：朴素迭代 O(V²)；**Lengauer–Tarjan O(E·α(E,V))**；工程常用 Cooper-Harvey-Kennedy 迭代版，实测接近线性
- **通用 CFG 的结构化是 NP-完全问题**（循环恢复 / 无 goto 表示的存在性）；实际反编译器用贪心模式匹配，对编译器生成的常见形态准确率很高
- 反编译整体是**不可判定**的（与停机问题等价：死代码检测、循环不变量判定都不可判定），工程上只能求"够好"

## 注意事项与常见坑

1. **把数据当代码** —— 现象：线性扫描在中途解出荒谬指令。原因：`__TEXT,__const`、跳表、对齐填充都在 `.text` 相邻区域。规避：只看标记为"纯指令"的节（Mach-O 的 `S_ATTR_PURE_INSTRUCTIONS`），并结合递归下降 + 跳表解析。
2. **用 DFS 回边代替"支配性回边"** —— 现象：把某些前向边误判成循环。原因：DFS 回边只是"指向祖先"，而循环头必须**支配**回边源点。规避：先算 idom，再判 `h ∈ dom(n)`。
3. **忘记虚拟 entry/exit** —— 现象：多入口函数算不出 idom，或死循环检测失效。规避：显式加 entry/exit 节点，并补 `entry → exit` 边表示"程序可能不运行"。
4. **不可归约图套用结构化模板** —— 现象：结构恢复循环不收敛或产出语义错误的代码。规避：先判定可归约性；不可归约时退化用 `goto` 或先做节点分裂（node splitting）。
5. **忽略 follow 节点的"第一个汇合点"语义** —— 现象：`if` 的边界划错，`else` 分支被吞进 `if` 体内。原因：follow 必须是"从两个分支出发都能到达的**第一个**共同节点"。规避：按支配关系求最早汇合点，而不是"任意共同后继"。
6. **多入口循环** —— 现象：结构化成畸形代码。原因：多入口循环没有唯一循环头，必然导向不可归约。规避：优先按多出口循环处理，或对入口边做节点分裂。

## 参考资料（实际阅读过的权威来源）

- [Bytecode-Level Analysis and Optimization of Java Classes — Nystrom, Cornell（§2.1 Control flow graphs / §2.1.1 Dominators / §2.1.2 Loops）](http://www.cs.cornell.edu/nystrom/papers/nystrom-ms-thesis.pdf) —— 基本块的形式化定义（"flow of control enters a block only at its first instruction and exits only at its last"）、CFG 的 entry/exit 节点与 `entry→exit` 边、支配与严格支配的定义、idom 是"不被任何其他支配者支配的那个支配者"、支配树定义、**Lengauer–Tarjan O(E·α(E,V))** 复杂度与 α 为 Ackermann 反函数、循环头"支配循环内所有块"、可归约循环"唯一入口在循环头"、loop inversion 与 pre-header —— 本 README 第 1、3、4、5 节的定义全部依据此论文
- [Structuring Decompiled Graphs — Cristina Cifuentes（Springer LNCS 1994）](https://link.springer.com/content/pdf/10.1007%2F3-540-61053-7_55.pdf) —— 通用结构集的构成（前测循环 `while` / 后测循环 `repeat-until` / 无限循环、2 路条件 `if-then(-else)`、n 路条件 `case`）、"`for` 是 `while` 的特例故不作通用结构"、"goto 仅在无法用上述结构表达时使用"、"多出口循环的多出口用 goto 表达"、"结构化语言（不含 goto）产生的图必然可归约"、以及"**优先结构化为多出口循环而非多入口循环**（后者更难理解且会导出不可归约图）"的工程准则 —— 本 README 第 6 节直接依据此文
- [Control-flow graph / Reducibility / Dominance relationships（内容摘要）](https://handwiki.org/wiki/Control-flow_graph) —— 可归约性的判据（"all its retreating edges are back edges"、其余为 forward edges、前向边构成 DAG）、循环头 = 被 loop-forming back edge 指向的支配者、pre-header 的构造方式、critical edge（既非源块唯一出边也非目标块唯一边）需拆分、back edge 的 DFS 祖先定义 —— 用于校核第 4、5 节；该条目标注了维基原文引用段落，原文页直连被阻断
- [Control flow graph（含 reachability / domination / special edges 定义摘要）](https://en-academic.com/dic.nsf/enwiki/27857) —— 具体示例代码到 4 个基本块（A:0-1 / B:2-3 / C:4 / D:5）与边集合 `A→B, A→C, B→D, C→D` 的推导过程、"exit 从 entry 不可达 ⇒ 存在死循环"、"M 是 N 的最后支配者"这一 idom 的等价表述 —— 用于校核第 1、2 节与坑 3、4
- [New Decompilation Techniques for Binary Synthesis — Stitt & Vahid（TODAES 2007, §4.2-4.4）](https://www.cs.ucr.edu/~vahid/pubs/todaes07_binsynth.pdf) —— 区间分析法（interval analysis）恢复控制结构的过程（"an interval contains a maximum of one loop which must start at the head of the interval"，逐区间折叠直到不可再折叠，**折叠顺序决定循环嵌套层级**）、循环类型判定（pretested / posttested / endless / multi-exit 由出口位置决定）、以及反编译的固有局限（间接跳转可能导致整片区域甚至整个应用无法反编译）—— 本 README 第 6 节末与"性能与边界"一节依据此论文
- [Structuring program code — HP 专利 US2004/0154009（对 Cifuentes follow-node 算法的评述）](https://www.freepatentsonline.com/y2004/0154009.html) —— follow 节点 = "the first node of the structure where any two paths from the header meet"，以及指出 Cifuentes 原始算法在 forward-forward crossing 结构下会残留跨分支的跳转语句这一已知缺陷 —— 用于校核坑 5 与第 6 节 follow 节点语义
