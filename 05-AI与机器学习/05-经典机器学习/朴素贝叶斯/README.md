# 朴素贝叶斯(Naive Bayes)

> 经典机器学习第二批 · 概率生成式分类器。前接 [决策树](../决策树/)、[逻辑回归](../逻辑回归/)。

## 简介

朴素贝叶斯是**生成式**分类器:先对 `P(x|y)` 与 `P(y)` 建模,再用贝叶斯定理反推 `P(y|x)` 做 MAP 决策。
它的"朴素"之处是假设**给定类别后各特征条件独立**——这个假设几乎从不成立,但在文本分类、垃圾邮件过滤等
高维稀疏场景长期是强 baseline:训练只需一遍计数(`O(n·d)`)、无需迭代、天然支持增量(`partial_fit`)。

与 [逻辑回归](../逻辑回归/) 的关系:两者都是**对数线性**分类器(`log P(y|x)` 对 `x` 线性),LR 是**判别式**
直接用条件似然训练,朴素贝叶斯是**生成式**用联合似然训练。`P(x|y)` 取指数族时二者给出同一函数族,
差别只在参数估计(生成式收敛更快、渐近误差更大;判别式反之)——Ng & Jordan (2001) 的经典结论。

## 原理详解

### 1. 从贝叶斯定理到 MAP 决策

```
P(y | x1..xn) = P(y) · P(x1..xn | y) / P(x1..xn)
```

条件独立假设 `P(xi | y, x1..x_{i-1}, x_{i+1}..xn) = P(xi | y)` 后,分母 `P(x1..xn)` 与 `y` 无关(对分类是常数),于是

```
P(y | x) ∝ P(y) · ∏_i P(xi | y)      ŷ = argmax_y  P(y) · ∏_i P(xi | y)
```

`P(y)` 用极大似然(MAP 意义上的相对频率)估计;三个变体的唯一区别是 **`P(xi|y)` 取什么分布**。

### 2. GaussianNB:连续特征

```
P(xi | y) = 1/√(2πσ²y) · exp( −(xi − μy)² / (2σ²y) )
```

`μy`、`σ²y` 用类内样本的极大似然估计。取对数后逐特征相加:

```
log P(x|y) = log P(y) + Σ_i [ −½·log(2πσ²yi) − (xi − μyi)² / (2σ²yi) ]
```

注意:上式**不是**对 `xi` 的线性函数(含二次项),所以 GaussianNB 不属于对数线性族。
sklearn 额外加 `var_smoothing`:`σ² ← σ² + ε·max_j(range(X_j))`,防止某类某特征方差为 0 导致除零。

### 3. MultinomialNB:词频计数

```
θ̂yi = (Nyi + α) / (Ny + α·n)
```

- `Nyi` = 类 `y` 中特征 `i` 的出现总次数;`Ny` = 类 `y` 所有特征计数之和;`n` = 特征数(词表大小)。
- `α = 1` → **Laplace 平滑**;`0 < α < 1` → **Lidstone 平滑**(更弱平滑,稀疏数据常更好);`α = 0` → 未平滑 MLE。
- 平滑的分子加 `α`、分母加 `αn`,保证 `Σ_i θ̂yi = 1` 且 `θ̂yi > 0`,这是它能工作的**必要条件**(见下方坑 ①)。

对数联合似然:`log P(y|x) ∝ log P(y) + Σ_i xi·log θ̂yi`,决策取 argmax。

### 4. BernoulliNB:二值出现/不出现

```
P(xi | y) = P(xi=1|y)^xi · (1 − P(xi=1|y))^(1−xi)
```

`θyi = (Nyi + α) / (Ny + 2α)`(`Ny` 这里是**文档数**,+2 因为每个特征只有两种取值)。

与多项式的**唯一实质差别**:伯努利对 `xi = 0` 也贡献 `log(1−θyi)`,即**显式惩罚"某指示性词没出现"**;
多项式对没出现的词**完全忽略**。后果:短文档上二者差异被放大(文档越短,未出现词越多);
长文档上多项式更稳。Manning 等的 IR 教材与 McCallum & Nigam (1998) 都强调过这一点。

### 5. 为什么用对数

`∏ P(xi|y)` 在几百维词表下会下溢到 0。工程上全部改成对数域相加,再用 `logsumexp` 归一:

```
logsumexp(x) = m + log Σ exp(xi − m),   m = max(x)
```

`m` 的减法是为了防上溢;直接 `Σ exp(xi)` 在 `xi` 很大时会溢出。

## 核心 API(本 demo 的实现面)

| 类 | 关键方法 | 说明 |
| --- | --- | --- |
| `GaussianNB` | `fit/predict/predict_log_proba/predict_proba` | `theta_`/`var_`/`class_prior_` |
| `MultinomialNB` | 同上 | `feature_log_prob_`/`class_log_prior_`/`feature_count_` |
| `BernoulliNB` | 同上 + `binarize` 阈值 | `feature_log_prob_` 存的是 `log θ` |

