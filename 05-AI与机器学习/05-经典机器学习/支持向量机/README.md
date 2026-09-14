# 支持向量机(SVM)

> 经典机器学习第二批 · 最大间隔分类器 + SMO 求解器。同族:[逻辑回归](../逻辑回归/)(线性判别)、[决策树](../决策树/)(非线性判别)。

## 简介

SVM 的核心思想只有一句话:**在所有能把两类分开的超平面里,挑那个离最近样本最远的**。
"最近样本到超平面的距离"称为**间隔(margin)**,把这些最近样本称为**支持向量(support vector)**——
解只由它们决定,其余样本怎么动都不影响结果。这是它区别于逻辑回归(用全部样本)的根本点。

两个关键扩展让它从线性变成通用分类器:

1. **软间隔(Cortes & Vapnik 1995)**:允许少量样本越界,用惩罚系数 `C` 权衡"间隔宽"与"训练错少"。
2. **核技巧**:把样本隐式映射到高维空间 `φ(x)`,只在内积里出现而**从不显式计算 `φ`**。
   线性不可分的数据在足够高维空间里往往线性可分(经典例子:XOR)。

必须记住的两个工程事实:
- 训练核心是一个**二次规划(QP)**,libsvm 的求解器复杂度在 `O(n_features·n²)` 与 `O(n_features·n³)` 之间,
  **样本数`n`是瓶颈**(不是特征数),`n` 上万后一般换线性专用求解器或降维。
- `C` 与 RBF 的 `γ` **必须指数尺度网格搜索**,它们对结果的影响比"选哪个核"还大。

## 原理详解

### 1. 原始问题(软间隔)

```
min_{w,b,ζ}  ½·wᵀw + C·Σ ζi
s.t.         yi(wᵀφ(xi) + b) ≥ 1 − ζi ,  ζi ≥ 0 , i = 1..n
```

`½wᵀw` 最小化等价于最大化间隔 `2/‖w‖`(间隔 = 2/‖w‖);`ζi` 是第 `i` 个样本的越界量,`C` 越大越不容忍越界。
`C → ∞` 退化为硬间隔。

### 2. 对偶问题(真正的求解对象)

```
min_α  ½·αᵀQα − eᵀα
s.t.   yᵀα = 0 ,  0 ≤ αi ≤ C
其中   Qij = yi·yj·K(xi, xj)
```

- `e` 是全 1 向量;`αi` 是**对偶系数**,上界为 `C`。
- 约束 `yᵀα = 0` 说明"不能只动一个乘子"(单变量改动必然破坏等式约束)——这正是 SMO 必须**一次动两个**的原因。

### 3. 决策函数与支持向量

```
f(x) = Σ_{i∈SV} yi·αi·K(xi, x) + b      predict = sign(f(x))
```

`αi = 0` 的样本**完全不参与求和**。因此:
- `w = Σ αi·yi·φ(xi)`(仅线性核有显式 `w`),`‖w‖` 只由支持向量决定;
- 预测代价与**支持向量数**成正比,而不是训练集大小;
- 这也解释了核技巧为什么成立:决策函数只依赖**内积**,而内积可以换成核。

`b` 由互补松弛条件反解:`b = yk − Σ αi·yi·K(xi, xk)`,取任意一个非边界支持向量 `xk`(0 < αk < C)。

### 4. KKT 条件(收敛判据)

| `αi` 取值 | 要求 | 几何含义 |
| --- | --- | --- |
| `αi = 0` | `yi·f(xi) ≥ 1` | 在间隔外,分类正确 |
| `0 < αi < C` | `yi·f(xi) = 1` | **恰好在间隔面上**(自由支持向量) |
| `αi = C` | `yi·f(xi) ≤ 1` | 越界样本,被 `C` 顶住 |

满足全部三行 ⟺ 已到全局最优。本 demo 的 `kkt_violations()` 逐条检查,输出恒为 `[]`/`0`。

### 5. SMO:把大 QP 拆成最小的解析子问题

每步固定其余乘子,只解 `(αi, αj)` 的二维子问题(可解析求最优):

1. **箱约束区间**。由 `yi·αi + yj·αj = const` 与 `0 ≤ α ≤ C` 得
   ```
   yi ≠ yj :  L = max(0, αj − αi) ,  H = min(C, C + αj − αi)
   yi = yj :  L = max(0, αi + αj − C) ,  H = min(C, αi + αj)
   ```
