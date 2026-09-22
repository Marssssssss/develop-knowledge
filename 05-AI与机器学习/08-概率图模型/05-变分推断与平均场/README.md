# 变分推断与平均场（ELBO / CAVI）

## 一、简介

后验 `p(z｜x)` 算不出来（配分函数要积掉 `z`）。变分推断把它换成**优化问题**：
从一个好算的族 `q(z)` 里挑一个与真后验最像的。平均场（mean-field）是最简单也最常用的族 ——
强制 `q(z) = ∏_j q_j(z_j)`，即假设所有隐变量**互相独立**。

这个假设是错的，但它把「求和/积分」变成了「逐个因子求期望」，代价是：
`q` 系统性低估不确定性。本 demo 把这句定性描述换成了两个可测的不等式。

## 二、原理（公式编号沿用 Blei et al. arXiv:1601.00670）

### 2.1 ELBO：把「最小化 KL」换成「最大化下界」

```
ELBO(q) = E[log p(z, x)] − E[log q(z)]                       # Eq (13)
log p(x) = KL( q(z) ‖ p(z|x) ) + ELBO(q)                     # Eq (14)
```

`log p(x)` 与 `q` 无关，所以「最小化 KL」 ⟺ 「最大化 ELBO」。
Eq (14) 是本 demo 第一条断言：对随机取的 `q`，`ELBO + KL` 必须严格等于 `log Z`
（离散侧容差 `1e-12`，高斯侧 `1e-10`）。这条比「ELBO 单调上升」更强 ——
后者只要实现基本正确就会过，前者一旦分解写错立刻炸。

### 2.2 CAVI：坐标上升（Algorithm 1）

```
对 j = 1..m：  q_j(z_j) ∝ exp{ E_{−j}[ log p(z_j | z_{−j}, x) ] }   # Eq (18)
```

即「把别的因子固定，对第 `j` 个求完全条件，取期望后作为新的 `q_j`」。
每步都在爬 ELBO，所以 ELBO 单调不降；但因为 ELBO 一般**非凸**，
CAVI 只保证到**局部最优**（§2.5，初值敏感）。

### 2.3 指数族捷径（Eq 36–40）

若完全条件属指数族且 `z_j` 自带充分统计量：

```
p(z_j | z_{−j}, x) = h(z_j) exp{ η_j(z_{−j}, x)ᵀ z_j − a(η_j) }   # Eq (36)
⇒  q_j 与完全条件同族，且  ν_j = E_{−j}[ η_j(z_{−j}, x) ]          # Eq (40)
```

**不用再算一次 KL**：把完全条件的自然参数对其他因子取期望即可。
本 demo 的 Ising 模型里 `η_j = θ_j + Σ_{k≠j} w_jk z_k`，
于是 `ν_j = θ_j + Σ_{k≠j} w_jk m_k`，`m_j = sigmoid(ν_j)` ——
自检里额外枚举 `2^{n−1}` 项按定义求 `E_{−j}[η_j]` 来验证这条捷径。

## 三、对比：两个模型把「代价」量化

| | 离散 Ising（n ≤ 4） | 二元高斯 `N(μ, Λ⁻¹)` |
|---|---|---|
| 真后验 | `2^n` 穷举可得 | 就是 `N(μ, Λ⁻¹)` |
| 坐标更新 | `m_j = σ(θ_j + Σ_k w_jk m_k)` | `var_j = 1/Λ_jj`（一步到位）<br>`m_j = μ_j − (1/Λ_jj)Σ_{k≠j}Λ_jk(m_k−μ_k)` |
| 不动点均值 | ≠ 真边缘（有偏） | `= μ`（**无偏**） |
| 方差/不确定性 | 边缘有偏 | `1/Λ_jj` **严格小于**真边缘方差 `(Λ⁻¹)_jj` |
| KL 闭式 | 无 | `KL(q*‖p) = −½ ln(1 − ρ²)`，`ρ` 为 `p` 的相关系数 |

**关键观察**：高斯的均值是无偏的，错的全在方差上 ——
平均场**必然低估不确定性**，且低估量只由相关性决定（`ρ` 越大错得越离谱）。
把 `q` 的方差当成后验方差用，会得到过窄的置信区间。

### 平均场把相关性抹成 0

`q` 因子化 ⟹ `E_q[z_i z_j] = m_i m_j`，协方差**恒等于 0**。
真后验的相关性被整体丢弃。离散侧的强排斥算例（`θ=0, w=−5`）里：
`q` 给 `(1,1)` 的概率是真值的 **20 倍以上** ——
`q` 被迫按 `m₁m₂` 出一个它本不该给的概率。

## 四、环境

- Python 3（仅标准库 `math` / `itertools` / `random`）
- Go 1.20+（无第三方依赖；本机无工具链时按 `_docs` 静态检查流程验收）

