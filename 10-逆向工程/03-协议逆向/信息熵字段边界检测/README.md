# 信息熵字段边界检测

> 协议逆向 NetT 路线中"字段切分"的另一条主干：**不做序列对齐，直接用信息论度量找边界**。代表工作 BinaryInferno（NDSS 2023）：给定一组同格式报文，用探测器集成（detector ensemble）推断字段边界与语义，其中**香农熵探测器**负责"高熵区 / 低熵区的分界"——加密或压缩 payload 是高熵区、定长头与常量字段是低熵区。在 AWRE / FieldHunter / Nemesys / Netplier / Netzob 五个同类工具中，BinaryInferno 以平均 precision 0.69 / recall 0.73 / FP 0.04 综合最优。

## 一、简介

三种互补的熵视角，本 demo 全部实现：

1. **纵向熵（跨报文按列）**：同一格式的一组报文按最长长度对齐，逐列统计字节分布的熵。常量字段（魔数/版本）列熵 = 0；单调计数器、长度字段低熵；随机 nonce/校验和高熵。BinaryInferno 的常量探测器（`inferconst`）本质就是它的退化版（列只有一种取值）。
2. **横向熵（单报文滑窗）**：在一条报文内滑动窗口计算局部熵，找"熵值跳变点"。BinaryInferno 的规则：**相邻区域熵差超过 1 bit（信息量翻倍）即划一条边界**（INTERVAL `|`）。
3. **序列启发式（sequenceHeur）**：某列取值跨报文单调递增的概率高 → 计数器/序列号字段。这是熵之外最便宜的语义标签。

## 二、原理详解

### 2.1 香农熵

对字节序列 `xs`，值域 0..255：

```
H(xs) = -Σ p(v)·log2(p(v))   bits/byte,  v 取遍出现过的字节值
```

均匀随机字节 → 8 bits；全同字节 → 0。注意熵是对"分布"的度量：样本越少估计越偏低（2 个样本的熵最多 1 bit），**跨报文列熵要求报文数足够多（经验 ≥8）才稳**。

### 2.2 纵向列熵与边界

- 列 `k` 的分布 = 第 k 字节在所有 N 条报文上的取值直方图；
- 熵 0 → 常量列；熵低但非 0 → 半结构化（枚举型 type 字段）；熵接近 8 → 随机数据/加密段；
- 相邻列熵差 `|H(k) - H(k-1)| > 1` bit → 候选边界。

### 2.3 横向滑窗熵与边界（长流上的补充手段）

- BinaryInferno 的"**相邻区域熵差 > 1 bit（信息量翻倍）→ 置一条 INTERVAL `|` 边界**"按其源码文档是作用在**纵向熵剖面**（相邻字节位置的跨报文熵）上的——本 demo 的 `classify_columns` 即忠实实现；
- 单条报文内的滑窗熵（`entropy_boundaries_stream`）只适合**长流**（如整段 TLS record、文件）：明文头 ≈4.3 bits/byte vs 随机体 6~8 bits/byte，ΔH 可过 1 bit；
- 两个硬限制：窗口熵上限 `log2(w)`（w=64 最多 6 bits，永远达不到 8）；边界定位精度 ±w/2。w 太小会把文本与随机都压到同一上限以下而失效。

### 2.4 序列启发式

列取值序列 `v[1..N]`（按报文到达顺序）中递增相邻对的比例：

```
P = #{i : v[i+1] > v[i]} / (N-1)
```

P 高 → 计数器/序列号（报文按时间产生，序列号递增）；P ≈ 0.5 且高熵 → 随机。BinaryInferno 用它给"长度/序列号"贴语义标签。

### 2.5 探测器集成思路（本 demo 简化为规则投票）

BinaryInferno 完整系统是"多探测器 → SIGMA 假设 → 黑板(blackboard)合成 → 冲突消解(deconflict)"，各探测器带置信权重（熵边界用 WCAT3 中等置信）。本 demo 只保留三探测器 + 简单合并，足以演示边界推断骨架。

## 三、对比：熵法 vs 序列对齐法