2. **解析步长**。沿 `yᵀα = 0` 的可行方向,目标函数是 `αj` 的二次函数,二阶导
   ```
   η = ∂²W/∂αj² = 2·Kij − Kii − Kjj  (≤ 0,核为半正定时)
   ```
   一阶导 `∂W/∂αj = yj·(Ei − Ej)`,其中 `Ei = f(xi) − yi`。牛顿步:
   ```
   αj ← αj − yj·(Ei − Ej) / η     然后投影回 [L, H]
   αi ← αi + yi·yj·(αj_old − αj_new)     (由等式约束推出)
   ```
3. **支持向量计数与动量**。这样每步只改两个乘子,内存 `O(n)`,
   实测复杂度介于线性与二次之间(Platt 原文:稀疏数据上比 chunking 快 1000 倍以上)。
4. **工作集选择(WSS1 最大违反对)**。定义
   ```
   I_up  = {t | αt < C, yt = +1} ∪ {t | αt > 0, yt = −1}
   I_low = {t | αt > 0, yt = +1} ∪ {t | αt < C, yt = −1}
   i = argmax_{I_up}(yt − gt) ,  j = argmin_{I_low}(yt − gt)
   ```
   收敛判据:`max_{I_up}(yt − gt) ≤ min_{I_low}(yt − gt)`。
   注意 `yt − gt = −yt·∇f(α)t`,**与 `b` 无关**,所以可以直接当违反度量用。

### 6. 核函数

| 核 | 形式 | 备注 |
| --- | --- | --- |
| linear | `⟨x, x'⟩` | 高维稀疏首选;可显式求 `w` |
| poly | `(γ⟨x,x'⟩ + r)^d` | `d` 为 `degree`,`r` 为 `coef0` |
| rbf | `exp(−γ‖x−x'‖²)` | `γ > 0`;最常用,`γ` 大则单个样本影响范围小 |
| sigmoid | `tanh(γ⟨x,x'⟩ + r)` | 与两层感知机同形,但常非半正定 |

## 环境与运行

```bash
python svm_smo.py                       # 纯 stdlib
gcc -O2 -o svm_smo svm_smo.c -lm && ./svm_smo
```

## 关键代码

```python
eta = 2.0 * self.K[i][j] - self.K[i][i] - self.K[j][j]   # ≤ 0
aj_new = min(H, max(L, aj_old - self.y[j] * (Ei - Ej) / eta))   # 牛顿步 + 投影
self.alphas[i] = ai_old + self.y[i] * self.y[j] * (aj_old - aj_new)
```

## 性能边界

- 训练 `O(n²)~O(n³)`(核矩阵本身 `O(n²)` 内存);`n` 是瓶颈。
- 预测 `O(#SV · d)`,`#SV` 一般随 `n` 增长(可能到 `n` 的量级,如本 demo 中 poly d=2 时 80 个样本里 76 个都是 SV)。
- 对**特征尺度极敏感**:必须标准化,否则 RBF 的 `γ` 没有可比性。

## 注意事项与常见坑

1. **`η ≥ 0` 时必须跳过该对**。核非半正定(如 sigmoid 核)、或两点几乎重合时 `Kii + Kjj − 2Kij ≈ 0`,牛顿步会炸;
   本 demo 用 `eta >= -EPS: continue` 兜住。
2. **`b` 不能只在循环里增量更新**。逐轮 `b1/b2` 会累积浮点漂移,导致收敛后非边界支持向量的 `y·f` 偏离 1
   (实测偏差可达 0.08,足以让 KKT 检查报出十几条假违规)。收尾必须按
   `max_{I_up}(y−g) ≤ b ≤ min_{I_low}(y−g)` 用**全部乘子**重新夹一次 `b`。
3. **"选违反 KKT 最严重的 + 选 |ΔE| 最大的" 这套两级启发式会卡死**。当最优的第二候选恰好被箱约束顶住
   (`H − L = 0` 或 `αj` 已在 `H` 上),该对无进展;若下几轮又反复选中同一对,循环会以
   "连续若干轮无改动" 为条件**误判收敛**。实测 80 样本时停在第 13 轮、`w` 只更新到一半、
   `y·f` 在支持向量上等于 1.38 而非 1。换成 WSS1 最大违反对后 KKT 违反数直接归零。