## 五、运行

```bash
cd python && python selfcheck_vi.py   # → PASS = 66
cd ../go   && go run .                # 打印两个模型的关键数字
```

## 六、关键代码

```python
elbo = elbo_ising(model, m)                 # Eq (13)
kl   = kl_ising(model, m)                   # 按定义枚举 2^n
assert abs(elbo + kl - model.log_z()) < 1e-12   # Eq (14)

m, hist = cavi_ising(model, iters=60)       # Algorithm 1，hist 单调不降
# Eq (40)：ν_j = E_{−j}[η_j]，伯努利族里 ν_j = logit(m_j)
nu = model.theta[j] + sum(model.w[j][k] * m[k] for k != j)
m[j] = sigmoid(nu)

m, var, hist = cavi_gauss(mu, lam, 80)      # var_j == 1/lam[j][j] < (Λ⁻¹)_jj
```

## 七、性能边界

- 离散侧的 `log_z` / `kl_ising` 都枚举 `2^n`，**只适用于 n ≤ 20 左右的玩具模型**；
  真实模型上正是算不动 `log Z` 才要用变分。
- CAVI 每轮 `O(m · 代价(单个因子))`，没有矩阵分解，所以能上大规模数据
  （随机变分推断 SVI 就是把 CAVI 的更新改成随机梯度）。
- 收敛判据用 ELBO 变化量；但全量 ELBO 在大数据集上算不起，
  Blei §2.5 建议改看**留出集的平均对数预测概率** —— 注意后者**不保证单调**。
- 高斯侧 `1/Λ_jj` 需要 `Λ` 正定；半正定时 `Λ_jj = 0` 会直接除零。

## 八、坑

1. **ELBO 里少算熵**：`ELBO = E_q[log p̃] + H(q)`，漏掉 `H(q)` 会得到一个
   在 `q` 退化成点质量时反而变大的错误目标（此时 `H → −∞`，正确行为是 ELBO 变小）。
2. **未归一化 vs 归一化**：本 demo 用 `p̃`（未归一化），所以 ELBO 下界的是 `log Z` 而不是
   `log p(x)`。混用会让 Eq (14) 差一个常数，且这个常数恰好会被误当成「KL 非负」的证据。
3. **别把 `q` 的方差当后验方差**：见 §三。这是平均场最贵的错误。
4. **KL 的方向有讲究**：`KL(q‖p)` 是 zero-forcing（倾向于躲开 `p` 的低质量区、锁定一个模），
   `KL(p‖q)` 是 mass-covering。本 demo 只在**强耦合**算例上断言两者不同 ——
   温和模型上两个方向数值恰好很接近（实测 0.0456 vs 0.0454），拿它当证据会得出错误结论。
5. **收敛 ≠ 正确**：CAVI 停在局部最优，且 ELBO 单调上升只说明它在爬山，
   不说明爬的是对的峰。判断近似好坏要看 KL / 留出似然，不是看 ELBO 涨了多少。
6. **负控必须过**：解耦（`w = 0` 或 `Λ` 对角）时平均场必须**完全精确**、KL 必须为 0。
   这条不过，说明实现里有耦合项被漏掉或重复计算。
7. **sklearn LDA 的两处细节**（读 `sklearn/decomposition/_lda.py` 源码所得）：
   - `_update_doc_distribution` 里先验是**加到 `doc_topic_d`（自然参数）上**的，
     再就地算 `exp(E[log θ])`；先加先验再取 exp 与先取 exp 再加先验是两回事。
   - `_approx_bound` 里 `E[log p(docs｜θ,β)]` 用 **`logsumexp`**（对 topic 维），
     写成 `log(sum(exp(·)))` 在小值区会直接下溢成 0。

## 九、参考（本轮实读）

1. Blei, Kucukelbir & McAuliffe (2017),
   [Variational Inference: A Review for Statisticians](https://arxiv.org/abs/1601.00670)
   —— Eq (13)(14) ELBO 分解；Algorithm 1 / Eq (17)(18) CAVI 更新；Eq (36)–(40) 指数族捷径；
   §2.5 局部最优与收敛判据。
2. scikit-learn [`sklearn/decomposition/_lda.py`](https://raw.githubusercontent.com/scikit-learn/scikit-learn/main/sklearn/decomposition/_lda.py)
   —— `_update_doc_distribution`（`norm_phi = exp(E[log θ])·exp(E[log β]) + eps`，
   先验加到自然参数上）、`_approx_bound`（`logsumexp` 求期望对数似然）。

> 说明：本 demo 的离散算例（Ising）与高斯算例都是为了**让真后验可解析/可穷举**而选的，
> 与上面两篇文献里的算例（高斯混合、LDA）不同 —— 选例的取舍是「误差可测量」优先。
