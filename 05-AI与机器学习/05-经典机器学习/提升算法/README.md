# 提升算法(Boosting)— AdaBoost.SAMME + 梯度提升 GBDT

> 2026-09-14 完成。属于 `05-经典机器学习/` 的"集成学习"知识点(Boosting 分支)。
> 姊妹篇:[随机森林](../随机森林/) 覆盖 Bagging 分支(并行、自助采样、降方差);
> 本篇覆盖 Boosting 分支(**串行、拟合残差、降偏差**)。两者对照阅读收益最大。

## 简介

Bagging 靠"并行 + 平均"降**方差**;Boosting 靠"串行 + 纠错"降**偏差**——每一轮都在
修补前序模型漏掉的样本。本篇实现两个代表算法:

| 算法 | 损失 | 弱学习器 | 组合方式 | 关键作者 |
| --- | --- | --- | --- | --- |
| **AdaBoost.SAMME** | 指数损失 | 深度 1 **加权**分类树桩 | 加权多数投票 | Freund & Schapire 1997 / Zhu 2009 |
| **梯度提升(GBDT)** | 任意可微(演示平方损失) | 加权回归树 | 加性模型 + 收缩 | Friedman 2001 / 2002 |

实现全部基于 stdlib(`math` + 手写 LCG 随机数),不依赖 numpy/sklearn。

## 原理详解

### 1. AdaBoost:把"错题"反复拿回来做

初始 `wᵢ = 1/N`。每轮训一个弱学习器 `h_t`,算其**加权错误率**

```
err_t = Σᵢ wᵢ · 1(h_t(xᵢ) ≠ yᵢ) / Σᵢ wᵢ
```

再由 `err_t` 反推该学习器的发言权,然后**放大错分样本的权重**:

```
α_t   = lr · ( log((1 − err_t) / err_t) + log(K − 1) )        # SAMME
wᵢ    ← wᵢ · exp( α_t · 1(h_t(xᵢ) ≠ yᵢ) )   , 最后整体归一化
```

注意权重更新对**正确样本乘 1**(不变)、只对**错分样本乘 exp(α)**,所以下一轮那个树桩
"看见"的分布已经被歪向难点。`α` 的单调性很直观:`err → 0` 时 `α → +∞`,越准的学习器越有
发言权;`err > 1/2` 时 `α < 0`,意味着该学习器还不如随机猜。

### 2. 为什么多分类要加 `log(K − 1)`(SAMME 的核心)

二分类的"随机猜"基线是 `1/2`;`K` 分类的随机基线是 `1/K`。若沿用二分类公式,
在 `1/K < err < 1/2` 这段(对多分类而言**已经比随机差**)会给出 `α > 0`,把差学习器当成好的。
加上 `log(K − 1)` 后,"比随机猜好"的判据被统一成 `err < 1 − 1/K`,负权重才不出现。

代码里对应的三道闸:

```python
if err <= 0.0:                # 完美拟合:权重置 1.0 并停止(sklearn 同款行为)
    ...
if err >= 1.0 - 1.0 / K:      # 不比随机猜好 → 直接丢弃该学习器
    break
```

### 3. 加权投票与概率输出

`decision_function` 中它对**自己预测的类投 +α、对其它每一类投 −α/(K−1)**(保证各分量
之和恒为 0);`predict` 取分量最大者。概率用 SAMME.R 论文 eq.(15):

```
K > 2 :  p(c|x) = softmax( f(c|x) / (K − 1) )
K = 2 :  p(1|x) = sigmoid( f(1|x) / 2 )   ⇔  z = [−f/2, +f/2] 后 softmax
```

> 注:新版 scikit-learn 已移除 SAMME.R 的**训练**流程(其"用概率直接构造 α"的化简在
> 数值上不稳定),只保留离散 SAMME 作为 `algorithm` 的默认;但**预测**侧仍沿用上式。

### 4. 梯度提升:把 Boosting 看成"函数空间的梯度下降"

不再要求弱学习器是分类器,而是把它当成**加性模型**逐项贪心求解:

```
F_m(x) = F_{m-1}(x) + ν · h_m(x)          # ν = learning_rate,即 shrinkage
h_m = argmin_h Σᵢ l( yᵢ , F_{m-1}(xᵢ) + h(xᵢ) )
```

