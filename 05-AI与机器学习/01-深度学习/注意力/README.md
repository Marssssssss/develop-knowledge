# 多头自注意力 · Scaled Dot-Product + Multi-Head Self-Attention

## 简介

自注意力是 Transformer(BERT/GPT/LLM 的基础)的核心算子。它让序列里**任意两个位置直接交互**——不靠 RNN 的时序递归,也不靠 CNN 的局部卷积——而是用"query-key 点积 + softmax 归一化 + 加权 value 混合"的机制。本 demo 用 NumPy 实现 Vaswani et al. 2017 *Attention Is All You Need* §3.2 的两个公式:**Scaled Dot-Product Attention** 与 **Multi-Head Attention**,并验证 causal mask(decoder 风格)对 attention 权重的约束。

- **关键概念**:
  - **Query / Key / Value (Q/K/V)**:三个矩阵,来自同一输入的不同线性投影,维度都 = $d_{\rm model}$。
  - **Scaled Dot-Product**:$\text{Attention}(Q,K,V) = \text{softmax}(QK^T / \sqrt{d_k}) V$,缩放因子 $\sqrt{d_k}$ 抵消点积方差随 $d_k$ 线性增长。
  - **Multi-Head**:把 $d_{\rm model}$ 维的 Q/K/V 各自线性投影 $h$ 次,得到 $h$ 个 $d_k = d_v = d_{\rm model}/h$ 维头,**并行**做 attention 后拼接。
  - **Causal mask**:decoder 必须只看到当前位置及之前的 token。用 $-∞$ 屏蔽上三角。
- **历史背景**:Bahdanau et al. 2014 提出加性 attention 用于 RNN 翻译;Luong et al. 2015 改用点积 attention(更快)。Vaswani et al. 2017 *Attention Is All You Need* 把 attention 从 RNN/CNN 的辅助模块**提升为唯一算子**,提出 scaled dot-product + multi-head + sinusoidal 位置编码 → Transformer。BERT(GPT)的出现使其成为现代 NLP / 多模态 / 几乎所有深度学习 SOTA 的事实标准。

## 原理详解

依据 Vaswani et al. 2017 §3.2 (公式标号沿用论文)。

### 1. Scaled Dot-Product Attention(公式 1)

$$\text{Attention}(Q, K, V) = \text{softmax}\left(\frac{Q K^T}{\sqrt{d_k}}\right) V$$

数据维度:

| 张量 | 形状 | 含义 |
| --- | --- | --- |
| $Q$ | $(n, d_k)$ | $n$ 个 query,每个维度 $d_k$ |
| $K$ | $(n, d_k)$ | $n$ 个 key |
| $V$ | $(n, d_v)$ | $n$ 个 value |
| $QK^T$ | $(n, n)$ | 每对 query-key 的相似度 |
| $\text{softmax}(...)$ | $(n, n)$ | 归一化的 attention 权重(行和 = 1) |
| 输出 | $(n, d_v)$ | 加权混合后的 value |

### 2. 为什么除以 $\sqrt{d_k}$(论文 §3.2.1 脚注 1,逐字引用)

> "To illustrate why the dot products get large, assume that the components of $q$ and $k$ are independent random variables with mean 0 and variance 1. Then their dot product, $q \cdot k = \sum_{i=1}^{d_k} q_i k_i$, has mean 0 and **variance $d_k$**. To counteract this effect, we scale the dot products by $1/\sqrt{d_k}$."

简言之:当 $q, k$ 各分量独立且方差为 1 时,点积方差 = $d_k$(因为 $\text{Var}(\sum X_i) = \sum \text{Var}(X_i)$)。大方差 → softmax 进入饱和区 → 梯度极小 → 训练停滞。**除以 $\sqrt{d_k}$ 把方差压回 ~1**,softmax 进入正常工作区间。

### 3. Multi-Head Attention(公式 2 + 3)

```text
MultiHead(Q,K,V) = Concat(head_1, ..., head_h) W^O

head_i = Attention(Q W_i^Q, K W_i^K, V W_i^V)

其中投影矩阵:
  W_i^Q ∈ ℝ^{d_model × d_k}
  W_i^K ∈ ℝ^{d_model × d_k}
  W_i^V ∈ ℝ^{d_model × d_v}
  W^O  ∈ ℝ^{h·d_v × d_model}
```

**为什么多头**(论文 §3.2.2 原文):
> "Multi-head attention allows the model to jointly attend to information from different representation subspaces at different positions. With a single attention head, averaging inhibits this."

每个头学习不同的线性投影 → 不同的"子空间" → 不同类型的依赖(语法 / 语义 / 长距离)。

**基础模型参数**:$h = 8$, $d_k = d_v = d_{\rm model} / h = 64$($d_{\rm model} = 512$)。总计算成本 ≈ 单头全维度 attention,因 $h \cdot d_k = d_{\rm model}$。

