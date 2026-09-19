# 概率校准与 Brier 分数(Platt / Isotonic / Temperature)

## 一、简介

分类器输出的 `predict_proba` **不一定**是概率。scikit-learn 文档给出的"良好校准"定义是:

> among the samples to which it gave `predict_proba` value close to 0.8, approximately 80% actually belong to the positive class

本 demo 用 **Python + Go** 两套纯标准库实现,把这件事拆成可验证的命题:

1. **怎么度量校准**:Brier 分数及其 Murphy 分解 `BS = REL − RES + UNC`;
2. **为什么"Brier 更低"不等于"校准更好"**(文档明写的一句话,常被忽略);
3. **三种校准器**:Platt sigmoid、isotonic 保序回归、temperature scaling,各自的假设与代价;
4. **校准曲线(reliability diagram)**怎么读。

## 二、原理详解

### 2.1 Brier 分数与 Murphy 分解

Brier 分数 `BS = mean((p − y)²)`。Murphy(1973)把它分解为三项:

| 项 | 公式 | 含义 |
|---|---|---|
| REL(reliability/可靠性) | `(1/N)·Σ_b n_b (ō_b − p̄_b)²` | **校准误差本身**;完美校准时为 0 |
| RES(resolution/分辨力) | `(1/N)·Σ_b n_b (ō_b − ō)²` | 预测把样本分开的能力 |
| UNC(uncertainty/不确定性) | `ō(1 − ō)` | 数据固有随机性,**与模型无关** |

恒等式:`BS = REL − RES + UNC`。文档据此指出:**"A lower Brier loss does not necessarily mean a better calibrated model"**——因为 Brier 同时被三项影响,一个"过度自信但分辨力极强"的模型完全可以拿到更低的 Brier,而它的 REL 反而更差。

> 数值前提:该恒等式在**每个箱内预测值为常数**时精确成立;箱内有波动时会多出一个余项
> `Σ[δ² − 2δ(y − ō_b)]/N`(其中 `δ = p_i − p̄_b`)。自检 A1/A2 分别验证这两种情形。

### 2.2 校准曲线(reliability diagram)

把预测值分箱,x 轴取**该箱平均预测概率**,y 轴取**该箱真实正例比例**。完美校准 ⇒ 点落在 `y = x` 上(Wilks 1995)。走 sigmoid 形的典型失真:**高分段真实率低于预测(过度自信)、低分段真实率高于预测(不够自信)**。

### 2.3 Platt scaling(`method="sigmoid"`)

拟合 `p = 1/(1 + exp(A·f + B))`,`A、B` 由**无惩罚**逻辑回归在分类器输出 `f` 上最小化对数损失得到。文档指出它假设校准误差**对称**(两类输出同方差),在**高度不平衡**分类上会有问题;适合小样本、或模型本身欠自信且高低端误差相近的情形。

scikit-learn 源码 `_sigmoid_calibration` 的三处工程细节(本 demo 逐一复现并断言):

- 目标值做平滑:`T[y>0] = (N_pos+1)/(N_pos+2)`,`T[y≤0] = 1/(N_neg+2)`(避免目标取到 0/1);
- 初值 `AB0 = [0, log((N_neg+1)/(N_pos+1))]`;
- 当 `max|f| >= max_abs_prediction_threshold`(**默认 30**,源码注释说明它由 `logit(np.finfo(np.float64).eps) ≈ −36` 近似而来)时,把 `F` 整体除以该最大值再拟合——无惩罚逻辑回归的缩放不变性保证结果不变,只是让优化器好收敛。

### 2.4 Isotonic regression(`method="isotonic"`)

非参数保序回归,最小化 `Σ(y_i − f̂_i)²`,唯一约束是 `f_i ≥ f_j ⇒ f̂_i ≥ f̂_j`,输出**阶跃非降函数**(PAVA 算法:违反单调就合并相邻块并取加权均值)。

- 比 sigmoid 更通用:能纠正**任何单调失真**;
- 更容易**过拟合**,文档建议样本 "greater than ~ 1000 samples" 才用它;
- **会引入并列(ties)**,因此可能改变 ROC-AUC;若必须保持排序与 AUC,用严格单调的 `sigmoid`。

### 2.5 Temperature scaling(`method="temperature"`)

`p = softmax(z / T)`,`T` 在留出(校准)集上最小化 `log_loss` 学出。文档要点:`T` **不改变 softmax 最大值的位置**,因此**不改变 accuracy**;且多分类下只需 **1 个自由参数**,天然优于 One-vs-Rest 的逐类校准。

### 2.6 通用前提

校准器必须拟合在**与训练该分类器无关**的数据上(交叉验证或独立留出集),否则会把概率推向 0/1(文档:「should never be fit on the training data」一类表述)。

## 三、对比

