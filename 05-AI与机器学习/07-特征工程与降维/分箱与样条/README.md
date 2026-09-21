# 分箱与样条

连续特征喂给线性模型之前,有两种「先掰弯再拟合」的路子:**分箱**把每一列切成阶梯常数
(非线性、抗噪、可解释,但边界外全被压平),**样条**用一组局部多项式基替换掉原始列
(平滑、外推可控,但基的个数与结位置都要自己定)。两者都逐列独立,也都**改变特征空间的
维度**——而且都有一堆默认值不合直觉的地方。

- **KBinsDiscretizer**:三种 `strategy`(`uniform` / `quantile` / `kmeans`)、三种编码
  (`ordinal` / `onehot` / `onehot-dense`)。
- **SplineTransformer**:`n_splines = n_knots + degree - 1` 个 B-spline 基,五个
  `extrapolation` 模式,`periodic` 专治「12/31 → 1/1 跳变」。

## 一、原理详解

### 1.1 KBinsDiscretizer 的三步

```
fit:    逐列求 col_min / col_max
        uniform  : edges = linspace(col_min, col_max, n_bins + 1)
        quantile : edges = percentile(col, linspace(0, 100, n_bins + 1), method=…)
        kmeans   : 1D k-means(init = 均匀箱中点,n_init = 1),edges = 相邻中心的中点
                  +(首尾补 col_min / col_max)
        quantile / kmeans 还要丢掉宽度 <= 1e-8 的箱(首元素永远保留)
transform:
        bin = searchsorted(edges[1:-1], x, side="right")
onehot : 每列按 n_bins_[j] 展成一块 one-hot
```

三条容易看漏的口径:

1. **首尾边界只服务 `inverse_transform`**,transform 时等效于把边界摊成
   `[-inf, edges[1:-1], +inf]`(官方 Notes 里明确写了这条 `np.concatenate`)。
   所以**越界值不报错,而是静默折进端点箱**——`out_of_bounds` 参数已被移除。
2. **`side="right"`**:恰好落在内部边界上的点归**上**一箱(`bisect_right`)。
3. **`n_bins` 是上限不是结果**:过滤窄箱后 `n_bins_[j]` 可能更小。

### 1.2 quantile 的三种口径(numpy 定义)

```
linear  (H&F type 7, numpy 默认): idx = (n-1)·q
inverted_cdf (type 1):            idx = n·q - 1;gamma == 0 取 prev,否则取 next
averaged_inverted_cdf (type 2):   idx = n·q - 1;gamma 被强制成 0.5(γ=0)或 1.0
```

`KBinsDiscretizer` 的 `quantile_method` **默认是 `averaged_inverted_cdf`**,不是
numpy 的默认 `linear`。而 `SplineTransformer(knots="quantile")` 用的是
`np.nanpercentile` 的**默认口径 `linear`**——同一个库内两种口径。

### 1.3 B-spline 基与结

```
n_splines = n_knots + degree - 1        (non-periodic)
          = n_knots - 1                 (periodic)
base 结   : uniform  → linspace(min, max, n_knots)
            quantile → nanpercentile(…, linspace(0,100,n_knots))(默认 linear)
端外结    : 下方 degree 个,间距 = base[1]-base[0];上方同理用 dist_max
            —— **不重复首末结**(Eilers & Marx 的建议,官方注释否掉了 np.tile 写法)
```

Cox-de Boor 递推给出基值(`B_{i,0}` 为区间指示函数,分母为 0 的项取 0):

```
B_{i,k}(x) = (x-t_i)/(t_{i+k}-t_i)·B_{i,k-1}(x) + (t_{i+k+1}-x)/(t_{i+k+1}-t_{i+1})·B_{i+1,k-1}(x)
```

基在数据范围内满足**单位分解**:每行之和恒为 1 —— 这就是「隐含截距列」的来源,
`include_bias=False` 丢掉的正是每列的最后一个基。

### 1.4 五个 extrapolation 模式

| 模式 | 域外取值 |
| --- | --- |
| `error` | 抛 `ValueError`("values beyond the limits of the knots") |
| `constant` | 冻结在边界基值 `f_min` / `f_max`(默认) |
| `linear` | `f_min[j] + (x-xmin)·f'_min[j]`,只有首/末 degree 个基参与 |
| `continue` | 端点 span 上的多项式**原样延拓**(scipy `extrapolate=True`) |
| `periodic` | 把 x 折进 `[t[k], t[n]]`;周期 = `base[-1]-base[0]` |

