# 高斯混合模型(GMM)与 EM 算法

## 简介

GMM 假设全部数据点由**有限个高斯分布**混合生成,是"带上协方差结构的 k-means"。
scikit-learn 用户指南的原话是:mixture models 可看作 **generalizing k-means clustering to
incorporate information about the covariance structure of the data as well as the centers of the
latent Gaussians**。它解决的是 k-means 答不了的问题:簇的**形状与朝向**(椭圆而非圆)、
**每个点的归属置信度**(软分配)、以及**密度估计与采样**。

关键概念:

| 概念 | 一句话 |
| --- | --- |
| 成分(component) | 一个带权重 π_k 的高斯 N(μ_k, Σ_k),Σπ_k = 1 |
| responsibility γ(z_nk) | E 步算出的"第 n 个点由第 k 个成分生成"的后验概率,每行和为 1 |
| 协方差约束 | spherical / diag / tied / full 四种,决定椭圆能不能倾斜、要不要共享 |
| reg_covar | 加在协方差**对角线**上的正则项(sklearn 默认 1e-6),用来压住奇异性 |
| BIC / AIC | 选成分数的信息准则,`bic = -2·score·n + p·ln n` |

历史:EM 是解决"不知每个点来自哪个隐成分"这类**含隐变量最大似然**的标准迭代法(Dempster et al. 1977);
sklearn 的 `GaussianMixture` 走 EM,`BayesianGaussianMixture` 走变分推断。

本目录用**纯标准库**从零实现 EM 版 GMM(Python + Go),自检 Python 侧 28 条、Go 侧 20 条。

```
python/  gmm.py(数值工具 + k-means 初始化 + EM)  gmm_check.py(自检 A~J 十段)
go/      num.go(logSumExp/Cholesky/马氏距离/k-means)  gmm.go(结构体与 E 步/Fit/准则)
         mstep.go(四种协方差的 M 步)  main.go(自检)
```

## 原理详解

### 1. 模型与似然

```
p(x) = Σ_k π_k · N(x | μ_k, Σ_k)
N(x|μ,Σ) = (2π)^(-d/2) |Σ|^(-1/2) exp(−½ (x−μ)ᵀΣ⁻¹(x−μ))
```

未知的是"哪个点属于哪个成分"(隐变量 z),所以直接最大化似然没法解。EM 的办法是迭代两步:

### 2. E 步(求 responsibility)

```
log γ(z_nk) = log π_k + log N(x_n | μ_k, Σ_k) − logsumexp_j(log π_j + log N(x_n | μ_j, Σ_j))
```

**必须先减最大值再 exp**(log-sum-exp 技巧),否则 d 稍大时每个密度都下溢成 0:
自检 A 段实测 `exp(-1000)+exp(-1001)+exp(-999.5)` 在双精度下**恰好等于 0**,而 log 域得到
有限的 `-998.8959`。

### 3. M 步(闭式解)

```
N_k = Σ_n γ(z_nk);  π_k = N_k / N;  μ_k = Σ_n γ(z_nk)x_n / N_k
full:      Σ_k = Σ_n γ(z_nk)(x_n−μ_k)(x_n−μ_k)ᵀ / N_k  + reg_covar·I
tied:      Σ   = Σ_k Σ_n γ(z_nk)(x_n−μ_k)(x_n−μ_k)ᵀ / N + reg_covar·I     (所有类共享)
diag:      σ²_kj = E_γ[x_j²] − μ_kj² + reg_covar
spherical: σ²_k  = mean_j(σ²_kj)
```

对应 sklearn 源码 `_gaussian_mixture.py`:
`covariances[k] = (resp[:,k]*diff.T) @ diff / nk[k]` 后 `_add_to_diagonal`;
diag 走 `avg_X2 = (resp.T @ X*X)/nk; return avg_X2 - means**2 + reg_covar`;
spherical 取 diag 的**逐行均值**。注意 `reg_covar` **只加到对角线**,自检 G3 断言
退化数据下协方差对角元恰好 ≈ 1e-6。

