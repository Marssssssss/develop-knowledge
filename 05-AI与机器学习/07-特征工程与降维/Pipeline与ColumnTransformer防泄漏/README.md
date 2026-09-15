# Pipeline 与 ColumnTransformer —— 把"用 y 学参数"的步骤关进折内

## 简介

`Pipeline` 的官方文档列了三个用途,前两个是"便利"和"联合调参",第三个是 **Safety**:

> Pipelines help avoid leaking statistics from your test data into the trained model in
> cross-validation, by ensuring that the same samples are used to train the transformers
> and predictors.

本 demo 用纯标准库把 `Pipeline` / `ColumnTransformer` / `SelectKBest` / `StandardScaler` /
`OneHot` 的契约实现到"够暴露泄漏"的程度,然后**量化**泄漏值多少分、随什么变化,并演示
`ColumnTransformer` 的按列路由与 `remainder` 三种语义。

本 demo 的量化结论:同一份数据、同一个分类器,只把选择器从折外挪到折内,CV 分数差 **+0.107**
(n=100, p=10000);而在**完全没有信号**的数据上,折外流程照样刷出 **0.573**(抛硬币上限 0.5)。

## 原理详解

### 1. 什么是"用 y 学参数的步骤"

预处理分两类:

| 类别 | 例子 | 是否引入泄漏风险 |
| --- | --- | --- |
| **只依赖 X** 的变换 | `StandardScaler`(μ/σ 来自 X)、`MinMaxScaler`、`OneHot`、`SimpleImputer`(中位数) | 有,但较弱 —— 泄漏的是"X 的分布信息" |
| **依赖 y** 的变换 | `SelectKBest`、`TargetEncoder`、`LDA`、`RFE`、`PolynomialFeatures`+选择 | **强** —— 泄漏的是"答案本身" |

第二类必须放进 `Pipeline`。原因很直接:它们的 `fit` 会读 `y`;如果在全量数据上 `fit`,
验证折的标签就被用去构造特征了。

### 2. 泄漏的机制(为什么它不只是"信息多了"这么简单)

以 `SelectKBest` 为例。ANOVA F 值在全量数据上计算时,一个**纯噪声**特征要挤进 top-25,
必须"碰巧"把**全部 n 个样本**分开 —— 其中就包含后面会被当作验证集的那部分。于是这个特征的
取值方向与那些验证样本的标签**系统性地同向**;分类器在训练折上给它定的**符号**,到验证折上
依然正确 —— 它看起来"泛化"了,实际只是把选择阶段偷看过的信息在验证阶段兑现了一次。

这解释了实验 1、2 里两个反直觉的现象:泄漏**不抬高训练精度**,只抬高验证精度;
而且它**随样本量稀释** —— 样本越少、特征越多,越致命。

### 3. `Pipeline` / `ColumnTransformer` 的契约

- `Pipeline`:除最后一级外每级都必须实现 `fit` + `transform`;最后一级是估计器,只 `fit`。
  `fit` 时逐级 `fit_transform` 传数据,`predict` 时用已 `fit` 好的各级 `transform` 再调末级
  `predict`。真正的"安全"来自 `cross_val_score` —— 它在**每一折的训练部分**重新构造并 `fit`
  整个 pipeline,所以选择器、编码器、缩放器都只看见训练折。
- `ColumnTransformer`:每条路由 `("名字", 变换器, [列号…])` 独立 `fit`/`transform` 自己那几列,
  输出**横向拼接**;`remainder` 三选一 —— `'drop'`(默认)/ `'passthrough'` / 一个变换器实例。
  列号只声明一次、训练与推理共用,这是它相对"手工切列"的核心价值:避免列错位。

## 实验与实测结果

### 实验 1 —— 泄漏值多少分:随样本量 n 缩小

设定:p=10000 个特征,前 25 个等权信号(其余纯噪声),5 折 CV,`SelectKBest(25)`。

| n | coef | 泄漏式 CV | (σ_fold) | 正确式 CV | (σ_fold) | Δ |
| --- | --- | --- | --- | --- | --- | --- |
| 100 | 0.30 | **0.627** | 0.098 | 0.520 | 0.098 | **+0.107** |
| 200 | 0.30 | 0.542 | 0.064 | 0.542 | 0.071 | +0.000 |
| 400 | 0.30 | 0.550 | 0.045 | 0.500 | 0.058 | +0.050 |
| 100 | **0.00** | **0.573** | 0.093 | 0.490 | 0.071 | **+0.083** |

