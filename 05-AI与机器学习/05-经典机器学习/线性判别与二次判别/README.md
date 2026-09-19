# 线性判别分析(LDA)与二次判别分析(QDA)

## 简介

LDA 与 QDA 是两条"**生成式分类器**"的直路:给每个类拟合一个高斯,再用贝叶斯法则比后验。
它们受欢迎的原因(scikit-learn 用户指南原话):有**闭式解**、**天然多分类**、
实践中效果好、**没有超参要调**。

二者唯一的结构差别在于**协方差是否共享**:

| | 协方差 | 决策面 | 参数量(d 维 K 类) |
| --- | --- | --- | --- |
| **LDA** | 所有类共享一个 Σ | **线性**(超平面) | K·d + d(d+1)/2 |
| **QDA** | 每类一个 Σ_k | **二次**(超曲面) | K·d + K·d(d+1)/2 |

本目录用**纯标准库**实现两者 + 有监督降维 + shrinkage + 与 GaussianNB 的等价性验证
(Python + Go),自检 Python 侧 18 条、Go 侧 15 条。

```
python/  lda_qda.py(LDA / QDA / GaussianNB / 收缩协方差 / 幂迭代 PCA)
         lda_qda_check.py(自检 A~G 七段)
go/      linalg.go(线性代数工具)  lda.go  qda.go(模型)  main.go(自检)
```

## 原理详解

### 1. 从类条件高斯到后验

```
P(x | y=k) = (2π)^(-d/2)|Σ_k|^(-1/2) exp(−½(x−μ_k)ᵀΣ_k⁻¹(x−μ_k))
log P(y=k | x) = log P(x|y=k) + log P(y=k) + Cst
```

**QDA** 直接代入,得到

```
log P(y=k|x) = −½log|Σ_k| −½(x−μ_k)ᵀΣ_k⁻¹(x−μ_k) + log P(y=k) + Cst
```

**LDA** 假设 Σ_k ≡ Σ,`−½log|Σ|` 与 `−½xᵀΣ⁻¹x` 变成与 k 无关的常数,被吸进 Cst,剩下的就是

```
log P(y=k|x) = ω_kᵀx + ω_k0 + Cst
ω_k  = Σ⁻¹μ_k                          ← sklearn 的 coef_
ω_k0 = −½μ_kᵀΣ⁻¹μ_k + log P(y=k)       ← sklearn 的 intercept_
```

自检 A 段钉的就是这个化简:把 QDA 的原始形式强行配上共享协方差,
两种写法对每个样本**只差一个与类别无关的常数**(首样本 0.654724813 逐位一致)。

### 2. 马氏距离视角

LDA 里那一项 `(x−μ_k)ᵀΣ⁻¹(x−μ_k)` 就是 x 到 μ_k 的**马氏距离**。
用户指南的解读:LDA = 把 x 分给"马氏距离最近的那个类均值",同时用先验加权;
等价说法是"**先把数据白化使 Σ = I,再按欧氏距离找最近的类均值**"。
本实现的 `transform` 就是照这个说法做的(见 §3)。

### 3. 有监督降维:为什么最多 K−1 维

K 个均值 μ_k 是 d 维向量,但它们至多张成一个 **K−1 维的仿射子空间**
(两点共线、三点共面……)。用户指南进一步说:把数据白化后,判别等价于在白化空间找最近的类均值,
而"投影到这个子空间再算距离"与"在原空间算距离"结果一致 —— 于是降维是**隐含在 LDA 里**的。
要压到更低的 L 维,就是对**白化后的类均值**再跑一次 PCA。

自检 D 段:K=3、d=5 的数据上只得到 **2** 个判别分量,把这两个分量收缩掉之后,
第 3 个特征值是 **9.999e-14**(≈ 0)——类均值张成的空间真的只有 K−1 维。

### 4. shrinkage:小样本下协方差估计的正则化

训练样本数远小于特征数时,经验协方差是很差的估计。sklearn 的做法是把它朝一个"靶心"收缩:

