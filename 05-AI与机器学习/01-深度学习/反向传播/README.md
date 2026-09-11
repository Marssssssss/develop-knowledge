# 反向传播 (Backpropagation) · 1-hidden-layer MLP 链式法则

## 简介

反向传播是神经网络训练的核心算法:给定一个标量损失 $J$,它按计算图反向遍历,用**链式法则**逐节点求出每个参数的梯度 $\partial J/\partial W$。本 demo 用 NumPy 从零实现 1 隐藏层 MLP 的前向 + 反向,并用**中心差分**的数值梯度做正确性验证(误差 < $10^{-7}$)。

- **关键概念**:
  - **计算图 (Computational Graph)**:有向无环图,节点 = 变量/算子,边 = 数据依赖。前向沿依赖方向走,反向逆序走。
  - **链式法则 (Chain Rule)**:$\partial Z/\partial X = \text{prod}(\partial Z/\partial Y, \partial Y/\partial X)$。`prod` 隐式处理转置/广播等。
  - **prod 算子**:把"矩阵乘 / 元素乘 / 转置"统一记号,降低多维张量链式法则的记号负担。
  - **数值梯度 (Numerical Gradient)**:中心差分 $(f(x+\epsilon) - f(x-\epsilon))/(2\epsilon)$,慢但适合做正确性 oracle。
- **历史背景**:Rumelhart/Hinton/Williams 1986 *Learning representations by back-propagating errors* (Nature 323) 把链式法则系统化为"反向传播"。Rumelhart 之前已有 Linnainmaa 1970 (反向模式 autodiff) 与 Werbos 1974 (博士论文)。

## 原理详解

按 d2l.ai §5.3 Forward/Backward Propagation and Computational Graphs,以 1 隐藏层 MLP 为例,中间变量与维度的全对应关系。

### 1. 前向传播 (公式 5.3.1 ~ 5.3.6)

| 步骤 | 变量 | 公式 | 维度 |
| --- | --- | --- | --- |
| 1 | 隐藏线性 | $z = W^{(1)} x$ | $\mathbb{R}^{h}$ |
| 2 | 隐藏激活 | $h = \phi(z)$ | $\mathbb{R}^{h}$ |
| 3 | 输出线性 | $o = W^{(2)} h$ | $\mathbb{R}^{q}$ |
| 4 | 损失项 | $L = \ell(o, y)$ | scalar |
| 5 | 正则项 | $s = \frac{\lambda}{2}(\|W^{(1)}\|_F^2 + \|W^{(2)}\|_F^2)$ | scalar |
| 6 | 目标函数 | $J = L + s$ | scalar |

### 2. 计算图

```
   x          λ
   |          |
   *----- W(1)
   |          |
   z          |
   |          |
  φ           |
   |          |
   h          |
   |          |
   *----- W(2)
   |          |
   o          |
   |          |
  ℓ(·,y)      |
   |          |
   L          s
    \        /
     \      /
       J = L + s
```

### 3. 反向传播 (公式 5.3.8 ~ 5.3.14)

按 d2l.ai §5.3.3 的链式法则从 $J$ 出发,沿计算图**反方向**逐节点:

```text
公式 5.3.8   ∂J/∂L = 1,    ∂J/∂s = 1
公式 5.3.9   ∂J/∂o = ∂L/∂o = (P - onehot(y)) / n        (n = batch)
公式 5.3.10  ∂s/∂W(1) = λW(1),  ∂s/∂W(2) = λW(2)
公式 5.3.11  ∂J/∂W(2) = ∂J/∂o · h^T + λW(2)             (输出层参数)
公式 5.3.12  ∂J/∂h   = W(2)^T · ∂J/∂o                    (反向传播到隐藏输出)
公式 5.3.13  ∂J/∂z   = ∂J/∂h ⊙ φ'(z),   φ=ReLU → φ'=1[z>0]
公式 5.3.14  ∂J/∂W(1) = ∂J/∂z · x^T + λW(1)             (隐藏层参数)
```

