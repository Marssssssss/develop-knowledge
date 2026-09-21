# 幂变换与分位数变换

偏态特征会把线性模型、距离度量、梯度下降一起带偏。**幂变换**用一族单调函数把数据拉成
钟形,**分位数变换**直接用经验 CDF 把每一列压成均匀或正态分布。两者都逐列独立、都可逆,
但代价完全不同:前者是参数化的(只动形状,尾部仍可能很长),后者是非参数的(**任何分布
都能变成接近正态,但线性关系会被改写**)。

- **Box-Cox(1964)**:`(x^λ−1)/λ`,只接受**严格正数**。
- **Yeo-Johnson(2000)**:四分支版本,负数走 `2−λ` 的镜像,正负通吃。
- **剖面对数似然**:λ 的估计目标;scipy 的 `*_llf` 就是它(丢掉了与 λ 无关的常数)。
- **两种 λ 口径**:`boxcox()` 走 MLE,`boxcox_normmax()` **默认走 pearsonr** —— 数值不同。
- **分位数变换**:先估经验 CDF 映到均匀分布,再用目标分布的**逆 CDF** 映出去;越界值夹到端点。

## 一、原理详解

### 1.1 两个变换的形式(scipy 文档原式)

```
Box-Cox:      y = (x^λ − 1)/λ  (λ≠0);  y = log x            (λ=0)       要求 x > 0
Yeo-Johnson:  x ≥ 0, λ≠0 → ((x+1)^λ − 1)/λ ;  λ=0 → log(x+1)
              x < 0, λ≠2 → −((−x+1)^(2−λ) − 1)/(2−λ) ;  λ=2 → −log(−x+1)
```

Yeo-Johnson 的负半轴不是「绝对值再取负」那么简单:它把 λ 换成 `2−λ` 才能保证
`YJ(−x, λ) = −YJ(x, 2−λ)`,从而整条曲线在 0 点光滑(自检里对这条恒等式有断言)。
两者在 x>0 上不是同一个映射(λ 的含义不同),但都能把偏度压下去。

### 1.2 λ 怎么定:剖面对数似然

scipy 的两个入口对应**两个完全不同的目标**:

| 入口 | 目标 | 出处 |
| --- | --- | --- |
| `stats.boxcox(x)` | 极大化 `boxcox_llf`(**MLE**) | 文档:"the method used in boxcox" |
| `stats.boxcox_normmax(x)` | 默认 `method='pearsonr'` | 文档里的默认值就是 pearsonr |
| `stats.boxcox_normmax(x, method='mle')` | 同第一个 | — |
| `stats.yeojohnson(x)` / `yeojohnson_normmax(x)` | 均为 MLE | 无 pearsonr 分支 |

剖面对数似然(已丢常数项,只留 λ 的极值位置有意义):

```
boxcox_llf:      l = (λ−1)·Σ log(x_i) − (N/2)·log( Σ(y_i − ȳ)² / N )
yeojohnson_llf:  l = −(N/2)·log(σ̂²)  + (λ−1)·Σ sign(x_i)·log(|x_i| + 1)
```

`pearsonr` 口径则是让**概率图**上「期望正态次序统计量」与「排序后的变换值」相关性最大:
期望值用 Filliben(1975) 的均匀次序统计量中位数近似——`v[0] = 1 − 0.5^(1/n)`、
`v[i] = (i−0.3175)/(n+0.365)`、`v[n−1] = 0.5^(1/n)`,再取 `ppf`。

### 1.3 分位数变换:三步与两条边界规则

```
fit:    references = linspace(0, 1, n_quantiles, endpoint=True)
        quantiles_[:, j] = percentile(X[:, j], references·100, method=…)
trans:  p = 0.5·[ interp(x, quantiles, references) − interp(−x, −quantiles[::-1], −references[::-1]) ]
        x ≤ quantiles[0] → p = 0 ;  x ≥ quantiles[-1] → p = 1
        output_distribution='normal' → 再取 ppf(p),并夹到 ±5.1993375826
        output_distribution='uniform' → 直接用 p
```

