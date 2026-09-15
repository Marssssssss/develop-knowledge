# Target 编码与泄漏 —— 为什么"用 y 编码 x"是特征工程里最容易出事的一步

## 简介

**目标编码(target encoding,又叫 mean encoding、likelihood encoding)** 把类别列 `c` 替换成该类别下的目标均值:

```
enc(c) = mean{ y_i | x_i = c }
```

它把高基数类别列压成 1 个数值列,对梯度提升树这类模型几乎是必需的预处理。但它同时是**唯一一门"用标签造特征"的常规预处理** —— 一旦实现方式不对,你会得到一列直接携带答案的特征。本 demo 用纯标准库实现三种正规做法(平滑 / K 折交叉拟合 / CatBoost 有序统计),并**量化**泄漏值多少分。

核心结论:**泄漏不在"用不用 y",而在"用谁的 y"**。训练期每个样本的编码必须只由**别的样本**的标签决定,这样训练期与推理期的编码口径才一致。

## 原理详解

### 1. 泄漏是怎么发生的

朴素实现 `enc(c) = mean{y | x=c}` 在**训练集**上会把每个类别的编码算成它自己标签的平均:

- 高频类别(几十个样本)影响不大 —— 类别均值本来就由大数定律决定。
- 稀有类别(1~2 个样本)直接**把标签抄进特征**:类别 `c1` 只有一个样本、label=1,则 `enc(c1)=1.0`。模型学到"`c1` ⟹ 正类",训练集完美,测试集崩塌。
- 更隐蔽的一层:即便类别都不稀有,**测试集**的标签也可能被算进编码里(先在全量数据上 fit 编码器、再切分)。这时评估指标里混入了答案。

本 demo 的实现与官方一致:官方文档明确 `fit_transform` 采用 **cross fitting** 目的就是
"to prevent target leakage and overfitting in downstream predictors, especially for
non-informative high-cardinality categorical variables"。

### 2. 平滑:用先验换方差

```
enc(c) = (n_c · mean_c + m · prior) / (n_c + m)
```

`m` 是平滑强度:样本量 `n_c` 小的时候把估计拉向全局均值 `prior`(通常是 `target_mean_`)。`m→∞` 时所有类别退化为同一个数(编码不含信息);`m=0` 时退回上面那个"抄标签"的实现。官方文档的原话是
"Larger smooth value will put more weight on the global target mean",并且提供 `smooth='auto'` 按类别内方差自动定 `m`。

### 3. K 折交叉拟合(cross fitting)

把训练集切 K 折,第 k 折样本的编码**只用其余 K-1 折的标签**计算;推理时用全量训练集的编码。这样训练期编码不含自身标签,而推理期编码的统计量更稳。代价是训练期与推理期用了**不同**的统计量(轻微分布偏移)。

### 4. CatBoost 有序统计(ordered target statistics)

不做显式分折,而是按一个随机置换顺序处理样本:第 i 个样本的编码只用**排在它前面**的样本的标签,带先验权重:

```
enc_i = (Σ_{j<i, same cat} y_j + a·prior) / (count_{j<i, same cat} + a)
```

它同时消除了"用自身标签"和"用未来标签"两个问题,且不引入折与折之间的口径差异。本 demo 用多个置换取平均以降低单次置换的方差。

## 实验与实测结果

`python/main.py` 四组实验(全部标准库,无 numpy):

### 实验 1 —— 对齐官方 docstring 的数值锚点

| 项 | 本实现 | 官方文档 |
| --- | --- | --- |
| `target_mean_` | `44.3` | `44.3` |
| `smooth=1.0` → `encodings_` | `[20.9, 80.8, 43.2]` | `[21, 80.8, 43.2]` |
| `smooth=5000` → `encodings_` | `[44.1, 44.4, 44.3]` | `[44.1, 44.4, 44.3]` |
| `smooth='auto'` → 等价 `m` | `0.0831` | 按类别内方差/全局方差 |

> 注:官方 docstring 里 `smooth=1.0` 的首值显示为 `21`,按公式算是 `20.93` —— 文档输出做了取整。本实现断言用 `< 0.1` 的容差。

### 实验 2 —— 高基数纯噪声特征上的泄漏量(核心结果)

设定:3000 样本、1500 个类别、**类别与标签完全独立**(纯噪声),训练 2000 / 测试 1000。

| 编码方式 | 训练 AUC | 测试 AUC | 说明 |
| --- | --- | --- | --- |
| A 训练集内全量编码(泄漏) | **0.937** | 0.712 | 训练分被自己的标签抬起来 |
| B 全量数据编码(测试也泄漏) | 0.911 | **0.911** | 连测试分都变成谎言 |
| C K 折交叉拟合 | 0.799 | 0.787 | 训练口径与测试一致 |
| D 常数编码(对照基线) | 0.799 | 0.787 | 一个无信息编码能拿到的分数 |
| E CatBoost 有序统计 | 0.799 | 0.787 | 同上 |

**怎么读这张表**:

1. 特征**完全没有信息**,参照系是 D 的 `0.799/0.787`。A 的 `0.937` 是纯泄漏 —— 训练 AUC 比基线高 `0.138`,而这部分"性能"在测试集上不存在(`0.712`)。
2. **B 是最阴险的一行**:它的测试 AUC 和训练 AUC 一样高。因为编码器在全量数据上 fit 时"看过"测试样本的标签,测试集不再是独立的。如果你用 B 的方式准备离线评测集,你的线上预估会整体虚高。
3. C/E 落回基线,说明它们**确实没有偷看** —— 在一个无信息特征上就该拿到基线分。这不代表它们"没用",只代表在这个刻意设计的零信号场景下它们诚实地什么都没学到。
4. 泄漏的代价:如果你按 A 的 `0.712` 去上线,真实表现会掉到 `0.787`(基线)附近;而 A 的 `0.937` 会诱使你把这个特征当成重要特征保留下来。