### 4. 奇异性(用户指南「Cons」第一条)

> When one has insufficiently many points per mixture, estimating the covariance matrices becomes
> difficult, and the algorithm is known to **diverge and find solutions with infinite likelihood**
> unless one regularizes the covariances artificially.

即某成分只吸住 1~2 个近似重合的点时,Σ_k → 0,密度 → ∞。自检 G 段构造"每簇 20 个完全相同点":
`reg_covar=0` 时 Cholesky 分解在第一步就失败(本实现抛 `ValueError` / Go 返回 error),
`reg_covar=1e-6` 时训练照常完成。

### 5. 收敛性

用户指南:**Repeating this process is guaranteed to always converge to a local optimum** ——
"局部"二字很重要:自检 E 段用同一份数据的三种初始化(kmeans / random_from_data / random)
得到最大相差 208 的对数似然,差的那个就是坏局部最优。工程上的兜底是 `n_init` 多次重启。

### 6. 模型选择:BIC / AIC

```
bic = -2·score(X)·n + p·ln n          aic = -2·score(X)·n + 2p
p = cov_params + d·K + K − 1          (score 是"平均"对数似然,故此处要乘回 n)
```

`cov_params`:full `K·d(d+1)/2`、diag `K·d`、tied `d(d+1)/2`、spherical `K`。
K=2, d=2 时四种类型分别是 11 / 9 / 8 / 7(自检 F 段逐条钉住)。
用户指南提醒:BIC 只在**渐近意义下**恢复真实成分数;若不想手定成分数,改用
`BayesianGaussianMixture`(Dirichlet 过程先验 + stick-breaking 截断)。

### 7. GMM 与 k-means 的关系

spherical + 等权重 + σ→0 时,responsibility 退化为 0/1 硬分配,划分与 k-means 一致
(自检 I 段:ARI == 1.000000,最大 responsibility 全部 > 0.99)。反过来,k-means 是 GMM 在
"等方差球形成分 + 硬分配"下的极限——这也解释了 k-means 为什么处理不了细长/倾斜的簇。

## 对比 / 选型

| | k-means | GMM(full) | GMM(diag) | BayesianGaussianMixture |
| --- | --- | --- | --- | --- |
| 簇形状 | 圆(等方差) | 任意椭圆,可倾斜 | 轴对齐椭圆 | 任意 |
| 分配 | 硬 | 软(概率) | 软 | 软 |
| 需指定 K | 是 | 是 | 是 | **不必**(先验自动压成分) |
| 参数量(d=2,K=2) | 4 | 11 | 9 | 更多(含浓度先验) |
| 主要风险 | 非凸簇失效 | 奇异 / 局部最优 | 不能刻画相关性 | 推断慢、先验引入偏置 |

## 环境准备

- 操作系统:任意(纯标准库)
- Python ≥ 3.8 / Go ≥ 1.21
- 依赖:**无**

## 运行方式

```bash
cd python && python3 gmm_check.py     # 28 条断言
cd go     && go run .                 # 20 条断言
```

## 关键代码片段

E 步与 M 步的核心(python/gmm.py,与 sklearn 口径对齐):

```python
def _e_step(self, X):
    lp = self._estimate_log_prob(X)                 # log π_k + log N_k(x_n)
    log_prob_norm = [logsumexp(r) for r in lp]      # 数值稳定的归一化项
    log_resp = [[r[k] - log_prob_norm[i] for k in range(self.K)] for i, r in enumerate(lp)]
    return log_resp, sum(log_prob_norm)             # 后者即本轮对数似然

def _m_step(self, X, log_resp):
    resp = [[math.exp(lr[k]) for k in range(self.K)] for lr in log_resp]
    nk = [sum(r[k] for r in resp) for k in range(self.K)]
    self.weights_ = [nk[k] / n for k in range(self.K)]                       # π_k = N_k/N
    self.means_ = [[sum(resp[i][k] * X[i][j] for i in range(n)) / nk[k]
                    for j in range(d)] for k in range(self.K)]
    # full:外积加权求和 / N_k,再往对角线加 reg_covar
    c[a][b] = sum(resp[i][k] * (X[i][a]-mu[a]) * (X[i][b]-mu[b]) for i in range(n)) / nk[k]
    c[j][j] += self.reg_covar
```