- **为什么要两个方向取平均**:分位数有重复(离散特征、大量并列)时,单向插值只会取到
  平台的上沿或下沿,平均后才是平台中点。
- **夹取阈值**:`BOUNDS_THRESHOLD = 1e-7`,normal 输出先 `ppf` 再夹到
  `ppf(1e-7 − spacing(1)) = ±5.1993375826`;uniform 输出用**严格相等**判边界。
- **新数据越界**:低于训练最小值 → 0(−5.1993),高于最大值 → 1(+5.1993)。
  这是它「抗离群」的来源,也是「测试集尾部全被压平」的来源。
- **`n_quantiles` 是分辨率**:1.9 及以前会被样本数封顶(`n_quantiles_ = min(n_quantiles, n_samples)`)。

## 二、对比 / 选型

| 变换 | 输入要求 | λ/参数 | 抗离群 | 对线性关系 | 可逆 |
| --- | --- | --- | --- | --- | --- |
| StandardScaler | 无 | 无 | 差 | 保持(线性) | 是 |
| Box-Cox | **严格正** | 逐列 MLE | 一般 | 单调非线性 | 是 |
| Yeo-Johnson | 任意 | 逐列 MLE | 一般 | 单调非线性 | 是 |
| QuantileTransformer | 无(稀疏需非负) | n_quantiles | **强** | **normal 输出会压低相关** | 近似 |

## 三、环境准备

- 操作系统:任意(Python 3.9+ / Go 1.21+;Go 侧用标准库 `math.Erfinv`,勿手抄有理逼近)
- 依赖:**无第三方库**
- 开发期另用 scipy 1.18.1 / scikit-learn 1.9.1 做逐值对拍(运行本 demo 不需要)

## 四、运行方式

```bash
cd python && python main.py                  # 6 组实验
cd python && python selfcheck_quantile.py    # 自检(58 条断言)
cd go && go run .                            # 与 Python 同题、同随机源
```

## 五、关键代码片段

```python
def boxcox_llf(lam, x):            # 剖面对数似然,λ 的极大点才是 MLE 估计
    y = [boxcox(v, lam) for v in x]
    var = sum((v - sum(y)/len(y))**2 for v in y) / len(y)
    return (lam - 1) * sum(map(math.log, x)) - len(x)/2 * math.log(var)

def percentile_linear(x, q):       # numpy 默认口径,Hyndman-Fan type 7
    h = (len(x) - 1) * q / 100
    lo = math.floor(h)
    return x[lo] + (h - lo) * (x[lo + 1] - x[lo])

# 分位数变换的两个方向取平均(否则平台只会取到上/下沿)
v = 0.5 * (interp(x, quantiles, refs) - interp(-x, [-q for q in quantiles[::-1]],
                                              [-r for r in refs[::-1]]))
```

## 六、实测结果(`python/main.py` 输出)

| 项 | 读数 |
| --- | --- |
| 300 个对数正态样本 | MLE λ = −0.053531489;`boxcox_normmax` 默认 pearsonr λ = −0.055428394(Δλ≈1.9e-3) |
| 偏度 | 原始 3.3489 → Box-Cox −0.0027 → Yeo-Johnson 0.0267 |
| sklearn 文档例 `[[1,2],[3,2],[4,5]]` | `lambdas_` = [1.38668182, −3.10053331](我算 [1.38668181, −3.10053310]) |
| 越界探针(uniform / normal) | `[0, 0.5224, 1, 1]` / `[−5.199338, 0.0561, 5.199338, 5.199338]` |
| 10/10/10 三台阶 | 台阶内映射到同一点:0 / 0.5 / 1,极差全为 0 |
| 联合正态列(a, 0.9a+噪声) | 原始 0.8619;标准化 0.8619;YJ 0.8481;分位数-uniform 0.8645;**分位数-normal 0.8334** |
| 参照:Blom 精确正态得分 | 0.8612(nq 越小压低越多:10 → 0.8165,5 → 0.7842) |

## 七、注意事项与常见坑

1. **`boxcox()` 与 `boxcox_normmax()` 的 λ 不是一回事**:后者默认 `pearsonr`。要复现别人的
   结果,必须写清是哪个入口、哪个 method。
