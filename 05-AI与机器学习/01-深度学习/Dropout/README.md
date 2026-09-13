# Dropout · 随机失活正则(Srivastava et al., JMLR 2014)

## 简介

Dropout 在训练时以概率 $p$ 随机把激活置零,迫使神经元不能依赖特定同伴(**反共适应 co-adaptation**),等价于训练指数级多个"瘦身子网络"并在推理时近似做集成平均。本 demo 从零实现**原始 2014 版**(训练不缩放、推理乘 $1-p$)与**inverted 版**(现代框架默认:训练缩放 $1/(1-p)$、推理恒等),含前向、反向与统计验证。

- **关键机制**:
  - 掩码:$r_j \sim \text{Bernoulli}(1-p)$,训练输出 $h'_j = r_j h_j$;
  - 原始版推理:$h'_{\text{test}} = (1-p) h$(补偿训练期期望缺口);
  - inverted 版训练:$h'_j = r_j h_j / (1-p)$ → $E[h'] = h$,推理恒等;
  - 反向:梯度只流过幸存单元,$dx = dout \cdot r / (1-p)$(inverted)。
- **出处**:Hinton 2012 arXiv 首创,Srivastava et al. 2014 JMLR(v15, pp.1929-1958)系统化,AlexNet 2012 的关键组件之一。

## 原理详解

### 1. 为什么缩放能对齐训练/推理分布

训练期每个单元只有 $(1-p)$ 的概率存活。inverted 版对幸存者乘 $1/(1-p)$:

$$E[m_i x_i / (1-p)] = \frac{(1-p) x_i}{(1-p)} = x_i$$

期望意义下输出与不 dropout 时一致 → 推理(`model.eval()`)直接恒等,部署代码完全不用知道 $p$ 的存在。原始 2014 论文反过来:训练原样、推理乘 $(1-p)$,缺点是推理代码被 p 侵入。

### 2. 三种生效解释(2014 论文 + 后续研究)

1. **集成**:每个掩码对应一个子网络,n 个神经元 → $2^n$ 个共享参数的子网,推理 = 近似平均;
2. **反共适应**:任何同伴都可能消失 → 每个神经元必须独立有用,学到分布式表征;
3. **近似贝叶斯推断**(Gal & Ghahramani 2016):训练带 dropout 的网络 ≈ 深度高斯过程,推理时多次前向(MC-Dropout)逼近后验预测分布。

### 3. 实测结果(本 demo 运行输出)

- p=0.4、5 万样本:保留率 0.601(目标 0.6),$E[\text{out}] - x$ 最大偏差 0.16(样本噪声量级);
- 反向梯度 vs 中心差分:相对误差 **3.6e-10**;
- 被丢弃单元梯度恒 0;eval 模式输出逐元素恒等。

## 对比

| 变体 | 训练时 | 推理时 | 出处 |
| --- | --- | --- | --- |
| 原始 dropout | 掩码不缩放 | 乘 $(1-p)$ | Srivastava 2014 |
| inverted dropout | 掩码 × 1/(1-p) | 恒等 | PyTorch/TF 默认 |
| Spatial dropout | 丢整个特征图 | — | Tompson 2015(CNN) |
| Variational dropout | 全时间步共享掩码 | — | Gal 2016(RNN) |
| DropConnect | 丢权重而非激活 | — | Wan 2013 |
| Stochastic Depth | 丢整个残差块 | — | Huang 2016(深 ResNet/ViT) |

**典型 p**:FC 隐层 0.5;conv 后 0.1-0.3;Transformer 0.1(FFN/attention 权重);大模型预训练(Llama 级)常用 0 —— 数据相对参数近乎无限时正则需求消失。

## 环境与运行

- Python 3.10+ / NumPy
- `python dropout.py`:6 组断言自测(期望守恒 / 两版对照 / 推理恒等 / 数值梯度 / 零梯度 / p=0 边界)

## 关键代码

```python
def dropout_forward(x, p, training=True, rng=RNG):
    if not training or p == 0.0:
        return x, None                      # eval: 恒等
    mask = (rng.random(x.shape) >= p).astype(x.dtype)   # 保留概率 1-p
    return x * mask / (1.0 - p), mask       # inverted: 训练期缩放

def dropout_backward(dout, mask, p):
    return dout * mask / (1.0 - p)          # 梯度只走幸存路径,同样缩放
```

## 性能边界

- 训练期每步开销:一次 `random` + 逐元素乘除,O(n) 可忽略;
- 收敛速度:等效容量下降,通常需要更多 epoch,但泛化更好(2014 论文各任务一致);
- 与 BN 叠加时统计会互相干扰(方差被 dropout 随机改变),常见做法是二者不同层使用或只用其一。

## 注意事项与常见坑

1. **忘了 `model.eval()`**:推理时 dropout 仍然生效,输出带随机噪声 —— 最常见线上事故;PyTorch `nn.Dropout` 只在 eval 模式关闭。
2. **p=1**:全部置零,inverted 会除以 0,必须特判(demo 中 p≥1 直接返回全 0)。
3. **掩码必须每个 batch 重采样**:固定掩码 = 固定剪枝,失去集成语义。
4. **反向忘带 1/(1-p)**:前向缩放了反向不缩放,学习率等效放大 $(1-p)$ 倍,梯度检查会立刻暴露。
5. **Transformer 里乱用 p=0.5**:FFN 上 0.5 会重创已调好的模型,0.1 是惯例。
6. **RNN 内部用普通 dropout**:时间步之间丢会破坏状态传递,应改 variational dropout(同掩码跨时间步)。

## 参考资料(实际阅读过的权威来源)

- [Srivastava et al. 2014, Dropout: A Simple Way to Prevent Neural Networks from Overfitting (JMLR v15)](https://jmlr.org/papers/v15/srivastava14a.html) — 原始论文(经 AI Wiki Dropout 词条全文核对:掩码定义、模型平均表述、推理缩放 (1-p))
- [AI Wiki: Dropout Regularization](https://aiwiki.ai/wiki/dropout_regularization) — 训练/推理两阶段 + inverted dropout 推导 + 引用量与历史
- [mlmentorship: Dropout](https://mlmentorship.com/concepts/dropout) — inverted 期望推导 worked example + 变体表 + 大模型时代使用现状
- [datafield.dev Ch.7 Training Deep Networks](https://datafield.dev/advanced-data-science/part-02/chapter-07) — 三种理论解释 + InvertedDropout 实现代码对照
- [AIpedia: Dropout](http://aipedia.org/dropout) — AlexNet 脉络 + 训练/推理缩放的等价性图示
- [Olly Britton, Oxford CS MT25 Notes: Dropout](https://ollybritton.com/notes/uni/part-c/mt25/computer-vision/notes/dropout/) — 牛津课程笔记:p=0.5 惯例、放置位置、co-adaptation 论证
