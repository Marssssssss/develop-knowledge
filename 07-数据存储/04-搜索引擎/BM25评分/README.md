# BM25 评分算法（Lucene/Elasticsearch 实现）

## 简介

BM25（Okapi Best Match 25）是 Lucene 4.x 起默认、Elasticsearch 5.0 起默认的文本相关性评分函数，用来解决 TF-IDF 的两个老问题：

1. **TF 无上界**——一个词在一篇文档里出现 1000 次，TF·IDF 的 `log(tf)` 会无限增长，人工刷词可"灌高"自己的相关性；
2. **文档长度未真正归一化**——TF·IDF 用 `1/sqrt(|D|)` 强制把全部字段同等压缩，导致 `title` 与 `body` 无法区分。

BM25 用**两个可调参数 `k1`、`b`** 替代：一个管"词频饱和曲线"，一个管"文档长度归一化强度"，是当下事实标准。

- **关键概念**
  - **k1（默认 1.2）**：词频饱和度，词出现第 `tf = k1` 次时贡献约达到饱和值的一半；`tf > k1` 之后边际收益递减。
  - **b（默认 0.75）**：文档长度归一化强度，`b = 0` 表示完全不考虑长度，`b = 1` 表示对每个词一律按 `|D|/avgdl` 压缩。
  - **tf（term frequency）**：在某篇文档里出现次数。
  - **df（document frequency）**：整个语料里包含此词的文档数（不是次数）。
  - **|D| / avgdl**：文档词数 / 平均词数（**按词数，不是字符数**，与 ES 实现完全一致）。
- **历史背景**：Robertson、Walker 等 1990 年代在伦敦城市大学提出的概率相关性模型；Trotman 等 2014 年把它正式替换为 Lucene 的默认 similarity（取代老的 TF·IDF）。

## 原理详解

### 1. 公式分项

```
score(D, Q) = Σ_{q ∈ Q}  IDF(q) · tf(q, D) · (k1 + 1)
                               ─────────────────────────────────
                               tf(q, D) + k1 · (1 − b + b · |D|/avgdl)

IDF(q)       = ln(1 + (N − df(q) + 0.5) / (df(q) + 0.5))
```

注意 Lucene 用的不是经典 Robertson IDF `log((N − df + 0.5)/(df + 0.5))`，而是 **`+1` 在外面**，避免 `df = N` 时 `IDF = 0`（会让"出现在每篇文档里的词"完全没贡献）。

### 2. 工作流（一次评分）

1. **解析查询** → 拆成若干 `q_i`，BM25 对这些词单独算分再求和（不是乘）。
2. **df 与 N** → 预扫描 posting list 拿每个词的 df；N 是字段里有值的文档数。
3. **每篇文档的开销**：
   - 取 `tf(q_i, D)`：posting list 已经按 doc 排序，找一个 O(log n)；
   - 取 `|D|` 与 `avgdl`：前者从 norms（norms 是倒排索引里长度编码的数据结构）拿；后者在 shard 级缓存。
   - 代入公式。
4. **三段解释**：
   - **IDF 项**：罕见词加分，常见词/停用词几乎不分；
   - **tf 项**：`tf/(tf + k1·norm)` 是单调递增、有上限的曲线；
   - **norm(D) 项**：长文档被压低，短文档被抬高。

### 3. k1：词频饱和

```
       BM25 tf 部分        TF·IDF tf 部分
tf →  tf·(k1+1)/(tf+k1)    ln(tf+1)
```

| tf  |  BM25 (k1=1.2) |  TF·IDF |
|----:|---------------:|--------:|
|   1 |          0.909 |   0.693 |
|   2 |          1.277 |   1.099 |
|   5 |          1.818 |   1.792 |
|  12 |          2.261 |   2.565 |
| 100 |          2.487 |   4.625 |
|1000 |          2.499 |   6.908 |