2. **Box-Cox 遇到非正数**:`stats.boxcox` 直接 ValueError;`scipy.special.boxcox` 返回 nan
   (x=0 且 λ<0 时 −inf)。Python 里 `(-1.0)**0.5` 会**静默**变成复数,务必自己加闸。
3. **分位数变换的 normal 输出会压低线性相关**:实测把 0.8619 压到 0.8334,而 uniform 输出
   几乎不动。原因是地标位置取 `i/(n_quantiles−1)`(端点 0 与 1)并夹到 ±5.1993,
   而不是经典正态得分的位置;`n_quantiles` 越小压得越多。
   sklearn 文档只定性说 "may distort linear correlations",这里给出了量化读数。
4. **越界值全被压成同一个数**:测试集里超出训练范围的部分,uniform 输出全变 0 或 1,
   normal 输出全变 ±5.1993 —— 树模型无所谓,线性模型会丢掉这段的排序信息。
5. **`n_quantiles` 别乱设**:小于 10 时映射变成粗台阶,单调性还在但分辨率极低。
6. **重复值(平台)**:分位数有重复,单向插值只取上/下沿,官方实现靠「两个方向取平均」修正;
   自己重写时漏掉这一步,离散特征会被系统性映射偏高。
7. **稀疏矩阵**:`ignore_implicit_zeros=False` 时要求非负;隐式零会被当成真零参与分位数。

## 八、参考资料(实际阅读过的来源)

- [scipy.stats.boxcox](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.boxcox.html)
  —— Box-Cox 公式、"requires the input data to be positive"、α 时的 χ²(1−α) 置信区间判据、
  "the method used in boxcox" 即 MLE
- [scipy.stats.boxcox_normmax](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.boxcox_normmax.html)
  —— **默认 `method='pearsonr'`**、"‘mle’ … This is the method used in `boxcox`"、brack 默认 (−2,2)
- [scipy.stats.yeojohnson](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.yeojohnson.html) /
  [yeojohnson_normmax](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.yeojohnson_normmax.html)
  —— 四分支公式、"Unlike boxcox, yeojohnson does not require the input data to be positive"
- [scipy.stats.boxcox_llf](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.boxcox_llf.html) /
  [yeojohnson_llf](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.yeojohnson_llf.html)
  —— 两条剖面对数似然的完整式子(本仓库按此实现,并对拍一致)
- [scipy 源码 `scipy/stats/_morestats.py`](https://cdn.jsdelivr.net/gh/scipy/scipy@main/scipy/stats/_morestats.py)
  —— `boxcox_normmax` 的 pearsonr 分支(排序后与 `norm.ppf(osm_uniform)` 求相关,返回 1−r 再最小化)、
  `_calc_uniform_order_statistic_medians` 的 Filliben 近似(0.5^(1/n) 与 0.3175/0.365 两个常数)
- [scikit-learn `PowerTransformer` API](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.PowerTransformer.html)
  —— `method` 默认 yeo-johnson、`standardize=True` 的含义、**文档例 `lambdas_=[1.386, −3.100]`** 与输出
- [scikit-learn `QuantileTransformer` API](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.QuantileTransformer.html)
  —— n_quantiles 的封顶规则、越界映射到端点、`ignore_implicit_zeros`、"may distort linear correlations"、
  1.5 起 `subsample=None` 可关子采样
- [scikit-learn §6.3 Preprocessing data](https://scikit-learn.org/stable/modules/preprocessing.html)
  —— 幂变换与分位数变换在官方预处理体系中的定位与使用建议
- [scikit-learn 源码 `sklearn/preprocessing/_data.py`](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/preprocessing/_data.py)
  —— `BOUNDS_THRESHOLD = 1e-7`、`_transform_col` 的两个方向插值与夹取、
  `references_ = linspace(0,1,n_quantiles,endpoint=True)`、main 分支改用 `averaged_inverted_cdf`
  (1.9.1 实测仍是 numpy 默认 `linear`,已在自检里分别对拍)
