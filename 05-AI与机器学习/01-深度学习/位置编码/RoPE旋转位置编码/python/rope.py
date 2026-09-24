"""RoPE 旋转位置编码 —— transformers modeling_rope_utils.py / modeling_llama.py 逐行转写。

只依赖标准库，用 list 代替 tensor，形状语义与源码一致：
  inv_freq        : [dim/2]
  position_ids    : [B][S]
  cos / sin       : [B][S][dim]   （RotaryEmbedding.forward 的输出）
  q / k           : [B][H][S][dim]（unsqueeze_dim=1 时 cos/sin 在第 1 维插轴）
"""

import math

# ---------------------------------------------------------------- inv_freq 家族


def default_inv_freq(base, dim):
    """LlamaRotaryEmbedding.compute_default_rope_parameters：base ** (arange(0,dim,2)/dim) 取倒数。"""
    return [1.0 / (base ** ((2 * i) / dim)) for i in range(dim // 2)]


def linear_scaling_inv_freq(base, dim, factor):
    """_compute_linear_scaling_rope_parameters：inv_freq /= factor（等价于缩放 position_ids）。"""
    return [v / factor for v in default_inv_freq(base, dim)]


def dynamic_ntk_base(base, dim, factor, seq_len, max_position_embeddings):
    """_compute_dynamic_ntk_parameters 里的新 base。seq_len 会被 max_position_embeddings 兜住。"""
    s = max(seq_len, max_position_embeddings)
    return base * ((factor * s / max_position_embeddings) - (factor - 1)) ** (dim / (dim - 2))


def dynamic_ntk_inv_freq(base, dim, factor, seq_len, max_position_embeddings):
    return default_inv_freq(dynamic_ntk_base(base, dim, factor, seq_len, max_position_embeddings), dim)


def get_mscale(scale, mscale=1.0):
    """YaRN 的 mscale：scale<=1 恒为 1，否则 0.1*mscale*log(scale)+1。"""
    if scale <= 1:
        return 1.0
    return 0.1 * mscale * math.log(scale) + 1.0


def find_correction_dim(num_rotations, dim, base, max_position_embeddings):
    return (dim * math.log(max_position_embeddings / (num_rotations * 2 * math.pi))) / (2 * math.log(base))


def find_correction_range(low_rot, high_rot, dim, base, max_position_embeddings, truncate):
    low = find_correction_dim(low_rot, dim, base, max_position_embeddings)
    high = find_correction_dim(high_rot, dim, base, max_position_embeddings)
    if truncate:
        low = math.floor(low)
        high = math.ceil(high)
    return max(low, 0), min(high, dim - 1)


def linear_ramp_factor(lo, hi, n):
    """源码在 min==max 时把 max 加 0.001 防奇点，再 clamp(线性函数,0,1)。"""
    if lo == hi:
        hi = hi + 0.001
    return [min(max((i - lo) / (hi - lo), 0.0), 1.0) for i in range(n)]


def yarn_inv_freq(
    base,
    dim,
    factor,
    original_max_position_embeddings,
    attention_factor=None,
    mscale=None,
    mscale_all_dim=None,
    beta_fast=32,
    beta_slow=1,
    truncate=True,
):
    """_compute_yarn_parameters：低频段外推、高频段内插，中间线性斜坡过渡。"""
    if attention_factor is None:
        if mscale and mscale_all_dim:
            attention_factor = get_mscale(factor, mscale) / get_mscale(factor, mscale_all_dim)
        else:
            attention_factor = get_mscale(factor)
    beta_fast = beta_fast or 32
    beta_slow = beta_slow or 1

    pos_freqs = [base ** ((2 * i) / dim) for i in range(dim // 2)]
    inv_freq_extrapolation = [1.0 / p for p in pos_freqs]
    inv_freq_interpolation = [1.0 / (factor * p) for p in pos_freqs]

    low, high = find_correction_range(
        beta_fast, beta_slow, dim, base, original_max_position_embeddings, truncate
    )
    ramp = linear_ramp_factor(low, high, dim // 2)
    extra_factor = [1.0 - r for r in ramp]
    inv_freq = [
        inv_freq_interpolation[i] * (1 - extra_factor[i]) + inv_freq_extrapolation[i] * extra_factor[i]
        for i in range(dim // 2)
    ]
    return inv_freq, attention_factor


def llama3_inv_freq(base, dim, factor, low_freq_factor, high_freq_factor, old_context_len):
    """_compute_llama3_parameters：按波长分三档，中频段做平滑插值（attention_factor 恒 1.0）。"""
    inv_freq = default_inv_freq(base, dim)
    low_freq_wavelen = old_context_len / low_freq_factor
    high_freq_wavelen = old_context_len / high_freq_factor
    wavelen = [2 * math.pi / v for v in inv_freq]

    inv_freq_llama = [
        (v / factor if w > low_freq_wavelen else v) for v, w in zip(inv_freq, wavelen)
    ]
    smooth = [
        (old_context_len / w - low_freq_factor) / (high_freq_factor - low_freq_factor) for w in wavelen
    ]
    smoothed = [(1 - s) * x / factor + s * x for s, x in zip(smooth, inv_freq_llama)]
    is_medium = [
        (not (w < high_freq_wavelen)) and (not (w > low_freq_wavelen)) for w in wavelen
    ]
    out = [smoothed[i] if is_medium[i] else inv_freq_llama[i] for i in range(len(inv_freq))]
    return out


def proportional_inv_freq(base, head_dim, factor=1.0, rope_proportion=1.0):
    """_compute_proportional_rope_parameters：分母用 head_dim（不是 dim），未旋转段补 0。"""
    rope_angles = int(rope_proportion * head_dim // 2)
    inv_rotated = [1.0 / (base ** ((2 * i) / head_dim)) for i in range(rope_angles)]
    nope = head_dim // 2 - rope_angles
    inv = inv_rotated + [0.0] * nope if nope > 0 else inv_rotated
    return [v / factor for v in inv]


# ---------------------------------------------------------------- cos/sin 与旋转


def rotary_emb(inv_freq, position_ids, attention_scaling=1.0):
    """RotaryEmbedding.forward：freqs = inv_freq @ pos，emb = cat(freqs, freqs)，再 cos/sin 乘 attention_scaling。"""
    cos, sin = [], []
    for batch in position_ids:
        c_row, s_row = [], []
        for p in batch:
            freqs = [f * p for f in inv_freq]
            emb = freqs + freqs
            c_row.append([math.cos(e) * attention_scaling for e in emb])
            s_row.append([math.sin(e) * attention_scaling for e in emb])
        cos.append(c_row)
        sin.append(s_row)
    return cos, sin


def rotate_half(x):
    """rotate_half：cat(-x2, x1)。奇数长度时两半不等长，但总长守恒。"""
    h = len(x) // 2
    return [-v for v in x[h:]] + list(x[:h])


def apply_rotary_pos_emb(q, k, cos, sin):
    """unsqueeze_dim=1：q/k 为 [B][H][S][D]，cos/sin 为 [B][S][D]。"""
    d = len(cos[0][0])
    qe, ke = [], []
    for b, (cb, sb) in enumerate(zip(cos, sin)):
        qb, kb = [], []
        for h in range(len(q[b])):
            qs, ks = [], []
            for s in range(len(cb)):
                rq = rotate_half(q[b][h][s])
                rk = rotate_half(k[b][h][s])
                qs.append([q[b][h][s][i] * cb[s][i] + rq[i] * sb[s][i] for i in range(d)])
                ks.append([k[b][h][s][i] * cb[s][i] + rk[i] * sb[s][i] for i in range(d)])
            qb.append(qs)
            kb.append(ks)
        qe.append(qb)
        ke.append(kb)
    return qe, ke


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def rotated_score(q, k, inv_freq, m, n, attention_scaling=1.0):
    """把 q 置于位置 m、k 置于位置 n 后的点积（单头）。"""
    cos, sin = rotary_emb(inv_freq, [[m, n]], attention_scaling)
    qv, kv = q, k
    rq = [qv[i] * cos[0][0][i] + rotate_half(qv)[i] * sin[0][0][i] for i in range(len(qv))]
    rk = [kv[i] * cos[0][1][i] + rotate_half(kv)[i] * sin[0][1][i] for i in range(len(kv))]
    return dot(rq, rk)
