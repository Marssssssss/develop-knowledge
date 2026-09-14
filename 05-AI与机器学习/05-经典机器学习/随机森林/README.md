# 随机森林(Random Forest)

> 经典机器学习第二批 · Bagging 系集成。对照:[提升算法](../提升算法/)(Boosting 系)、[决策树](../决策树/)(基学习器)。

## 简介

随机森林 = **决策树 + 两处随机性 + 投票**。Breiman 2001 的定义只有一句话:

> 一组树形分类器 `{h(x, Θk)}`,其中 `{Θk}` 是**独立同分布**随机向量,每棵树在输入 `x` 投一票。

两处随机性是全部精髓:
1. **样本随机**——每棵树在**有放回**抽出的 bootstrap 样本上训练(Bagging);
2. **特征随机**——**每个分裂点**只在一个随机特征子集里挑最优切分(Breiman §3 的 `F`)。

为什么这样做:泛化误差上界是 `PE* ≤ ρ̄·(1−s²)/s²`(`s` 是单棵树的"强度",`ρ̄` 是树间平均相关)。
**降低 `ρ̄` 比提高 `s` 更划算**——因为单棵树再强也就到顶,而相关性只要降一点,上界就明显改善。
随机特征子集正是用"牺牲一点强度"换"大幅降相关"。这也是它比单纯 Bagging 好的原因。

三件事让它成为表格数据的默认首选:**几乎不用调参、对特征尺度不敏感、能直接给出 OOB 误差和变量重要性**。

## 原理详解

### 1. 自助采样与"袋外"样本

对 `n` 个训练样本**有放回**抽 `n` 次。某个样本一次都没被抽到的概率:
```
P(未被抽中) = (1 − 1/n)^n → 1/e ≈ 0.368      (n 大时)
```
所以**每个 bootstrap 集平均把约 36.8% 的样本留在袋外**。这不是缺陷,是白送的一份"免费测试集"。

### 2. OOB 误差(Breiman §3.1)

对训练集中每个样本 `(x, y)`:

> 聚合投票时**只用那些 bootstrap 样本不含 `(x, y)` 的树**,得到的分类器叫 out-of-bag 分类器,
> 它在整个训练集上的错误率就是 OOB 误差。

三个必须记住的性质:

- **无需留出测试集**:OOB 误差直接可用作泛化误差估计,训练/验证不再互斥。
- **会略微高估**当前误差:每棵树只用约 `1/3` 的树来组合(而不是全部),而误差率随组合数增加而下降。
- **本身是无偏的**:对照交叉验证——后者的偏差存在但幅度未知,Breiman 明确说 OOB "unbiased"。

### 3. 边缘函数、强度与相关性

```
mg(X,Y) = av_k I(h_k(X)=Y) − max_{j≠Y} av_k I(h_k(X)=j)          (2)
PE*     = P_{X,Y}(mg(X,Y) < 0)                                     (1)
s       = E_{X,Y}·mr(X,Y)                                          (3)
PE*    ≤ ρ̄·(1−s²)/s²                (定理 2.3)                      
```

`mg` 是"投给正确类的平均票数"减去"投给最强的错误类的平均票数"——是**置信度**而非仅仅正确与否。
本 demo 用 OOB 的类概率直接算 `mg`,进而得到 `s`(强度),它就是上界里那个被平方惩罚的量。

定理 2.3 的实际价值:**树数增加时 `PE*` 依强大数律收敛到极限值,不会过拟合**。
所以"树越多越好"是对的,但收益递减(本 demo:`n_estimators` 从 1 → 300,OOB 误差 0.21 → 0.033)。

### 4. 随机特征子集

| 任务 | `max_features` 默认 | 含义 |
| --- | --- | --- |
| 分类 | `"sqrt"` | 每次分裂只看 `√n_features` 个特征 |
| 回归 | `1.0` / `None` | 看全部特征(= 纯 bagged trees) |

`max_features` 越小 → 树间相关性 `ρ̄` 越低、但单棵树越弱;反之亦然。
Breiman 原文用 `F` 表示(`F=1` 或 `F=int(log₂M + 1)`),并强调 `F` 应由 OOB 估计来选。

### 5. 变量重要性(两条完全不同的路径)

| 方法 | 做法 | 特点 |
| --- | --- | --- |
| **置换重要性**(§10) | 把第 `f` 列**整体打乱**后重算 OOB 误分类率,取相对上升 | 与"该列对**预测**的贡献"直接对应;可低到略负 |
| **Gini 重要性**(不纯度) | 累加该列在所有分裂点带来的加权不纯度下降 | 计算免费;但**偏向取值多的连续特征** |

Breiman §10 还指出一个反直觉现象:两个**完全相同的变量**,重要性会各分一半——
因为分裂点会随机地在两者之间游走。所以"重要性低"不等于"没信息",要看变量间是否冗余。

