# Softmax + Cross-Entropy(损失与梯度)

## 简介

交叉熵损失把模型的**未归一化 logits** 映成一个标量:正确类的负对数似然。它是分类任务的默认损失,而它之所以好写又好训,全靠一条关键化简:

```
∂ℓ/∂x = p − y          # p = softmax(x),y = one-hot(或平滑后的目标)
```

这条式子把「softmax 的 Jacobian」与「−log 的导数」完全相消,结果**只依赖预测分布与目标分布之差**,并且每个分量天然落在 `[−1, 1]`。它同时说明 CE 不会梯度爆炸(但有饱和问题)。

关键概念:

- **log-sum-exp(max-shift)**:`log Σexp(x)` 必须先减去每行最大值再求指数,否则 logit 稍大就溢出成 `inf`,loss 直接变 `nan`。
- **log_softmax 而不是 log(softmax)**:先 softmax 再取 log,概率下溢成 0 后 log 出 `−inf`;而 `x − logsumexp(x)` 永远精确。
- **reduction='mean' 的除数**:带 `weight` 时除数不是 batch size N,而是 **Σ w_{y_n}**。
- **label smoothing**:目标从 one-hot 变成 `(1−ε)·one-hot + ε/C` 的混合分布,梯度不再是 `p − y` 而是 `p − y_smooth`,且 loss 有下界 `ε·log C`。

## 原理详解

### 1. 前向:稳定版与不稳定版

```
不稳定:  loss = −log( exp(x_y) / Σ_c exp(x_c) )        # Σexp 会溢出
稳定:    m    = max_c x_c
         lse  = m + log Σ_c exp(x_c − m)                # 指数最大为 exp(0)=1
         logp = x − lse                                # = log_softmax(x)
         loss = −w_y · logp[y]
```

本目录实测:200 组 `scale ∈ 10^1~10^4` 的随机 logits 里,**朴素路线失效 80 组**(首个失效样例 `max_logit = 1062.1` → `inf`,稳定版给出 `377.26`)。

`log_softmax` 与 `log(softmax)` 的差别在极端 logits 下是定性的:logit 差 800 时,前者给出精确的 `−800.000000`,后者给出 `−inf`(float64 下 `exp(−800)` 直接是 0)。

### 2. 反向:为什么是 p − y

对单个样本,`ℓ = −Σ_c y_c log p_c`,`p = softmax(x)`。softmax 的 Jacobian 是 `∂p_i/∂x_j = p_i(δ_ij − p_j)`,代入后

```
∂ℓ/∂x_j = −Σ_c (y_c / p_c) · p_c (δ_cj − p_j) = p_j Σ_c y_c − y_j = p_j − y_j     (Σy = 1)
```

带 class weight 时就是 `w_y · (p − y)`。本目录用中心差分交叉验证,相对误差 `3.9e-10`(无 weight)/ `2.3e-10`(带 weight)。

### 3. reduction 与 class weight 的语义

```
l_n        = −w_{y_n} · log p_{n, y_n}
mean       = Σ_n l_n / Σ_n w_{y_n}        # 注意:除数是权重之和
sum        = Σ_n l_n
```

实测:C=2、`w=[1,3]`、两条样本各命中一个类,`Σ w_{y_n} = 4`(不是 `N=2`)。用 `N` 做除数会把 loss 放大一倍(`0.126928 → 0.253856`)。**这一条与常见直觉相反,配错权重时 loss 曲线会整体平移**。

### 4. label smoothing

目标变成 `y_smooth = (1−ε)·one-hot + ε/C`。对任意输入,

```
loss = (1−ε)·CE + ε·(−(1/C)Σ_c log p_c)
```

第二项是「模型分布与均匀分布的交叉熵」,其最小值为 `log C`(在 `p` 也均匀时取得),所以平滑后的 loss 有**不可约下界 `ε·log C`**(ε=0.5、C=3 时为 0.5493)。梯度仍满足 `Σ_c (p_c − y_c) = 0`(因为两边都归一),故整体尺度不受影响;但**不再指向 one-hot**,`min|g|` 有正下限(实测 `5.67e-2`),这正是平滑能抑制过自信的来源。

### 5. 平移不变与缩放饱和

- 全体 logits 加常数 `c`:`softmax` 不变 ⇒ loss 不变(实测 `Δ = 6.2e-15`)。
- 全体 logits 乘 `s`:softmax 变尖锐,梯度进入饱和区(实测 `2×4` 的随机 batch):

| 情形 | ×1 | ×10 | ×100 | 极限 |
| --- | --- | --- | --- | --- |
| 预测正确(argmax = target) | 0.868 | 0.105 | **0.000000** | 0(梯度完全消失) |
| 预测错误(argmax ≠ target) | 1.397 | 1.927 | **2.000000** | `√(2N) = 2.0`(饱和) |

