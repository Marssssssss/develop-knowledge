# 混合检索与结果融合（BM25 + RRF）

## 1. 简介

稠密召回（向量）与稀疏召回（词面）各有所长，把它们的结果合起来是 RAG 里最实用的一步。融合看似简单，真正的坑在两个地方：**BM25 的分数不能直接相加**（量纲与分布都不同），**融合公式对分数尺度是否敏感**决定了你需不需要先归一化。

本 demo 按两份权威资料转写：

- **Apache Lucene** `BM25Similarity.java` —— 稀疏侧的官方打分实现，与教科书公式有若干不一致；
- **Cormack, Clarke & Büttcher**, *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods*, SIGIR 2009 —— RRF 的原始论文，`k = 60` 的出处与理由。

## 2. 原理

### 2.1 Lucene 的 BM25：三处和教科书不一样

**① idf 恒为正**

```java
return (float) Math.log(1 + (docCount - docFreq + 0.5D) / (docFreq + 0.5D));
```

分子 `N − n + 0.5` 恒为正，所以**即一个词出现在所有文档里，idf 也只是趋近于某个正数**（`df = N` 时是 `log(1 + 0.5/(N+0.5))`，N=1000 时约 0.0005），不会像 `log(N/df)` 那样变负。

**② tf 分子上没有 `(k1 + 1)`**

```java
// tf, computed as freq / (freq + k1 * (1 - b + b * dl / avgdl)) from:
```

教科书 BM25 是 `tf·(k1+1) / (tf + k1(1−b+b·dl/avgdl))`。Lucene 把 `(k1+1)` 省了——**它对所有文档是同一个常数因子，不影响排序**。代价是 Lucene 的分数不能直接和别人的 BM25 分数比大小。本 demo 断言了两者之比恰好是 `(k1+1)`。

**③ 打分用的是改写后的表达式**

```java
private float doScore(float freq, float normInverse) {
    // In order to guarantee monotonicity with both freq and norm without
    // promoting to doubles, we rewrite freq / (freq + norm) to
    // 1 - 1 / (1 + freq * 1/norm).
    return weight - weight / (1f + freq * normInverse);
}
```

官方注释写明动机：**在 float 下保证对 freq 与 norm 都单调**。`weight = boost * idf`。

其他参数：`k1 = 1.2`、`b = 0.75`、`discountOverlaps = true`、`k3 = -1`（禁用，查询词频线性生效）；启用时权重是 `((k3+1)·qtf)/(k3+qtf)`，上界 `k3+1`。

**④ 文档长度被压缩成 1 字节**

```java
private static final float[] LENGTH_TABLE = new float[256];
for (int i = 0; i < 256; i++) LENGTH_TABLE[i] = SmallFloat.byte4ToInt((byte) i);
```

只有 256 档，4 位尾数 ⇒ **相邻档之间的相对步长在 1/15 ~ 1/8 之间**（本 demo 实测 byte 199→200 相差 6.67%）。官方 `explain` 里还专门分了 `norm > 39` 的分支，因为那时长度已经是近似值。

### 2.2 RRF：只用秩，不用分数

论文原文（PDF 文本层抽取）：

> RRFscore(d ∈ D) = Σ_{r∈R} 1 / (k + r(d)), where **k = 60 was fixed during a pilot investigation and not altered during subsequent validation**.

两个设计理由（原文）：

1. **靠后排名的文档重要性不会消失**——`1/(k+r)` 是双曲衰减，不像指数那样迅速趋零；
2. **常数 k 削弱「离群系统给出的高排名」的影响**——k 越大，第 1 名与第 2 名的差距越小（本 demo 给了 `k = 0/10/60/100/500` 的对照表）。

k 的敏感性，论文 Table 1（30 个 Wumpus 配置融合，TREC topics 351–400）：

| k | 0 | 10 | 20 | 30 | 40 | 50 | **60** | 70 | 80 | 90 | 100 | 500 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MAP | .2072 | .2123 | .2134 | .2139 | .2138 | .2144 | **.2145** | .2146 | .2147 | .2145 | .2145 | .2142 |

最优值其实落在 60–80，但 60 与最优值相差不到 0.0005 —— 论文自己的结论是 `k=60 was near-optimal, but that the choice was not critical`。

