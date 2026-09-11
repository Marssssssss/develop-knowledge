# 深度学习

聚焦神经网络的**原理级最小实现**:从最基础的算子(反向传播、卷积、注意力)出发,每实现一个就配一份数值梯度或形状验证,**不调框架的现成函数**。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [反向传播/](./反向传播/) | MLP 链式法则手写 + 数值梯度验证 |
| [卷积/](./卷积/) | im2col + col2im 完整 forward/backward |
| [注意力/](./注意力/) | Scaled Dot-Product + Multi-Head Self-Attention |

## 已完成 demo

| ID | 知识点 | 核心机制 | 语言 |
| --- | --- | --- | --- |
| 033 | 反向传播链式法则 | d2l.ai §5.3 公式 5.3.1~14,前向缓存中间值,反向按链式法则逐节点 | Python |
| 034 | 2D 卷积 im2col | arXiv 2408.12561 Eq 3/4/5,GEMM-based convolution + col2im 累加 | Python |
| 035 | 多头自注意力 | Vaswani 2017 §3.2 公式 1/2/3,QK^T/√d_k + h 头并行 + Concat | Python |

## 待研究

- [ ] RNN / LSTM / GRU 门控机制(连续时间步反向 BPTT)
- [ ] BatchNorm / LayerNorm 归一化
- [ ] Adam / SGD-momentum 优化器
- [ ] Dropout / Label Smoothing 正则化
- [ ] Softmax + Cross-Entropy 梯度反传推导
- [ ] Word2Vec / Embedding 负采样
