# 离线检索评估指标

## 1. 简介

「召回率 95%」这句话在没有口径的情况下毫无意义：截断点取 10 还是 100、并列怎么处理、增益是等级本身还是 `2^rel − 1`、没有相关文档时算 0 还是跳过——每一条都能让数字差出一倍。

本 demo 按两套最常被引用的官方实现逐行转写并对照：

- **scikit-learn** `sklearn/metrics/_ranking.py`（`ndcg_score` / `dcg_score` / `_tie_averaged_dcg`）
- **trec_eval** `m_ndcg.c`、`m_recall.c`（IR 评测的事实标准）

## 2. 原理

### 2.1 折扣向量：同一个公式，两种截断

```python
discount = 1 / (np.log(np.arange(y_true.shape[1]) + 2) / np.log(log_base))
if k is not None:
    discount[k:] = 0
```

`1/log₂(i+2)`：第 1 名权重 1，第 2 名 `1/log₂3 ≈ 0.6309`，第 3 名恰好 0.5。

**截断方式是把 `discount[k:]` 置零，不是对序列切片**。两者在第 k 名之后的贡献上等价（都是 0），但「置零」保留了向量长度，也让「k 之后的条目完全不影响结果」成为可以直接断言的性质。

trec_eval 的注释同样写明 `/* Note: i+2 since doc i has rank i+1 */`——两套实现在这个公式上是**一致的**。

### 2.2 增益口径：等级本身，不是 2^rel − 1

trec_eval `m_ndcg.c` 的文档字符串：

```
Gain values are set to the appropriate relevance level by default.
Eg, 'trec_eval -m ndcg.1=3.5,2=9.0,4=7.0 ...'
will give gains 3.5, 9.0, 3.0, 7.0 for relevance levels 1,2,3,4
respectively (level 3 remains at the default).
```

**level 3 保持默认 ⇒ 默认增益就是等级 3.0**，而不是很多人以为的 `2^rel − 1 = 7`。要换口径必须显式传 `-m ndcg.1=...`。

sklearn 侧更直接：gain 就是 `y_true` 本身，写什么是什么。本 demo 显式算了一遍 `2^rel−1` 与原始等级两种增益，确认它们给出不同的 DCG——**跨库比对 NDCG 前必须先统一增益口径**。

### 2.3 并列：默认会「摊平」

```python
if ignore_ties:
    ranking = np.argsort(y_score)[:, ::-1]
    ...
else:
    cumulative_gains = [_tie_averaged_dcg(y_t, y_s, discount_cumsum) for ...]
```

`ignore_ties` 默认 **False**，即走 `_tie_averaged_dcg`：把并列组内部的增益**取平均**，再乘该组所占秩位的折扣之和。官方注释说这等价于「对并列组的所有可能排列取平均」——本 demo 直接用 `itertools.permutations` 枚举全部 3! = 6 种排列取平均做了对拍，两者严格相等。

无并列时两条路径必须给出同一个数，本 demo 也做了负控。

### 2.4 IDCG 与「全不相关」

```python
normalizing_gain = _dcg_sample_scores(y_true, y_true, k, ignore_ties=True)
all_irrelevant = normalizing_gain == 0
gain[all_irrelevant] = 0
gain[~all_irrelevant] /= normalizing_gain[~all_irrelevant]
```

两个细节：

1. IDCG 用 **`y_score = y_true` 且强制 `ignore_ties=True`** ——因为相同 `y_true` 之间换序不影响重排结果，此时并列处理没有意义。
2. **全不相关时结果置 0，不是 NaN**。trec_eval 的处理不同：它**不产出该 topic 的值**（`num_rel == 0` 时 `return 0` 跳过），所以跨 topic 平均时该 topic 直接不参与——这也是两套库在「平均分母」上会分歧的地方。

另外官方文档明确警告：**`y_true` 含负值时 NDCG 可能不在 [0,1]**。本 demo 用 `[1, -1]` 实测得到 1.26。

### 2.5 recall@k 的位置语义