```
Σ̂ = (1 − γ)·Σ + γ·(tr(Σ)/p)·I          # 源码 sklearn/covariance/_shrunk_covariance.py
```

- γ = 0:经验协方差;γ = 1:完全收缩。
- `'auto'`:按 Ledoit & Wolf 的引理解析地定 γ;**只能配 `solver='lsqr'` 或 `'eigen'`**
  (QDA 只实现了 eigen)。

⚠️ **口径坑**:用户指南写 γ=1 得到 "the diagonal matrix of variances",
但源码的靶心是 `(tr Σ / p)·I` —— 各维方差被抹成**同一个平均值**,
并不是"保留各维自己的方差"。自检 E 段按源码口径断言:γ=1 时非对角为 0、
两个对角元都等于 `(4+2)/2 = 3`。

### 5. 与 GaussianNB 的关系

用户指南 Note:QDA 若假设协方差矩阵为**对角**,等价于"类内条件独立",
也就是 `GaussianNB`。自检 C 段不止比对 argmax:对角协方差下 `log|Σ| = Σ log v_j`、
马氏距离 `= Σ(x−μ)²/v`,所以两式的联合对数似然**只差被文档塞进 Cst 的 ½·d·log2π 一项**
(d=2 时 = 1.837877066,实测逐位相同)。

### 6. LDA vs PCA:有监督与无监督可以南辕北辙

自检 F 段构造了一份"打脸"数据:两类沿 **x2** 分离(仅差 2.4),但 **x1** 的方差是 x2 的

```
σ(x1) = 4.0    σ(x2) = 0.3
```

结果:

| 方向 | 值 | 沿该方向投影后的分类准确率 |
| --- | --- | --- |
| PCA 第一主成分 | (−0.999, −0.037) ≈ **x1** | **0.444**(≈ 随机) |
| LDA 判别方向 | (−0.007, +1.000) ≈ **x2** | **1.000** |

PCA 找的是"方差最大的方向",LDA 找的是"最能分开类的方向"——**二者没有义务一致**。
这就是"先 PCA 降维再分类"经常失效的机制。

## 对比 / 选型

| | LDA | QDA | LogisticRegression | GaussianNB |
| --- | --- | --- | --- | --- |
| 决策面 | 线性 | 二次 | 线性 | (对角 QDA)二次 |
| 假设 | 各类同协方差高斯 | 各类高斯 | 无分布假设 | 类内条件独立 |
| 需要超参 | 无(可选 shrinkage) | 无(可选 reg) | C | var_smoothing |
| 小样本高维 | 配 shrinkage 可用 | 参数爆炸,慎用 | 配正则可用 | **最稳** |
| 判别式/生成式 | 生成式 | 生成式 | 判别式 | 生成式 |

自检 G 段的实测:异方差数据(类 0 是紧的圆、类 1 是拉长的椭圆)上
**LDA 0.537 / QDA 0.887** —— QDA 因为每类一个椭圆,能贴合二次边界。

## 环境准备

- 操作系统:任意(纯标准库)
- Python ≥ 3.8 / Go ≥ 1.21
- 依赖:**无**

## 运行方式

```bash
cd python && python3 lda_qda_check.py    # 18 条断言
cd go     && go run .                    # 15 条断言
```

## 关键代码片段

```python
def decision_function(self, X):
    """ω_kᵀx + ω_k0 —— sklearn 的 coef_ 与 intercept_。"""
    for x in X:
        for k in range(K):
            S_inv_mu = chol_solve(self._L, self.means_[k])       # Σ⁻¹μ_k
            quad = sum(self.means_[k][j] * S_inv_mu[j] ...)      # μ_kᵀΣ⁻¹μ_k
            row.append(dot(S_inv_mu, x) - 0.5*quad + log(prior[k]))
```

