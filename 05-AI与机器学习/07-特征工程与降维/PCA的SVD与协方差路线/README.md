# PCA 的 SVD 与协方差路线

## 简介

同一个 PCA,sklearn 提供了两个**精确**求解器:`svd_solver='covariance_eigh'`(先物化协方差矩阵
再对它做特征分解)与 `svd_solver='full'`(直接对中心化后的数据矩阵做 SVD)。二者数学上等价
(`λ_i = σ_i²/(n-1)`),但在浮点里**不是同一件事**:官方文档对 `covariance_eigh` 的原话是
"compared to the `full` solver, this solver effectively doubles the condition number and is
therefore less numerically stable (e.g. on input data with a large range of singular values)"。
本 demo 用两套自实现的 Jacobi 算法把这句话量化出来,并复现官方文档里的数值锚点。

关键概念:

| 概念 | 一句话 |
| --- | --- |
| `components_` | 中心化数据的**右奇异向量**(= 协方差矩阵的特征向量);X 已中心化、**未缩放** |
| `explained_variance_` | 协方差矩阵的前 `n_components` 个**特征值**(按 `n_samples-1` 自由度估计) |
| `singular_values_` | 低维空间里各变量的 2-范数,即 `σ_i = √(λ_i·(n-1))` |
| `whiten=True` | `components_` 乘以 `√n_samples` 再除以奇异值 ⇒ 各分量方差恒为 1 |
| 条件数 κ | `σ_max/σ_min`;协方差矩阵的条件数是它的**平方** |

历史背景:这是数值线性代数里的经典取舍 —— "正规方程(eigen-of-covariance)"路线比正交化路线
快,但精度差一个 κ 因子;LAPACK 时代起,用 SVD/QR 做最小二乘与主成分就是教科书建议,而
sklearn 1.5 仍把 `covariance_eigh` 加回来,是因为 `n_samples >> n_features` 时物化 `p×p`
矩阵反而更快(官方 `auto` 策略里写明:特征数 < 1000 且样本数是其 10 倍以上 → 选它)。

## 原理详解

1. **中心化**:`Xc = X - 1μᵀ`。官方原文 "PCA centers but does not scale the input data for
   each feature before applying the SVD" —— 只去均值,**不**按标准差缩放。
2. **路线一(协方差)**:`C = XcᵀXc/(n-1)`,对 `C` 做对称特征分解 `C = QΛQᵀ`,
   `components_ = Q[:, :k]ᵀ`,`explained_variance_ = Λ[:k]`。
3. **路线二(直接 SVD)**:`Xc = UΣVᵀ`,`components_ = Vᵀ[:k]`、`singular_values_ = Σ[:k]`,
   `explained_variance_ = Σ²[:k]/(n-1)`。
4. **等价性**:把 `XcᵀXc` 代入 SVD 得 `XcᵀXc = VΣ²Vᵀ`,于是 `λ_i = σ_i²/(n-1)`。
5. **精度差在哪**:两条路线的输入噪声都是 ε≈2.2e-16,但传到结果上的放大倍数不同 ——

   ```
   σ 的绝对误差     ~ ε·σ_max          ⇒ σ_min 的相对误差 ~ ε·κ
   λ 的绝对误差     ~ ε·λ_max=ε·σ_max² ⇒ σ=√(λ(n-1)) 的相对误差 ~ ε·κ²/2
   ```

   即**协方差路线把小奇异值的相对误差放大了 κ/2 倍**。当 κ > 1/√ε ≈ 6.7e7 时,
   `λ_min` 的误差甚至能盖过它自己 ⇒ 特征值算成**负数** ⇒ 开方得到 nan。
6. **官方 `auto` 策略**(`svd_solver='auto'`):特征数 < 1000 且样本数 > 10×特征数 →
   `covariance_eigh`;数据 > 500×500 且 `n_components` < 最小维度的 80% → `randomized`;
   否则精确 `full` SVD(再按需截断)。
7. **本 demo 的实现**:`jacobi_svd`(单边 Jacobi:反复用 2×2 旋转把列两两正交化,
   列范数即奇异值)+ `jacobi_eigh`(循环 Jacobi 对称特征分解)。两者共享同一套基础算法,
   所以观察到的差异只可能来自"协方差"这一步,而不是求解器水平不同。

## 实测结果(本机 float64)

实验 1 复现 sklearn 文档 docstring 的样例 `X = [[-1,-1],[-2,-1],[-3,-2],[1,1],[2,1],[3,2]]`:

