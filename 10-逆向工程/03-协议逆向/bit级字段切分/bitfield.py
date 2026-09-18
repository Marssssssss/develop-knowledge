#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bit 级字段切分：把「字节粒度」这一业界通病变成可断言的能力边界。

动机（两条原始出处）：
  · Tupni (CCS 2008) §3.3 原文："Currently we track input chunks at **byte
    granularity**; we believe it takes only engineering efforts to refine this
    to bit granularity."
  · BinPRE (CCS 2024, arXiv:2409.01994) §3.2 原文："It taints protocol message
    data at the **byte level**"，§3.3 的字段候选也是「字节序列」（Algorithm 1 行
    4-5："extracts the sequence of bytes ... treats them as a candidate field"）
    —— 即四年后业界 SOTA 仍是字节粒度，Tupni 那句「只需工程投入」并未兑现。

判据：相邻 bit 若「联合取值基数 < 各自基数之积」→ 统计相关 → 同属一个字段；
      否则独立 → 此处是字段边界。真值来自 RFC 1035 §4.1.1 的 DNS 头标志位。
"""

NBITS = 16


def bit(msg, i):
    """bit 0 是 16 位标志字段的最高位（网络序）。"""
    return (msg >> (NBITS - 1 - i)) & 1


def window_value(msg, idxs):
    v = 0
    for i in idxs:
        v = (v << 1) | bit(msg, i)
    return v


def cardinality(msgs, idxs):
    """该 bit 窗口在语料里观测到的**联合取值个数**。"""
    return len({window_value(m, idxs) for m in msgs})


def entropy_bits(msgs, idxs):
    from collections import Counter
    import math
    c = Counter(window_value(m, idxs) for m in msgs)
    n = len(msgs)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def varying_bits(msgs, nbits=NBITS):
    return [i for i in range(nbits) if cardinality(msgs, [i]) > 1]


def constant_runs(const, nbits=NBITS):
    """把常量位聚成连续段 [(lo, hi)]。"""
    runs, cur = [], None
    for i in range(nbits):
        if i in const:
            if cur is None:
                cur = [i, i + 1]
            else:
                cur[1] = i + 1
        elif cur is not None:
            runs.append(tuple(cur))
            cur = None
    if cur is not None:
        runs.append(tuple(cur))
    return runs


def segment(msgs, nbits=NBITS):
    """返回 (blocks, const_runs)。

    blocks 是**仅由变量位**构成的字段块；常量位**无法**由统计判据归属，
    单独以 const_runs 返回 —— 这是 bit 级分析的真实能力边界。
    """
    vb = varying_bits(msgs, nbits)
    if not vb:
        return [], constant_runs(set(range(nbits)), nbits)
    blocks, cur = [], [vb[0]]
    for b in vb[1:]:
        ca, cb = cardinality(msgs, cur), cardinality(msgs, [b])
        if cardinality(msgs, cur + [b]) < ca * cb:
            cur.append(b)                      # 相关 → 同一字段
        else:
            blocks.append(tuple(cur))          # 独立 → 边界
            cur = [b]
    blocks.append(tuple(cur))
    const = set(range(nbits)) - set(vb)
    return [(b[0], b[-1] + 1) for b in blocks], constant_runs(const, nbits)


def byte_view(msgs, nbits=NBITS):
    """字节粒度的对照视图：每 8 位一个「字段」，返回各字节的取值基数。"""
    out = []
    for s in range(0, nbits, 8):
        idxs = list(range(s, min(s + 8, nbits)))
        out.append((s // 8, cardinality(msgs, idxs)))
    return out
