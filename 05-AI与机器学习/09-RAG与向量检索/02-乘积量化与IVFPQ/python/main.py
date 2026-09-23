"""IVF-PQ 演示入口：码长 / 量化误差 / nprobe-召回 / 预计算表决策。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ivfpq import IndexIVFPQ  # noqa: E402
from pq import ProductQuantizer, lcg_points  # noqa: E402


def main():
    # n 控制在 600：k-means++ 初始化是 O(k^2 * n)，纯 Python 下再大会明显变慢
    d, n = 8, 600
    data = lcg_points(n, d, seed=1234)
    qs = lcg_points(20, d, seed=99)

    print("== code_size = ceil(nbits*M/8) ==")
    for dim, M, nb in [(8, 2, 8), (12, 3, 6), (8, 4, 8), (8, 8, 4)]:
        p = ProductQuantizer(dim, M, nb)
        print("  d=%2d M=%d nbits=%2d -> dsub=%d ksub=%4d code_size=%d 压缩比 %.1fx"
              % (dim, M, nb, p.dsub, p.ksub, p.code_size, dim * 4.0 / p.code_size))
    try:
        ProductQuantizer(8, 3, 8)
    except ValueError as e:
        print("  d=8 M=3  -> 官方拒答: %s" % e)

    print("\n== 量化误差（同一份 8 维数据，M=8/nbits=8 因太慢省略） ==")
    for M, nb in [(2, 8), (4, 4), (4, 8), (8, 4)]:
        p = ProductQuantizer(d, M, nb).train(data, niter=8, seed=7)
        print("  M=%d nbits=%2d code=%dB  平均重构误差 %.6f"
              % (M, nb, p.code_size, p.reconstruction_error(data)))

    print("\n== by_residual 对编码的影响 ==")
    for br in (True, False):
        idx = IndexIVFPQ(d=d, nlist=16, M=4, nbits=8, by_residual=br)
        idx.train(data, seed=5)
        idx.add(data)
        c = idx.lists[0][0][1] if idx.lists[0] else []
        print("  by_residual=%-5s 首个倒排项 code=%s" % (br, c))

    print("\n== nprobe 与召回（nlist=16） ==")
    idx = IndexIVFPQ(d=d, nlist=16, M=4, nbits=8)
    idx.train(data, seed=5)
    idx.add(data)
    for np_ in (1, 2, 4, 8, 16, 64):
        print("  nprobe=%3d (有效 %2d)  recall@5 = %.3f"
              % (np_, idx.effective_nprobe(np_), idx.recall_at_k(qs, 5, nprobe=np_)))

    print("\n== 预计算表决策（M*ksub*nlist*4 vs 2 GiB） ==")
    for nlist in (16, 100, 1 << 16, 1 << 21):
        ix = IndexIVFPQ(d=d, nlist=nlist, M=4, nbits=8)
        t = ix.precompute_table()
        print("  nlist=%8d -> use_precomputed_table=%d  表 %d 字节"
              % (nlist, t, ix.precomputed_table_size))


if __name__ == "__main__":
    main()
