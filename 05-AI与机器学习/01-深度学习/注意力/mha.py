"""demo 035 — 多头自注意力 (Scaled Dot-Product + Multi-Head Self-Attention)

依据:
  - 'Attention Is All You Need' (Vaswani et al. 2017) Section 3.2
    https://arxiv.org/abs/1706.03762
    公式: Attention(Q,K,V) = softmax(QK^T / √d_k) V
          MultiHead(Q,K,V) = Concat(head_1, ..., head_h) W^O
          head_i = Attention(Q W_i^Q, K W_i^K, V W_i^V)
    基础模型: h=8, d_k=d_v=d_model/h=64 (d_model=512)

包含:
  1. Scaled Dot-Product Attention (含可选 mask)
  2. Multi-Head Self-Attention: 线性投影 + h 头并行 + 拼接 + 输出投影
  3. Causal (下三角) mask: decoder 风格
  4. 验证 attention 权重行和 = 1, 上三角被 mask 为 0
"""

import numpy as np


# ============================================================
# 1. Scaled Dot-Product Attention (公式 1)
# ============================================================
def softmax(x, axis=-1):
    x_max = x.max(axis=axis, keepdims=True)
    e = np.exp(x - x_max)
    return e / e.sum(axis=axis, keepdims=True)


def scaled_dot_product_attention(Q, K, V, mask=None):
    """Q: (..., n_q, d_k), K: (..., n_k, d_k), V: (..., n_k, d_v)
    mask: (..., n_q, n_k), True 表示该位置被 mask 掉 (设为 -∞)
    返回: (output: (..., n_q, d_v), attn_weights: (..., n_q, n_k))
    """
    d_k = Q.shape[-1]
    # scores = Q K^T / √d_k, 这是论文第 3.2.1 节的关键缩放
    scores = Q @ K.swapaxes(-2, -1) / np.sqrt(d_k)

    if mask is not None:
        scores = np.where(mask, -1e9, scores)

    attn = softmax(scores, axis=-1)
    output = attn @ V
    return output, attn


# ============================================================
# 2. Multi-Head Self-Attention (公式 2 + 3)
# ============================================================
def multi_head_attention(X, W_q, W_k, W_v, W_o, h, mask=None):
    """X: (n, d_model)
    W_q: (d_model, h * d_k), W_k 同, W_v: (d_model, h * d_v), W_o: (h * d_v, d_model)
    返回 (output: (n, d_model), attn_weights: (h, n, n))
    """
    n, d_model = X.shape
    assert d_model % h == 0, "d_model 必须能整除 h"
    d_k = d_v = d_model // h

    # 线性投影 (论文 3.2.2): "linearly project the queries, keys and values h times"
    Q = X @ W_q                              # (n, h * d_k)
    K = X @ W_k
    V = X @ W_v

    # 拆成多头: (n, h, d_k) → (h, n, d_k)
    Q = Q.reshape(n, h, d_k).transpose(1, 0, 2)
    K = K.reshape(n, h, d_k).transpose(1, 0, 2)
    V = V.reshape(n, h, d_v).transpose(1, 0, 2)

    # mask: (n, n) → (1, n, n),广播到 (h, n, n)
    if mask is not None and mask.ndim == 2:
        mask = mask[None, :, :]

    # 并行 h 个 Scaled Dot-Product
    attn_out, attn = scaled_dot_product_attention(Q, K, V, mask=mask)
    # attn_out: (h, n, d_v), attn: (h, n, n)

    # 拼接 + 输出投影 (论文 3.2.2): "These are concatenated and once again projected"
    concat = attn_out.transpose(1, 0, 2).reshape(n, h * d_v)
    output = concat @ W_o                              # (n, d_model)
    return output, attn


# ============================================================
# 3. Causal (下三角) mask — decoder 风格 (论文 3.2.3)
# ============================================================
def causal_mask(n):
    """位置 i 只能 attend 到 [0, i] (含自身)。
    返回 (n, n) bool 矩阵,True = mask 掉。"""
    return np.tril(np.ones((n, n), dtype=bool), k=0)


