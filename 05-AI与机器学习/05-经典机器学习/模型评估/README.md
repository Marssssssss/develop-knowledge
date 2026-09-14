# 模型评估 — 交叉验证 / 混淆矩阵 / P·R·F1 / ROC-AUC

> 2026-09-14 完成。`05-经典机器学习/` 的最后一个核心知识点:把前面所有模型"评好"。
> 前面的 demo 都在讲"怎么学",这个 demo 讲"怎么判优劣"——而绝大多数线上事故
> 不是模型学不出来,是**指标看得不对**。

## 简介

三层内容:

| 层次 | 内容 | 典型错误 |
| --- | --- | --- |
| **怎么切数据** | K 折 / 分层 K 折 / LOO / GroupKFold / TimeSeriesSplit | 用有序数据直接切折 |
| **怎么算分** | 混淆矩阵、P/R/F_β、micro/macro/weighted | 只看 accuracy |
| **怎么排序** | ROC、AUC(梯形/秩和/Bamber)、Gini、OvR | 忽略类不平衡与并列 |

文件划分(单文件均 ≤ 300 行):`_synth.py` 共享合成数据 · `model_eval.py` 折/混淆矩阵/
P·R·F1 · `roc_auc.py` ROC/AUC/多分类 OvR。

## 原理详解

### 1. 混淆矩阵:行是真实类

scikit-learn 的定义是"matrix entry `i, j` is the number of observations **actually in
group i**, but **predicted to be in group j**" —— 所以行 = 真实类、列 = 预测类:

```
               预测0   预测1
      真实0      TN      FP
      真实1      FN      TP
```

矩阵**不对称**:`FP`(把负判成正)与 `FN`(把正判成负)业务代价完全不同,单看 accuracy
会丢掉全部信息。

### 2. 精确率 / 召回率 / F_β

```
precision = tp/(tp + fp)        预测为正的样本里,真正为正的比例
recall    = tp/(tp + fn)        真实为正的样本里,被揪出来的比例
F_β       = (1+β²)·tp / ((1+β²)·tp + fp + β²·fn)
```

`F_β` 是 precision 与 recall 的**调和**平均再加权:`β<1` 偏精确率、`β>1` 偏召回率、
`β=1` 即 F1。用调和平均而非算术平均,是因为只要有一项近 0 它就近 0 —— 偏科该被惩罚。

### 3. 三种平均口径(多分类的关键)

| 口径 | 做法 | 特点 |
| --- | --- | --- |
| **macro** | 各单类指标**等权**平均 | 对**小类**敏感:小类差会被完整暴露 |
| **weighted** | 按各类 support 加权平均 | 向大类倾斜,≈"总体表现" |
| **micro** | 先把各类 tp/fp/fn **汇总**再算 | 被**大类**主导;多标签下 = accuracy |
| samples | 逐样本算再平均(仅多标签) | 关注"每个样本的标签集合" |

**二分类时三者并不相等** —— 实测(矩阵 `[[40,4],[6,50]]`):

```
macro      precision=0.8977  recall=0.9010  F1=0.8990
weighted   precision=0.9011  recall=0.9000  F1=0.9002
micro      precision=0.9000  recall=0.9000  F1=0.9000
```

三者只差在小数点后第三位,但类越不平衡差距越大。**在极不平衡数据上,报 macro 还是
micro 直接决定结论方向**。

### 4. 交叉验证:折是怎么切的

测试折 `i` 的下标区间是 `[n·i/k, n·(i+1)/k)`(整除切分,不重不漏)。scikit-learn 的
docstring 例:`X = ["a","b","c","d"]`、`n_splits=2` → 测试 `[2 3]` 再 `[0 1]`,本 demo 复现。

**最大的坑:`KFold` 默认 `shuffle=False`** —— 直接把数据切连续块,数据一旦有序
(按时间/ID/标签),各折就不是同分布的。实测 600 条极不平衡(正例率 6.67%)且按特征
排序的数据:

```
普通 KFold      各折正例数 [1, 6, 10, 12, 11]   折间比例极差 0.0917
分层 Stratified 各折正例数 [8, 8, 8, 8, 8]      折间比例极差 0.0000
```

首折只有 1 个正例:该折 `recall = 0/(0+0)` **无定义**,折间方差被虚假放大。
`StratifiedKFold` 在**每个类别内部**各自均分到 k 折,各折比例≈整体比例。

