"""FlashAttention 演示入口：在线 softmax 的递推、因果掩码、数值稳定性。"""

import math
import random

from flash import flash_attention, naive_attention, seqlen_q_rounded, softmax_scale_for


def main():
    rnd = random.Random(20260924)

    print("== 启动参数 ==")
    print("  softmax_scale 缺省 = 1/sqrt(d)：d=64 → %.9f" % softmax_scale_for(64))
    print("  seqlen_q_rounded = ceil(S/128)·128：S=1/128/129 → %d/%d/%d"
          % (seqlen_q_rounded(1), seqlen_q_rounded(128), seqlen_q_rounded(129)))

    print("\n== 与朴素注意力的数值偏差（S_q=24, S_k=20, d=8）==")
    q = [[rnd.gauss(0, 1) for _ in range(8)] for _ in range(24)]
    k = [[rnd.gauss(0, 1) for _ in range(8)] for _ in range(20)]
    v = [[rnd.gauss(0, 1) for _ in range(8)] for _ in range(20)]
    ref = naive_attention(q, k, v)
    for bm, bn in ((128, 128), (8, 8), (3, 7), (24, 1)):
        o, _, _ = flash_attention(q, k, v, block_m=bm, block_n=bn)
        worst = max(abs(a - b) for ra, rb in zip(o, ref) for a, b in zip(ra, rb))
        print("  分块 %3dx%-3d → 最大偏差 %.3e" % (bm, bn, worst))

    print("\n== 因果掩码：改 k[j>i] 只影响第 j 行之后 ==")
    q = [[rnd.gauss(0, 1) for _ in range(8)] for _ in range(10)]
    k = [[rnd.gauss(0, 1) for _ in range(8)] for _ in range(10)]
    v = [[rnd.gauss(0, 1) for _ in range(8)] for _ in range(10)]
    o0, _, _ = flash_attention(q, k, v, causal=True)
    k2 = [list(r) for r in k]
    for j in range(6, 10):
        for t in range(8):
            k2[j][t] += 5.0
    o1, _, _ = flash_attention(q, k2, v, causal=True)
    for i in range(10):
        d = max(abs(a - b) for a, b in zip(o0[i], o1[i]))
        print("  行 %d 偏差 %.3e %s" % (i, d, "（未变）" if d < 1e-12 else ""))

    print("\n== 在线 softmax 的递推量（S_q=S_k=6, d=8, BLOCK_N=2）==")
    q = [[rnd.gauss(0, 1) for _ in range(8)] for _ in range(6)]
    k = [[rnd.gauss(0, 1) for _ in range(8)] for _ in range(6)]
    v = [[rnd.gauss(0, 1) for _ in range(8)] for _ in range(6)]
    _o, lse, sc = flash_attention(q, k, v, block_n=2)
    for i in range(6):
        scores = [sum(a * b for a, b in zip(q[i], k[j])) * sc for j in range(6)]
        mx = max(scores)
        tot = sum(math.exp(s - mx) for s in scores)
        print("  行 %d：max=%.6f  lse=%.6f  参考 logΣexp=%.6f" % (i, mx, lse[i], mx + math.log(tot)))

    print("\n== 数值稳定性（q·k = 10000，scale=1/2 → logit 5000）==")
    qb = [[50.0] * 4]
    kb = [[50.0] * 4, [0.0] * 4]
    vb = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    ob, lseb, scb = flash_attention(qb, kb, vb)
    print("  lse = %.6f（= 5000）" % lseb[0])
    print("  out = %s（注意力全部落在 v[0]）" % ["%.6f" % x for x in ob[0]])
    try:
        print("  朴素 exp(5000) = %r" % math.exp(5000.0))
    except OverflowError as e:
        print("  朴素 exp(5000) → OverflowError: %s" % e)


if __name__ == "__main__":
    main()