| 方法 | 形式 | 参数量 | 保序/保 AUC | 短板 |
|---|---|---|---|---|
| sigmoid (Platt) | `σ(A·f+B)` | 2 | **严格单调 ⇒ AUC 不变** | 假设误差对称,不平衡数据吃亏 |
| isotonic | 阶跃非降 | 数据决定 | **产生并列 ⇒ 可能改 AUC** | 小样本过拟合(< ~1000 慎用) |
| temperature | `softmax(z/T)` | **1** | 单调 ⇒ 不变 | 只扭置信度,不修非单调失真 |

## 四、环境与运行

无第三方依赖,只用标准库。

```bash
cd python && python calibration_check.py     # 26 条断言
cd go     && go run .                        # 同一组结论(Go 侧)
```

## 五、关键代码

`python/calibration.py` 中 Murphy 分解(与 Go 侧 `brierDecomposition` 同构):

```python
def brier_decomposition(y, p, n_bins=10):
    n = len(y); o_bar = sum(y) / n
    bins = {}
    for pi, yi in zip(p, y):
        bins.setdefault(min(int(pi * n_bins), n_bins - 1), []).append((pi, yi))
    rel = res = 0.0
    for members in bins.values():
        nb = len(members)
        p_bar = sum(m[0] for m in members) / nb
        o_b   = sum(m[1] for m in members) / nb
        rel += nb * (o_b - p_bar) ** 2
        res += nb * (o_b - o_bar) ** 2
    return rel / n, res / n, o_bar * (1 - o_bar)
```

`go/isotonic.go` 中 PAVA 的合并一步(块值 = 块内加权均值):

```go
n1, n2 := sizes[i], sizes[i+1]
merged := (blocks[i]*n1 + blocks[i+1]*n2) / (n1 + n2)
```

## 六、性能与边界

- **分箱是 O(n)**,但 Brier 分解的箱数决定粒度:箱太少会把失真平均掉(REL 被低估),箱太多则每箱样本过少、`ō_b` 方差大。`sklearn` 的 `calibration_curve` 默认 `n_bins=5`,`strategy` 可选 `uniform`/`quantile`。
- **AUC 是 O(n log n)**(排序 + 并列平均秩),与校准本身无关。
- **isotonic 的 PAVA 期望 O(n)**,但本 demo 用切片删除实现块合并,最坏 O(n²)——仅为可读性,n 很大时应换成栈式实现。
- **temperature scaling 的 T 搜索**用三分法假设 log loss 对 `T` 单峰;严格来说应在对数尺度上搜 `log T`。
- Platt 的目标平滑把目标压在 `[1/(N_neg+2), (N_pos+1)/(N_pos+2)]`,**样本极少时会被明显拉向中间**,这是有意为之的正则。

## 七、注意事项与常见坑

1. **别用 Brier 单独判断校准好坏**——必须看 REL,或看校准曲线。自检 C 组专门构造了反例:模型 A 的 Brier `0.22000` 低于模型 B 的 `0.24750`,但 A 的 REL `0.01000` **差于** B 的 `0.00000`,便宜全来自 RES(`0.04000` vs `0.00250`)。
2. **Murphy 恒等式不是无条件成立的**——箱内预测有波动时会有余项(A2 验证)。
3. **`T` 不改 argmax,所以别指望 temperature scaling 提升 accuracy**;它只改善概率质量。自检 F1 断言硬预测逐个相同。
4. **过度自信的模型上学出的 `T > 1`**(softmax 被拉软);反之欠自信模型会得到 `T < 1`。自检 F2 得 `T = 1.4427`。
5. **校准曲线的高分段"真实率 < 预测"只在过度自信时成立**;低分段方向相反(自检 G1 曾因此写错断言)。
6. **isotonic 的输出是阶跃的**,把它接在需要连续分数的下游(如排序)前要想清楚;本 demo E2 实测 120 个样本里出现 **118 处并列**。
7. **Go 侧两个真实编译/运行缺陷(已修,记录备查)**:`map[int]*bucket` 在键缺失时取到 nil,取字段会 panic——必须用值类型并回写;多返回值函数不能赋给单变量(`a := f()` 编译不过,要 `a, _, _ := f()`)。

## 八、参考资料(均为本 demo 实际读取)

- scikit-learn《1.16. Probability calibration》——https://scikit-learn.org/stable/modules/calibration.html
  (复核下载 72,417 字节;良好校准定义、Brier 分解与"低 Brier ≠ 校准好"、三种方法的假设与短板均出自此页)
- scikit-learn 源码 `sklearn/calibration.py`——https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/calibration.py
  (复核下载 62,711 字节;`_sigmoid_calibration` 的目标平滑、初值、缩放阈值 30 出自此文件)
- Allan H. Murphy (1973). "A New Vector Partition of the Probability Score", *Journal of Applied Meteorology and Climatology*, 12(4), 595–600.
  ——**经 scikit-learn 文档引用,本轮未实读全文**,故本文只按其引用的分解形式使用,未引用其原文章节/页码。
- Niculescu-Mizil, A. & Caruana, R. (2005). "Predicting Good Probabilities With Supervised Learning", ICML 2005.
  ——同上,经文档引用(isotonic 的 ~1000 样本经验阈值出处),**本轮未实读全文**。
