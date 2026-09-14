# 报文序列对齐（Needleman-Wunsch / Smith-Waterman）

> 协议逆向 NetT 路线的第一步工具：把同类型报文当"生物序列"做动态规划对齐，**不变段 = 常量字段（魔数/版本/命令码），可变段 = 变长字段（长度/数据/校验和）**。该思路由 Beddoe《Network Protocol Analysis Using Bioinformatics Algorithms》（2004）首次系统化，后续 Netzob / Netplier / PIP 均以此为骨架。

## 一、简介

- **Needleman-Wunsch（NW，1970）**：全局对齐——两条序列**首尾强制对齐**，适合"同类型报文、长度接近"的场景（同 message class 内对齐）。
- **Smith-Waterman（SW，1981）**：局部对齐——允许丢弃两端对不上的部分，只保留最高分片段，适合"长度差异大 / 只想找公共头部"的场景。
- 对齐后把 N 条报文逐列（column-wise）比对：一列上所有报文字节相同 → 常量；不同 → 变量。常量段连成 run 即得到字段边界假设。

## 二、原理详解

### 2.1 NW 全局对齐（动态规划）

设序列 `x[1..M]`、`y[1..N]`，打分：match `+m`、mismatch `-s`、gap `-d`。

**初始化**（端部 gap 也要罚分，保证全局）：

```
F(0,0)=0, F(0,j)=-j*d, F(i,0)=-i*d
```

**递推**（MIT OCW 6.096 Lecture 5）：

```
F(i,j) = max( F(i-1,j-1) + s(x[i],y[j]),   # DIAG：对齐 x[i]<->y[j]
              F(i-1,j)   - d,              # LEFT ：x[i] 对 gap
              F(i,j-1)   - d )             # UP   ：y[j] 对 gap
```

**回溯**：从 `F(M,N)` 沿指针走回 `(0,0)`，DIAG 输出一对字符，LEFT/UP 输出一侧 gap。

### 2.2 SW 局部对齐

只改三处（同上 Lecture 5）：

1. 初始化全 0：`F(0,j)=F(i,0)=0`；
2. 递推多一个候选 `0`（分数为负就"重开一段"）；
3. 回溯起点是**全矩阵最大值**（不一定是右下角），回溯到 0 为止。

### 2.3 仿射 gap（Gotoh 1982，本 demo 未实现、只留接口说明）

线性 gap 会让"一个长 indel"被拆成多次独立罚分。仿射模型 `W(l)=γ+δ×(l-1)`（开 gap 罚 γ、延续罚 δ，典型 −12/−1）需要三个矩阵 M/I/D 分别递推。报文对齐中变长 payload 一次插删就是几十字节，**不上仿射 gap 会对齐面严重碎片化**——这是把生物算法搬来必须补的账。

### 2.4 从对齐到字段切分（consensus 推进）

1. 取第一条报文为参考 `ref`；
2. 其余每条与 `ref` 做 NW 对齐，得到含 `-` 的对齐串；
3. 逐列统计：该列出现过的字节集合大小 ==1 → 常量列，否则变量列（`-` 也算一种取值）；
4. 常量 run / 变量 run 即字段边界假设，再交给语义推断（长度？校验和？见同目录其它 demo）。

## 三、NW vs SW 对比

| | NW | SW |
| --- | --- | --- |
| 对齐范围 | 全长 | 最高分子串 |
| 回溯起点 | F(M,N) 右下角 | 全矩阵 argmax |
| 分数为负 | 允许 | 被 0 截断 |
| 报文场景 | 同类报文互相 | 长度悬殊 / 只找公共头 |
| 风险 | 端部垃圾互相对齐 | 公共头外的结构信息丢失 |

## 四、环境与运行

- Python ≥3.8：`python nw_sw.py`
- Go ≥1.21：`go run nw_sw.go`
- 输出：两条报文对齐示例（含 traceback）、合成协议 5 条报文的逐列分类与字段段切分、自检断言。

## 五、关键代码

```python
def nw_align(a, b, m=2, s=-1, d=-2):
    F = [[0]*(len(b)+1) for _ in range(len(a)+1)]
    P = [['']*(len(b)+1) for _ in range(len(a)+1)]
    for i in range(1, len(a)+1): F[i][0], P[i][0] = F[i-1][0]+d, 'U'
    for j in range(1, len(b)+1): F[0][j], P[0][j] = F[0][j-1]+d, 'L'
    for i in range(1, len(a)+1):
        for j in range(1, len(b)+1):
            cand = [(F[i-1][j-1] + (m if a[i-1]==b[j-1] else s), 'D'),
                    (F[i-1][j] + d, 'U'), (F[i][j-1] + d, 'L')]
            F[i][j], P[i][j] = max(cand)
    # traceback from (len(a), len(b)) ...
```

## 六、性能边界

- 时间/空间均 O(M×N)；报文按字节算 M、N ≤ ~1.5KB → 一次对齐 ~225 万格，纯 Python 毫秒~几十毫秒级，可接受。
- N 条报文两两对齐是 O(N²) 次调用；推进式（star alignment，全部对第一条）降为 O(N)，是工程默认。NW 原始论文用的不是 max 而是求和最大化，现代实现一律用 max。
- 字节字母表 256，不压缩空间（位打包省 1/8 意义不大）。

## 七、注意事项与常见坑

1. **回溯并列任取其一但必须稳定**：max 相同时 Python `max` 取第一个候选，Go 手写循环也固定优先级，否则同输入两次跑出不同对齐，字段切分不可复现。
2. **gap 罚分过小 → 变长字段被拆碎**；过大 → 该对齐成变量的字节被强行 mismatch 对齐。经验：match 2 / mismatch −1 / gap −2 起步，观察对齐面再调。
3. **先聚类后对齐**：不同 message class 的报文互相对齐，常量段会被稀释。必须先按方向/长度/首字节粗分簇。
4. **SW 用于"找一个公共头"时，分数矩阵要允许 mismatch 负分**，否则 SW 退化为最长公共子串的近似（mismatch=0 会让它倾向拉长）。
5. `-` 填充列在 consensus 统计中要单列一类：某一侧"整段缺失"往往意味着可选字段，与"字节值不同"语义不同。

## 八、参考资料（实际读过）

- [Sequence Alignment and Dynamic Programming — MIT OCW 6.096 Lecture 5](https://ocw.mit.edu/courses/6-096-algorithms-for-computational-biology-spring-2005/01f55f348ea1e95f7015bd1b40586012_lecture5.pdf) —— NW 初始化/三分量递推/指针回溯、SW 的三处修改（初始化为 0、加 0 候选、全矩阵找最大值）、线性 vs 凸 gap 讨论
- [Protein Sequencing — ScienceDirect Topics](https://www.sciencedirect.com/topics/medicine-and-dentistry/protein-sequencing) —— 仿射 gap `W=γ+δ×(l-1)`（Gotoh 1982）、−12/−1 惯例、端部 gap 免罚、NW 适合近缘等长序列 / SW 适合远缘或长短不一
- [MCB112 w04 section — Harvard](http://mcb112.org/w04/w04-section.html) —— 手填 DP 矩阵的完整数值例子（全局例 X=AATC/Y=GATCT 得分 0.5；局部例 X=ATTG/Y=GATTCA 得分 3），用于核对自测实现
- [Polyglot: Automatic Extraction of Protocol Message Format — CCS'07](https://www.cs.ucr.edu/~heng/pubs/polyglot-ccs07.pdf) —— 参考文献第 [9] 条给出 Beddoe 2004 生物信息学协议分析出处的上下文；NetT/ExeT 分工
