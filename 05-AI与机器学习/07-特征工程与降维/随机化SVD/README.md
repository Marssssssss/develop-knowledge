# 随机化 SVD

## 简介

随机化 SVD(randomized SVD / randomized range finder)是 Halko、Martinsson、Tropp 在
*Finding structure with randomness*(2009, SIAM Review 2011)里系统化的方法:**只要前 `k` 个
奇异三元组时,不必做完整 SVD** —— 用随机矩阵把 `A` 的值域"扫"到一个 `k + n_oversamples`
维的子空间上,再在这个小矩阵上做(廉价的)SVD,最后映射回原空间。

sklearn 的 `PCA(svd_solver='randomized')` 与 `sklearn.utils.extmath.randomized_svd` 就是它
(文档引用:Halko et al. 2009 Algorithm 4.3)。官方给的复杂度对比(`nmax=max(n,p)`、
`nmin=min(n,p)`):

```
随机化 PCA :O(nmax²·n_components)   内存 ≈ 2·nmax·n_components
精确  PCA  :O(nmax²·nmin)           内存 ≈ nmax·nmin
```

关键概念:

| 概念 | 一句话 |
| --- | --- |
| 随机投影 `Ω` | `p×(k+ov)` 的高斯矩阵;"扫"一遍 `A` 得到 `Y = AΩ` |
| `n_oversamples` | 额外抽的随机向量数,**总列数 = `n_components + n_oversamples`**(默认 10) |
| `n_iter` | 幂迭代次数,`Y ← A(AᵀY)` 重复 `n_iter` 次,放大主方向、压制噪声 |
| `power_iteration_normalizer` | 迭代之间的归一化:`QR`/`LU`/`none`;`auto` = `n_iter<=2?none:LU` |
| 值域基 `Q` | 对 `Y` 做 Gram-Schmidt 得到的正交基,`QQᵀA ≈ A` |

## 原理详解

1. **抽随机投影**:`Ω ∈ R^{p×l}`,`l = k + n_oversamples`。为什么需要 oversampling?
   因为 `AΩ` 的值域只有 `l` 维,而我们希望它**至少包含**前 `k` 个奇异方向;多出来的
   `l-k` 个向量把"擦边"方向也捞进来,保证子空间条件数良好。
2. **幂迭代**:`Y ← (AAᵀ)^q·AΩ`。把 `A` 的奇异值取 `2q+1` 次幂,谱衰减越快、
   "想抓的 k 个方向"和"噪声方向"的比值越大 ⇒ 值域近似越准。
3. **归一化(可选,但重要)**:`AAᵀ` 的每次应用都把列尺度按 `σ²` 拉伸一遍;`q` 次之后
   尺度比变成 `κ^{2q+1}`。不归一化时小数方向会掉到机器精度以下,`QR` 最稳但最贵,
   `LU` 用消元得到的下三角因子当新基(便宜且稳),`none` 则完全不处理。
4. **正交化**:对 `Y` 做(改进)Gram-Schmidt 得 `Q ∈ R^{n×l}`,满足 `QQᵀA ≈ A`。
5. **小矩阵 SVD**:`B = QᵀA`(`l×p`,很小),对 `B` 做 SVD 得左奇异向量 `U_B` 与 `σ`;
   原空间的左奇异向量 `U = Q·U_B`。**代价主要在这里被砍掉**:完整 SVD 是 `n×p`,
   这里只有 `l×p`。
6. **精度**:`σ` 的近似误差随 `l - k`(oversampling)与 `q`(幂迭代)指数下降;
   官方原则是"先加 oversampling,再加幂迭代",因为幂迭代是随机化方法想绕开的那部分成本。

## 实测结果(本机 float64,真值 σ 由构造给定)

实验 1(`n=400, p=60, k=10`,谱 `σ_i = e^{-0.15i}`,κ≈7e3,`n_iter=2`):

```
oversamples   σ 最大相对误差   子空间 sinθ     ‖QᵀQ-I‖_F
          0   1.710e-01        8.688e-01      6.307e-16
          2   9.971e-03        1.884e-01      3.243e-16
          5   6.849e-05        1.529e-02      5.972e-16
         10   9.899e-09        2.038e-04      6.064e-16
         20   1.405e-13        5.497e-07      8.407e-16
```