即:logits 尺度一大,**正确样本不再贡献梯度,错误样本的梯度也不再反映错得有多离谱** —— 只保留一个固定模长。这就是「logits 别太大、必要时用 temperature」的定量依据。

### 6. 概率型 target 不做校验

PyTorch 允许 `target` 传概率分布(否则 `p − y` 的零和性被破坏),但**不检查**它是否在 `[0,1]` 内、是否归一。实测把合法 target 乘 10 倍,loss 精确放大 10 倍且不报错,而 `Σ(p − y) = −9.0`(正常应为 0)—— 梯度方向已经失去意义。官方文档给出的这类"误导性 loss"示例是 `4.638/2.553` 的对比,本目录用同构构造复现了同一现象。

## 对比 / 选型

| 维度 | CE(logits) | 先 softmax 再算 | BCEWithLogits(多标签) |
| --- | --- | --- | --- |
| 数值稳定 | 稳定(log-sum-exp) | 易下溢/溢出 | 稳定 |
| 梯度 | `p − y`,有界 | 含 `1/p`,无界 | `σ(x) − y`,逐元素 |
| 适用 | 单标签多分类 | 不推荐 | 多标签 / 二分类 |
| label smoothing | 内建支持 | 需自己构造 | 不适用 |

## 环境准备

- 操作系统:任意(实测 Windows 11 + Git Bash)
- 语言版本:Python 3.13 + NumPy 2.5;Go 1.21+(本机未装工具链,走人工代码审查)
- 依赖:仅 NumPy

## 运行方式

```bash
cd python && python softmax_ce.py && python softmax_ce_check.py   # 报告 + 27 项断言
cd go && go run .                                                 # 6 项断言
```

## 关键代码片段

```python
def logsumexp_stable(x):
    m = x.max(axis=-1, keepdims=True)              # 减去每行 max,指数上界 exp(0)=1
    return m + np.log(np.exp(x - m).sum(axis=-1, keepdims=True))

def ce_grad_logits(logits, targets, weight=None):  # ∂ℓ/∂x = w_y·(p − y)
    logp = logits - logsumexp_stable(logits)       # = log_softmax,永不 −inf
    p = np.exp(logp)
    y = onehot(targets)
    return weight[targets][:, None] * (p - y)
```

## 性能与边界

- 复杂度:`O(N·C)`,一次 `exp` 一遍 `sum`;是 memory-bound 的,不是算力瓶颈。
- 上溢边界:float64 的 `exp` 在 arg > 709.78 溢出,float32 在 > 88.72 溢出。logit 量级通常在 ±30 内,所以**只有 bug 才会触发** —— 这正是它容易被掩盖的原因。
- 下溢边界:`exp(−745.1)` 在 float64 下为 0;`log_softmax` 的 `x − lse` 形式把可表示范围延伸到 `±1e308` 量级。
- class weight 与 `ignore_index` 叠加时,被忽略样本既不进分子也不进分母(否则 loss 尺度随 padding 比例漂移)。

## 注意事项与常见坑

1. **`log(softmax(x))` 而不是 `log_softmax(x)`**:logit 差 800 时前者是 `−inf`,反向传播出 `nan`。
2. **mean 的除数**:带 `weight` 时是 `Σ w_{y_n}`;同时用 `weight` + `ignore_index` 时建议手算验证 loss 尺度。
3. **概率型 target 必须自己保证归一**:不归一不会报错,loss 会线性放大,`Σ(p−y) ≠ 0`。
4. **label smoothing 后 loss 不趋 0**:别把 0.5 左右的 loss 当成没收敛,下界是 `ε·log C`。
5. **平滑与 class weight 同时用时**要确认框架的叠加顺序(PyTorch 先平滑再乘 weight,本目录实现同口径)。
6. **饱和**:logits 尺度过大时正确样本梯度→0;如果发现 loss 卡住但准确率不动,先看 `‖p − y‖`。

## 参考资料(实际阅读过的权威来源)

- [torch.nn.CrossEntropyLoss — PyTorch 2.14 文档](https://pytorch.org/docs/stable/generated/torch.nn.CrossEntropyLoss.html) — `weight`/`reduction`/`label_smoothing` 的精确定义;`reduction='mean'` 的分母为 `Σ w_{y_n}·1{y_n≠ignore_index}`;`label_smoothing` 的语义("targets become a mixture of the original ground truth and a uniform distribution",引 Szegedy et al. arXiv:1512.00567);以及"PyTorch does not validate whether the values provided in target lie in [0,1]"的明确警告与误导性 loss 示例。
- [Szegedy et al., Rethinking the Inception Architecture for Computer Vision (arXiv:1512.00567)](https://arxiv.org/abs/1512.00567) — label smoothing 的出处(经 PyTorch 文档索引确认)。