`linear` 用的是一阶导。**唯一需要区分左右导的情形是 degree=1**:一阶 B-spline 在结上
只是 C⁰,scipy 取左单侧导,写错会让 `linear` 外推的斜率差一个符号。

## 二、对比 / 选型

| 方案 | 输出维度 | 非线性 | 外推 | 主要风险 |
| --- | --- | --- | --- | --- |
| 原始列 | 不变 | 无 | 无 | 线性模型欠拟合 |
| KBins + onehot | n_bins 倍 | 阶梯 | 端点箱压平 | 空箱 → 常量列 |
| KBins + ordinal | 不变 | 阶梯 | 同上 | 人为引入序关系 |
| SplineTransformer | n_knots+degree-1 倍 | 平滑 | 五种可选 | 基增多、外推放大 |
| 分箱 + 样条混用 | — | — | — | 结位置与箱边界不一致 |

## 三、环境准备

- 操作系统:任意(Python 3.9+ / Go 1.21+)
- 依赖:**无第三方库**
- 开发期另用 scikit-learn 1.9.1 + scipy 1.18.1 + numpy 2.5.3 逐值对拍(跑本 demo 不需要)

## 四、运行方式

```bash
cd python && python main.py               # 6 组实验
cd python && python selfcheck_bins.py     # 自检(257 条断言)
cd go && go run .                         # 与 Python 同题、同口径(本 demo 全确定性)
```

## 五、关键代码片段

```python
def _cox_de_boor(t, degree, x, left_closed=False):
    n = len(t) - degree - 1
    if degree == 0:
        return [1.0 if t[i] <= x < t[i + 1] else 0.0 for i in range(n)]
    prev = _cox_de_boor(t, degree - 1, x, left_closed)
    out = []
    for i in range(n):
        d1 = t[i + degree] - t[i]
        d2 = t[i + degree + 1] - t[i + 1]
        a = (x - t[i]) / d1 * prev[i] if d1 > 0 else 0.0
        b = (t[i + degree + 1] - x) / d2 * prev[i + 1] if d2 > 0 else 0.0
        out.append(a + b)
    return out

def code_at(edges, v):        # 边界点归上一箱
    return bisect.bisect_right(edges[1:-1], v)

# averaged_inverted_cdf:gamma 被**强制**成 0.5 或 1.0,不是插值系数
g = 0.5 if raw_gamma == 0 else 1.0
```

## 六、实测结果(`python/main.py` 输出)

| 项 | 读数 |
| --- | --- |
| 文档例 `n_bins=3 uniform` 列0 | 边界 `[-2,-1,0,1]`,编码 `[0,1,2,2]`(与官方一致) |
| 同数据 `kmeans` | 边界 `[-2,-0.75,0.5,1]`,编码 `[0,0,1,2]`(≠ 等分箱) |
| `[1,2,3]` 三种口径边界 | linear `[1,1.6667,2.3333,3]`;avg_inv `[1,1.5,2.5,3]`;inverted_cdf 过滤后 `[1,2,3]` 且箱数掉到 2 |
| 越界探针(边界 `[0,.25,.5,.75,1]`) | `[-5,-0.001,0,.25,.5,.75,1,1.001,9]` → `[0,0,0,1,2,3,3,3,3]` |
| 常量列 | `edges=[-inf,+inf]`、`n_bins_=1`、编码全 0、**反变换 = nan** |
| 窄箱(7 点含 1e-12,`n_bins=5`) | linear 未过滤 `[0,0,4e-13,0.6,1.8,3]` → 过滤后 3 箱;avg_inv 直接 `[0,1,2,3]` |
| 11 点等距 `kmeans n_bins=4` | `[0,0.25,0.525,0.775,1]`(内部边界**不在** 0.5) |
| degree=3 `base=[0,⅓,⅔,1]` 结向量 | `[-1,-⅔,-⅓,0,⅓,⅔,1,4/3,5/3,2]` |
| 每个模式的行和 | 域内与域外恒为 1(`constant`/`linear`/`continue`/`periodic` 全部成立) |
| degree=3 域外三行(列 0) | `constant` 冻结在 `f_min`;`linear` `[0.9167,0.6667,-0.5833,…]`;`continue` `[2.6042,-3.2708,2.2292,-0.5625,…]` |
| `periodic`(degree 3,周期 1) | `-0.5` / `0.5` / `1.5` 同余于 0.5 → 三行**完全相同** |
| `quantile` 结(7 点,`n_knots=5`) | `[0,0,0,0,1,2.5,10,17.5,25]`(base 结允许重复) |