对 `l` 做一阶泰勒展开 ⇒ **`h_m` 每轮只需拟合当前模型的负梯度**。最小二乘损失下
`−∂l/∂F = y − F` 恰好是**残差**,于是"梯度提升"在回归里退化成"不断拟合残差":

```python
resid = [y[i] - F[i] for i in ...]        # 负梯度 = 残差
t = RegTree(max_depth).fit(Xs, resid, w)  # 用回归树去拟合残差
F = [F[i] + self.lr * t.predict(X)[i] for i in ...]   # shrinkage
```

初始模型取常数:`F₀ = ȳ`(平方损失下的最优常数)。分类时只需把最终输出过一个
`sigmoid`/`softmax`,但每个子学习器 `h_m` **始终是回归器** —— 这是 GBDT 与
"树 + 投票"最本质的区别。

### 5. shrinkage 与子采样

- **shrinkage `ν`**:每棵树只贡献 `ν` 倍,等于给每轮"打折扣"。`ν` 与 `n_estimators`
  强交互 —— 经验上小 `ν` 泛化更好,但需要更多轮;建议 `ν ≤ 0.1` 并配合早停。
- **subsample**(Friedman 2002 随机梯度提升):每轮只抽 `subsample` 比例样本训树,
  引入随机性以**降低方差**。典型 `0.5` —— 代价是同轮数下训练误差略高。

## 核心 API

| 类 / 方法 | 说明 |
| --- | --- |
| `Stump(K).fit(X, y, w)` | 深度 1 的**加权**分类树桩,用加权基尼增益选切分;支持多分类 |
| `RegTree(max_depth, min_samples_leaf).fit(X, y, w)` | 加权回归树,增益 = `s₁²/swl + s₂²/swr`(⇔ 最小化左右加权平方误差之和) |
| `AdaBoost(n_estimators, learning_rate)` | `.fit` / `.predict` / `.decision_function` / `.predict_proba` |
| `GradientBoosting(n_estimators, learning_rate, max_depth, subsample, seed)` | `.fit` / `.predict` / `.loss_curve`(逐轮训练 MSE) |

## 环境与运行

```bash
# 仅需 stdlib,Python ≥ 3.10
python boosting.py
```

输出五段:① 二分类 AdaBoost(圆内/圆外)随树桩数的 acc;② 多分类 `K=3` 的 `α` 与
`log(K−1)` 项;③ GBDT 回归不同 `(lr, #est)` 的 MSE;④ `subsample=0.5` vs `1.0`;
⑤ 单树桩 → 集成的测试集提升。

## 性能边界(实测)

| 场景 | 结果 |
| --- | --- |
| 二分类单树桩(测试) | 0.8625 |
| AdaBoost 20 / 50 / 200 树桩(测试) | 0.9625 / 0.9625 / 0.9750 |
| 多分类 K=3,前 6 轮 α | 1.3863 / 2.3026 / 3.3322 / 2.9444 / 3.0350 / 3.0099(均含 `log2 ≈ 0.6931`) |
| GBDT `lr=0.5,#20` | MSE 0.002601 |
| GBDT `lr=0.1,#100` / `#300` | 0.000325 / 0.000092 |
| GBDT `lr=0.01,#800` | 0.000329(lr 缩 10 倍 ≈ 需 10 倍轮数) |
| `subsample=0.5` vs `1.0`(同为 `lr=0.1,#300`) | 0.000272 vs 0.000092 |

复杂度:每轮 `O(n · p · log n)`(排序主导);总 `O(T · n · p · log n)`。
树桩深度 1 使单轮极快,树桩数一般取 50~500。

## 注意事项与常见坑

1. **多分类忘了 `log(K−1)`** —— 最隐蔽的 bug:`K=3` 时 `err = 0.45` 仍会给出正 α,
   把比随机猜(`1/3` 基线)差的学习器当好的用,准确率反而不如单桩。判据必须统一为
   `err < 1 − 1/K`。
2. **样本权重不归一化会指数上溢** —— 连续几十轮 `exp(α)` 相乘后 `w` 能溢出到 `inf`。
   每轮必须 `w /= sum(w)`。这也是 sklearn 源码里用 `exp(log(w) + α·incorrect)` 而非
   直接乘的原因(数值更稳,且能天然跳过 `w = 0` 的样本)。