# ============================================================
# 4. 演示
# ============================================================
def main():
    rng = np.random.default_rng(0)

    # ============ 单头 Scaled Dot-Product ============
    print("=" * 60)
    print("Test 1: Scaled Dot-Product Attention 单头 (公式 1)")
    print("=" * 60)
    n, d_k = 4, 8
    Q = rng.normal(0, 1, (n, d_k))
    K = rng.normal(0, 1, (n, d_k))
    V = rng.normal(0, 1, (n, d_k))
    out, attn = scaled_dot_product_attention(Q, K, V)
    print(f"  Q/K/V: ({n}, {d_k})  →  output: {out.shape}, attn: {attn.shape}")
    print(f"  attn 行和 (应为 1.0): {np.round(attn.sum(axis=-1), 4)}")

    # 验证缩放原因: d_k 大时 Q K^T 方差 ≈ d_k
    Q_unit = rng.normal(0, 1, (10000, d_k))
    K_unit = rng.normal(0, 1, (10000, d_k))
    raw = (Q_unit * K_unit).sum(axis=1)
    print(f"  10000 次随机 Q K^T (Q,K~N(0,1)): var = {raw.var():.2f}  (期望 = d_k = {d_k})")
    print(f"  → 所以除以 √d_k = {np.sqrt(d_k):.2f} 才能把方差压回 ~1")

    # ============ 多头注意力 ============
    print()
    print("=" * 60)
    print("Test 2: Multi-Head Self-Attention (h=8, d_k=d_v=8, d_model=64)")
    print("=" * 60)
    n = 5
    d_model = 64
    h = 8
    d_k = d_v = d_model // h                       # = 8
    X = rng.normal(0, 1, (n, d_model))
    W_q = rng.normal(0, 0.1, (d_model, h * d_k))
    W_k = rng.normal(0, 0.1, (d_model, h * d_k))
    W_v = rng.normal(0, 0.1, (d_model, h * d_v))
    W_o = rng.normal(0, 0.1, (h * d_v, d_model))

    out_mh, attn_mh = multi_head_attention(X, W_q, W_k, W_v, W_o, h)
    print(f"  输入 X: {X.shape}  →  output: {out_mh.shape}")
    print(f"  attn weights: {attn_mh.shape}  = (h, n_q, n_k)")
    print(f"  每个 head 的 attention 行和: {np.round(attn_mh.sum(axis=-1)[0], 3)}")

    # 参数总数
    n_params = h * (d_model * d_k * 3 + d_model * d_v)
    print(f"  多头投影参数 = h × (3 × d_model × d_k + d_model × d_v)")
    print(f"                = {h} × (3 × {d_model} × {d_k} + {d_model} × {d_v})")
    print(f"                = {n_params}")
    print(f"  (与单头 d_model × d_model 全维度相比,成本近似相同,论文 3.2.2)")

    # ============ Causal mask (decoder 风格) ============
    print()
    print("=" * 60)
    print("Test 3: Causal Self-Attention (下三角 mask, 论文 3.2.3)")
    print("=" * 60)
    mask = causal_mask(n)
    out_c, attn_c = multi_head_attention(X, W_q, W_k, W_v, W_o, h, mask=mask)
    print(f"  Mask 矩阵 (True=被 mask):")
    for i in range(n):
        print(f"    {np.array2string(mask[i])}")

    print(f"\n  Head 0 的 attention 权重:")
    for i in range(n):
        row = attn_c[0, i]
        # 显示 5 行 5 列,4 位小数
        formatted = "[" + " ".join(f"{v:.3f}" for v in row) + "]"
        print(f"    pos {i}: {formatted}")

    upper = attn_c[:, np.triu_indices(n, k=1)[0], np.triu_indices(n, k=1)[1]]
    print(f"\n  上三角 attention 最大值: {upper.max():.2e}  (应 ~ 0)")
    assert upper.max() < 1e-6, "Causal mask 失效,上三角有非零权重"

    # ============ 三种应用场景 (论文 3.2.3) ============
    print()
    print("=" * 60)
    print("Test 4: 三种使用方式 (论文 3.2.3)")
    print("=" * 60)
    print("  (1) encoder-decoder attention: Q 来自 decoder,K/V 来自 encoder")
    print("  (2) encoder self-attention:    Q/K/V 都来自 encoder 上一层")
    print("  (3) decoder self-attention:    Q/K/V 都来自 decoder 上一层 + causal mask")
    print("  本 demo 的 multi_head_attention 默认是 (2)+(3) 的 self-attention 形式")
    print("  (1) 的实现只需把 X 拆成 X_q (decoder) 和 X_kv (encoder) 各算一次投影")


if __name__ == "__main__":
    main()
