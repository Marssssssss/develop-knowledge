# 经典机器学习

> 2026-09-12 类目自动拓展新增(深度学习之前的统计学习基础,与 01-深度学习互补)。
> 2026-09-13 首批 5 demo 完成;2026-09-14 第二批 5 demo 完成(覆盖面闭合)。

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
| [朴素贝叶斯/](./朴素贝叶斯/) | 高斯/多项式/伯努利三变体 + 对数域 logsumexp + Laplace/Lidstone 平滑 + 未平滑的 −inf 灾难 + 0×(−inf)=NaN | sklearn `_joint_log_likelihood` 对齐 |
| [支持向量机/](./支持向量机/) | 对偶 + KKT + 箱约束 [L,H] + 解析牛顿步 + WSS1 最大违反对 + 核技巧(linear/poly/rbf/sigmoid) + b 区间精化 | Platt 1998 SMO + Fan/Chen/Lin 2005 WSS1 |
| [随机森林/](./随机森林/) | 自助采样 + OOB 估计 + margin/strength 与泛化界 + 排列重要性 vs 基尼重要性 + 单次排序前缀扫描切分 | Breiman 2001 原论文 |
| [提升算法/](./提升算法/) | AdaBoost.SAMME(`α=lr·(log((1−err)/err)+log(K−1))`) + 指数权重更新与归一 + 加权投票 + GBDT 残差拟合 + shrinkage/subsample | sklearn `_weight_boosting.py` 源码 + Friedman 2001/2002 |
| [模型评估/](./模型评估/) | K 折/分层折下标 + 混淆矩阵(行=真实) + P/R/F_β 四口径 + ROC 与 AUC 三算法(梯形/秩和/Bamber 恒等) + Gini + 多分类 OvR macro/micro | sklearn metrics/CV/roc_auc_score + Bamber 1975 |
| [高斯混合模型与EM/](./高斯混合模型与EM/) | EM 两步(E 步响应度 + M 步闭式) + Cholesky 求 log\|Σ\| 与马氏距离 + full/tied/diag/spherical 四协方差类型 + `reg_covar` 只加在对角 + BIC/AIC 参数计数 | sklearn `_gaussian_mixture.py` 源码 + 奇异性 |
| [DBSCAN密度聚类/](./DBSCAN密度聚类/) | Definition 1–6(core/border/noise、直接密度可达的**非对称**)+ 每点至多一次区域查询 ⇒ O(n·log n) + `min_samples` 含自身 + k-dist 选 eps | Ester et al. KDD-1996 原论文 |
| [层次聚类与连接准则/](./层次聚类与连接准则/) | Lance-Williams 递推(α_i, α_j, β, γ)四准则 + Ward 的 ΔSSE=(n_i·n_j/(n_i+n_j))·‖c_i−c_j‖² + 距离阵取 **d²/2** 使高度=ΔSSE + K−1 维上限 | sklearn `_agglomerative.py` 数值对照 |
| [线性判别与二次判别/](./线性判别与二次判别/) | 类条件高斯 + Bayes ⇒ ω_k=Σ⁻¹μ_k、ω_k0=−½μ_kᵀΣ⁻¹μ_k+log π_k + shrinkage 目标 tr(Σ)/p·I + 白化均值 PCA ⇒ 至多 K−1 维 | sklearn LDA/QDA 源码 + 对角 QDA ≡ GaussianNB |
| [概率校准与Brier分数/](./概率校准与Brier分数/) | Murphy 分解 BS=REL−RES+UNC + 校准曲线 + Platt 目标平滑/初值/缩放阈值 30 + isotonic PAVA 产生并列 + temperature 不改 argmax | sklearn《1.16 Probability calibration》+ `sklearn/calibration.py` |

## 核心知识点(待研究清单)