```
奇异值       : [6.30061, 0.54980]   期望 [6.30061, 0.54980]
方差解释比例 : [0.9924,  0.0076]    期望 [0.9924,  0.0075]
λ_i = σ_i²/(n-1) 校验:True
```

实验 2 造一个奇异值恰好为 `[1, 1e-2, 1e-4, 1e-8, 1e-10, 1e-12]`(κ=1e12)的 200×6 矩阵:

```
i   σ_真值    σ_SVD路线     相对误差    σ_协方差路线  相对误差
0   1e+00    1.000000e+00  0.00e+00   1.000000e+00  0.00e+00
1   1e-02    1.000000e-02  3.47e-16   1.000000e-02  2.32e-14
2   1e-04    1.000000e-04  1.69e-14   1.000000e-04  5.60e-10
3   1e-08    1.000000e-08  2.31e-10   9.790124e-09  2.10e-02
4   1e-10    1.000000e-10  2.32e-08   nan           nan  ← λ<0
5   1e-12    1.000004e-12  4.20e-06   nan           nan  ← λ<0
实测 λ_min = -1.518e-19(理论 5.025e-27)
```

**SVD 路线全程可用(最大相对误差 4.2e-6);协方差路线从 σ=1e-8 起就掉到 2% 误差,
到 σ=1e-10 时最小特征值已被压成负数、开方直接得到 nan。**

实验 3(`PCA centers but does not scale`):两个特征方差分别为 1 与 1e6 时,

- 不缩放:`第 1 主成分 = 0.000013·f0 - 1.000000·f1`,方差贡献 f1 占 **100.0000%**;
- 标准化后:`0.706908·f0 - 0.707306·f1`,方差贡献 49.97% / 50.03%。

两个特征只是**单位不同**,f0 的信息就被彻底挤出主成分;`whiten=True` 也救不了 ——
白化只改分量尺度,不改方向(官方:whitening removes some information from the transformed
signal, the relative variance scales of the components)。

实验 4(白化与开销):前 3 个成分方差 `[0.0033, 0.0008, 0.0004]` → 白化后恒为 `[1, 1, 1]`。
内存上协方差路线要额外物化 `p×p`,官方给的对比是

```
随机化 PCA :O(nmax²·n_components)   内存 ≈ 2·nmax·n_components
精确  PCA  :O(nmax²·nmin)           内存 ≈ nmax·nmin     (nmax=max(n,p), nmin=min(n,p))
```

`p=4096` 时 `p×p` 已经是 128 MiB,所以官方只在小 `p` 且样本远多于特征时才用 `covariance_eigh`。

## 对比 / 选型

| 维度 | `covariance_eigh` | `full`(直接 SVD) | `randomized` |
| --- | --- | --- | --- |
| 精度 | 最小奇异值相对误差 ~ ε·κ²/2 | ~ ε·κ | 近似,受 `n_oversamples`/`n_iter` 控制 |
| 大 `p` 内存 | 必须物化 `p×p`,不可行 | `n×p` | `2·nmax·k` |
| 适用形状 | `n >> p` 且 `p` 小 | 通用 | 只要前 `k` 个且数据较大 |
| `inverse_transform` | 精确 | 精确 | **不精确**(官方明确声明) |
| 触发条件 | `p<1000` 且 `n>10p` | 兜底 | `>500×500` 且 `k<80%·min(n,p)` |

## 环境准备

- 操作系统:任意(本机 Windows + Git Bash)
- C:`gcc`/`clang`,只依赖 libm;Python:3.8+(**无第三方库**);Go:1.21+
- 本机无 C/Go 工具链时,Python 版会实跑,另两个版本走人工代码审查 + 括号配平校验

## 运行方式

```bash
# C(需要 math 库)
gcc -O2 -Wall -Wextra main.c linalg.c -lm -o pca && ./pca

# Python(纯标准库,约 0.6 s)
python3 python/main.py

# Go
cd go && go run .
```

## 关键代码

单边 Jacobi SVD 的核心 —— 每次把一对列旋转到正交,旋转角由 2×2 Gram 矩阵决定:

```python
a = sum(B[r][i] ** 2 for r in range(n))      # ⟨col_i, col_i⟩
b = sum(B[r][j] ** 2 for r in range(n))      # ⟨col_j, col_j⟩
g = sum(B[r][i] * B[r][j] for r in range(n)) # ⟨col_i, col_j⟩
if abs(g) <= tol * sqrt(a * b):  continue    # 已正交,跳过
theta = 0.5 * atan2(2.0 * g, a - b)          # 让 Gram 的非对角元归零
c, s = cos(theta), sin(theta)
# 对列做旋转:B ← B·Q,V ← V·Q;收敛后列范数就是奇异值
```