降维方向:先把 x 白化成 `x* = L⁻¹x`(Σ = L·Lᵀ),对白化后的类均值做 PCA 得到 v,
再还原成原始空间的方向 `w = L⁻ᵀv`(因为 `vᵀx* = vᵀL⁻¹x = (L⁻ᵀv)ᵀx`)。

## 性能与边界

- **训练**:闭式解,代价主要是算协方差 O(n·d²) 与一次 Cholesky O(d³);
  没有迭代、没有学习率。d 很大时用 `solver='svd'`(不需要显式算 Σ)。
- **预测**:LDA 每样本 K 次 d 维内积 → O(K·d);QDA 每样本 K 次马氏距离 → O(K·d²)。
- **样本量**:QDA 要为**每个类**估一个 d×d 协方差,经验规则是每类至少
  ~10·d 个样本,否则 Σ_k 容易接近奇异(需要 `reg_covar` 之类的正则)。
- **降维上限**:`transform` 最多给 **K−1** 维;K=2 时只有一维,别指望"LDA 降到 2 维可视化"
  在二分类上成立。

## 注意事项与常见坑

1. **把 LDA 当降维工具用在二分类上**:K=2 时只能降到 1 维,`n_components=2` 是无效的。
2. **"shrinkage 只能配 lsqr / eigen"**:默认 solver 是 `'svd'`,直接设 `shrinkage='auto'`
   会报错(用户指南明写)。本实现不走 solver 分支,但仍按同一收缩公式。
3. **shrinkage 的靶心是平均方差 × I,不是"各维方差组成的对角阵"**(见 §4),
   按文档字面理解会在高维小样本上低估收缩强度。
4. **QDA 的 `reg_covar` 必须加**:某类样本少或共线时 Σ_k 奇异,Cholesky 直接失败;
   本实现的 QDA 默认 reg=1e-6,对角版本默认 0(为与 GaussianNB 严格等价,见自检 C)。
5. **类别不平衡时先验很关键**:`priors` 默认取经验频率;若训练集类别比例与实际不符,
   必须手工设 `priors` —— ω_k0 里的 `log P(y=k)` 项直接把比例搬进决策面。
6. **LDA 假设"各类协方差相同"**:自检 G 段是这个假设失效时的样子(0.537)。
   检验办法是看各类协方差是否量级接近,或直接用 QDA 对照。
7. **特征共线**:Σ 奇异 → 需要 shrinkage 或先降维;`_cov` 里加对角线正则是最省事的兜底。

## 参考资料(实际阅读过的权威来源)

- [scikit-learn《1.2. Linear and Quadratic Discriminant Analysis》](https://scikit-learn.org/stable/modules/lda_qda.html)
  — 类条件高斯的贝叶斯推导、QDA 与 LDA 的 log-posterior 两式、
  ω_k = Σ⁻¹μ_k 与 ω_k0 = −½μ_kᵀΣ⁻¹μ_k + log P(y=k)(对应 coef_/intercept_)、
  马氏距离与"白化后最近均值"的等价说法、降维最多 K−1 维的仿射子空间论证、
  shrinkage 的 0/1/'auto' 三档与 Ledoit-Wolf 引理、"只能配 lsqr/eigen"、
  对角 QDA ⇔ GaussianNB 的 Note、svd/lsqr/eigen 三个 solver 的差异。
- [scikit-learn 源码 `sklearn/covariance/_shrunk_covariance.py`](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/covariance/_shrunk_covariance.py)
  — `shrunk_cov = (1−γ)·emp_cov + γ·(tr Σ/p)·I` 的**源码口径**
  (与用户指南"diagonal matrix of variances"的措辞差异是本 demo 的一处重点标注)。
  (经 jsDelivr 镜像取到。)
- [scikit-learn 源码 `sklearn/discriminant_analysis.py`](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/discriminant_analysis.py)
  — 池化协方差按类先验加权(`cov += priors[idx] * _cov(Xg, ...)`)、
  shrinkage 与 covariance_estimator 的互斥关系、svd 求解器对 QDA 的实现思路。
  (经 jsDelivr 镜像取到。)
