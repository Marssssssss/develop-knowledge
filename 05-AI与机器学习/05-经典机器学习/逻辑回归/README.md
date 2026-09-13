# 逻辑回归 (Logistic Regression)

## 简介

逻辑回归**是分类算法**(虽然名字带"回归")。通过 sigmoid 把线性输出映射到 (0,1) 区间,代表正类概率;用交叉熵 (log-loss) 替代 MSE 以避免 sigmoid 饱和梯度消失。sklearn 的 L2 正则化目标等价于对系数加零均值高斯先验,提高数值稳定性。

- **sigmoid**: `σ(z) = 1 / (1 + exp(−z))`
- **二元 log-loss**: `L = −y log p − (1−y) log(1−p)`,p = σ(Xᵀw)
- **L2 目标**: `min_w ½wᵀw + C·Σ log(1 + exp(−y_i·X_iᵀw))`
- **C 与 α 关系**: `α = 1/(n_samples·C)`,C 大→弱正则,小→强正则
- **5 solver**: lbfgs / liblinear / newton-cg / sag / saga;默认 lbfgs(鲁棒),大数据 saga
- **多分类**: OvR(one-vs-rest) / multinomial(softmax 全局归一化)

## 原理详解

1. **sigmoid**:把 ℝ 映射到 (0,1) 解释为概率;`σ(0)=0.5`,`σ(z) = 1 − σ(−z)`(避免 exp 溢出)。
2. **log-loss**:对 σ 输出的概率用对数似然的负号,正好给 −log p 的稀疏惩罚;梯度 `∂L/∂w = Xᵀ(σ(Xw) − y)/n`,无 MSE 在饱和区(σ≈0/1)梯度消失的缺陷。
3. **L2 正则**:加 `½wᵀw/(n·C)` 项,等价贝叶斯视角的零均值高斯先验;C 越大越接近无正则。
4. **梯度下降**:`w ← w − η·(Xᵀerr/n + w/(n·C))`,batch GD;大数据换 `SGDClassifier(loss='log')` 或 `solver='sag'`。
5. **多分类 OvR**:对 K 类训练 K 个二元 LR,预测时归一化 `p_k = σ_k / Σσ_j`,取最大;等价 sklearn `multi_class='ovr'`。`multinomial` 一次拟合全部类(softmax 交叉熵),概率校准更好。
6. **算法选择(sklearn 表格)**:
   - `liblinear`:小数据 / L1 penalty,坐标下降
   - `lbfgs`:L2 + 无正则,默认,准牛顿法,鲁棒
   - `newton-cg`:L2 + 无正则,Newton 法,二次收敛
   - `sag` / `saga`:大数据,SAG + 增量,L1 仅 saga 支持
   - 仅 `saga` 支持 `elasticnet`

## 对比 / 选型

| 损失 | 公式 | 适用 |
| --- | --- | --- |
| log-loss | −y log p − (1−y) log(1−p) | 二元分类 |
| cross-entropy | −Σ y_c log p_c | 多分类(softmax) |
| hinge | max(0, 1 − y·f) | SVM(产生稀疏解) |

| Solver | L2 | L1 | ElasticNet | 大数据 | 无正则 |
| --- | --- | --- | --- | --- | --- |
| lbfgs | ✓ | ✗ | ✗ | ✗ | ✓ |
| liblinear | ✓(OvR) | ✓(OvR) | ✗ | ✗ | ✗ |
| newton-cg | ✓ | ✗ | ✗ | ✗ | ✓ |
| sag | ✓ | ✗ | ✗ | ✓ | ✓ |
| saga | ✓ | ✓ | ✓ | ✓ | ✓ |

## 环境准备

- Python 3.10+ (纯 stdlib)
- OS:跨平台

## 运行方式

```bash
python3 logistic_regression.py
```

## 关键代码片段

```python
def sigmoid(z: float) -> float:
    # 数值稳定:z>=0 与 z<0 分两支,避免 exp 溢出
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)

class LogisticRegression:
    def fit(self, X, y):
        # 梯度下降 + L2 正则
        for _ in range(self.n_iter):
            errs = [sigmoid(dot(w, row)) - yi for row, yi in zip(X, y)]
            grad = [sum(errs[i] * X[i][j] for i in range(n)) / n + w[j] / (n * C) for j in range(p)]
            w[:] = [w[j] - self.lr * grad[j] for j in range(p)]
```

## 性能与边界

- batch GD 每步 O(n·p),`n_iter` 默认 2000;小数据快速收敛
- 大数据用 `solver='sag'` 单次扫描 O(max_iter·n·p);`saga` 支持 L1
- 多分类 OvR 复杂度 O(K·n·p);`multinomial` 单次拟合 O(K·n·p)
- 数值梯度验证:中心差分 `(L(w+ε) − L(w−ε))/(2ε)`,max err < 1e-7 验证解析梯度正确

## 注意事项与常见坑

1. **数值稳定**:sigmoid 直接 `exp` 在大负数会下溢;分 z≥0 / z<0 两支,等价 1 − σ(−z) 利用
2. **学习率选择**:lr 太大震荡,太小收敛慢;建议 `lr=0.1`,n_iter=2000-5000
3. **特征必须标准化**:不标准化则 w 各维梯度量级差异大,GD 收敛困难或震荡
4. **C=∞ 对应 α=0** 完全不收缩,等价 OLS 的 LR;**C 极小** 强收缩 → 偏差大
5. **类别不平衡**:`class_weight='balanced'` 给少数类更高权重,或 `sample_weight`
6. **多分类 OvR vs multinomial**:liblinear 用 OvR;saga/lbfgs 用 multinomial(概率校准更准)
7. **决策边界 0.5 阈值**:不平衡时调阈值;`predict_proba` 后自定义

## 参考资料(实际阅读过的权威来源)

- [scikit-learn LogisticRegression §1.1.11](https://scikit-learn.org/1.5/modules/linear_model.html) — 二元 L2 目标、L1/ElasticNet、6 solver 对照表、multinomial 数学细节、Bishop PRML Ch.4.3.4 引
- [scikit-learn LogisticRegression 1.0](https://scikit-learn.org/1.0/modules/linear_model.html) — saga 描述 + SCHMIDT 2007 SAG 算法引用
- Bishop《Pattern Recognition and Machine Learning》Ch.4.3.4 — Logistic Regression 的迭代加权最小二乘 (IRLS) 推导
- Friedman, Hastie, Tibshirani《Elements of Statistical Learning》Ch.4.4 — Multinomial Logistic Regression / softmax