**oversampling 从 0 到 20,奇异值误差从 1.7e-1 掉到 1.4e-13**,而 `Q` 的正交性始终在
`10^-16` 量级 —— 说明误差全部来自"值域近似",不是正交化本身。

实验 2(慢衰减 `σ_i = 1/(i+1)`,κ=60,`oversamples=10`):此时**幂迭代才是关键**,

```
n_iter   σ 最大相对误差   子空间 sinθ
     0   1.679e-01        6.588e-01
     1   9.988e-03        1.666e-01
     2   5.801e-04        3.964e-02
     4   1.255e-06        1.792e-03
     7   7.866e-11        1.412e-05
```

实验 3(快衰减 `σ_i = 10^{-i}`,`n_components=20 ≥` 数值有效秩):`n_iter=0/1/4` 的误差分别是
`2.8e-07 / 2.5e-07 / 2.6e-07` —— **n_iter=0 已经够准**,直接印证官方那句
"when `n_components` is equal or greater to the effective matrix rank and the spectrum does not
present a slow decay, `n_iter=0` or `1` should even work fine in theory"。

实验 4(`n_iter=7`,谱为 `1, 1e-2×4, 1e-4×35`):归一化的作用,

```
normalizer   ‖QᵀQ-I‖_F    σ 最大相对误差
none         7.849e-16      8.123e-01
LU           7.296e-16      1.239e-10
QR           7.157e-16      1.057e-14
```

**注意这里有个反直觉之处**:不归一化时 `Q` 仍然是正交的(约 1e-16),但奇异值误差高达
`0.81` —— 烂掉的不是正交化,而是被投影进去的**值域**:幂迭代把尺度比拉到 `κ^(2q+1)`,
小奇异方向在浮点里被抹平,Gram-Schmidt 再努力也救不回来。这与官方"`none` 最快但
`n_iter` 较大时数值不稳定"的说明一致。

## 对比 / 选型

| 维度 | `randomized` | `full`(精确 SVD) | `arpack` |
| --- | --- | --- | --- |
| 精度 | 近似,误差随 oversampling/幂迭代下降 | 机器精度 | 近似(隐式重启 Lanczos) |
| 只要前 k 个时的代价 | `O(nmax²·k)`,内存 `2·nmax·k` | `O(nmax²·nmin)` | 迭代,`k` 很小时快 |
| 适用场景 | 大矩阵 + 小 `k`(如 4096 维人脸降到 200) | 通用,小矩阵 | `k` 极小(`<10`)且样本多 |
| `inverse_transform` | **不精确**(官方明确声明) | 精确 | 不适用 |

## 环境准备

- C 不需要;Python 3.8+(纯标准库);Go 1.21+
- 数据构造:A = U·diag(σ)·Vᵀ,σ 解析已知 ⇒ 误差不依赖任何外部实现对拍

## 运行方式

```bash
python3 python/main.py     # 约 6 s(纯 Python 矩阵运算)
cd go && go run .          # 同样三个实验
```

## 关键代码

```python
omega = [[rng.gauss(0.0, 1.0) for _ in range(l)] for _ in range(p)]  # l = k + oversamples
Y = matmul(A, omega)                        # 随机"扫"一遍值域
for _ in range(n_iter):
    if normalizer == "QR":  Y = qr_q(Y)     # 归一化:防止尺度比涨到 κ^(2q+1)
    elif normalizer == "LU": Y = lu_l_factor(Y)
    Y = matmul(A, matmul(transpose(A), Y))  # 幂迭代
Q = qr_q(Y)                                 # 值域的正交基
B = matmul(transpose(Q), A)                 # 压到 l×p 的小矩阵
Ub, s = small_svd(B)                        # 只在小矩阵上做 SVD
U = matmul(Q, [[Ub[r][k] for k in range(k)] for r in range(l)])
```

两个开发期踩到的坑,都写进代码注释了:

1. **Gram-Schmidt 必须对着已归一化的前序列投影**(每列算完就地归一化)。否则减掉的是
   "投影 × 未归一列的模长",列与列根本不会正交 —— 症状是 `‖QᵀQ-I‖_F` 是 `O(l)` 而不是
   `O(ε)`,本 demo 开发时正是先在 `‖QᵀQ-I‖_F ≈ 4.5`(l=5)上发现的。
