# 稀疏矩阵降维路线:TruncatedSVD 不中心化的代价 · IncrementalPCA 的分块近似

> demo 515 · `05-AI与机器学习/07-特征工程与降维/稀疏矩阵降维路线`
> Python(纯标准库,163 断言实跑) / Go(人工审查 + 静态检查 + 转写实跑)

## 一、这个 demo 在讲什么

PCA 有两条"省内存"的变体,代价各不相同:

- **`TruncatedSVD`**:入口只做**截断**,不做**中心化**。好处是能直接吃稀疏矩阵(减均值会把稀疏矩阵变成稠密的),
  代价是**第一个分量被均值方向吃掉**。
- **`IncrementalPCA`**:把数据切成小块逐块 `partial_fit`,每块只留 `2 × batch_size` 行在内存里。
  代价是**结果依赖 `batch_size`** —— 它不是"浮点误差级别的一致",而是真实的近似误差。

两个"代价"都不是传闻:一个是本 demo 实测量化的,一个是 sklearn 文档亲自写明的。

## 二、原理详解

### 2.1 TruncatedSVD 为什么不能中心化

减均值会把每个原本为 0 的条目变成非 0,稀疏结构立刻消失(密度从 1% 变成 100%),内存优势荡然无存。
所以 `TruncatedSVD` 选择不中心化,直接用 `X` 的 SVD。

后果可以精确描述:记 `μ` 为列均值,`X = 1μᵀ + X_c`。第一右奇异向量几乎等于 `μ/‖μ‖`,
于是 `k` 个分量里只有 `k−1` 个在描述**变动**。E2 实测:`不中心化 rank-(k+1)` 的重建误差与
`中心化 rank-k` 同级(相对差 < 2%)——**不中心化要多花一个分量**。

还有个更隐蔽的点(实测出来的,不是推的):`explained_variance_ratio_` 用的是
`np.var(投影列)`,而 `np.var` 会**减掉投影自己的均值**。均值方向的贡献因此被系统性地隐去 ——
E1 里 components_[0] 与列均值 μ 的余弦绝对值是 0.9999999785(方向完全就是均值),但它的
`ratio` 只有 **0.018**,反而不如中心化后的 0.241 好看。**别拿这个 ratio 判断"第一分量重不重要"。**

### 2.2 随机化截断 SVD 的两个旋钮

`TruncatedSVD(algorithm="randomized")` 走 Halko et al. 2009 的 Algorithm 4.3:
随机 Ω → 幂迭代 → `Q = qr(AΩ)` 取值域正交基 → 在小矩阵 `B = QᵀA` 上做精确 SVD → 拼回 `U = QÛ`。

- `n_iter`:幂迭代次数。源码里 `"auto"` 的规则是 `n_components < 0.1·min(shape)` 取 **7**,否则 **4**。
- `power_iteration_normalizer`:`"auto"` 时 `n_iter <= 2` 用 `"none"`,否则 `"LU"`。

E3 把两者分开了:
- **谱衰减慢**(σᵢ = 1/√(i+1)),子空间抓不住,靠 `n_iter` 补:主余弦 0.99897 → 1.0。
- **谱衰减快**(σᵢ = 0.5ⁱ),子空间一次就准,但 `none` 在 `n_iter=8` 时**自己坏掉**(残差 0.015623 → 0.022294),
  而 `LU` / `QR` 稳在 0.015623。这正是官方那句 "numerically unstable when `n_iter` is large, e.g. typically 5 or larger"
  —— 本 demo 复现了它。

### 2.3 IncrementalPCA 的增量均值方差

`fit` 的分块大小在 `batch_size=None` 时是 **`5 × n_features`**(1.9.1 的值,旧版是 10,别背错)。
分块用 `gen_batches`,它有一条容易漏的规则:尾块若不足 `min_batch_size`(= `n_components`),
则**该切片被跳过但不推进 `start`**,于是余量并进最后一块。

均值方差是 Chan/Golub/LeVeque 的**校正两趟法**:

```
T = new_sum / new_count;  temp = X − T
new_unnorm    = Σtemp² − (Σtemp)²/new_count          ← 减去校正项,这就是"校正两趟"
last_unnorm   = last_var · last_count
upd_unnorm    = last_unnorm + new_unnorm
                + (last_count/new_count)/upd_count · (last_sum/(last_count/new_count) − new_sum)²
upd_var       = upd_unnorm / upd_count
```

`last_count == 0` 时最后一项是 `0/0`:numpy 给 NaN 并由 `zeros` 掩码覆盖,纯 Python 会直接抛
`ZeroDivisionError` —— 所以那个分支**不可省**(自检里对这条做了负向断言)。

