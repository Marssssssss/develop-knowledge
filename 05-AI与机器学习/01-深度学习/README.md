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
| [LayerNorm/](./LayerNorm/) | LayerNorm / RMSNorm 解析反向 + 不变量 + Pre/Post-LN 梯度剖面 |
| [Softmax交叉熵/](./Softmax交叉熵/) | log-sum-exp 数值稳定 + ∂ℓ/∂x = p−y 化简 + weight/label smoothing 语义 |
| [残差连接/](./残差连接/) | 退化问题复现 + 恒等短路的梯度直通 + shortcut 消融 |
| [学习率调度/](./学习率调度/) | inverse-sqrt warmup / SGDR 余弦退火 / AdamW 解耦 / 梯度裁剪 |
| [混合精度/](./混合精度/) | FP16 可表示范围 + loss scaling + master weights + FP32 累加 |
| [位置编码/RoPE旋转位置编码/](./位置编码/RoPE旋转位置编码/) | 六种 rope_type 的 inv_freq + rotate_half + 相对位置不变性 |
| [GRU门控机制/](./GRU门控机制/) | PyTorch 四式与「与原论文不同」的 n_t + ∏z_t 长程梯度 |
| [卷积变体/](./卷积变体/) | groups 支持集探针 + 空洞等效核 + 转置 output_padding 区间 |
| [量化与校准/](./量化与校准/) | qparams 三分支 + 三类 observer + 直方图非线性搜索 |
| [FlashAttention在线Softmax/](./FlashAttention在线Softmax/) | log-sum-exp 递推 + 分块不变性 + 因果掩码支持集 |

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
| 232 | LayerNorm / RMSNorm | 统计量取自单样本特征维(batch 无关,实测 max\|Δy\|=0);LN 平移+缩放不变、RMSNorm 只缩放不变;Pre/Post-LN 梯度首尾比 3.58 vs 0.67、量级差 3.9e6 倍 | Python / Go |
| 233 | Softmax + Cross-Entropy | max-shift 使 200 组大 logits 从 80 组 inf 变全有限;∂ℓ/∂x = p−y(差分验证 3.9e-10);mean 除数是 Σw_{y_n};平滑后梯度零和且 loss ≥ ε·log C | Python / Go |
| 234 | 残差连接 / Identity Mapping | ∂ε/∂x_l = ∂ε/∂x_L·∏(1+∂F/∂x) 里的「1」:plain 首尾梯度比 93.9 vs residual 3.3;退化问题复现(plain 7.62→9.36 vs residual 4.34→1.50);shortcut 消融恒等 1.50 < 缩放 3.50 | Python / Go |
| 235 | 学习率调度与梯度裁剪 | 原文公式 3 两分支在 step=warmup 相交(峰值 6.9877e-04);SGDR 重启点 0/10/30/70 且周期倍增;AdamW 衰减强度比 1.000001 vs Adam+L2 1.098;按范数裁剪方向 cos=1、按元素偏 11.59° | Python / Go |
| 236 | 混合精度训练 | FP16 归零阈值 2^-25(20.75% 梯度静默丢失)、S≥2^18 精度饱和于 2^-11;纯 FP16 权重 20000 步纹丝不动,FP32 master 累计 2e-3;FP16 累加误差 1.06e-2 vs FP32 0 | Python / Go |
| 685 | RoPE 旋转位置编码 | inv_freq 只有 dim/2 个、emb=cat(freqs,freqs) 与 rotate_half 配套;linear/dynamic-NTK/YaRN/llama3/proportional 六种参数化;(3,11)≡(0,8)≡(30,38) 分数只依赖相对位置、旋转保范数 | Python / Go |
| 686 | GRU 门控机制 | gate_size=3H;PyTorch 的 n=tanh(Win·x+bin+r⊙(Whn·h+bhn)) 与原论文的 tanh(Win·x+bin+Whn(r⊙h)+bhn) 在 b_hn≠0 且 Whn 非对角时才分叉;r=0 且 W_hz=0 时 ∂h_T/∂h_0=∏z_t(z=0.7685 时 5 步剩 26.8%) | Python / Go |
| 687 | 卷积变体 | groups 支持集探针(depthwise 一一对应、K=2 时 in_j→out[2j]/out[2j+1]);空洞 k_eff=d(k−1)+1;转置卷积 output_padding 只能补 [0,stride−1](H_in=4,k=3,s=2 → 尺寸只能选 7/8) | Python / Go |
| 688 | 量化与校准 | affine 与 symmetric 在 ReLU 后 [0,1] 上 scale 差一倍(1/255 vs 2/255);min==max=0.3 时 scale 是 0.3/255 而非 0;直方图校准裁的是空区间不是离群点 | Python / Go |
| 689 | FlashAttention 在线 softmax | 维护 (m_i, lse_i) 而非 (m_i, l_i),收尾 acc*=exp(m_i−lse_i);m_ij 的比较基准是 lse_i;分块 128×128/8×8/3×7/24×1 与朴素实现偏差均 <1e-15;logit=5000 时朴素 exp 溢出而在线版 lse 有限 | Python / Go |

## 待研究

- [x] GRU 门控机制 / peephole 变体 → [GRU门控机制/](./GRU门控机制/)（源码只有 r/z/n 三门,**无 peephole**,不臆造）
- [x] LayerNorm / RMSNorm(Transformer 归一化)→ [LayerNorm/](./LayerNorm/)
- [x] Softmax + Cross-Entropy 梯度反传推导 → [Softmax交叉熵/](./Softmax交叉熵/)
- [ ] Word2Vec / Embedding 负采样
- [x] ResNet 残差连接与恒等映射 → [残差连接/](./残差连接/)
- [x] 量化感知训练 / PTQ 校准 → [量化与校准/](./量化与校准/)（observer 与 qparams 部分;QAT 的 fake-quant 训练循环未做）
- [ ] 知识蒸馏(soft target / KL 温度)
- [x] RoPE 旋转位置编码 → [位置编码/RoPE旋转位置编码/](./位置编码/RoPE旋转位置编码/)
- [x] 卷积变体(DWConv / 空洞卷积 / 转置卷积)→ [卷积变体/](./卷积变体/)
- [ ] KV Cache 与分页注意力(PagedAttention)
- [ ] MoE 路由与负载均衡损失
- [ ] LoRA 低秩适配的秩选择
- [ ] 梯度检查点与重计算(activation checkpointing)
- [ ] 对比学习损失(InfoNCE / 温度系数)