2. **验证子空间误差时,参照物必须是 `A` 自己的左奇异向量 `U`**,而不是另外抽一个随机正交基。
   用随机基做参照,`sinθ` 恒等于 1,什么也验证不了(子空间之间本来就是"正交"的)。

## 性能边界

- 本 demo 的 `Ω` 与全流程都是纯 Python 双层列表,`n=400, p=60` 的四个实验合计约 6 s;
  生产实现(LAPACK/BLAS)会快两三个数量级,这里关心的是**误差的量级关系**。
- 随机化 SVD 的误差上限(Halko et al. 的概率界)随 `l - k` 指数下降,但**只是高概率界**:
  换种子会看到小幅波动,所以本 demo 固定 seed 以复现表格。
- `l = k + n_oversamples` 必须 `≤ min(n, p)`(本 demo 显式检查并报错):
  它要成为值域的一组基,列数不可能超过空间的维数。
- `n_components >= 0.1·min(shape)` 时官方把 `n_iter` 从 7 降到 4;这是"多抽点比多迭代便宜"
  这一原则的工程体现。

## 注意事项与常见坑

1. **`n_oversamples` 不是越大越好**:`l` 增大后小矩阵 SVD 与正交化的成本都线性上涨,
   官方建议上限约 `2k - n_components`(k 为有效秩),常规场景取到 `n_components` 就够。
2. **先加 oversampling,再加幂迭代** —— 官方原话 "users should rather increase
   `n_oversamples` before increasing `n_iter`"。
3. **`normalizer='none'` 只适合 `n_iter<=2`**:这是 `'auto'` 的默认切换点,不是随便定的。
4. **没中心化的数据不要用 `PCA`**:`PCA` 默认中心化,不想中心化要用 `TruncatedSVD`;
   随机化路线对"是否中心化"没有豁免。
5. **`PCA(svd_solver='randomized')` 的 `inverse_transform` 不是精确逆**(官方明确声明,
   即使 `whiten=False`),别把重建误差当无损校验。
6. 随机化方法的"快"体现在**只要前 k 个**时;要全部奇异值就该用 `full`,
   否则会以近似换到更慢的结果。
7. `random_state` 会同时影响 `Ω` 与 `n_oversamples` 以外的初始化路径;要复现表格必须固定它
   (sklearn 1.2 起 `randomized_svd` 的默认值由 `0` 改为 `None`,即默认不可复现)。

## 参考资料(实际阅读过的来源)

- [scikit-learn `sklearn.utils.extmath.randomized_svd` API](https://scikit-learn.org/stable/modules/generated/sklearn.utils.extmath.randomized_svd.html)
  —— 全部参数原文:`n_oversamples`(默认 10、"Smaller number can improve speed but can
  negatively impact the quality of approximation"、可加大到 `2k - n_components`)、
  `n_iter='auto'`(4 / 7 的切换条件、"users should rather increase `n_oversamples` before
  increasing `n_iter`"、"`n_iter=0` or `1` should even work fine in theory")、
  `power_iteration_normalizer`(`QR` 最慢最准、`none` 最快但 `n_iter≥5` 不稳定、
  `'auto'` 的 `n_iter<=2 → none` 策略)、`transpose`、`flip_sign`、`svd_lapack_driver`
- [scikit-learn 1.9 §2.5 Decomposing signals in components](https://scikit-learn.org/stable/modules/decomposition.html)
  —— 随机化 PCA 的复杂度 `O(nmax²·n_components)` vs `O(nmax²·nmin)`、内存
  `2·nmax·n_components` vs `nmax·nmin`、Olivetti 人脸 4096→200 的动机、
  "Algorithm 4.3 in Finding structure with randomness" 的指认
- [Halko, Martinsson, Tropp, *Finding structure with randomness*, arXiv:0909.4061](https://arxiv.org/abs/0909.4061)
  —— 方法学出处(SIAM Rev. 53(2):217-288, 2011);摘要里的核心思路:
  "use random sampling to identify a subspace that captures most of the action of a matrix …
  the input matrix is then compressed … and the reduced matrix is manipulated deterministically"