BM25 在 `tf = k1` 时（约 1.2）达到 `0.91`，再往后越平；1000 次也只是 2.499。  
**人话**：刷词很难靠"重复"显著灌高分数——BM25 从设计上回击了 TF·IDF 时代的"keyword stuffing"。

### 4. b：文档长度归一化

`norm(D) = 1 − b + b · |D|/avgdl` → 出现在分母里：

| `|D|` | b = 0 |  b = 0.5 | b = 0.75 | b = 1.0 |
|------:|------:|---------:|---------:|--------:|
|     1 | 1.000 | 0.6250   | 0.4375   | 0.2500  |
|     4 | 1.000 | 1.0000   | 1.0000   | 1.0000  |
|    16 | 1.000 | 2.5000   | 3.2500   | 4.0000  |

- `b = 0` → "长度不影响分数"，所有字段长度效果等价；
- `b = 1` → 严格按长度压，长文档的每词贡献指数级下降。

ES 默认 `b = 0.75` 平衡两端。

### 5. 默认值与可调

| 参数 | Lucene 默认 | ES 默认（`index.similarity`） | 推荐调整场景 |
|------|-------------|------------------------------|--------------|
| `k1` | 1.2         | 1.2                           | 调低 → 词出现 1-2 次就高度相关（短查询）；调高 → 长文档 / 长 query 更敏感 |
| `b`  | 0.75        | 0.75                          | `b = 0` 想完全忽略长度；`b = 1` 想严格惩罚长字段 |

## 对比 / 选型

| 维度 | **BM25**（默认） | TF·IDF（Lucene legacy / `classic`） | DFR / DFI / IB / LM（Jelinek-Mercer 等）|
|------|------------------|----------------------------------|------------------------------------------|
| 数学基础 | 概率相关性模型 | 向量空间模型 | 信息论 / 贝叶斯 |
| TF 上限 | 有（`tf → k1+1` 渐近） | 无（`ln(tf+1)`） | 各种（DFR/IB 通常有界） |
| 字段长度 | `norm = 1-b+b·|D|/avgdl` 自适应 | `1/sqrt(|D|)` 一刀切 | 学过 H1/H2/H3/Z 多种归一化 |
| 可调参数 | 2（k1、b） | 0 | 多（basic_model、after_effect、normalization） |
| 工程实现 | Lucene/ES 默认 | ES 6.3 已 deprecated | 备用 |
| 默认可用性 | 各 ES 集群 5.0+ | 已 deprecated | 通过 `index.similarity` 显式配置 |

**实战选择**：BM25 默认值 `k1=1.2, b=0.75` 适用于绝大多数业务语料。仍踩坑时，可临时把字段映射的 similarity 改为 BM25 + 自定义 `k1/b`，或者用 multi_match + tie_breaker 调整多字段合成。

## 环境准备

- Python ≥ 3.8（仅标准库 `math` / `dataclasses`）
- Go ≥ 1.20（仅标准库）

## 运行方式

```bash
# Python
python3 python/bm25_demo.py

# Go
cd go && go run bm25_demo.go
```

两个程序执行**完全相同的 3 段演示**：

1. 对查询 `["shane", "connelly"]` 在 4 篇文段里计算每篇 BM25 score 并排序；
2. 打印词频饱和曲线（k1=1.2 半饱和点）；
3. 打印文档长度归一化表（b=0 / 0.5 / 0.75 / 1.0 对比）。

## 关键代码片段

以 Python 版（`python/bm25_demo.py`）为例，BM25 核心：

```python
def idf_lucene(n_q: int, N: int) -> float:
    """Lucene/ES 的 BM25 IDF: ln(1 + (N − n + 0.5) / (n + 0.5))"""
    return math.log(1 + (N - n_q + 0.5) / (n_q + 0.5))

def bm25_score(index, doc_id, query_terms, k1=K1, b=B):
    dl = index.doc_len[doc_id]
    norm = 1 - b + b * dl / index.avgdl   # 长文档的 norm 大 → 分母大 → 分数低
    score = 0.0
    for q in query_terms:
        n_q = len(index.postings.get(q, []))           # 包含 q 的文档数
        if n_q == 0: continue                          # 不在语料里 → 不贡献
        tf = sum(1 for tok in tokenize(index.docs[doc_id]) if tok == q)
        score += idf_lucene(n_q, N) * (tf * (k1 + 1)) / (tf + k1 * norm)
    return score
```