效果（论文 Table 2 的 MAP，融合各 TREC track 的 submitted runs）：

| 集合 | RRF | 最佳单系统 |
| --- | --- | --- |
| TREC Robust | .3686 | .3586 |
| TREC3 | .4350 | .4226 |
| TREC5 | .3394 | .3165 |
| TREC9 | .2830 | .3519 |

**TREC9 是论文明确承认的例外**：那一组里的「最佳单系统」用了人工参与。总体结论是 RRF 比 Condorcet 与 CombMNZ 平均高 4%~5%，且 7/7 次胜过 Condorcet（p ≈ 0.008）、6/7 次胜过 CombMNZ（p ≈ .04）。

### 2.3 为什么 RRF 不需要归一化

| 方法 | 输入 | 对分数尺度 |
| --- | --- | --- |
| **RRF** | 只有秩 | **无关** |
| **CombMNZ** | 归一化后的分数 | 线性放大 |
| **Condorcet** | 只有两两顺序 | 无关 |

`CombMNZscore(d) = |{r ∈ R | r(d) ≤ c}| × Σ_{r: r(d) ≤ c} s_r(d)` —— 它把分数直接相加，所以**各路必须先归一化到可比的尺度**，否则 BM25（通常 0~20）会淹没余弦相似度（0~1）。本 demo 把两份分数整体乘 100，CombMNZ 的输出随之放大 100 倍，而 RRF 的结果一字不变。

## 3. 代码结构

| 文件 | 说明 |
| --- | --- |
| `python/hybrid.py` | BM25（idf / avgdl / tf / 两种打分写法 / k3 / SmallFloat）+ RRF / CombMNZ / Condorcet + 论文表格常量 |
| `python/selfcheck_hybrid.py` | 自检，51 条断言 |
| `python/main.py` | 演示：idf 恒正 / b 的作用 / 两种写法对拍 / RRF 融合 / k 敏感性 / CombMNZ 尺度 |
| `go/hybrid.go` | Go 同题实现（含 `main`） |

## 4. 运行

```bash
cd python && python selfcheck_hybrid.py   # PASS=51  FAIL=0
cd python && python main.py
cd go && go run .                          # 本机无 Go 工具链，未实跑
```

## 5. 自检覆盖的坑

| 断言 | 说明 |
| --- | --- |
| idf 在 `df == N` 时仍为正 | 与 `log(N/df)` 的行为差异 |
| Lucene tf 与经典 BM25 tf 之比恰为 `k1+1` | 排序不变但数值不可比 |
| 单调改写与经典写法数值相等（差 ≤ 1e-15） | `weight − weight/(1+f·n)` |
| `k1 = 0 → tf = 1`、`b = 0 → dl 不参与` | 两个退化边界 |
| `k3 = 0` 时权重恒为 1 | `((k3+1)qtf)/(k3+qtf)` 的奇点 |
| SmallFloat 相邻档相对步长 6.67% | 长度量化是有损的 |
| `k` 官方取 60；k 越大相邻名次差距越小 | 论文原文 + 机制 |
| 单系统时 RRF 不改变顺序 | 单调性 |
| CombMNZ 随尺度线性放大 / RRF 不变 | 归一化必要性的成对对照 |
| 论文 Table 1 最优 k 落在 60~80 且与 60 相差 < 0.0005 | 论文结论的如实转写 |

## 6. 参考资料（本轮实读）

- [Lucene `BM25Similarity.java`](https://raw.githubusercontent.com/apache/lucene/main/lucene/core/src/java/org/apache/lucene/search/similarities/BM25Similarity.java) —— idf / tf / `doScore` 单调改写 / k1·b·k3 默认值 / `LENGTH_TABLE`
- Cormack, Clarke & Büttcher, *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods*, SIGIR 2009（PDF 原文抽取：<https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf>）—— `k = 60` 的出处与理由、Table 1 的 k 敏感性、Table 2 的效果对比、CombMNZ 与 Condorcet 的定义
- Robertson & Zaragoza, *The Probabilistic Relevance Framework: BM25 and Beyond*, 2009 —— BM25 的经典形式（本轮作定性对照，定量结论一律以 Lucene 源码为准）