| | 熵法（本 demo） | 序列对齐（同目录 `报文序列对齐`） |
| --- | --- | --- |
| 输入要求 | 报文同格式、按列大致对齐（同长度或可填充） | 任意长度，对齐自己找 |
| 变长字段 | 纵向列熵会被位移污染，需先对齐或按长度分层 | 强项（indel 显式建模） |
| 计算量 | O(N×L) 线性 | O(N×L²) DP |
| 语义线索 | 高熵/低熵/单调 = 加密/常量/计数器 | 无（只切边界） |
| 典型用法 | 先熵粗切 → 再对齐细切 | 样本少但长度整齐时单独可用 |

## 四、环境与运行

- Python ≥3.8：`python entropy_fields.py`
- Go ≥1.18：`go run entropy_fields.go`
- 输出：合成协议报文的列熵曲线、常量/变量分类、滑窗边界命中、序列启发式标签、字段段汇总。

## 五、关键代码

```python
def shannon(xs):
    h, n = 0.0, len(xs)
    for c in Counter(xs).values():
        p = c / n
        h -= p * math.log2(p)
    return h

def col_entropies(msgs):
    L = max(map(len, msgs))
    return [shannon([m[k] if k < len(m) else None for m in msgs]) for k in range(L)]
# 边界: |H[k] - H[k-1]| > 1 bit (BinaryInferno 规则, 作用于纵向剖面)
```

## 六、性能边界

- 纵向熵 O(N×L)、滑窗熵 O(L×w)：一千条 1KB 报文秒级；无二次方复杂度，是熵法相对对齐法的最大工程优势。
- 熵估计偏差：列样本 N < 8 时最高熵被压到 log2(N)，**会系统性低估短样本列的熵**——跨列比较时只要 N 一致就不影响相对排序，但"绝对阈值 8 bits"永远达不到。
- 滑窗 w 太小 → 熵抖动、边界密集误报；太大 → 边界被抹平（定位偏差约 w/2）。

## 七、注意事项与常见坑

1. **变长字段会让纵向列熵失真**：后面的字段整体位移，"列"不再对应同一字段。必须先按长度分桶或先对齐。本 demo 的合成报文故意用等长头 + 变长尾演示该效应。
2. 熵差 1 bit 阈值来自 BinaryInferno 的实现选择，不是理论常数；数据集噪声大时要调。
3. 单调启发式只对"报文顺序 = 生成顺序"的采集成立；乱序抓包会把计数器列错判成随机。
4. 加密 payload 的熵 ≈ 压缩数据的熵 ≈ 8 bits，**熵分不出"加密"和"压缩"**，需要结合熵 + 可解压性双指标。
5. Go 版列熵把"报文越界"记为哨兵值 256，与真实字节 0..255 区分——gap 在分布里占位会把熵抬高，和 Python 版 None 语义一致。

## 八、参考资料（实际读过）

- [BinaryInferno: A Semantic-Driven Approach to Field Inference for Binary Message Formats — NDSS 2023](https://www.eecs.tufts.edu/~chandler/BinaryInferno2023Chandler.pdf) —— 探测器集成架构（常量/浮点/时间戳/长度/熵边界/重复模式）、SIGMA 假设与黑板合成、10 协议评测 precision 0.69 / recall 0.73 / FP 0.04 与五个竞品对比
- [BinaryInferno Entropy and Edge Case Detectors — DeepWiki（源码文档）](https://deepwiki.com/binaryinferno/binaryinferno/4.4-entropy-and-edge-case-detectors) —— 熵边界规则"相邻区域熵差 > 1 bit 置 INTERVAL 边界"、常量探测 `inferconst`、序列启发 `sequenceHeur`（单调递增概率）、条纹模式
- [BinaryInferno Detection System — DeepWiki](https://deepwiki.com/binaryinferno/binaryinferno/4-detection-system) —— 探测器清单（`inferentropyboundBE/LE`、`inferseq8/32`、`inferlength` 等）、并行执行与置信权重（熵边界 WCAT3）