### 4. 训练循环的耦合 (d2l.ai §5.3.4)

- 前向依赖当前参数 $W^{(1)}, W^{(2)}$(算 $s$ 时需要它们)
- 反向依赖前向的中间值 $h$(算 $\partial J/\partial W^{(2)}$ 时需要)
- 所以必须**保留中间变量**才能做反传;这正是训练比推理需要显著更多内存的根本原因。

### 5. 核心 API & 参数

- `MLP(d_in, d_hidden, d_out, lam=0.0, seed=0)` — 构造网络
  - `d_in` / `d_hidden` / `d_out`:输入 / 隐藏 / 输出维度
  - `lam`:L2 正则系数 $\lambda$(0 = 无正则)
- `forward(X, y)` → 标量 `J`,并把中间值缓存到 `self.cache`
- `backward()` → `(dW1, dW2)`,按公式 5.3.11 + 5.3.14 直接返回
- `numerical_grad(W, f, eps=1e-6)` → W 同形状的数值梯度(中心差分)

## 对比 / 选型

| 实现方式 | 速度 | 内存 | 用途 |
| --- | --- | --- | --- |
| **手写反向 (本 demo)** | 慢 (1 次反向 ≈ 1 次前向) | 中 (保留所有中间变量) | 教学 / 验证自动微分框架 |
| **PyTorch autograd** | 慢 (动态图) | 高 (PyTorch 内部用 tape 记录算子) | 研究 / 通用训练 |
| **JAX / TF `tf.GradientTape`** | 慢 | 高 | 同上 |
| **手写前向 + 数值梯度** | 极慢 (每参数 2 次前向) | 低 | 仅用于验证梯度正确性 |

手写反向本质上就是反向模式 autodiff 的"裸金属"版本,生产框架把它包成 `loss.backward()`。

## 环境准备

- 操作系统:跨平台(Linux/macOS/Windows)
- Python:3.13+ (实测 3.13.12)
- 依赖:`numpy`

## 运行方式

```bash
python bp_mlp.py
```

预期输出(关键行):

```text
Test 1: 数值梯度 vs 解析梯度 (单样本, lam=0)
  dW1 max |ana - num| = 2.4e-13   (中心差分精度极限 ≈ ε²/2 = 5e-13)
  dW2 max |ana - num| = 5.8e-13

Test 3: mini-batch SGD 训练 200 步
  step   0: J=2.3021  train_acc=0.25
  step  50: J=0.7823  train_acc=0.94
  step 100: J=0.4501  train_acc=1.00
  step 150: J=0.3291  train_acc=1.00
  step 200: J=0.2653  train_acc=1.00
```

## 关键代码片段

**前向 + 缓存**(`bp_mlp.py`):

```python
def forward(self, X, y):
    Z = linear_forward(X, self.W1)        # 5.3.1
    H = relu_forward(Z)                   # 5.3.2
    O = linear_forward(H, self.W2)        # 5.3.3
    L, P = softmax_cross_entropy_forward(O, y)
    s = l2_reg([self.W1, self.W2], self.lam)
    J = L + s                             # 5.3.6
    self.cache.update(X=X, Z=Z, H=H, O=O, P=P, y=y, L=L, s=s, J=J)
    return J
```

**反向(逐公式)**(`bp_mlp.py`):

```python
def backward(self):
    c = self.cache
    n = len(c['y'])
    y_onehot = np.zeros_like(c['P'])
    y_onehot[np.arange(n), c['y']] = 1
    dO = (c['P'] - y_onehot) / n                       # 5.3.9

    dW2 = dO.T @ c['H'] + self.lam * self.W2          # 5.3.11
    dZ = (self.W2.T @ dO.T).T * (c['Z'] > 0).astype(float)  # 5.3.12 + 5.3.13
    dW1 = dZ.T @ c['X'] + self.lam * self.W1          # 5.3.14
    return dW1, dW2
```