`partial_fit` 的拼装也很讲究:首块减**本块均值**;后续块减**本块均值**再把
`[S·V ; 去均值新块 ; mean_correction]` 三块摞起来做 SVD,其中
`mean_correction = √((seen/total)·n_batch)·(last_mean − col_batch_mean)`。

## 三、为什么 batch_size 会改变结果

每调一次 `partial_fit`,新的状态只保留 **`n_components` 行**(`S·V`),**残差方向被丢掉**。
块数越多,截断次数越多,丢掉的残差方向越多。E4 实测(k=3,60×6):

| batch_size | max Δmean | max ΔS | max Δcomponent |
| --- | --- | --- | --- |
| 4 | 4.4e-16 | 2.49e-01 | 8.27e-02 |
| 7 | 4.4e-16 | 1.70e-01 | 6.16e-02 |
| 13 | 2.2e-16 | 2.39e-01 | 6.64e-02 |
| 20 | 4.4e-16 | 1.09e-01 | 4.39e-02 |
| 37 | 4.4e-16 | 7.95e-02 | 3.72e-02 |
| 60(单块) | 0 | 0 | 0 |

**均值始终精确到 1e-16**(对称统计量),但奇异值差到 **1e-1**。sklearn 自己也一样:
同一份数据 `batch_size=4` 与 `=60` 的 `singular_values_` 差 **0.2875**。
官方文档把 `batch_size` 定义为「approximation accuracy 与内存消耗之间的权衡」——这句话是字面意思。

同为 60×6 数据下,`batch_size=17` 只把行序打乱,分量也会差到 1e-2 量级(E6):
均值与顺序无关,但**块的划分变了,截断残差方向的方式也跟着变**。

**推论**:想用 IncrementalPCA 复现全量 PCA,`batch_size` 必须 `>= n_samples`
(或直接 `fit` 一个不切块的数据),此时实测与中心化后的精确 SVD 逐位相同。

## 四、环境与运行

- Python 3.13,只用标准库(`math` / `bisect` 级别),无需 numpy / sklearn。
- Go 侧为同构转写,本机无 Go 工具链(`which go` 为空),走人工审查 + 静态检查。

```bash
cd python
python main.py                # 6 组实验(E1–E6)
python selfcheck_rsvd.py      # 99 条断言
python selfcheck_ipca.py      # 64 条断言
cd ../go && go run .          # 同包多文件必须用 `go run .`
```

## 五、关键代码

`TruncatedSVD` 的返回值有两个反直觉处(源码实读,不是推测):

```python
U, Sigma, VT = randomized_svd(X, k, ..., flip_sign=False)   # 先关掉内部符号修正
U, VT = svd_flip(U, VT, u_based_decision=False)             # 再按 v 的行来定符号
self.components_ = VT
X_transformed = matmul(X, transpose(self.components_))      # 是 X @ V,不是 U * Sigma
self.explained_variance_ = [_var(col) for col in transpose(X_transformed)]   # np.var 默认 ddof=0
full_var = sum(_var(col) for col in transpose(X))           # 分母是 X 各列 np.var 之和
```

`randomized_svd` 里 `flip_sign` 的判决基准随 transpose 分支切换(非 transpose 用 `u_based=True`,
transpose 用 `u_based=False`),所以"返回的 `U` 每一列最大绝对值元素非负"这一条在**两条分支上都成立** —— 
自检里就是这么断言的。

`svd_flip` 用的是 `np.sign`,**0 就是 0**(不是 +1):全零的行/列会被整行/整列抹平。本实现照抄了这个语义。

## 六、性能边界与实测数字

- 随机化 SVD 的代价按官方口径是 `O(nmax²·k)` 时间、`2·nmax·k` 内存(`nmax = max(n_samples, n_features)`),
  对照精确 SVD 的 `nmax²·nmin`;本 demo 未做大规模计时,只量化**精度**。
- 本 demo 的最大矩阵 120×60,单边 Jacobi SVD 在纯 Python 下秒级完成;规模再大需要换算法。
- 自检总断言数:**163**(99 + 64),另有 114 条与 sklearn 1.9.1 / scipy 1.18.1 的离线对拍(全部通过)。
- 结论只在"均值很大且列间结构远小于均值"这类计数型数据上谈"不中心化的代价";若各列均值本来就接近 0,
  这条代价几乎不存在(E2 的分母会告诉你剩多少)。

## 七、注意事项与常见坑

