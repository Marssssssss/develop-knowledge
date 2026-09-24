"""FlashAttention 在线 softmax —— Dao-AILab/flash-attention 的 flash_attn/flash_attn_triton.py 逐行转写。

核心是 _fwd_kernel 的「log-sum-exp 版在线 softmax」：
    m_ij  = max(max(qk·scale), lse_i)
    p     = exp(qk·scale − m_ij)
    l_ij  = Σ p
    acc  *= exp(m_i − m_ij)          # m_i 是**上一轮**的 max
    acc  += p @ v
    m_i   = m_ij
    lse_i = m_ij + log(exp(lse_i − m_ij) + l_ij)
    out   = acc * exp(m_i − lse_i)

形状（源码是 (batch, seqlen_q, nheads, d)，本 demo 取单 batch 单头）：
    q : [S_q][D]      k, v : [S_k][D]
"""

import math

NEG_INF = float("-inf")


def softmax_scale_for(d, scale=None):
    """_flash_attn_forward：softmax_scale = softmax_scale or 1/sqrt(d)。"""
    return scale if scale else 1.0 / math.sqrt(d)


def seqlen_q_rounded(seqlen_q):
    """_flash_attn_forward：math.ceil(seqlen_q / 128) * 128（lse/tmp 缓冲的对齐长度）。"""
    return math.ceil(seqlen_q / 128) * 128


def validate(q, k, v, d):
    """_flash_attn_forward 开头的断言：d ≤ 128。"""
    if d > 128:
        raise AssertionError("FlashAttention only support head dimensions up to 128")
    if not (len(q[0]) == len(k[0]) == len(v[0]) == d):
        raise AssertionError("All tensors must have the same head dimension")


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def naive_attention(q, k, v, causal=False, softmax_scale=None, bias=None):
    """朴素实现：整张 QK^T 落地 + 全局 softmax（作为对照组）。"""
    sq, sk, d = len(q), len(k), len(v[0])
    scale = softmax_scale_for(d, softmax_scale)
    out = []
    for i in range(sq):
        scores = []
        for j in range(sk):
            s = dot(q[i], k[j]) * scale
            if bias is not None:
                s += bias[j]
            if causal and j > i:
                s = NEG_INF
            scores.append(s)
        mx = max(scores)
        exps = [math.exp(s - mx) for s in scores]
        tot = 0.0
        for e in exps:
            tot += e
        row = [0.0] * d
        for j, e in enumerate(exps):
            w = e / tot
            for t in range(d):
                row[t] += w * v[j][t]
        out.append(row)
    return out


def flash_attention(q, k, v, causal=False, softmax_scale=None, bias=None,
                    block_m=128, block_n=128, use_lse_for_max=True, d=None):
    """_fwd_kernel 的逐块在线 softmax。返回 (out, lse, softmax_scale)。

    use_lse_for_max=False 时把 m_ij 的基准换成 m_i（源码用的是 lse_i）。
    """
    sq, sk = len(q), len(k)
    dd = d if d else len(q[0])
    validate(q, k, v, dd)
    scale = softmax_scale_for(dd, softmax_scale)
    out, lse = [], []
    for start_m in range(0, sq, block_m):
        rows = range(start_m, min(start_m + block_m, sq))
        m_i = [NEG_INF] * len(rows)
        lse_i = [NEG_INF] * len(rows)
        acc = [[0.0] * dd for _ in rows]
        end_n = sk if not causal else min((start_m + 1) * block_m, sk)
        blocks = 0
        for start_n in range(0, end_n, block_n):
            cols = list(range(start_n, min(start_n + block_n, sk)))
            blocks += 1
            for r, i in enumerate(rows):
                qk = []
                for j in cols:
                    s = dot(q[i], k[j]) * scale
                    if bias is not None:
                        s += bias[j]
                    if causal and j > i:
                        s = NEG_INF
                    qk.append(s)
                row_max = max(qk) if qk else NEG_INF
                base = lse_i[r] if use_lse_for_max else m_i[r]
                m_ij = max(row_max, base)
                if m_ij == NEG_INF:
                    # 全掩码且尚无历史（源码默认 BLOCK_M == BLOCK_N 时不会出现）
                    p = [0.0] * len(cols)
                    l_ij = 0.0
                else:
                    p = [math.exp(s - m_ij) if s != NEG_INF else 0.0 for s in qk]
                    l_ij = 0.0
                    for e in p:
                        l_ij += e
                acc_scale = math.exp(m_i[r] - m_ij) if m_ij != NEG_INF else 0.0
                for t in range(dd):
                    acc[r][t] *= acc_scale
                for jj, e in enumerate(p):
                    if e == 0.0:
                        continue
                    vj = v[cols[jj]]
                    for t in range(dd):
                        acc[r][t] += e * vj[t]
                new_l = (math.exp(lse_i[r] - m_ij) if m_ij != NEG_INF else 0.0) + l_ij
                m_i[r] = m_ij
                lse_i[r] = m_ij + math.log(new_l) if new_l > 0.0 else NEG_INF
        for r in range(len(rows)):
            o_scale = math.exp(m_i[r] - lse_i[r]) if lse_i[r] != NEG_INF else 0.0
            out.append([acc[r][t] * o_scale for t in range(dd)])
            lse.append(lse_i[r])
    return out, lse, scale


def naive_lse(q, k, causal=False, softmax_scale=None, bias=None):
    """对照用的 log-sum-exp：log Σ exp(score)。"""
    sq, sk, d = len(q), len(k), len(q[0])
    scale = softmax_scale_for(d, softmax_scale)
    res = []
    for i in range(sq):
        scores = []
        for j in range(sk):
            s = dot(q[i], k[j]) * scale
            if bias is not None:
                s += bias[j]
            if causal and j > i:
                continue
            scores.append(s)
        mx = max(scores)
        tot = 0.0
        for s in scores:
            tot += math.exp(s - mx)
        res.append(mx + math.log(tot))
    return res