其它切分器:`LeaveOneOut`(折=样本数,方差小但极慢)、`ShuffleSplit`(折间可能重叠)、
`GroupKFold`(**同组不许跨折**)、`TimeSeriesSplit`(**只用过去训练、未来测试**,严禁打乱)。

### 5. ROC 与 AUC 的三种算法

ROC 把阈值从高到低扫一遍,每个阈值给一个点:

```
TPR = TP/(TP+FN)        FPR = FP/(FP+TN)        AUC = ∫₀¹ TPR(FPR⁻¹(u)) du
```

AUC 有三种**等价**算法,本 demo 都实现并交叉验证:

- **(a) 梯形积分** —— 把 ROC 离散点连成折线求面积,也是 sklearn 的实现方式。
- **(b) 秩和公式**(Mann-Whitney U / Wilcoxon):
  `AUC = (Σ_{pos} rank_i − n_pos(n_pos+1)/2) / (n_pos · n_neg)`,
  其中 `rank_i` 是得分在全体样本里的升序名次,**并列必须取平均秩**。
- **(c) Bamber 等价定理** —— 按定义直接数有序对:
  `AUC = P(score_pos > score_neg) + 0.5·P(score_pos = score_neg)`。

即"AUC 就是**随机抽一个正例和一个负例,正例得分更高的概率**",所以 AUC 又叫
**c 统计量**。实测三者在小数点后 10 位完全一致(`|梯形 − 秩和| = 0.00e+00`)。
这个恒等**只在并列取平均秩时成立**:随手打破平局,秩和就会偏离面积。