- [x] **线性回归 / Ridge / Lasso**:normal equation / 正则化 / 偏差-方差 / 多重共线性 (2026-09-13)
- [x] **逻辑回归**:sigmoid / 交叉熵 / L1-L2-ElasticNet / OvR (2026-09-13)
- [x] **决策树 CART**:基尼/熵 / 贪心穷举 / 预剪枝 / ccp_alpha 路径 (2026-09-13)
- [x] **K 近邻**:brute / KD-Tree / Ball-Tree / 距离度量 / k 权衡 (2026-09-13)
- [x] **K-Means**:Lloyd / k-means++ / inertia / n_init / 肘部法 (2026-09-13)
- [x] **朴素贝叶斯**:条件独立性假设;文本分类基线;拉普拉斯平滑。(2026-09-14)
- [x] **支持向量机(SVM)**:最大间隔超平面;核技巧(隐式高维映射);软间隔与 C 参数;对偶问题与 SMO。(2026-09-14)
- [x] **集成学习**:Bagging(随机森林 = 决策树 + 自助采样 + 随机特征子集)vs Boosting(AdaBoost / GBDT,XGBoost 为其工程实现);偏差-方差分解视角。(2026-09-14)
- [x] **模型评估**:交叉验证(K 折 / 分层);混淆矩阵、精确率/召回率/F1、ROC-AUC;过拟合诊断(学习曲线)。(2026-09-14)
- [x] **高斯混合模型与 EM**:隐变量 + E 步响应度 / M 步闭式更新;协方差类型与奇异性;BIC/AIC 选 K。(2026-09-19)
- [x] **DBSCAN 密度聚类**:核心点/边界点/噪声;ε 与 MinPts;密度可达 vs 密度相连;对非球形簇的优势。(2026-09-19)
- [x] **层次聚类与连接准则**:凝聚/分裂;single/complete/average/ward 的 Lance-Williams 递推;树状图切分。(2026-09-19)
- [x] **线性/二次判别分析(LDA/QDA)**:类条件高斯 + 贝叶斯决策;共享协方差 ⇒ 线性边界;shrinkage。(2026-09-19)
- [x] **概率校准**:Brier 分数与 Murphy 分解;校准曲线;Platt scaling / isotonic / temperature scaling。(2026-09-19)

## 参考资料(实际阅读过的权威来源)

