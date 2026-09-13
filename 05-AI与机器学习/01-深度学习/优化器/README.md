# 优化器 · SGD → Momentum → RMSProp → Adam(Kingma & Ba 2014)

## 简介

深度网络优化器 60 分种的演进主线:**动量**(方向记忆)× **逐参数自适应步长**(幅度记忆)。Adam = 两者之和再补一个**偏差校正**,是 2015 年后几乎所有 LLM/扩散模型的默认优化器。本 demo 从零实现全部四种,并用病态二次型实测"自适应"到底买了多少加速。

- **Adam Algorithm 1(原文逐行)**:

$$m_t = \beta_1 m_{t-1} + (1-\beta_1) g_t,\qquad v_t = \beta_2 v_{t-1} + (1-\beta_2) g_t^2$$

$$\hat{m}_t = \frac{m_t}{1-\beta_1^t},\qquad \hat{v}_t = \frac{v_t}{1-\beta_2^t},\qquad \theta_t = \theta_{t-1} - \alpha \frac{\hat{m}_t}{\sqrt{\hat{v}_t}+\epsilon}$$

- **默认超参**:$\alpha=10^{-3}$、$\beta_1=0.9$、$\beta_2=0.999$、$\epsilon=10^{-8}$;
- 出处:Kingma & Ba, ICLR 2015(arXiv:1412.6980);名字 = **Ada**ptive **M**oment estimation。

## 原理详解

### 1. 偏差校正为什么必须

$m_0 = v_0 = 0$ 时展开递推:$m_t = (1-\beta_1)\sum_{k=1}^{t}\beta_1^{t-k} g_k$。梯度分布平稳(均值 $\mu$)时 $E[m_t] = \mu(1-\beta_1^t)$ —— 缺了 $(1-\beta_1^t)$ 这块"质量"。$\beta_2=0.999$ 时该偏差持续上千步:不校正,开头几千步的步长会系统性偏小。除以 $(1-\beta^t)$ 即还原无偏估计。

### 2. 冷启动第一步的几何(本 demo 验证 [1])

常数梯度 $g$ 下 $t=1$:$\hat{m}_1 = g$、$\hat{v}_1 = g^2$,更新 $= \alpha \cdot g/(|g|+\epsilon) \approx \alpha\,\text{sign}(g)$ —— **步长恒为 α,与梯度尺度无关**(demo 用 g∈{1e-3, 1, 1e3} 验证,三种尺度第一步位移全部相等)。这就是论文宣称的"stepsize 被 α 近似上界约束"。

### 3. 病态二次型实验(本 demo 验证 [3]/[4])

$f(x,y) = 0.002x^2 + 50y^2$(曲率比 25000:1),起点 (-8, 4),阈值 f<0.05:

| 优化器 | 步数 | 说明 |
| --- | --- | --- |
| SGD lr=0.019 | **6185** | lr 上限被最陡方向钉死(>0.02 即发散),平缓方向每步只挪 0.019×0.004×|x| |
| Momentum lr=0.03 | 397 | 动量等效放大平缓方向进度 ~1/(1-μ) 倍 |
| RMSProp lr=0.05 | 102 | 逐参数除以 √v,平缓方向步长自动放大 |
| Adam lr=0.05 | 166 | RMSProp + 动量 + 校正(冷启动多花几十步,后期更稳) |

SGD lr=0.021(仅超上限 5%)时 y 方向发散到 f>1e6 —— 病态条件下**单一全局学习率**是根本瓶颈。

### 4. 谱系

| 优化器 | 更新式 | 解决的问题 |
| --- | --- | --- |
| SGD (1951) | $\theta - \alpha g$ | — |
| Momentum (Polyak 1964) | $v \leftarrow \mu v + g$;$\theta - \alpha v$ | 梯度噪声、峡谷震荡 |
| AdaGrad (Duchi 2011) | 除以 $\sum g^2$(累积) | 稀疏梯度 |
| RMSProp (Tieleman & Hinton 2012) | 除以 EMA$(g^2)$ | AdaGrad 学习率归零 |
| Adam (2014) | EMA(g) / √EMA(g²) + 校正 | 上述全部 |
| AdamW (Loshchilov 2019) | 解耦权重衰减 | Adam+L2 泛化差 |