4. **线性可分数据上支持向量很少,但不等于没有**(本 demo 40 个样本只有 2 个)。
   稀疏的支持向量集正是 SVM 相对逻辑回归在预测端便宜的原因。
5. **`C` 不是"越大越好"**。`C` 太大 → 窄间隔、对噪声敏感、易过拟合;`C` 太小 → 欠拟合。
   本 demo 中 `C` 从 0.01 到 100,支持向量数从 64 降到 24,间隔从 3.88 收到 1.10。
6. **线性核在 XOR 上会给出 `w ≡ 0` 的退化解,这不是 bug**。XOR 的 `v_i = yi·xi` 之和恰为零向量,
   于是 `w = Σ αi·v_i = 0`,对任何 `b` 都推不出信息。这说明"线性不可分"不是"效果差一点",而是"完全无解"。
7. **必须做特征标准化**;`C` 与 `γ` 要用指数网格(`10^-3 … 10^3`)搜索,不要线性搜索。

## 参考资料

### 本轮实际读取的来源

- [scikit-learn 1.9 `1.4. Support Vector Machines`](https://scikit-learn.org/stable/modules/svm.html) — 原始/对偶问题、核函数四式(`linear`/`poly`/`rbf`/`sigmoid` 含 `γ`、`r`、`d` 的确切形式)、决策函数 `Σ yi·αi·K(xi,x)+b`、复杂度 `O(n_features·n²)~O(n_features·n³)`、"`C` 与 `γ` 需指数尺度网格搜索" 的实践建议、`alpha` 与 `C` 的等价关系说明
- [J. Platt (1998) *Sequential Minimal Optimization: A Fast Algorithm for Training Support Vector Machines*](https://www.microsoft.com/en-us/research/wp-content/uploads/1998/04/sequential-minimal-optimization.pdf) — §1.1 原始/对偶推导、间隔 `m = 2/‖w‖`、`w = Σ αi·yi·xi`、软间隔把 `αi ≥ 0` 改成箱约束、内存 `O(n)`、"稀疏数据上可比 chunking 快 1000 倍"
- [R.-E. Fan, P.-H. Chen, C.-J. Lin (2005) *Working Set Selection Using Second Order Information for Training SVMs*, JMLR 6:1889–1918](https://makerhacker.github.io/paper-mining/jmlr/jmlr2005/jmlr-2005-Working_Set_Selection_Using_Second_Order_Information_for_Training_Support_Vector_Machines.html) — WSS1 最大违反对定义与 `I_up`/`I_low`、WSS2 二阶信息规则、`a_it = Kii + Ktt − 2Kit`
- [Chen, Fan & Lin *A Study on SMO-type Decomposition Methods for Support Vector Machines*](https://www.csie.ntu.edu.tw/~cjlin/papers/generalSMO.pdf) — `∇f(α) = Qα − e`、KKT 的 `m(α) ≤ M(α)` 形式、`∇f(α)i + b·yi ≥ 0` 的分段条件
- [DPWSS (PeerJ CS 2021)](https://www.peerj.com/articles/cs-799) — 违反对定义回顾:Keerthi et al. 2001 提出最大违反对、Hush & Scovel 2003 的严格下降定理
- 一次检索式确认:[SMO 工作集选择/`I_up`,`I_low`](https://www.jmlr.org/papers/volume22/19-632/19-632.pdf)(Two-Level Decomposition Framework,JMLR 22)—— WSS1 为 `O(n)` 且全局收敛(Lin 2001/2002)

### 延伸阅读(本轮未逐篇通读)

- Cortes & Vapnik (1995) *Support-Vector Networks* — 软间隔的原始论文
- C.-C. Chang & C.-J. Lin *LIBSVM: A Library for Support Vector Machines*
- Schölkopf et al. (2000) *New Support Vector Algorithms* — ν-SVC 参数化
- Smola & Schölkopf (2004) *A Tutorial on Support Vector Regression* — ε-SVR
- Bishop《Pattern Recognition and Machine Learning》Ch. 7 *Sparse Kernel Machines*