### 实验 3 —— `smooth` 扫掠(1200 样本,类别频次差异巨大)

| smooth | 稀有类别的编码(×10) | 多数类别的编码(×10) |
| --- | --- | --- |
| 0 | `[0.5, 0, 0, 0, 0, 0, 0, 0, 0, 0]` | `[0.6, 0.437, 0.765, 0.1, …]` |
| 1 | `[0.456, 0.184, …, 0.184]` | `[0.585, 0.433, 0.743, …]` |
| 10 | `[0.39, 0.334, …, 0.334]` | `[0.507, 0.411, 0.618, …]` |
| 100 | `[0.37, 0.364, …, 0.364]` | `[0.398, 0.377, 0.425, …]` |
| 10⁶ | `[0.368, 0.367, …, 0.367]` | `[0.368, 0.368, …, 0.367]` |

`smooth=0` 时稀有类别拿到自己的单样本均值 `0.5`/`0.0`(把噪声当信号);`smooth→10⁶` 时**所有**类别收敛到全局均值 `0.367`,编码不再含任何类别信息。中间值才是可用的,而"中间"该取多少取决于类别频次分布 —— 这正是官方提供 `smooth='auto'` 的原因。

## 对比

| 做法 | 训练期口径 | 泄漏风险 | 适用场景 |
| --- | --- | --- | --- |
| 朴素全量编码 | 自身标签 | **高** | 只能用在高频类别,且必须放在 CV 折内 |
| 平滑 + 全量编码 | 自身标签(被先验稀释) | 中(频次越低越危险) | 类别频次都较高时的快速方案 |
| K 折交叉拟合 | 其他折的标签 | 低 | 通用;sklearn `TargetEncoder` 的 `fit_transform` 默认语义 |
| CatBoost 有序统计 | 置换中排在前面样本的标签 | 低 | 梯度提升树内部使用;无需额外分折 |

## 环境准备与运行

```bash
# Python(纯标准库,无需 pip install)
python python/main.py

# Go 版(同一套实验的 Go 实现:encoding.go 为核心算法,main.go 为实验)
cd go && go run .
```

## 关键代码

Python(`python/main.py`)导出的函数与官方 API 一一对应:

| 本 demo | sklearn / CatBoost |
| --- | --- |
| `target_encoding(x, y, smooth)` | `TargetEncoder(smooth=…)` 的 `fit` 部分 |
| `auto_smooth(x, y)` | `TargetEncoder(smooth='auto')` |
| `cross_fitted_encoding(x, y, n_folds)` | `TargetEncoder.fit_transform`(cross fitting) |
| `ordered_target_statistics(x, y)` | CatBoost 的 ordered target statistics |
| `auc(y_true, scores)` | `sklearn.metrics.roc_auc_score`(秩和实现) |

Go(`go/encoding.go`)实现同一组:`targetEncoding` / `autoSmooth` / `crossFittedEncoding` / `orderedTargetStatistics` / `auc` / `logregFit`。

## 性能边界

- 全部为 O(n) 单遍统计(除 AUC 的排序 O(n log n)),3000 样本 / 1500 类别在 2 秒内跑完。
- 编码本身对小数据**极其敏感**:类别频次 < 5 时,`smooth` 取值能改变编码的一倍以上。
- 本实现的逻辑回归是自写的全批量梯度下降(非 L-BFGS),AUC 的绝对水平低于 sklearn;实验 2 的结论看的是**组间差**,与分类器强弱无关。

## 注意事项与常见坑

1. **不要在 CV 之外做编码。** 只要编码器的 `fit` 看见了验证/测试样本的标签,分数就不可信 —— 包括"在整个训练集上 fit 编码器,再切 CV"这种看似合理的写法(正确做法是把 `TargetEncoder` 作为 `Pipeline` 的一级)。
2. **稀有类别必须平滑。** 频次 1 的类别在 `smooth=0` 时编码 = 标签本身,等于把答案编码成一列特征。
3. **训练期与推理期的编码不是同一个数。** 交叉拟合下训练期用"其他折的均值"、推理期用"全量训练集均值"。这个微小偏移是有意设计的权衡,不是 bug。
4. **`smooth='auto'` 不是万能的。** 它按类别内方差/全局方差定 `m`,当类别数极多时该比例的估计本身噪声很大。
5. **别用测试集验证"编码是否有效"。** 实验 2 的 B 行说明,一次不小心的全量 fit 就能让测试 AUC 从 `0.787` 变成 `0.911`,而且看起来一切正常。
6. **它与目标泄漏(target leakage)不同。** 这里说的是**特征与标签的统计泄漏**;真正的"未来信息泄漏"是另一类问题,两者常在同一份 pipeline 里同时出现。

## 参考资料

1. scikit-learn — *TargetEncoder* API(数值锚点来源:`target_mean_=44.3`、`encodings_` 示例、`smooth='auto'` 语义) —
   https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.TargetEncoder.html
2. scikit-learn 用户指南 — *Preprocessing data* / Target encoding 小节(cross fitting 的设计意图原话) —
   https://scikit-learn.org/stable/modules/preprocessing.html
3. CatBoost 官方文档 — *Transforming categorical features to numerical features*(ordered target statistics;经检索确认存在,本轮未逐页通读) —
   https://catboost.ai/docs/concepts/algorithm-main-stages_cat-to-number.html