3. **完美拟合时 α 爆炸** —— `err → 0` 时 `log((1−err)/err) → +∞`。必须像 sklearn 一样
   特判 `err ≤ 0` → `α = 1.0` 并**立刻停止**(继续加树只会让权重 NaN)。
4. **`err ≥ 1 − 1/K` 时不是"继续用",而是"丢弃该学习器"** —— 一旦出现就 `break`,
   因为后续轮次只会基于一个更歪的分布继续恶化。sklearn 的实现是把已 append 的模型
   弹出后再 break,效果等价。
5. **GBDT 的学习率与轮数不能独立调** —— 实测 `lr=0.1` 要 ~300 轮才到 `lr=0.5` 用 20 轮
   的水平。只调小 `lr` 而不加 `n_estimators` 会得到一个"欠拟合但看起来很稳"的模型。
6. **`F₀` 不能漏** —— 回归的起始模型是训练集均值 `ȳ`。漏掉后第一棵树的残差就是
   `y − 0`,相当于强行拟合一个带大偏置的目标,需要更多轮才能补回来。
7. **XOR 上别指望树桩 AdaBoost** —— 每轮只切一条轴平行条带,`err` 会长期卡在
   `0.44~0.50`(接近随机),50 轮也只有 0.59。这是**数据与弱学习器不匹配**,不是算法错;
   换成"圆内/圆外"数据集即可看到 0.79 → 0.975 的清晰提升。
8. **子采样"无放回"是近似** —— 正规做法是 `np.random.choice(replace=False)`;
   stdlib 下用随机整数索引近似,允许重复,`subsample=0.5` 时重复率约 1−e^(−0.5) ≈ 39%
   的样本被重复抽到,故降方差效果弱于理论值。

## 参考资料

### 本轮实际读取的联网来源

- [scikit-learn 1.9 §1.11.7 AdaBoost](https://scikit-learn.org/stable/modules/ensemble.html)
  —— "在反复修改的数据版本上拟合一系列弱学习器、再加权多数投票"、`AdaBoost.R2`、
  `n_estimators`/`learning_rate` 调参说明
- [scikit-learn 1.9 §1.11.1 Gradient-boosted trees](https://scikit-learn.org/stable/modules/ensemble.html)
  —— 加性模型、`h_m` 拟合负梯度、`F₀ = ȳ`、分类也必须用回归树、shrinkage 与 subsample
- [`sklearn/ensemble/_weight_boosting.py`(raw 源码)](https://raw.githubusercontent.com/scikit-learn/scikit-learn/main/sklearn/ensemble/_weight_boosting.py)
  —— 读到的关键代码:`learning_rate * (log((1-err)/err) + log(n_classes-1))`、
  `exp(log(w) + α·incorrect·(w>0))`、`err <= 0 → 1.0` 提前停、`err >= 1-1/K → 弹出`、
  加权投票 `+w / −w/(K−1)`、`predict_proba` 的 `K=2` 分支
- [scikit-learn `AdaBoostClassifier` API](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.AdaBoostClassifier.html)
- [scikit-learn 示例:多分类 AdaBoost](https://scikit-learn.org/stable/auto_examples/ensemble/plot_adaboost_multiclass.html)
  —— SAMME 在多分类上的 `log(K−1)` 修正动机

### 延伸阅读(由上列官方页面的参考文献索引,本轮未逐篇通读)

- Freund & Schapire, *A Decision-Theoretic Generalization of On-Line Learning and an
  Application to Boosting*, JCSS 1997 —— AdaBoost 原始论文
- Zhu, Zou, Rosset, Hastie, *Multi-class AdaBoost*, Statistics and Its Interface 2009
  —— SAMME 与 `log(K−1)`、eq.(15)
- Friedman, *Greedy Function Approximation: A Gradient Boosting Machine*, Annals of
  Statistics 2001 —— 函数空间梯度下降
- Friedman, *Stochastic Gradient Boosting*, Computational Statistics & Data Analysis 2002
  —— `subsample` 子采样
- Hastie, Tibshirani, Friedman, *The Elements of Statistical Learning*, Ch.10 (Boosting)
  —— 指数损失与偏差-方差视角