## 七、注意事项与常见坑

1. **越界值静默折进端点箱**,不报错。想发现越界只能自己比对 min/max,或改用
   `SplineTransformer(extrapolation="error")`。
2. **`quantile_method` 默认 `averaged_inverted_cdf`**,与 numpy 默认的 `linear` 不同;
   `inverted_cdf` 会产生重复边界,被 1e-8 规则过滤后**箱数变少**(只给 warning)。
3. **`n_bins` 只是上限**,过滤后看 `n_bins_[j]` 而不是自己传进去的值。
4. **常量列的 `inverse_transform` 是 nan**:箱中心算的是 `(-inf + inf)/2`。
5. **kmeans 策略不是等分箱**:内部边界由中心中点决定,实测 11 点等距数据得到
   0.525 而不是 0.5。
6. **样条基的行和恒为 1**,所以别再往特征里补常数列,否则完全共线;要截距就
   `include_bias=False` 并让模型自带截距。
7. **`include_bias=False` 丢的是每列最后一个基**,不是第一个;输出维度是
   `n_features × (n_splines - 1)`。
8. **`extrapolation="constant"` 是默认值**,域外行等于边界行而不是 0;测试集尾部全被
   压成同一个向量。
9. **`periodic` 的周期是 `base[-1] - base[0]`**,不是 2π。做「星期」「DOY」这类特征
   必须自己给 knots 控制周期,否则折出来的相位是错的。
10. **degree=1 在结上是 C⁰**,左/右导不相等;写 `linear` 外推时要和 scipy 一样取左单侧导。
11. 分箱后再 onehot 容易造出**常量列**(空箱),记得用 `VarianceThreshold` 之类清掉。

## 八、参考资料(实际阅读过的来源)

- [scikit-learn `KBinsDiscretizer` API](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.KBinsDiscretizer.html)
  —— 三种 `strategy` 与 `encode` 的定义、"Number of bins must be at least 2"、
  Notes 里那条 `np.concatenate([-np.inf, bin_edges_[i][1:-1], np.inf])`
- [scikit-learn `SplineTransformer` API](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.SplineTransformer.html)
  —— `n_splines = n_knots + degree - 1`(`periodic` 为 `n_knots - 1`)、五个
  `extrapolation` 的语义、`include_bias` 的说明、`handle_missing`(1.8+)
- [scikit-learn §6.3 Preprocessing data](https://scikit-learn.org/stable/modules/preprocessing.html)
  —— 分箱与样条在官方预处理体系中的定位
- [scikit-learn 源码 `sklearn/preprocessing/_discretization.py`](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/preprocessing/_discretization.py)
  —— `searchsorted(..., side="right")`、窄箱过滤的 `np.ediff1d(..., to_begin=np.inf) > 1e-8`、
  常量列分支、`_validate_n_bins`、`inverse_transform` 的箱中心公式
- [scikit-learn 源码 `sklearn/preprocessing/_polynomial.py`](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/preprocessing/_polynomial.py)
  —— 结扩展(`dist_min` / `dist_max` 复用,注释引 Eilers & Marx)、`linear` 分支的
  `if degree <= 1: degree += 1`、`constant` 分支只改首/末 degree 列、periodic 的
  `coef` 拼接与 `x % period` 折回
- numpy 源码 `numpy/lib/_function_base_impl.py`(本机 2.5.3)
  —— `_QuantileMethods` 里 `inverted_cdf` / `averaged_inverted_cdf` 的 `fix_gamma`、
  `_get_indexes` 的越界夹取、H&F 的 `m` 表(type 1/2/7 的定义)
- [scipy `BSpline` API](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.BSpline.html)
  —— `extrapolate=True` 的语义(线性外推多项式延拓的官方依据)
- Eilers & Marx, *Flexible smoothing with B-splines and penalties*(DOI 10.1214/ss/1038425655)
  —— **未直接阅读**,经 sklearn 源码注释转引(仅用于解释端外结为何不重复)