Cholesky 与马氏距离:不显式求逆,`Σ = L·Lᵀ` 后前代解 `L·y = d`,`dᵀΣ⁻¹d = ||y||²`,
`log|Σ| = 2·Σ log L_ii` —— 顺带把"是否正定"变成一次可捕获的失败(奇异性探测器)。

## 性能与边界

- **复杂度**:每轮 EM 为 O(n·K·d²)(full 的协方差估计是 O(n·K·d²),对角型降到 O(n·K·d));
  分解 d×d 的 Cholesky 是 O(d³)/成分。空间 O(n·K) 存 responsibility。
- **迭代轮数**:用户指南说"永远收敛到局部最优"但**没给轮数界**;本实现用
  `|Δll| < tol·|ll|`(sklearn `tol=1e-3` 语义)提前停,分离良好的数据 2~5 轮即停。
- **规模上限**:`n·K·d²` 随维度平方增长;d > 几十时应改用 diag/spherical,或先降维。
- **数值边界**:密度值本身会下溢(自检 A),**只在 log 域运算**;
  d 较大或特征量纲差异大时,务必先标准化——`reg_covar` 是相对量纲的,1e-6 在"坐标以千米计"
  的数据上等于没加。

## 注意事项与常见坑

1. **把 responsibility 当"概率"用前先检查行和**:E 步必须对 K 个成分归一化(自检 C1),
   漏掉 logsumexp 那一步会得到"每个点对每个成分的似然",数值上天差地别。
2. **不要用 `predict_proba` 的最大值当置信度阈值**:奇异性会让某成分方差趋 0、
   该成分下的密度冲到极大值,从而"虚假自信"。先设 `reg_covar`。
3. **只看 BIC 绝对值没有意义**:BIC 只在**同一份数据、同一协方差类型**间可比;
   跨类型比较时参数量口径不同(11 vs 7),相当于换了把尺子。
4. **成分数永远会被用满**:用户指南明确 "This algorithm will always use all the components
   it has access to"。K 设大了不会自动留空(除非用变分版),只会把簇切碎。
5. **初始化不是小事**:EM 只有局部最优保证;`init_params='kmeans'` 是默认,
   但 `random` 可能慢很多甚至掉进坏解(自检 E 段 Δll = 208)。
6. **特征未标准化时 full 几乎必然奇异**:sklearn 源码的报错提示就是
   "increase reg_covar, or scale the input data"。

## 参考资料(实际阅读过的权威来源)

- [scikit-learn《2.1. Gaussian mixture models》](https://scikit-learn.org/stable/modules/mixture.html)
  — 模型定义、四种协方差约束、EM 流程与"保证收敛到局部最优"、初始化四法、
  奇异性(Singularities)与成分数(Number of components)两条 Cons、BIC 选 K 的渐近性、
  BayesianGaussianMixture 的 Dirichlet 过程与 stick-breaking。
- [scikit-learn 源码 `sklearn/mixture/_gaussian_mixture.py`](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/mixture/_gaussian_mixture.py)
  — `reg_covar` 默认值 1e-6 与"只加对角"的 `_add_to_diagonal`;四种协方差的 M 步闭式解;
  `_n_parameters` 的计数规则;`bic = -2*score*N + p*log(N)` 与 `aic = -2*score*N + 2p`。
  (经 jsDelivr 镜像取到;`raw.githubusercontent.com` 在本机会间歇 exit 56。)
