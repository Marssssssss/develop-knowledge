# 经典机器学习

> 2026-09-12 类目自动拓展新增(深度学习之前的统计学习基础,与 01-深度学习互补)。
> 2026-09-13 首批 5 demo 完成。

## 简介

经典(传统)机器学习指深度学习流行之前、以统计学习理论为基础的算法族:决策树、支持向量机、K 近邻、朴素贝叶斯、线性模型、聚类与集成学习。它们在小样本、表格数据、可解释性要求高的场景仍是首选(如风控、医疗、工业),也是理解深度学习(如梯度提升 → 残差思想)的基础。

## 已完成 demo

| 目录 | demo | 核心机制 |
| --- | --- | --- |
| [线性回归/](./线性回归/) | OLS normal equation `w=(XᵀX)⁻¹Xᵀy` + Ridge `(XᵀX+αI)⁻¹Xᵀy` + 梯度下降 + 多重共线性收缩 + 复杂度 O(n·p²) | C/Python 数值验证 |
| [逻辑回归/](./逻辑回归/) | sigmoid + 交叉熵 + L2 + 数值梯度验证 + OvR 多分类 + 决策边界可视化 | sklearn 6 solver 对照 + C↔α 关系 |
| [决策树/](./决策树/) | CART 二叉树 + 基尼/熵 + 贪心穷举 (feature, threshold) + 预剪枝 + Grid 调优 + 标签噪声演示 | 5 算法对比 ID3/C4.5/C5.0/CART |
| [K近邻/](./K近邻/) | Brute force + KD-Tree 加速 + uniform/distance 加权投票 + 距离度量对比 + k 偏差-方差权衡 | 4 algorithm 对比 + KD-Tree 17x 加速 |
| [K均值聚类/](./K均值聚类/) | Lloyd 三步迭代 + k-means++ 概率初始化 + 多次重启 + 肘部法 + 假设演示(球形 vs 同心圆) | sklearn n_init='auto' + Arthur 2007 |

## 核心知识点(待研究清单)

- [x] **线性回归 / Ridge / Lasso**:normal equation / 正则化 / 偏差-方差 / 多重共线性 (2026-09-13)
- [x] **逻辑回归**:sigmoid / 交叉熵 / L1-L2-ElasticNet / OvR (2026-09-13)
- [x] **决策树 CART**:基尼/熵 / 贪心穷举 / 预剪枝 / ccp_alpha 路径 (2026-09-13)
- [x] **K 近邻**:brute / KD-Tree / Ball-Tree / 距离度量 / k 权衡 (2026-09-13)
- [x] **K-Means**:Lloyd / k-means++ / inertia / n_init / 肘部法 (2026-09-13)
- [ ] **朴素贝叶斯**:条件独立性假设;文本分类基线;拉普拉斯平滑。
- [ ] **支持向量机(SVM)**:最大间隔超平面;核技巧(隐式高维映射);软间隔与 C 参数;对偶问题与 SMO。
- [ ] **集成学习**:Bagging(随机森林 = 决策树 + 自助采样 + 随机特征子集)vs Boosting(AdaBoost / GBDT / XGBoost:序列拟合残差);偏差-方差分解视角。
- [ ] **模型评估**:交叉验证(K 折 / 分层);混淆矩阵、精确率/召回率/F1、ROC-AUC;过拟合诊断(学习曲线)。

## 参考资料(实际阅读过的权威来源)

- [scikit-learn Linear Models](https://scikit-learn.org/dev/modules/linear_model.html) — OLS / Ridge / Lasso / LogisticRegression 全套公式 + 6 solver 对照
- [scikit-learn Decision Trees](https://scikit-learn.org/0.22/modules/tree.html) — ID3/C4.5/C5.0/CART 对比 + Gini/Entropy + 复杂度
- [scikit-learn Cost Complexity Pruning](https://scikit-learn.org/1.1/auto_examples/tree/plot_cost_complexity_pruning.html) — ccp_alpha 路径
- [scikit-learn Nearest Neighbors §1.6](https://scikit-learn.org/stable/modules/neighbors.html) — brute/KDTree/BallTree + 平票警告
- [scikit-learn KMeans API](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.KMeans.html) — n_init='auto' + algorithm='lloyd'
- [scikit-learn Clustering §2.3](https://scikit-learn.org/stable/modules/clustering.html) — Lloyd + k-means++ + inertia 缺陷
- Bishop《Pattern Recognition and Machine Learning》Ch.3-9 — 经典 ML 数学基础
- Friedman, Hastie, Tibshirani《Elements of Statistical Learning》Ch.3-13 — 统计学习视角