**最刺眼的是最后一行**:`coef=0.00` 意味着那 25 个"特征"和其它 9975 个一样是纯噪声、标签也是抛硬币生成的,
理论上限就是 0.5。但泄漏式照样报出 `0.573`。这 `+0.083` 分是**纯粹凭空变出来的** ——
一条只靠流程缺陷就能拿到的"性能",没有任何物理含义。

第二点是**稀释效应**:n=100 时 Δ=`+0.107`,n=200/400 时 Δ=`+0.000`/`+0.050`,
而这三档自身的单折波动 σ_fold ∈ [0.05, 0.10] —— 差值不再稳定地压在噪声之上。
**样本一多,单看一次实验就可能完全看不到这个泄漏**;而流程缺陷一直都在。

### 实验 2 —— 留出集上的形状(8 次随机重复,n=100)

| 重复 | 泄漏式 训练/测试 | 正确式 训练/测试 | 测试差 |
| --- | --- | --- | --- |
| 0 | 0.940 / 0.760 | 1.000 / 0.580 | +0.180 |
| 3 | 0.780 / 0.300 | 0.940 / 0.560 | **−0.260** |
| 4 | 0.980 / 0.640 | 0.860 / 0.440 | +0.200 |
| 7 | 0.980 / 0.440 | 1.000 / 0.460 | −0.020 |
| **均值** | — | — | **+0.053** |

泄漏式测试 `0.535 ± 0.146`,正确式 `0.482 ± 0.092`。注意 **2/8 次重复里泄漏式反而更低** ——
单次实验根本分辨不出来。泄漏不只污染 CV:任何"先用全部数据加工特征、再切分评估"的流程
(包括离线评测集的准备)都会被同样污染。

### 实验 3 —— `ColumnTransformer` 的路由与 `remainder`

5 列:0=年龄、1=收入、2=城市、3=等级、4=活跃天数(未被任何路由声明)。

| remainder | 训练列数 | 测试列数 | 测试第 0 行 |
| --- | --- | --- | --- |
| `'drop'`(默认) | 8 | 8 | `[-0.41, -0.35, 0, 0, 0, 1.00, 0, 0]` |
| `'passthrough'` | 9 | 9 | `[ …, +15.00]` ← 原样,未加工 |
| `StandardScaler()` | 9 | 9 | `[ …, -0.51]` ← 用训练集 μ/σ 标准化 |

前 8 列在三种 `remainder` 下**逐位相同** —— `remainder` 只影响"多出来的那几列"。

未见类别 `深圳`(只在测试集出现):全量 fit 的类别表宽度为 4,仅训练 fit 的宽度为 3。
用仅训练拟合的编码器处理测试集,`深圳` → `[0.0, 0.0, 0.0]`(全 0,不报错,列宽稳定,
等价于 sklearn 的 `handle_unknown='ignore'`)。**列宽必须由训练数据决定** ——
否则不同折之间特征矩阵宽度不一致,训练时 9 列、上线时 8 列,静默错位。

### 实验 4 —— 嵌套 CV:超参选择也要放在外层折之内

设定:n=150, p=300, 候选 k ∈ {5, 25, 100};外层 4 折 × 3 种子,内层 3 折。

| 协议 | 外层平均精度 |
| --- | --- |
| (A) 嵌套 CV:内层选 k,外层评估 | 0.615 |
| (B) 在外层验证集上调 k(偷看) | **0.664** |
| (C) 固定 k=25(不调超参,基准) | 0.602 |

内层 12 次选出的 k 分布:`{25: 4, 100: 8}`。

(B) 比 (A) 高 **+0.049**,而这部分差值**与特征选择无关**:(A)(B) 里每个评估器都是完整
`Pipeline`(选择在折内),唯一差别是"选 k 时看内层 CV 还是看外层验证集"。
**取最大这一步**把外层验证集的噪声挑了出来 —— 即使某个 k 的真实效果平平,
它在这一次切分上的随机波动也可能让它当选。

(C) 与 (A) 相差 `−0.013`,说明这个数据上的最优 k 随数据波动不大,内层选择的方差有限。

## 对比

| 写法 | 折内重建? | CV 分数可用? | 备注 |
| --- | --- | --- | --- |
| `X_sel = SelectKBest(k).fit_transform(X, y)` 后 `cross_val_score(clf, X_sel, y)` | ✗ | **不可用** | 本 demo 的"泄漏式" |
| `cross_val_score(Pipeline([...]), X, y)` | ✓ | 可用 | 官方推荐 |
| 手工在每折内 `fit` 变换器 | ✓ | 可用 | 等价但易漏步骤 |
| 固定超参 + `cross_val_score(Pipeline)` | ✓ | 可用 | 调了超参就要上嵌套 CV |