### 4. 三种使用方式(论文 §3.2.3)

| 模式 | Q 来源 | K/V 来源 | Mask |
| --- | --- | --- | --- |
| encoder-decoder attention | decoder 上一层 | encoder 输出 | 无 |
| encoder self-attention | encoder 上一层 | encoder 上一层 | 无 |
| decoder self-attention | decoder 上一层 | decoder 上一层 | **causal(下三角)** |

Causal mask 把上三角设为 $-\infty$(softmax 后 = 0),保证位置 $i$ 只能看到 $0..i$。

### 5. 核心 API

- `scaled_dot_product_attention(Q, K, V, mask=None)`
  - `Q`: `(..., n_q, d_k)`,`K/V`: 同
  - `mask`: `(..., n_q, n_k)` bool,True = mask 掉
  - 返回 `(output, attn_weights)`
- `multi_head_attention(X, W_q, W_k, W_v, W_o, h, mask=None)`
  - `X`: `(n, d_model)`
  - `W_q, W_k`: `(d_model, h*d_k)`,`W_v`: `(d_model, h*d_v)`,`W_o`: `(h*d_v, d_model)`
  - 返回 `(output (n, d_model), attn_weights (h, n, n))`
- `causal_mask(n)` → `(n, n)` bool 下三角矩阵,True 位置被 mask

## 对比 / 选型

| 机制 | 复杂度 (n = 序列长度) | 长距离依赖 | 并行 | 适用 |
| --- | --- | --- | --- | --- |
| **RNN (LSTM/GRU)** | $O(n \cdot d^2)$ | $O(n)$ 路径长度 | ❌ 串行 | 短序列 / 流式 |
| **1D CNN** | $O(n \cdot k \cdot d^2)$ | $O(n/k)$ 路径长度 | ✅ | 中等序列 |
| **Scaled Dot-Product (单头)** | $O(n^2 \cdot d)$ | $O(1)$ 路径长度 | ✅ | 通用 |
| **Multi-Head (h 头)** | $O(n^2 \cdot d)$(总成本同单头) | $O(1)$ | ✅ | 通用(论文选择) |

> ⚠️ **n² 复杂度**:自注意力的核心瓶颈是 $QK^T$ 的 $O(n^2)$ 内存。$n = 2048$(GPT-2 context)→ $2048^2 \cdot d$ = 数十亿浮点数。长序列场景研究:稀疏 attention (Longformer/BigBird)、Linear attention (Performer)、State-space (Mamba/S4)、滑动窗口 + 全局 token (Mistral)。

## 环境准备

- Python 3.13+(实测 3.13.12)
- 依赖:`numpy`

## 运行方式

```bash
python mha.py
```

预期输出(关键行):

```text
Test 1: Scaled Dot-Product Attention 单头
  Q/K/V: (4, 8)  →  output: (4, 8), attn: (4, 4)
  attn 行和 (应为 1.0): [1. 1. 1. 1.]
  10000 次随机 Q K^T (Q,K~N(0,1)): var = 8.01  (期望 = d_k = 8)
  → 所以除以 √d_k = 2.83 才能把方差压回 ~1

Test 2: Multi-Head Self-Attention
  输入 X: (5, 64)  →  output: (5, 64)
  attn weights: (8, 5, 5)  = (h, n_q, n_k)

Test 3: Causal Self-Attention
  Head 0 的 attention 权重:
    pos 0: [1.000 0.000 0.000 0.000 0.000]
    pos 1: [0.480 0.520 0.000 0.000 0.000]
    pos 2: [0.310 0.395 0.295 0.000 0.000]
    ...
  上三角 attention 最大值: 0.00e+00
```

## 关键代码片段

**Scaled Dot-Product Attention**(论文公式 1):

```python
def scaled_dot_product_attention(Q, K, V, mask=None):
    d_k = Q.shape[-1]
    scores = Q @ K.swapaxes(-2, -1) / np.sqrt(d_k)   # QK^T / √d_k
    if mask is not None:
        scores = np.where(mask, -1e9, scores)        # 论文 3.2.3: -∞ mask
    attn = softmax(scores, axis=-1)                  # softmax 行归一
    output = attn @ V                                # 加权 value
    return output, attn
```

**Multi-Head Self-Attention**(论文公式 2 + 3):