## 环境与运行

- Python 3.10+ / NumPy
- `python optimizers.py`:4 组自测(冷启动不变性 / 尺度不变性 / 病态对比 / 发散验证)

## 关键代码

```python
def step(self, theta, grad):                 # Adam,逐行对应论文 Algorithm 1
    self.t += 1
    self.m = self.b1 * self.m + (1 - self.b1) * grad
    self.v = self.b2 * self.v + (1 - self.b2) * grad * grad
    m_hat = self.m / (1 - self.b1 ** self.t)   # 偏差校正
    v_hat = self.v / (1 - self.b2 ** self.t)
    return theta - self.lr * m_hat / (np.sqrt(v_hat) + self.eps)
```

## 性能边界

- 内存:每参数 2 份动量(m, v)→ 3× 参数量显存,LLM 训练主要显存开销之一(AdamW + fp16/bf16 + 8bit 优化器都在压缩这份开销);
- 计算:每步每参数十几次标量运算,相对反向传播可忽略;
- 梯度尺度不变性(demo 验证 [2]:梯度 ×1000,50 步轨迹偏差 1.4e-7)—— 对 loss 缩放 / 梯度裁剪策略鲁棒。

## 注意事项与常见坑

1. **ε 在根号外**:$\hat{m}/(\sqrt{\hat{v}}+\epsilon)$,不是 $\hat{m}/\sqrt{\hat{v}+\epsilon}$(论文 Algorithm 1 原式;某些早期实现放根号内,行为在大 ε 时不同)。
2. **忘做偏差校正**:开头步长偏小,β₂ 偏差要上千步才消退,短训练任务直接吃亏。
3. **Adam ≠ 万能泛化**:某些视觉任务 SGD+momentum 泛化仍更好;Adam+L2 与 SGD 的 weight decay 不等价 → 用 AdamW(解耦)。
4. **病态问题上 SGD 的 lr 是"木桶"**:被最陡方向限制,自适应优化器的价值就在此;但接近最优点时 Adam 步长 ≈ α·sign 振荡不衰减,需要 lr 调度收尾。
5. **t=0 还是 t=1 起步**:先自增再校正(t 从 1 起),否则 $1-\beta^0 = 0$ 除零。
6. **β₂ 调低的场景**:梯度极稀疏/方差异常大(某些 GAN、embedding)时 0.999 太慢,0.99 或 0.9 更稳。

## 参考资料(实际阅读过的权威来源)

- [Kingma & Ba 2014, Adam: A Method for Stochastic Optimization (arXiv:1412.6980)](https://www.intel.com/content/dam/www/public/us/en/ai/documents/1412.6980.pdf) — **本轮 WebFetch 全文阅读**:Algorithm 1 逐行(本 demo 实现的直接依据)、默认超参原文、AdaMax 变体、与 AdaGrad/RMSProp 的关系
- [Ruder 2016, An overview of gradient descent optimization algorithms (arXiv:1609.04747)](https://arxiv.org/pdf/1609.04747.pdf) — 全谱系对照(公式 19-21 与 Adam 原文一致)+ AdaMax 推导
- [TheoremPath: Adam Optimizer](https://theorempath.com/papers/adam-optimizer) — 偏差校正的期望推导 $E[m_t]=\mu(1-\beta_1^t)$ + 历史地位(2018 起 transformer 默认)
- [artificial-intelligence-wiki: Adam Optimizer Explained](https://www.artificial-intelligence-wiki.com/deep-learning/training-optimization-techniques/adam-optimizer-explained) — β₁/β₂ 记忆窗口(约 10 步/1000 步)、O(m) 内存分析
- [DeepModel (CSDN): 通俗易懂讲透 Adam 优化器](https://blog.csdn.net/DeepModel/article/details/159939231) — 中文对照:优缺点清单("可能收敛不充分"与 demo 实测的 sign 振荡一致)