1. **别按 `explained_variance_ratio_` 判第一分量的重要性** —— `np.var` 减掉了投影均值,E1 里它报 0.018 而方向就是均值。
2. `TruncatedSVD` 的 `n_components` 必须 `<= n_features`,否则 `ValueError`;且 **`inverse_transform` 不是无损逆**。
3. `IncrementalPCA` 的 `batch_size` 是**精度旋钮**,不是纯性能旋钮;调小它会让结果偏离全量 PCA。
4. `gen_batches` 的 `min_batch_size` 会**并尾**,不是丢掉尾块 —— 断言块数时要按这个规则数。
5. `_incremental_mean_and_var` 的 `last_count == 0` 分支在纯 Python 下不写会直接抛 `ZeroDivisionError`。
6. `n_components` 在第一次 `partial_fit` 时必须 `<= 该块的样本数`,但**后续块没有这个限制**。
7. 特征数在两次 `partial_fit` 之间变化要报错(sklearn 靠 validator,本实现显式检查)。
8. 写 Vt 的装配时下标别写反:`Vt[k][i] = V[i][order[k]]`。写成 `V[order[k]][k]` 会得到一个秩 1 的矩阵,
   **奇异值看着完全正确**,只有重建和正交性会崩 —— 本 demo 开发期就这么翻过一次车。

## 八、参考资料(实际阅读过的来源)

- scikit-learn 1.9.1 已安装源码 `sklearn/decomposition/_truncated_svd.py`
  —— `fit_transform` 全文:`flip_sign=False` 后自行 `svd_flip(..., u_based_decision=False)`、
  `X_transformed = X @ components_.T`、`explained_variance_ = np.var(X_transformed, axis=0)`、
  `full_var = np.var(X, axis=0).sum()`、`n_components > n_features` 抛错
- scikit-learn 1.9.1 `sklearn/utils/extmath.py` —— `randomized_svd`(n_iter="auto" 的 7/4 规则、
  transpose="auto"、两条分支各自的 svd_flip 基准)、`randomized_range_finder`(Ω 形状为 `(n_features, size)`、
  normalizer 的 auto/none/LU/QR 选择、收尾必做一次 `qr(A@Q)`)、`svd_flip`(用 `np.sign`)、
  `_incremental_mean_and_var`(校正两趟法与 `zeros` 掩码)
- scikit-learn 1.9.1 `sklearn/decomposition/_incremental_pca.py` —— `fit` 的 `batch_size_ = 5 * n_features`、
  `partial_fit` 的三块拼装与 `mean_correction`、`explained_variance_ratio_ = S²/Σ(col_var·n_total)`、
  `noise_variance_` 的两个分支;文档把 `batch_size` 描述为「approximation accuracy 与内存消耗」的权衡
- scikit-learn 1.9.1 `sklearn/utils/_chunking.py` —— `gen_batches` 的 `continue` 不推进 `start`(即并尾规则)
- scikit-learn §2.5.1 Decomposing signals in components(PCA / IncrementalPCA / Randomized PCA)
  <https://scikit-learn.org/stable/modules/decomposition.html> —— `nmax²·k` 与 `nmax²·nmin` 的复杂度对比、
  `n_oversamples` 默认 10 的含义
- scikit-learn `sklearn.decomposition.TruncatedSVD` API
  <https://scikit-learn.org/stable/modules/generated/sklearn.decomposition.TruncatedSVD.html>
  —— "this estimator does not center the data"、稀疏输入的支持范围
- scikit-learn `sklearn.utils.extmath.randomized_svd` API
  <https://scikit-learn.org/stable/modules/generated/sklearn.utils.extmath.randomized_svd.html>
  —— `n_iter` / `power_iteration_normalizer` 的官方语义,含 "numerically unstable when n_iter is large,
  e.g. typically 5 or larger"
- Halko, Martinsson, Tropp, *Finding structure with randomness*, 2009 <https://arxiv.org/abs/0909.4061>
  —— Algorithm 4.3(`randomized_range_finder` 的出处,由 sklearn 文档注释指向)
- Chan, Golub, LeVeque, *Algorithms for computing the sample variance: analysis and recommendations*,
  The American Statistician 37(3), 1983 —— 校正两趟法的出处(由 sklearn 源码注释指向)
- scipy 1.18.1 `scipy.linalg.lu(permute_l=True)` —— 离线对拍 LU 的参照(5 种形状 × 3 项)

## 九、自检

| 脚本 | 断言数 | 覆盖 |
| --- | --- | --- |
| `python/selfcheck_rsvd.py` | 99 | LU 重建/三角性、range finder 正交与值域、rsvd 三形状与两分支符号、Eckart–Young 上下界 |
| `python/selfcheck_ipca.py` | 64 | gen_batches 并尾规则、Youngs-Cramer 一致性、单块==精确 PCA、batch/顺序敏感度、4 条错误路径 |
| 离线对拍(仓库外) | 114 | `gen_batches` / `lu` / `_incremental_mean_and_var` / `IncrementalPCA` 6 种 batch 配置逐属性对齐 sklearn 与 scipy |
| Go 转写实跑 | 49 组 | `genBatches` 9 组 + `incrementalMeanAndVar` 40 组逐值比对 |