```python
def multi_head_attention(X, W_q, W_k, W_v, W_o, h, mask=None):
    n, d_model = X.shape
    d_k = d_v = d_model // h

    # 线性投影 → 拆多头
    Q = (X @ W_q).reshape(n, h, d_k).transpose(1, 0, 2)   # (h, n, d_k)
    K = (X @ W_k).reshape(n, h, d_k).transpose(1, 0, 2)
    V = (X @ W_v).reshape(n, h, d_v).transpose(1, 0, 2)

    # 并行 h 头 attention
    attn_out, attn = scaled_dot_product_attention(Q, K, V, mask=mask)

    # 拼接 + 输出投影
    concat = attn_out.transpose(1, 0, 2).reshape(n, h * d_v)
    return concat @ W_o, attn
```

## 性能与边界

- **复杂度**:forward 核心是 $h$ 次 $(n, d_k) \times (n, d_k)^T$ 矩阵乘 → $O(n^2 \cdot d)$。backward 同阶(softmax + 矩阵乘)。
- **内存峰值**:attention 矩阵 $(h, n, n)$,$n = 2048, h = 32$ → 64 MB(单精度);$n = 32768$(GPT-4 早期)→ 128 GB,**显存吃紧**,需要 flash-attention 分块。
- **并行度**:每个 head / 每个 batch 样本独立,GPU 上几乎线性加速。
- **数值精度**:$d_k$ 越大,$QK^T$ 方差越大,缩放越关键;不除 $\sqrt{d_k}$ 时 softmax 输出接近 one-hot,反向梯度消失(论文 §3.2.1 末尾)。
- **形状约束**:`d_model` 必须整除 `h`,否则 reshape 失败。

## 注意事项与常见坑

1. **不除 $\sqrt{d_k}$ → softmax 饱和 → 梯度消失**:论文 §3.2.1 反复强调。HF Transformers 的 `scaled_dot_product_attention` 默认带缩放;手写时极易漏。
2. **mask 必须用 $-\infty$ 而非大负数**:用 `-1e9` 通常够,但极端 $d_k$ 下 softmax 仍可能给非零。论文推荐 `-np.inf`。
3. **多头拆分的 reshape 顺序**:`(n, h*d_k).reshape(n, h, d_k).transpose(1, 0, 2)` 是 (n, h, d_k) → (h, n, d_k)。漏 `.transpose` 会导致 head 维度错位。
4. **transpose vs reshape 的内存代价**:`transpose` 是 view(零拷贝);`reshape` 必要时才 copy。生产框架(PyTorch)transpose 后必须 `contiguous()` 才能用 `view`。
5. **Q/K/V 来源区分**:self-attention 三者都来自 `X`;cross-attention 的 K/V 来自 encoder 输出,Q 来自 decoder。本 demo 默认 self-attention,做 cross-attention 时分别传 `X_q` 和 `X_kv`。
6. **Causal mask 在 training 时是必需的**:否则训练时能看到未来 token,推理时却看不到 → "训练-推理不一致" bug。
7. **$h \cdot d_k = d_{\rm model}$ 不是巧合**:论文显式选 $d_k = d_{\rm model}/h$ 以保持总参数 / 计算成本与单头全维度等价。误用 $d_k = d_{\rm model}$(不分头)会让 $h \cdot d_k = h \cdot d_{\rm model}$ → 参数翻 $h$ 倍。
8. **position encoding 与 self-attention 解耦**:self-attention 本身**不知道序列顺序**(置换 X 后输出同等置换);位置信息由输入端的 `PE(pos, 2i) = sin(pos / 10000^{2i/d})` 提供。本 demo 不含 PE,真实 Transformer 必加。

## 参考资料(实际阅读过的权威来源)

- [Attention Is All You Need — Vaswani et al. 2017, arXiv 1706.03762 §3.2](https://arxiv.org/abs/1706.03762) — 本 demo 的直接依据:公式 1 (Scaled Dot-Product)、公式 2 (Multi-Head)、公式 3 (head_i)、§3.2.1 缩放原因(脚注 1 完整引用)、§3.2.2 多头投影矩阵定义 + h=8 d_k=d_v=64、§3.2.3 三种使用方式 + causal mask 实现
- [Attention Is All You Need — Wikiwand](https://www.wikiwand.com/en/Attention_Is_All_You_Need) — 公式可视化 + 为什么选 sinusoidal position encoding(交叉验证 +1 解释)
- [The Illustrated Transformer — Jay Alammar](https://jalammar.github.io/illustrated-transformer/) — 多头 Q/K/V 投影 + 拼接过程的图文讲解(可视化)
- [The Annotated Transformer — Harvard NLP](http://nlp.seas.harvard.edu/annotated-transformer/) — 论文逐行 PyTorch 实现,本 demo 的 NumPy 版与该 PyTorch 版逐函数对应
- [Transformer Models in NLP — UBC Wiki](https://wiki.ubc.ca/index.php?diff=prev&oldid=746069&title=Transformers) — 课程笔记形式重新组织公式,验证 §3.2.2 "Multi-head allows the model to jointly attend to different representation subspaces" 原文出处