```c
for (i = 0; i < res_rels.num_ret; i++) {
    if (i == cutoffs[cutoff_index]) {           /* 判定在前 */
        eval->...value = (double) rel_so_far / (double) res_rels.num_rel;
        ...
    }
    if (res_rels.results_rel_list[i] >= epi->relevance_level)
        rel_so_far++;                            /* 计数在后 */
}
```

**判定在计数之前**，所以 `recall@k` 统计的正好是 rank 1..k——不是 rank 1..k−1，也不是 rank 0..k−1。默认截断点是 `5,10,15,20,30,100,200,500,1000`。超出召回长度的截断点会沿用最终计数（官方：假定后面的位置全是不相关文档）。

## 3. 代码结构

| 文件 | 说明 |
| --- | --- |
| `python/ieval.py` | sklearn 口径（`discount_vector` / `dcg` / `tie_averaged_dcg` / `ndcg`）+ trec_eval 口径（`trec_ndcg` / `recall_at` / `precision_at`）+ MRR/MAP |
| `python/selfcheck_ieval.py` | 自检，39 条断言 |
| `python/main.py` | 演示：折扣向量 / 两套 NDCG / 排序前后 / 截断点 / MRR-MAP / 全不相关 / 并列 |
| `go/ieval.go` | Go 同题实现（含 `main`） |

## 4. 运行

```bash
cd python && python selfcheck_ieval.py   # PASS=39  FAIL=0
cd python && python main.py
cd go && go run .                         # 本机无 Go 工具链，未实跑
```

## 5. 实测读数（结果 `[2,0,1,0,3,0]`）

| 指标 | 值 |
| --- | --- |
| sklearn NDCG | 0.768725 |
| sklearn NDCG@3 | 0.525005 |
| trec_eval NDCG | 0.768725 |
| trec_eval NDCG@3 | 0.525005 |
| 重排后 NDCG | 1.000000 |

两套实现在这个数据上数值相同——**前提是增益都取等级本身**。一旦换成 `2^rel−1` 就会分岔，所以「两个库数一样」不等于「口径一致」。

## 6. 自检覆盖的坑

| 断言 | 说明 |
| --- | --- |
| `discount[k:] = 0` | 截断是置零不是切片 |
| 并列平均 DCG == 6 种排列的平均 | 枚举对拍，官方注释的性质 |
| 无并列时 `ignore_ties` 不影响结果 | 负控 |
| 全不相关 → sklearn 置 0 / trec_eval 不给值 | 两套库的分母分歧 |
| 负 `y_true` 让 NDCG > 1 | 官方文档明确警告 |
| `recall@1 = 1/4`、`recall@3 = 2/4` | 判定在计数之前 ⇒ rank 1..k |
| 超出召回长度的截断点沿用最终计数 | m_recall.c 的收尾 while |
| trec_eval 默认增益 = 等级（level 3 → 3.0） | 不是 `2^rel−1` |

## 7. 参考资料（本轮实读）

- [scikit-learn `sklearn/metrics/_ranking.py`](https://raw.githubusercontent.com/scikit-learn/scikit-learn/main/sklearn/metrics/_ranking.py) —— `_dcg_sample_scores`、`_tie_averaged_dcg`、`_ndcg_sample_scores`、`ndcg_score`
- [trec_eval `m_ndcg.c`](https://raw.githubusercontent.com/usnistgov/trec_eval/main/m_ndcg.c) —— 默认增益口径、`log2(i+2)`、ideal DCG 的累加条件
- [trec_eval `m_recall.c`](https://raw.githubusercontent.com/usnistgov/trec_eval/main/m_recall.c) —— 默认截断点、判定与计数的先后顺序
- [trec_eval `m_P.c`](https://raw.githubusercontent.com/usnistgov/trec_eval/main/m_P.c) —— P@k
- Järvelin & Kekaläinen, *Cumulated gain-based evaluation of IR techniques*, ACM TOIS 20(4), 2002 —— NDCG 原论文（trec_eval 文档自称按此实现；本轮作定性著录）
- McSherry & Najork, *Computing information retrieval performance measures efficiently in the presence of tied scores*, ECIR 2008 —— sklearn `_tie_averaged_dcg` 文档引用的并列处理依据