**Gini = 2·AUC − 1**(Somers' D)只是 AUC 的线性重标定,信息量与 AUC 完全相同 ——
不要当成两个独立指标。

### 6. 多分类 OvR 的 AUC

```
macro = (1/(c(c−1))) Σj Σ_{k>j} ( AUC(j|k) + AUC(k|j) )
```

`AUC(j|k)` 表示**只看类 j 与类 k 的样本**、但用**第 j 列得分**算出的二分类 AUC。两列得分
不同,所以 `AUC(j|k) + AUC(k|j) ≠ 1`,两者都要算。`micro` 把 `(样本 × 类别)` 展平成单个
二分类问题,此时 `TPR = ΣTPc/Σ(TPc+FNc)`,与 sklearn 的 micro OvR 定义一致。实测(K=3):

```
各类 vs 其余 AUC = [0.9226, 0.9350, 0.9375]
macro = 0.931895      micro = 0.932511
```

二分类时 macro 只剩一对,退化回普通 AUC(实测 `0.922575 = per[0]`)。

## 核心 API

| 函数 | 说明 |
| --- | --- |
| `kfold_indices(n, k)` / `stratified_kfold_indices(y, k, seed)` | 生成 (train_idx, test_idx) |
| `confusion_matrix(yt, yp, labels)` / `accuracy(yt, yp)` | 行=真实类的混淆矩阵 / 准确率 |
| `prf(yt, yp, labels, average, beta)` | average ∈ `macro`/`weighted`/`micro`/`None` |
| `roc_curve(y, scores)` | → `(fpr, tpr, thresholds)`,首点是原点 |
| `auc_trapezoid` / `average_ranks` | 梯形积分 / 升序平均秩 |
| `auc_rank_sum(y, scores)` / `auc_bruteforce(y, scores)` | 秩和公式 / Bamber 定义(O(n²) 验算) |
| `ovr_auc_macro(y, S, labels)` / `ovr_auc_micro(y, S, labels)` | 多分类 OvR |

## 环境与运行

```bash
# 仅需 stdlib,Python ≥ 3.10;两个脚本相互独立
python model_eval.py      # 折 + 混淆矩阵 + P/R/F1 + 交叉验证方差
python roc_auc.py         # ROC + AUC 三种算法 + 多分类 OvR
```

## 性能边界(实测)

| 场景 | 结果 |
| --- | --- |
| 二分类阈值 0.5(n=100) | acc 0.9000,混淆矩阵 `[[40,4],[6,50]]` |
| AUC 梯形 / 秩和 / Bamber(三者恒等) / Gini | `0.9809253247`(差 0.00e+00)/ 0.961851 |
| 5 折 CV(n=300) | accuracy `0.8465 ± 0.0463`,AUC `0.9346 ± 0.0187` |
| 极不平衡有序数据 | 普通 K 折极差 0.0917 → 分层后 0.0000 |
| K=3 OvR AUC | macro 0.931895 / micro 0.932511 |

复杂度:混淆矩阵与 P/R/F1 为 `O(n)`;`roc_curve` 与 `auc_rank_sum` 为 `O(n log n)`(排序
主导);`auc_bruteforce` 为 `O(n_pos·n_neg)` 仅作验算;分层 K 折 `O(n log n)`。

## 注意事项与常见坑

1. **只看 accuracy** —— 99% 负例的数据上"全猜负例"就有 0.99,必须同时报 recall 与 precision。
2. **混淆矩阵行列搞反** —— sklearn 是**行 = 真实类**,很多教材画成列 = 真实类。搞反后
   precision 与 recall 对调,结论完全相反。对照 `[[40,4],[6,50]]`:真实 1 共 56 个、
   判对 50 个 → `recall = 50/56 = 0.8929` ✓。
3. **`KFold` 默认 `shuffle=False`** —— 数据有序时各折不同分布,实测首折正例只有 1 个、
   `recall` 直接无定义。要么 `shuffle=True` 并固定 `random_state`,要么用
   `StratifiedKFold`;时间序列必须用 `TimeSeriesSplit`。
4. **同一组样本跨折泄漏** —— 同一用户/病人的多条记录分散在不同折,模型等于"见过"测试
   样本。必须用 `GroupKFold`,且**特征工程(标准化、编码)要放进 Pipeline 内部**。
5. **忘了取平均秩** —— 秩和公式里若有并列而未取平均秩,秩和会偏离梯形面积。本 demo 数据
   无并列故三者相等;有大量并列时(如离散得分)差异立刻出现。
6. **把 Gini 当成第二个指标** —— `Gini = 2·AUC − 1`,与 AUC 信息等价。KS 才需单独算
   (KS = max|TPR − FPR|)。并列报"Gini 与 AUC 都很好"是重复计分。
7. **多分类乱选平均口径** —— 小类重要(故障类型、罕见病)必须看 macro;只关心总体吞吐
   才看 micro。实测 K=3 时两者差 0.0006,类越不平衡差距越大,选错会得出相反结论。
8. **用测试集调超参 / 只报单点分数** —— 交叉验证是**选**模型,选完要用从未参与任何一步的
   **留出集**(或嵌套交叉验证)报最终分数,否则乐观偏置。且实测同一模型 5 折 accuracy
   从 0.80 到 0.918(极差 0.118):不给误差棒就分不清"新版好 1 个点"是提升还是噪声。

## 参考资料

### 本轮实际读取的联网来源

- [scikit-learn 1.9 §3.3 Metrics and scoring](https://scikit-learn.org/stable/modules/model_evaluation.html)
  —— 混淆矩阵 "entry i, j … actually in group i, but predicted to be in group j";
  `accuracy`/`precision`/`recall`/`F_β` 公式;micro/macro/weighted/samples 四口径对照表;
  `P(A,B)=|A∩B|/|B|`、`R(A,B)=|A∩B|/|A|`;ROC 的 `TPR`/`FPR` 与 `AUC=∫TPR d(FPR)`
- [scikit-learn §3.1 Cross-validation](https://scikit-learn.org/stable/modules/cross_validation.html)
  —— KFold 的 `[n·i/k, n·(i+1)/k)` 切分与 docstring 例子、**默认 `shuffle=False`**、
  `StratifiedKFold` / `LeaveOneOut` / `ShuffleSplit` / `GroupKFold` / `TimeSeriesSplit`
- [scikit-learn `roc_auc_score` API](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.roc_auc_score.html)
  —— 多分类 OvR 的 macro/micro 公式与 `multi_class` 参数
- [scikit-learn 示例 `plot_roc`](https://scikit-learn.org/stable/auto_examples/model_selection/plot_roc.html)
  —— ROC 曲线绘制与多分类 OvR/OvO 扩展

**延伸阅读**(由上列官方页面索引、本轮未逐篇通读):Bamber 1975(面积 = 有序对胜率)·
Hanley & McNeil 1982(AUC 的 c 统计量)· Hand & Till 2001(多分类 AUC)·
Hastie/Tibshirani/Friedman《ESL》Ch.7(选模型 ≠ 报分数)。