- [scikit-learn Linear Models](https://scikit-learn.org/dev/modules/linear_model.html) — OLS / Ridge / Lasso / LogisticRegression 全套公式 + 6 solver 对照
- [scikit-learn Decision Trees](https://scikit-learn.org/0.22/modules/tree.html) — ID3/C4.5/C5.0/CART 对比 + Gini/Entropy + 复杂度
- [scikit-learn Cost Complexity Pruning](https://scikit-learn.org/1.1/auto_examples/tree/plot_cost_complexity_pruning.html) — ccp_alpha 路径
- [scikit-learn Nearest Neighbors §1.6](https://scikit-learn.org/stable/modules/neighbors.html) — brute/KDTree/BallTree + 平票警告
- [scikit-learn KMeans API](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.KMeans.html) — n_init='auto' + algorithm='lloyd'
- [scikit-learn Clustering §2.3](https://scikit-learn.org/stable/modules/clustering.html) — Lloyd + k-means++ + inertia 缺陷
- [scikit-learn Naive Bayes §1.9](https://scikit-learn.org/stable/modules/naive_bayes.html) — 三变体公式 + θ̂yi=(Nyi+α)/(Ny+αn) + "decent classifier but a bad estimator" + `_joint_log_likelihood`
- [scikit-learn SVM §1.4](https://scikit-learn.org/stable/modules/svm.html) — 对偶/KKT/核函数表/`C` 与 `gamma` 量纲 + libsvm 的 `Platt scaling`
- [scikit-learn Ensemble §1.11](https://scikit-learn.org/stable/modules/ensemble.html) — 随机森林(bootstrap/OOB/max_features)+ AdaBoost.SAMME + GBDT(加性模型/负梯度/shrinkage/subsample)
- [`sklearn/ensemble/_weight_boosting.py`(源码)](https://raw.githubusercontent.com/scikit-learn/scikit-learn/main/sklearn/ensemble/_weight_boosting.py) — `α = lr·(log((1−err)/err)+log(K−1))`、`exp(log(w)+α·incorrect·(w>0))`、`err≤0` 早停、`err≥1−1/K` 丢弃
- [scikit-learn Metrics §3.3](https://scikit-learn.org/stable/modules/model_evaluation.html) — 混淆矩阵"行=真实类" + P/R/F_β + micro/macro/weighted/samples 四口径
- [scikit-learn Cross-validation §3.1](https://scikit-learn.org/stable/modules/cross_validation.html) — KFold `[n·i/k, n·(i+1)/k)` 切分 + **默认 shuffle=False** + StratifiedKFold
- [scikit-learn `roc_auc_score`](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.roc_auc_score.html) — 多分类 OvR macro/micro 公式
- [scikit-learn 示例 `plot_roc`](https://scikit-learn.org/stable/auto_examples/model_selection/plot_roc.html) — ROC 绘制 + 多分类 OvR/OvO
- Breiman, *Random Forests*, Machine Learning 45(1), 2001 — margin 函数、strength、泛化误差界 `PE* ≤ ρ̄(1−s²)/s²`、OOB 估计
- Platt, *Sequential Minimal Optimization*, 1998 — SMO 的解析两步与启发式遍历
- Fan, Chen, Lin, *Working Set Selection Using Second Order Information*, JMLR 2005 — WSS1/WSS2 与 `m(α) ≤ M(α)` 停机判据
- Zhu, Zou, Rosset, Hastie, *Multi-class AdaBoost*, 2009 — SAMME 与 `log(K−1)` 项、eq.(15) 概率
- Friedman, *Greedy Function Approximation* 2001 / *Stochastic Gradient Boosting* 2002 — 函数空间梯度下降与 subsample
- Bamber, *The Area Above the Ordinal Dominance Graph…*, J. Math. Psychol. 1975 — AUC = 有序对胜率(秩和等价)

### 2026-09-19 第三批(GMM/EM · DBSCAN · 层次聚类 · LDA-QDA · 概率校准)新增来源

以下链接均在本轮实际抓取并用于撰写 README(括号内为本次复核下载字节数):

- [scikit-learn Gaussian mixture models](https://scikit-learn.org/stable/modules/mixture.html) (64,237 B) — EM 迭代、`covariance_type` 四种、`reg_covar`、`n_parameters` 与 BIC/AIC
- [`sklearn/mixture/_gaussian_mixture.py`(源码)](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/mixture/_gaussian_mixture.py) (35,904 B) — `_estimate_log_prob` / `_m_step` / `_estimate_log_prob_resp`
- Ester, Kriegel, Sander, Xu, *A Density-Based Algorithm for Discovering Clusters in Large Spatial Databases with Noise*, KDD-1996 — [aaai.org PDF](https://www.aaai.org/Papers/KDD/1996/KDD96-037.pdf) (631,443 B,6 页) — Definition 1–6 与区域查询复杂度
- [scikit-learn Clustering §2.3](https://scikit-learn.org/stable/modules/clustering.html) (245,702 B) — DBSCAN / 层次聚类与连接准则
- [`sklearn/cluster/_dbscan.py`(源码)](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/cluster/_dbscan.py) (20,733 B)
- [`sklearn/cluster/_agglomerative.py`(源码)](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/cluster/_agglomerative.py) (49,491 B) — 连接准则与 Ward `d²/2` 距离阵
- [scikit-learn LDA/QDA §1.2](https://scikit-learn.org/stable/modules/lda_qda.html) (61,291 B) — 类条件高斯、shrinkage 目标、维度上限 K−1
- [`sklearn/discriminant_analysis.py`(源码)](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/discriminant_analysis.py) (45,243 B)
- [scikit-learn Probability calibration §1.16](https://scikit-learn.org/stable/modules/calibration.html) (72,417 B) — 良好校准定义、Murphy 分解、三种校准器
- [`sklearn/calibration.py`(源码)](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/calibration.py) (62,711 B) — `_sigmoid_calibration` 目标平滑与缩放阈值 30

> 文献著录(经上述官方文档引用,**本轮未实读全文**,故未用于数值口径):
> Murphy, *A New Vector Partition of the Probability Score*, J. Appl. Meteor. Climatol. 12(4), 1973;
> Niculescu-Mizil & Caruana, *Predicting Good Probabilities With Supervised Learning*, ICML 2005;
> Lance & Williams, *A General Theory of Classificatory Sorting Strategies*, Comput. J. 1967。
- Hastie, Tibshirani, Friedman《The Elements of Statistical Learning》Ch.3-13 — 统计学习视角
- Bishop《Pattern Recognition and Machine Learning》Ch.3-9 — 经典 ML 数学基础
