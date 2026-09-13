# 线性回归 (OLS / Ridge)

## 简介

线性回归是统计学习与机器学习最基础的监督模型:用一条直线/超平面拟合特征与连续目标的关系。普通最小二乘 (OLS) 求解**残差平方和**的闭式解;当特征近似线性相关 (多重共线性) 时 OLS 系数爆炸,Ridge 通过 L2 收缩抗共线性。

- **OLS**: `min_w ‖Xw − y‖²`,normal equation `w = (XᵀX)⁻¹ Xᵀ y`
- **Ridge**: `min_w ‖Xw − y‖² + α‖w‖²`,closed form `w = (XᵀX + αI)⁻¹ Xᵀ y`
- **GD**: 迭代法 `w ← w − η·(Xᵀ(Xw − y)/n + λw)`,大数据集 / 在线学习时用
- 复杂度 O(n_sample·n_features²),闭式解 vs GD 权衡
- 多重共线性 (multicollinearity): 设计矩阵近似奇异 → OLS 对方差敏感

## 原理详解

1. **OLS normal equation**:对损失 L(w) = (Xw − y)ᵀ(Xw − y) 求梯度并令 = 0,得 `XᵀX w = Xᵀ y`,解 `w = (XᵀX)⁻¹ Xᵀ y`。
2. **Ridge closed form**:在损失加上 α·wᵀw(shrinkage prior,系数服从零均值高斯先验),驻点方程变为 `(XᵀX + αI) w = Xᵀ y`,αI 让矩阵严格正定,即使 XᵀX 奇异也能解。
3. **fit_intercept**:True 时 X 拼一列 1,w 的第一维对应 intercept_,可让模型平移。
4. **梯度下降**:∂L/∂w = Xᵀ(Xw − y)/n + λw(scikit-learn SGDRegressor / Ridge with solver='sag'),适合 n 或 p 极大不能求逆的情况。
5. **复杂度**:求逆 O(p³) 主项;sklearn 用 SVD 复杂度 O(n·p²) 比 Cholesky 稳。
6. **多重共线性**:x₂ ≈ x₁ + ε → XᵀX 行列式 ≈ 0 → OLS 解爆炸;Ridge 通过 αI 阻尼,α 越大系数越小方差越低(bias-variance 权衡)。

## 对比 / 选型

| 算法 | 公式 | 优点 | 缺点 |
| --- | --- | --- | --- |
| OLS | w = (XᵀX)⁻¹ Xᵀ y | 无偏估计 (Gauss-Markov 最优) | 多重共线性下方差极大 |
| Ridge | w = (XᵀX + αI)⁻¹ Xᵀ y | 抗共线性 + 数值稳定 | 引入 bias,需调 α |
| Lasso | w = argmin‖Xw − y‖² + α‖w‖₁ | 自动特征选择(稀疏解) | 不可微,需坐标下降 |
| Elastic-Net | L1 + L2 组合 | 折中 Ridge 与 Lasso | 需调 α、l1_ratio |

## 环境准备

- Python 3.10+ (仅用 stdlib,无 numpy / sklearn)
- OS:跨平台

## 运行方式

```bash
python3 linear_regression.py
```

## 关键代码片段

```python
class LinearRegression:
    """OLS 闭式解 normal equation:w = (XᵀX)⁻¹ Xᵀ y。"""

    def fit(self, X, y):
        if self.fit_intercept:
            X = [[1.0] + row for row in X]    # 加偏置列
        Xt = transpose(X)
        XtX = matmul(Xt, X)
        Xty = matvec(Xt, y)
        w = matvec(inverse(XtX), Xty)          # 求逆 → 解向量
        self.intercept_ = w[0]
        self.coef_ = w[1:]
        return self
```

## 性能与边界

- 求逆 O(p³),只适合 p 较小(数十以内);大数据集用 Cholesky 或 SVD
- sklearn `LinearRegression` 内部走 LAPACK gelsd SVD,比 naive 求逆稳定
- 大数据用 `Ridge(solver='sag')` 或 `SGDRegressor`
- Ridge α=0 等价 OLS(数学上;数值上 αI 仍改善矩阵条件)

## 注意事项与常见坑

1. **矩阵奇异**:`inverse()` 抛 ValueError → 数据多重共线性或 p > n,改用 Ridge 或 SVD 解法
2. **特征尺度**:`StandardScaler` 后再拟合,否则 α 对各特征不均衡;非标准化时 GradientDescent 收敛慢
3. **intercept 必须显式建模**:`fit_intercept=False` 时模型强制过原点
4. **α 选择**:`RidgeCV` LOOCV (公式闭式) 或 K 折;α=0 ↔ OLS
5. **噪声/异常值**:OLS 对 outlier 平方放大影响;用 Huber / RANSAC 回归更稳
6. **数值对比**:`time perf_counter()` 实测 p=10 比 p=2 慢 ~25x 与 O(p²) 趋势吻合

## 参考资料(实际阅读过的权威来源)

- [scikit-learn Linear Models §1.1](https://scikit-learn.org/dev/modules/linear_model.html) — OLS / Ridge 闭式解、SVD 复杂度 O(n·p²)、多重共线性、α 选择、RidgeCV LOO
- [scikit-learn Plot OLS vs Ridge Variance](https://scikit-learn.org/stable/auto_examples/linear_model/plot_ols) — 2 点样本噪声演示 OLS 高方差、Ridge 稳定
- Bishop《Pattern Recognition and Machine Learning》Ch.3.1.1 — Linear Basis Function Models 的正则化与几何解释
- Friedman, Hastie, Tibshirani《Elements of Statistical Learning》Ch.3.2 — Shrinkage Methods / Ridge 推导