`logsumexp()`、`_softmax_from_log()` 是公共工具;`_slog()` 让 `log 0 = −inf` 而不抛异常,
专门用于演示未平滑时的零概率灾难。

## 变体对比

| 变体 | `P(xi\|y)` | 适用数据 | 未出现特征的代价 |
| --- | --- | --- | --- |
| GaussianNB | 正态分布 | 连续、且近似单峰 | 不适用 |
| MultinomialNB | 多项式(计数) | 词频 / tf-idf / 计数型 | 忽略 |
| BernoulliNB | 伯努利(0/1) | 短文本、二值特征 | 乘 `(1−θ)`,显式惩罚 |
| ComplementNB | 补集统计 | **类别不平衡**的文本 | 权重更稳定 |
| CategoricalNB | 分类分布 | 取值为有限类别 | 逐类别平滑 |

## 环境与运行

```bash
python naive_bayes.py        # 纯 stdlib,Python ≥ 3.8(PEP 585 泛型注解用 future import)
go run naive_bayes.go        # 需 Go ≥ 1.18
```

零第三方依赖。

## 关键代码

```python
def _jll(self, x):
    out = []
    for c in self.classes_:
        fl = self.feature_log_prob_[c]
        ll = self.class_log_prior_[c]
        for j in range(self.n_features):
            if x[j]:                    # 跳过 0 计数:既省算力,又避免 0·(−inf)=nan
                ll += x[j] * fl[j]
        out.append(ll)
    return out
```

## 性能边界

- 训练 `O(n·d)` 单遍计数;预测 `O(C·d)` 点积。**无迭代、无收敛问题**,比 LR/SVM 快 1~2 个数量级。
- 与 SVM/Boosting 小样本下精度接近;样本量 `n` 增大后(尤其判别式任务)会被 LR/GBDT 反超。
- 概率输出**不可信**:sklearn 文档明说 "naive Bayes is known as a decent classifier, but a bad estimator"。
  需要校准概率时接 `CalibratedClassifierCV` 或换 LR。

## 注意事项与常见坑

1. **零概率灾难**:某一类从未见过某特征 → `θ = 0` → `log θ = −inf` → **整个类后验直接归零**,
   且这个类的得分对其它所有证据不再敏感(单点证据钉死结果)。`α = 0` 只在特征空间完全覆盖时可用,
   实际必须 `α > 0`(文本常用 `0.01 ~ 1`)。
2. **`0 × (−inf) = NaN`**:若把 `x[j]=0` 也拿去乘 `−inf` 的对数概率,向量化实现会静默产出 `nan`
   (如 `X @ feature_log_prob_.T` 在未平滑时)。本 demo 在 `_jll` 里显式跳过 0 计数。
3. **先验受类别频率支配**:`class_log_prior_` 直接是相对频率,不平衡数据上可改用 uniform prior
   (`fit_prior=False`) 或 `class_prior` 显式指定。
4. **特征相关时精度受损,但排序常仍可用**。Zhang (2004) 证明:在依赖分布满足一定条件时,
   朴素贝叶斯的**最优性**(argmax 正确)并不要求独立假设成立——这解释了它"假设明显错却好用"。
5. **tf-idf 上也能用 MultinomialNB**(sklearn 文档认可),但此时 `θ` 不再是概率,`predict_proba`
   的"概率"含义更弱。
6. **伯努利 vs 多项式的选择依赖文档长度**:长文档多项式通常更好,短文档 `BernoulliNB` 常更好,
   sklearn 官方建议两者都试。
7. **GaussianNB 对方差为 0 的特征会崩**(除零);`var_smoothing` 默认 `1e-9` 是必要的,不是装饰。

## 参考资料

### 本轮实际读取的联网来源

- [scikit-learn 1.9 `1.9. Naive Bayes`](https://scikit-learn.org/stable/modules/naive_bayes.html) — 条件独立推导、Gaussian/Multinomial/Bernoulli/Complement/Categorical 全部公式、`α` 平滑定义(θ̂yi=(Nyi+α)/(Ny+αn))、"decent classifier but a bad estimator" 原话、`partial_fit` 与样本权重说明。本 demo 的公式与参数命名均以此页为准。

### 延伸阅读(由上列官方页面的参考文献索引,本轮未逐篇通读)

- H. Zhang (2004) *The optimality of Naive Bayes* — 依赖不独立时 argmax 仍最优的条件
- A. McCallum & K. Nigam (1998) *A comparison of event models for Naive Bayes text classification* — 多项式 vs 伯努利事件模型
- V. Metsis et al. (2006) *Spam filtering with Naive Bayes — Which Naive Bayes?*
- Rennie et al. (2003) *Tackling the poor assumptions of naive Bayes text classifiers* — ComplementNB 动机
- C.D. Manning, P. Raghavan, H. Schütze《Introduction to Information Retrieval》pp. 234–265 — 文本分类中的 NB 与平滑
- Ng & Jordan (2001) *On Discriminative vs. Generative Classifiers* — 生成式/判别式收敛速率的经典对比(正文"简介"一节引用)