饱和曲线（"反向"操作）：

```python
def tf_saturation_curve(tf_max=30, k1=K1):
    return [(tf, tf * (k1 + 1) / (tf + k1), math.log(tf + 1))   # BM25 vs TF·IDF
            for tf in range(1, tf_max + 1)]
```

## 性能与边界

- **计算量**：理论上 `O(|Q| · |matched_docs|)`，posting list 是按 tf 倒序的，省 IO；ES 实际还会把 norms 装进内存（numOrds）加速 `|D|` 取值。
- **不缓存分数**：BM25 在 query 阶段现算（不像 TF·IDF 时代存过 normalized tf）；这是 ES 5.0+ 启动比旧版本慢的部分原因。
- **`b` 极端值**：`b = 0` 完全忽略长度，长 corpus 易被堆词贴身的"长目标页"占据；`b = 1` 完全归一化，短 query 在长 body 上吃大亏。

## 注意事项与常见坑

1. **认为"BM25 不再调"**——错误。短文本搜索、行业黑话多的语料，`k1` 调到 0.5-0.9 通常能拿到 5-15% 的 NDCG 提升。
2. **对 `keyword` 字段调 `b`**——`keyword` 字段不分析、不分词，文档长度通常为 1，`b=0` 与 `b=1` 表现一致；但同字段混 `text` + `keyword` 时 `b` 才有意义。
3. **混淆 IDF 与 TF·IDF 的 IDF**——公式不一样：Lucene 在外层加了 `+1`，避免 `df=N`（即"每篇文档都有的词"）退化为 0。
4. **`discount_overlaps`**：synonym / multi-word 切词会产生 `position increment = 0` 的 token（overlap），Elasticsearch 默认不计入 `|D|`；关闭会让词密度低的同义词显著的文档被错估。
5. **跨语言一致性**：中文经标准分词后平均 token 数远低于英文（中文行 5 tokens vs 英文行 80 tokens），`avgdl` 显著偏小，对长 query 不友好；IK Analyzer 等可以拉长 `avgdl`。

## 参考资料（实际阅读过的权威来源）

- [Practical BM25 — Part 2: The BM25 Algorithm and its variables | Elastic Blog](https://www.elastic.co/blog/practical-bm25-part-2-the-bm25-algorithm-and-its-variables) — ES 默认值、`k1=1.2` 半饱和点解释、`b=0.75` 默认
- [Found: Similarity in Elasticsearch | Elastic Blog](https://www.elastic.co/blog/found-similarity-in-elasticsearch) — BM25 vs TF·IDF 饱和曲线对比、Robertson–BM25 与 Lucene 实战公式
- [Pluggable Similarity Algorithms (官方指南)](https://www.elastic.co/guide/en/elasticsearch/guide/master/pluggable-similarites.html) — k1/b 参数语义、调优建议、`discount_overlaps`
- [Similarity module | Elasticsearch 6.x Reference](https://www.elastic.co/guide/en/elasticsearch/reference/6.x/index-modules-similarity.html) — `index.similarity` 字段级 similarity 配置语法、BM25/Classic/DFR/DFI/IB/LM 系列
- [BM25 实用详解 - 第 2 部分 (官方中文)](https://www.elastic.co/cn/blog/practical-bm25-part-2-the-bm25-algorithm-and-its-variables) — 中文官方译稿，便于核对公式与术语
- [The Probabilistic Relevance Framework: BM25 and Beyond — Stephen Robertson & Hugo Zaragoza, Foundations & Trends in IR, 2009](https://www.nowpublishers.com/article/Download_summary/INR-013) — 经典论文总览（公式完整推导）
