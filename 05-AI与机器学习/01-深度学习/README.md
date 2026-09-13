# 深度学习

聚焦神经网络的**原理级最小实现**:从最基础的算子(反向传播、卷积、注意力)出发,每实现一个就配一份数值梯度或形状验证,**不调框架的现成函数**。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [反向传播/](./反向传播/) | MLP 链式法则手写 + 数值梯度验证 |
| [卷积/](./卷积/) | im2col + col2im 完整 forward/backward |
| [注意力/](./注意力/) | Scaled Dot-Product + Multi-Head Self-Attention |
| [权重初始化/](./权重初始化/) | Xavier/He 方差守恒推导 + 20 层网络前向/反向方差实测 |
| [Dropout/](./Dropout/) | 原始版 + inverted 版,期望守恒与梯度验证 |
| [BatchNorm/](./BatchNorm/) | Algorithm 1/2 前向推理 + 完整解析反向 + 数值梯度 |
| [优化器/](./优化器/) | SGD → Momentum → RMSProp → Adam 全谱系 + 病态二次型对比 |
| [LSTM/](./LSTM/) | 前向 5 式 + 解析 BPTT + 长程梯度流 ∏f_t vs ∏W_hh^T |

## 已完成 demo

| ID | 知识点 | 核心机制 | 语言 |
| --- | --- | --- | --- |
| 033 | 反向传播链式法则 | d2l.ai §5.3 公式 5.3.1~14,前向缓存中间值,反向按链式法则逐节点 | Python |
| 034 | 2D 卷积 im2col | arXiv 2408.12561 Eq 3/4/5,GEMM-based convolution + col2im 累加 | Python |
| 035 | 多头自注意力 | Vaswani 2017 §3.2 公式 1/2/3,QK^T/√d_k + h 头并行 + Concat | Python |
| 137 | 权重初始化 Xavier/He | Glorot 2010 + He 2015 方差守恒,N(0,2/n) 补偿 ReLU 减半;20 层实测 Xavier+ReLU→0.5^20、He 稳定~1 | Python |
| 138 | Dropout 正则 | Srivastava 2014 JMLR,Bernoulli 掩码 + inverted 缩放 1/(1-p),期望守恒验证 | Python |
| 139 | Batch Normalization | Ioffe & Szegedy 2015 Algorithm 1/2,三路径反向 + EMA running stats | Python |
| 140 | Adam 优化器 | Kingma & Ba 2014 Algorithm 1,一阶/二阶矩 + 偏差校正(1-β^t);病态二次型 166 步 vs SGD 6185 步 | Python |
| 141 | LSTM | Hochreiter & Schmidhuber 1997,加性细胞状态,∂C_T/∂C_0 = ∏f_t;BPTT 数值梯度 <1e-6 | Python |

## 待研究

- [ ] GRU 门控机制 / peephole 变体
- [ ] LayerNorm / RMSNorm(Transformer 归一化)
- [ ] Softmax + Cross-Entropy 梯度反传推导
- [ ] Word2Vec / Embedding 负采样
- [ ] ResNet 残差连接与恒等映射