## 环境准备与运行

```bash
# Python(纯标准库,无需 pip install;实验规模较大,约需 3 分钟)
python python/main.py

# Go 版(同一套泄漏机制,实验 1/2 的 Go 实现:ml.go 为算法,main.go 为实验)
cd go && go run .
```

## 关键代码

`python/pipeline_lite.py` 导出:

| 本 demo | sklearn 对应 | 契约要点 |
| --- | --- | --- |
| `StandardScaler` | `StandardScaler` | μ/σ 只在 `fit` 数据上算 |
| `SelectKBest(k)` | `SelectKBest(score_func=f_classic)` | 组间平方和,`fit` 时定 `self.idx` |
| `OneHot` | `OneHotEncoder(handle_unknown='ignore')` | 类别表只在 `fit` 时收集 |
| `LogReg` | `LogisticRegression` | 最后一级估计器,`Transform` 为恒等 |
| `Pipeline(steps)` | `Pipeline` | 前 n-1 级 `fit_transform`,末级 `fit` |
| `ColumnTransformer(...)` | `ColumnTransformer` | 按列路由 + `remainder` 三语义 |
| `cross_val_score(make_est, X, y, k)` | `cross_val_score` | **每折重建**整个 estimator |

Go 版(`go/ml.go`)把变换器与分类器统一到一个 `Estimator` 接口
(`Fit` / `Transform` / `Predict`),`crossValScore` 通过工厂函数 `func() Estimator` 保证每折重建。

## 性能边界

- 分类器是自写的全批量梯度下降逻辑回归(非 L-BFGS),绝对精度低于 sklearn;实验结论看的是
  **组间差**,与分类器强弱无关。
- 实验 1 中 n=400、p=10000 的单折 `SelectKBest` 是 O(n·p) = 4×10⁶ 次比较,是这一批实验的耗时主体;
  实验 4 对同一个外层折要反复 `fit` 数十次,故把训练轮数从 1000 压到 250(代码中已注明)。

## 注意事项与常见坑

1. **`Pipeline` 只解决"折内重建",不解决"你手动拆步骤"。** 只要有一句 `fit_transform(X, y)`
   写在 `cross_val_score` 外面,整条 pipeline 的安全就没了。
2. **`ColumnTransformer` 的类别块宽度算错**是高频 bug:宽度 = Σ(各列类别数)。
   高基数类别列(比如用户 ID)会让宽度爆炸,这时应该用 `TargetEncoder` 而不是 one-hot。
3. **`remainder` 默认是 `'drop'`,不是 `'passthrough'`。** 忘了声明某列会被**静默丢弃**,
   而模型照常训练成功 —— 只是少了一个特征。
4. **列宽依赖"这一折见过哪些样本"**是 CV 里最难查的错:`OneHot` 若在全量数据上 `fit`,
   不同折的类别表不同,矩阵宽度随折变化,线性模型直接形状不匹配。
5. **调过超参的 CV 分数不能再当性能估计。** 见实验 4:只要你在同一批验证数据上试了多个配置
   并取最大,分数就被污染了 `+0.049`。要么上嵌套 CV,要么留一份从未被看过的测试集。
6. **`random_state` / 折的划分方式本身也会影响结论。** 本 demo 的实验 2 里 2/8 次重复符号相反,
   所以判断"泄漏有没有发生"要看**多次重复的均值**,而不是单次实验。

## 参考资料

1. scikit-learn — §12 *Common pitfalls* → *Data leakage*:§12.1 两条纪律、
   §12.2 特征选择泄漏的原始实验设定(n=100、10000 特征、选 25 个,泄漏式 ~0.7 / 正确式 ~0.5) —
   https://scikit-learn.org/stable/common_pitfalls.html
2. scikit-learn — §8.1 *Pipelines and composite estimators*:`Pipeline` 三大用途(含 Safety 原文)、
   `ColumnTransformer` 与 `remainder='drop'|'passthrough'|变换器` 语义 —
   https://scikit-learn.org/stable/modules/compose.html
3. scikit-learn — §3.4 *Preprocessing data*(`OneHotEncoder` 的 `handle_unknown='ignore'` 语义) —
   https://scikit-learn.org/stable/modules/preprocessing.html
