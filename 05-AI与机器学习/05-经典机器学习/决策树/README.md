# 决策树 CART (Classification and Regression Trees)

## 简介

决策树通过递归二分特征空间建立可解释的预测模型。CART (Breiman et al. 1984) 是 sklearn 唯一实现的决策树算法——只生成二叉树、支持分类和回归、不输出规则集。本 demo 聚焦 CART 分类:基尼不纯度 + 贪心穷举 + 预剪枝 + 成本复杂度剪枝 + 树结构度量。

- **基尼不纯度**: `Gini(S) = 1 − Σ p_k²`,等价"随机抽两个样本类别不同的概率"
- **信息熵**: `H(S) = −Σ p_k log₂ p_k`,sklearn `criterion='entropy'`
- **贪心穷举**: 对每个特征、每个候选阈值(相邻唯一值的中点)算加权不纯度,选最小
- **预剪枝**: `max_depth` / `min_samples_split` / `min_samples_leaf` / `max_features`
- **后剪枝**: `ccp_alpha` 成本复杂度剪枝(Breiman 1984)

## 原理详解

1. **不纯度度量**:基尼(0..0.5 二分类区间)与熵(0..1 二分类区间,极值相同)数学性质相近;极端纯净时都 = 0,等分 50/50 时基尼 = 0.5 / 熵 = 1.0
2. **最优分裂**:遍历每个特征 j、相邻唯一值 (x_i, x_{i+1}) 的中点作阈值 thr,把样本分成左右两个子集,加权不纯度最小者当选
3. **递归终止**:`max_depth` 到 / `min_samples_split` 不够 / 当前节点纯净 / 没有有效分裂
4. **复杂度**:每次分裂扫描 O(n_features · n_samples),整树近似 O(n_features · n_samples² · log n_samples)(平衡假设,sklearn 0.22 docs)
5. **特征随机性**:`max_features` 控制每次分裂只考虑随机特征子集,Random Forest 必用,降低方差
6. **剪枝**:
   - **预剪枝**:`max_depth` 限制深度 / `min_samples_leaf` 限制叶最小样本数
   - **成本复杂度剪枝**: `R_α(T) = R(T) + α|T|`,|T| 叶节点数;α 大剪枝力度大,sklearn `cost_complexity_pruning_path` 返回 α 序列 + 各 α 下总杂质
7. **多分类**:sklearn 自动多分类(目标 y 整数),不需 OvR;概率 `predict_proba` 来自叶内类别比例

## 对比 / 选型

| 算法 | 提出 | 树结构 | 特征 | 优缺点 |
| --- | --- | --- | --- | --- |
| ID3 | Quinlan 1986 | 多叉树 | 信息增益 | 仅离散特征、无剪枝 |
| C4.5 | Quinlan 1993 | 多叉树 | 信息增益率 | 连续特征离散化、规则集输出 |
| CART | Breiman 1984 | 二叉树 | 基尼 / 方差 | sklearn 唯一实现、支持回归 |
| C5.0 | Quinlan(商业) | 多叉树 | 增益率 | 比 C4.5 内存小、规则更精 |

## 环境准备

- Python 3.10+ (纯 stdlib)
- OS:跨平台

## 运行方式

```bash
python3 decision_tree.py
```

## 关键代码片段

```python
def _grow(self, X, y, sorted_idx, depth, rng):
    n = len(y)
    cur_impurity = self._impurity(y)  # Gini 或 Entropy
    if (depth >= max_depth or n < min_samples_split or cur_impurity == 0.0):
        return Node(label=majority(y))  # 终止:建叶节点
    best_gain, best = 0.0, None
    for j in feats:
        order = sorted_idx[j]
        for k in range(len(order) - 1):
            # 候选阈值 = 相邻唯一值中点
            thr = (X[order[k]][j] + X[order[k+1]][j]) / 2.0
            # 加权不纯度
            weighted = (nl/n) * Gini(yl) + (nr/n) * Gini(yr)
            gain = cur_impurity - weighted
            if gain > best_gain:
                best_gain, best = gain, (j, thr, left, right)
    # 递归建子树
    return Node(feature=j, threshold=thr,
                left=self._grow(left_X, left_y, ...),
                right=self._grow(right_X, right_y, ...))
```

## 性能与边界

- 单次 fit:n=105 训练 + 阈值扫描 + 递归,实测 < 0.1s(纯 stdlib)
- 数据规模:n > 10⁴ 时深度递归栈深可控;sklearn 用 Cython 编译加速 100x
- 高维稀疏数据用 `csc_matrix` 训练 + `csr_matrix` 预测(sklearn 建议)
- 特征多 / 样本少容易过拟合,先降维(PCA)或做特征选择

## 注意事项与常见坑

1. **基尼 vs 熵**:sklearn 文档明示"Most of the time they lead to identical trees";Gini 计算更快(sklearn 默认)
2. **类别变量**:sklearn CART 不直接支持类别变量,需 one-hot 编码
3. **样本不均衡**:`class_weight='balanced'` 或 `sample_weight`;否则叶节点几乎全部预测多数类
4. **max_features='sqrt'**:Random Forest 默认,单树一般 None(全用)
5. **predict 概率**:来自叶节点类别比例,`predict_proba` 列按 `classes_` 顺序
6. **特征顺序敏感**:`sorted_idx[j]` 预排序索引,分裂判断 `row[j] <= thr`,ties 走左分支
7. **树深度与可解释性**:深度 ≤ 5 可读;深度 10+ 失去可解释性,不如改用 GBDT 或线性模型
8. **后剪枝更优但代价高**:`ccp_alpha` 0..max_eff_alpha 序列拟合多棵树,选验证集最优(参考 sklearn `plot_cost_complexity_pruning`)

## 参考资料(实际阅读过的权威来源)

- [scikit-learn DecisionTreeClassifier API](https://scikit-learn.org/1.0/modules/generated/sklearn.tree.DecisionTreeClassifier.html) — 全部参数(criterion/splitter/max_depth/min_samples_leaf/ccp_alpha)+ Gini/Entropy/min_samples_split 官方定义
- [scikit-learn Decision Trees §1.10](https://scikit-learn.org/0.22/modules/tree.html) — ID3/C4.5/C5.0/CART 算法对比、复杂度 O(n_features·n_samples²·log n_samples)、实践建议 max_depth=3 起步
- [scikit-learn Cost Complexity Pruning 教程](https://scikit-learn.org/1.1/auto_examples/tree/plot_cost_complexity_pruning.html) — 成本复杂度剪枝 α 路径 + 验证集调优示例
- Breiman, Friedman, Olshen, Stone《Classification and Regression Trees》(1984 Wadsworth) — CART 原著
- Quinlan《C4.5: Programs for Machine Learning》(1993 Morgan Kaufmann) — ID3/C4.5 完整描述