实验里最容易翻车的一步是**数据构造**:为了让两条路线看到同一个已知谱,必须令
`X = U·diag(σ)` 且 **U 的列与常向量 `1/√n` 正交**(即各列均值为 0)。否则 `center()`
等于又减掉一个秩 1 分量,足以改写设定的谱(实测能吃掉约 98% 的方差),实验就变成在
测别的东西了。

## 性能边界

- 单边 Jacobi 是 `O(n·p²·sweeps)`(本机 p=40、n=300 时 <1 s);LAPACK 的 `gesdd` 用分治,
  大规模下快得多,本 demo 只是为了让两条路线共享同一套基础算法而自实现。
- 本 demo 的谱跨度到 `1e12`;再往上(如 `1e16`)连"构造出该矩阵"都做不到 —— 稠密双精度
  无法表示跨度超过 `1/ε ≈ 4.5e15` 的谱,此时任何求解器都无意义。
- `n_oversamples`(默认 10,总量 = `n_components + n_oversamples`)与 `n_iter`(默认 `auto`:
  `n_components < 0.1·min(shape)` 取 7,否则取 4)属于 `randomized` 求解器的调参,
  与本文两条精确路线无关。

## 注意事项与常见坑

1. **`covariance_eigh` 不是更差,是更快**:只在 `n >> p` 且谱不跨数量级时用它;数据跨
   数量级(未标准化的金额、像素强度、embedding 分量)会让 κ 飙升,`λ_min` 直接算成负数。
2. **`explained_variance_ratio_` 的分母是全部特征值**:截断 `n_components` 不会让比例和为 1
   (`n_components=None` 时才是 1.0)。
3. **`X` 中心化但不缩放**:忘了 `StandardScaler` 就会像实验 3 那样,主成分被
   大方差特征独占;`whiten=True` 修不了方向问题。
4. **`randomized` 的 `inverse_transform` 不是精确逆**:官方明确声明即使 `whiten=False`
   也不精确,别拿重建误差当"无损校验"。
5. **`n_components='mle'`**:只有配 `svd_solver='full'` 才有意义(`'auto'` 会被解释成
   `'full'`),其它求解器下不会启用 Minka MLE。
6. **符号歧义**:SVD 的特征向量只在"每个分量整体变号"意义下唯一;sklearn 用 `flip_sign`
   把每个分量最大载荷取正(`randomized_svd` 的默认行为),自实现时若不加这一步,
   和官方数值对不上号是正常的。
7. 本 demo 的两条路线都自实现,数值上**不等同于** LAPACK 的输出;可比的是"同一条路线的
   相对误差量级",这已经足以复现官方文档的结论。

## 参考资料(实际阅读过的来源)

- [scikit-learn 1.9 §2.5 Decomposing signals in components](https://scikit-learn.org/stable/modules/decomposition.html)
  —— "PCA centers but does not scale the input data"、随机化 PCA 复杂度 `O(nmax²·n_components)`
  vs `O(nmax²·nmin)` 与内存 `2·nmax·n_components` vs `nmax·nmin`、
  `inverse_transform` 在 randomized 下不精确的声明、Olivetti 人脸 4096→200 的例子
- [scikit-learn `sklearn.decomposition.PCA` API](https://scikit-learn.org/stable/modules/generated/sklearn.decomposition.PCA.html)
  —— `svd_solver` 五个取值与 `'auto'` 策略原文、`covariance_eigh` "effectively doubles the
  condition number and is therefore less numerically stable"、`explained_variance_` 等于
  协方差矩阵的前 k 个最大特征值、`singular_values_` 的定义、`whiten=True` 的
  `√n_samples / σ` 缩放、docstring 里的 `singular_values_ = [6.30061, 0.54980]` 与
  `explained_variance_ratio_ = [0.9924, 0.0075]` 锚点
- [scikit-learn `sklearn.utils.extmath.randomized_svd` API](https://scikit-learn.org/stable/modules/generated/sklearn.utils.extmath.randomized_svd.html)
  —— `n_oversamples`/`n_iter='auto'`/`power_iteration_normalizer`/`flip_sign` 语义,
  以及"随机化方法的原则是避免昂贵的幂迭代"这一取舍说明