## 环境与运行

```bash
python random_forest.py    # 纯 stdlib
gcc -O2 -o random_forest random_forest.c -lm && ./random_forest
```

## 关键代码

```python
def _oob_votes(self):
    for i in range(n):
        vs = self.oob_trees[i]        # 只用"没见过样本 i"的那些树
        if not vs: continue
        self.oob_proba[i] = self._aggregate(self.X[i], vs)
```

## 性能边界

- 训练 `O(T · n log n · √d)`(T 棵树,每节点一次排序扫描);**完全可并行**,树之间无依赖。
- 预测 `O(T · depth)`,比单棵树慢 `T` 倍但常数极小。`T = 100~500` 已足够。
- 样本量 `n` 大时 bootstrap 的收益下降(袋外比例固定 36.8%,但"独立性"变弱),此时可考虑 `bootstrap=False` + 特征子集的 Extra-Trees。

## 注意事项与常见坑

1. **OOB 只在 `bootstrap=True` 时有意义**。若用 `bootstrap=False`,所有样本都在袋内,OOB 是 `nan`
   (本 demo 演示了这一情况)。Extra-Trees 默认就是 `bootstrap=False`,故它没有 OOB——这是常被忽略的差别。
2. **OOB 与测试集估计会有可见差距,别急着怀疑实现**。本 demo 中 OOB 准确率 0.9667 而 60 个样本的
   测试集只有 0.90:`n_test = 60` 时单次估计的标准误约 4%,而 OOB 用了全部 180 个训练点。
   比较两个模型的 OOB 时也要用**同一组 OOB 样本**。
3. **超参交互**:`max_features` 与 `n_estimators` 强耦合。`max_features` 小时单树弱,需要更多树来补偿。
   别单独调其中一个。
4. **`predict` 的实现差异会带来微小差别**:原论文是"每棵树投单一类别",sklearn 是"对概率取平均"。
   本 demo 两种都实现(`split="vote"` / `split="proba"`),在边界清晰的数据上结果一致,
   但在类别数多、概率分布分散时会有可见差别。
5. **完全生长树的默认值**:`max_depth=None` + `min_samples_split=2`(每个内部节点最少 2 个样本才尝试分裂)。
   随机森林靠平均来抑过拟合,**不需要**给单棵树剪枝——这是它和单棵树最大的操作差别。
6. **特征重要性不能当因果**。Gini 重要性偏向高基数/连续特征;置换重要性在有强相关特征群时会**互相稀释**。
   需要严格结论时应做条件置换(conditional permutation)或直接做消融实验。
7. **不要用随机森林处理外推任务**(回归尤其明显):树只能输出训练集见过的标签组合/叶值,
   预测区间之外的值一律被钉在边界上。

## 参考资料

### 本轮实际读取的来源

- [L. Breiman (2001) *Random Forests*, Machine Learning 45(1):5–32](https://www.stat.berkeley.edu/~breiman/randomforest2001.pdf) —— Definition 1.1(树集合 + iid 随机向量 + 单位投票)、§2.1 Eq.(1)(2) 边缘函数与 `PE*` 收敛、"随机森林不会因加树而过拟合"、§2.2 Def 2.1–2.4(Eq.(2)(3)(4)(5)(6)(7)(8) 与 Theorem 2.3 `PE* ≤ ρ̄(1−s²)/s²`)、§3.1 OOB 定义与 "约 1/3 样本留外 / 略高估 / 无偏"、§3 "at each node F variables randomly selected;F=1 或 F=int(log₂M+1)"、§10 变量重要性(逐列加噪)与"两个完全相同变量各分一半重要性"
- [scikit-learn 1.9 `1.11. Ensembles`](https://scikit-learn.org/stable/modules/ensemble.html) —— 随机森林在每个分裂点考虑随机特征子集;分类默认 `max_features="sqrt"`、回归默认 `1.0`;`n_estimators` 越大越好但收益递减;`max_depth=None` + `min_samples_split=2`;sklearn "对概率预测取平均而不是让每棵树投单一类别"的原话;Extra-Trees 默认 `bootstrap=False`;`oob_improvement_` 与随机梯度提升的 OOB deviance

### 延伸阅读(本轮未逐篇通读)

- L. Breiman (1996) *Bagging Predictors* — 自助聚合的原始论文
- P. Geurts, D. Ernst, L. Wehenkel (2006) *Extremely Randomized Trees* — Extra-Trees
- Breiman (1996) *Out-of-bag estimation*(Tech. Report)— OOB 的无偏性实证
- Strobl et al. (2007) *Bias in random forest variable importance measures* — Gini 重要性偏向高基数特征
- G. Louppe (2014) *Understanding Random Forests: From Theory to Practice*(博士论文)— 从不纯度到重要性的系统推导