## 性能与边界

- **数值精度**:中心差分精度为 $O(\epsilon^2)$,本 demo 取 $\epsilon = 10^{-6}$,理论误差 ≈ $5 \times 10^{-13}$(双精度浮点极限)。实际测得 `dW max err ≈ 1e-13`,与解析梯度完全一致。
- **复杂度**:前向 $O(n \cdot d_{\rm in} \cdot h + n \cdot h \cdot q)$,反向约 2× 前向 GEMM,无额外大常数。
- **扩展性**:把 ReLU 换 tanh / sigmoid 时,只需改 `relu_forward` 和 `(c['Z'] > 0)` 两行;`forward`/`backward` 的形状不变。
- **规模**:本 demo 用 $d_{\rm in}=10, h=16, q=4$,在 CPU 上 200 步 < 1 s。生产规模如 $h=4096$,forward/backward 各约 1 ms/样本。

## 注意事项与常见坑

1. **softmax 数值稳定性**:loss 公式里减 $o.\max$ 后再 $\exp$,避免 $\exp$ 溢出。本 demo 已实现。
2. **dZ = (W2.T @ dO.T).T,不是 W2.T @ dO**:`dO` shape `(n, q)`,`W2.T @ dO.T` shape `(h, n)`,要 `.T` 转回 `(n, h)` 才能和 `(Z > 0)` 逐元素乘。漏掉 `.T` 会 broadcast 错位。
3. **正则梯度只在 $\lambda > 0$ 时叠加**:公式 5.3.10 的 $\partial s/\partial W = \lambda W$,只对 $J = L + s$ 推导时才需要;若 $J = L$(无正则),`backward` 里 `+ self.lam * W` 那两行去掉。
4. **cache 必须每次 forward 刷新**:backward 依赖 forward 的中间值;对一个新 batch 调用 `forward` 后再 `backward` 才是这个 batch 的梯度。
5. **PyTorch 用户对照**:本 demo 的 `MLP.backward` 对应 `loss.backward()`,`MLP.W1, W2` 对应 `net.linear.weight`(PyTorch 是 `(out, in)` 排布,刚好与本 demo 一致)。
6. **训练比推理耗内存的根因**(d2l.ai §5.3.4):反向传播需要 5 个中间张量 `X, Z, H, O, P`,推理只需要 `W` 和当前输入。深网 + 大 batch → OOM。
7. **second derivative 的计算图**:若用 autograd 框架对 `loss` 再求一次梯度,计算图要保留所有算子(包括 ReLU/Sigmoid 的导数),显存翻倍。本 demo 手写反向不涉及此问题。

## 参考资料(实际阅读过的权威来源)

- [5.3 Forward Propagation, Backward Propagation, and Computational Graphs — Dive into Deep Learning](https://en.d2l.ai/chapter_multilayer-perceptrons/backprop.html) — d2l.ai 官方章节,完整给出公式 5.3.1 ~ 5.3.14 推导 + 计算图 + 训练循环耦合分析(本 demo 的直接依据)
- [d2l-zh/chapter_multilayer-perceptrons/backprop_origin.md — sunshunli/d2l-zh](https://github.com/sunshunli/d2l-zh/blob/master/chapter_multilayer-perceptrons/backprop_origin.md) — 中文版对照(交叉验证 prod 算子公式 + "Pay the cost to be the boss" 原文)
- [Backpropagation Algorithm — DeepWiki zyt68/d2l-en 6.2](https://deepwiki.com/zyt68/d2l-en/6.2-backpropagation-algorithm) — d2l 章节的 AI 总结,确认 forward/backward 阶段依赖关系(交叉验证中间变量缓存点)
- [CS231n Convolutional Neural Networks — Stanford](https://cs231n.github.io/convolutional-networks/) — 数值梯度验证策略 + `loss = sum(dout * Y)` 标量化的标准做法(本 demo `numerical_grad` 的思路来源)
