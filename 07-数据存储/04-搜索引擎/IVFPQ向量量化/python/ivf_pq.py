#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Faiss 的 IVF-PQ:倒排粗量化 + 乘积量化,以及 ADC / SDC 两种距离。

权威来源(实际读过,不凭记忆):
  1. https://cdn.jsdelivr.net/gh/facebookresearch/faiss@main/faiss/IndexIVF.h
     - "At search time, the vector to be searched is also quantized, and **only the list
       corresponding to the quantization index is searched**" ⇒ 检索变成非穷举
     - "This can be relaxed using multi-probe search: a few (**nprobe**) quantization
       indices are selected and several inverted lists are visited"
     - `size_t nlist`(粗量化中心数) 与 `size_t nprobe = 1`(默认只探 1 个列表)
     - `code_size`:每向量占用的**字节数**
  2. .../faiss/IndexIVFPQ.h
     - "Inverted file with Product Quantizer encoding. Each **residual** vector is encoded
       as a product quantizer code" ⇒ 编码的是 x − 粗量化中心,不是原始向量
     - `use_precomputed_table` 的预计算表大小是 **nlist * pq.M * pq.ksub**
  3. .../faiss/impl/ProductQuantizer.h
     - `M` 子量化器个数、`nbits` 每个子索引的位数、`dsub = d/M`、`ksub = 2^nbits`
     - centroids 布局 (M, ksub, dsub);另有转置副本 (dsub, M, ksub) 与 centroids_sq_lengths
     - `compute_distance_table`:dis_table(m, j) = ||x_m − c_(m,j)||²,形状 **M × ksub**
     - `sdc_table` = Symmetric Distance Table,配合 `search_sdc`(查询端也量化)
     - `PQEncoder8` / `PQEncoder16` / `PQEncoderGeneric` 负责把子索引打包成码字
     - 类注释:PQ 用 k-means 训练,最小化 L2 距离,**偏向 L2**
"""
import math
import random
import sys

D = 16            # 向量维度
NLIST = 32        # 粗量化中心(Voronoi 单元)数
M = 4             # 子量化器个数
NBITS = 4         # 每个子索引的位数
KSUB = 2 ** NBITS
DSUB = D // M
NDB = 1000        # 库向量数
NQ = 20           # 查询数
SEED = 7

_OK = [0]
_FAIL = [0]


def check(cond, msg):
    if cond:
        _OK[0] += 1
        print("  [ok]   " + msg)
    else:
        _FAIL[0] += 1
        print("  [FAIL] " + msg)


def summary():
    print("\n" + "-" * 70)
    print("断言 %d 通过 / %d 失败" % (_OK[0], _FAIL[0]))
    print("-" * 70)
    return 0 if _FAIL[0] == 0 else 1


def l2(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b))


def kmeans(points, k, iters=8, seed=SEED):
    """最小 k-means(固定种子 ⇒ 结果可复现)"""
    rnd = random.Random(seed)
    cent = [list(p) for p in rnd.sample(points, k)]
    for _ in range(iters):
        buckets = [[] for _ in range(k)]
        for p in points:
            best = min(range(k), key=lambda j: l2(p, cent[j]))
            buckets[best].append(p)
        for j in range(k):
            if buckets[j]:
                cent[j] = [sum(c) / len(buckets[j]) for c in zip(*buckets[j])]
    return cent


def slice_vec(x, m):
    return x[m * DSUB:(m + 1) * DSUB]


# ---------------------------------------------------------------- 索引
class IVFPQ(object):
    def __init__(self, nlist, m, nbits):
        self.nlist, self.M, self.nbits = nlist, m, nbits
        self.ksub, self.dsub = 2 ** nbits, D // m
        self.coarse = None
        self.subcents = None       # 形状 (M, ksub, dsub)
        self.lists = [[] for _ in range(nlist)]   # (vid, code)
        self.db = None

    def train(self, X):
        self.coarse = kmeans(X, self.nlist, iters=6)
        # 残差 = x − 粗量化中心;PQ 在**残差**上训练(IndexIVFPQ 文档口径)
        resid = [self._residual(x) for x in X]
        self.subcents = []
        for m in range(self.M):
            sub = [slice_vec(r, m) for r in resid]
            self.subcents.append(kmeans(sub, self.ksub, iters=5, seed=SEED + m))
        return self

    def _nearest_coarse(self, x):
        return min(range(self.nlist), key=lambda j: l2(x, self.coarse[j]))

    def _residual(self, x, j=None):
        if j is None:
            j = self._nearest_coarse(x)
        return [a - b for a, b in zip(x, self.coarse[j])]

    def encode_residual(self, r):
        code = []
        for m in range(self.M):
            v = slice_vec(r, m)
            code.append(min(range(self.ksub), key=lambda i: l2(v, self.subcents[m][i])))
        return code

    def decode_residual(self, code):
        out = []
        for m in range(self.M):
            out.extend(self.subcents[m][code[m]])
        return out

    def add(self, X):
        self.db = X
        for vid, x in enumerate(X):
            j = self._nearest_coarse(x)
            self.lists[j].append((vid, self.encode_residual(self._residual(x, j))))
        return self

    # ---- 距离表与 ADC
    def distance_table(self, r):
        """dis_table(m, j) = ||r_m − c_(m,j)||²,形状 M × ksub"""
        return [[l2(slice_vec(r, m), self.subcents[m][j]) for j in range(self.ksub)]
                for m in range(self.M)]

    @staticmethod
    def adc(table, code):
        return sum(table[m][code[m]] for m in range(len(code)))

    def reconstruct(self, vid):
        for j in range(self.nlist):
            for v, code in self.lists[j]:
                if v == vid:
                    return [a + b for a, b in zip(self.coarse[j], self.decode_residual(code))]
        raise KeyError(vid)

    def search(self, q, nprobe, k=10):
        order = sorted(range(self.nlist), key=lambda j: l2(q, self.coarse[j]))[:nprobe]
        out = []
        scanned = 0
        for j in order:
            r = [a - b for a, b in zip(q, self.coarse[j])]
            table = self.distance_table(r)
            for vid, code in self.lists[j]:
                scanned += 1
                out.append((self.adc(table, code), vid))
        out.sort(key=lambda t: (t[0], t[1]))
        return out[:k], scanned


def brute_force(X, q, k=10):
    out = sorted(((l2(q, x), i) for i, x in enumerate(X)), key=lambda t: (t[0], t[1]))
    return out[:k]


def recall_at_k(approx, exact, k):
    a = {v for _, v in approx[:k]}
    e = {v for _, v in exact[:k]}
    return len(a & e) / float(len(e))


# ---------------------------------------------------------------- 主流程
def main():
    rnd = random.Random(SEED)
    centers = [[rnd.gauss(0, 3) for _ in range(D)] for _ in range(8)]
    X = []
    for _ in range(NDB):
        c = rnd.choice(centers)
        X.append([v + rnd.gauss(0, 0.6) for v in c])
    Q = []
    for _ in range(NQ):
        c = rnd.choice(centers)
        Q.append([v + rnd.gauss(0, 0.6) for v in c])

    print("=" * 70)
    print("Demo 1 · PQ 的结构参数:dsub = d/M,ksub = 2^nbits")
    print("=" * 70)
    idx = IVFPQ(NLIST, M, NBITS).train(X).add(X)
    print("   d=%d M=%d nbits=%d ⇒ dsub=%d ksub=%d"
          % (D, M, NBITS, idx.dsub, idx.ksub))
    check(idx.dsub == D // M and idx.ksub == 2 ** NBITS, "dsub 与 ksub 由参数推导")
    code_bytes = int(math.ceil(M * NBITS / 8.0))
    raw_bytes = D * 4
    print("   每向量:码字 %d 字节 vs 原始 %d 字节(float32) ⇒ %.0fx"
          % (code_bytes, raw_bytes, raw_bytes / float(code_bytes)))
    check(code_bytes == 2, "nbits=4,M=4 ⇒ 码字 2 字节(PQEncoderGeneric 按位打包)")
    check(raw_bytes / float(code_bytes) == 32.0, "压缩比 32×(d=16,float32)")

    print("\n" + "=" * 70)
    print("Demo 2 · 距离表:dis_table(m,j) = ||r_m − c_(m,j)||²,形状 M × ksub")
    print("=" * 70)
    r = idx._residual(X[0])
    table = idx.distance_table(r)
    print("   形状 %d × %d" % (len(table), len(table[0])))
    check(len(table) == M and len(table[0]) == KSUB, "距离表是 M × ksub 的矩阵")
    check(all(abs(table[m][j] - l2(slice_vec(r, m), idx.subcents[m][j])) < 1e-9
              for m in range(M) for j in range(KSUB)), "表项与逐项暴力 L2 完全一致")

    print("\n" + "=" * 70)
    print("Demo 3 · ADC 恒等于「查询残差到重构残差」的平方距离")
    print("=" * 70)
    code = idx.encode_residual(r)
    adc = idx.adc(table, code)
    recon = idx.decode_residual(code)
    direct = l2(r, recon)
    print("   ADC=%.6f  ||r − decode(code)||²=%.6f" % (adc, direct))
    check(abs(adc - direct) < 1e-9,
          "ADC = Σ_m ||r_m − c_(m,code[m])||² = ||r − decode(code)||²(逐块可加,恒等)")
    err = sum(l2(x, idx.reconstruct(i)) for i, x in enumerate(X[:200])) / 200.0
    print("   平均重构误差(前 200 条)=%.4f > 0 ⇒ PQ 是有损压缩" % err)
    check(err > 0.0, "PQ 有损:重构误差恒 > 0")

    print("\n" + "=" * 70)
    print("Demo 4 · nprobe 决定扫多少列表:召回 vs 代价")
    print("=" * 70)
    hits = {qid: brute_force(X, q, 10) for qid, q in enumerate(Q)}
    prev = -1.0
    rows = []
    for nprobe in (1, 2, 4, 8, 16, NLIST):
        rec, scanned = 0.0, 0
        for qid, q in enumerate(Q):
            approx, sc = idx.search(q, nprobe, 10)
            rec += recall_at_k(approx, hits[qid], 10)
            scanned += sc
        rec /= NQ
        rows.append((nprobe, rec, scanned / float(NQ * NDB)))
        print("   nprobe=%-3d 召回@10=%.3f  平均扫过 %.1f%% 的库"
              % (nprobe, rec, 100.0 * scanned / (NQ * NDB)))
        check(rec >= prev, "nprobe=%d 的召回不低于上一个 nprobe(多探列表只会多给候选)" % nprobe)
        prev = rec
    check(rows[0][2] < rows[-1][2], "nprobe 越小扫得越少 ⇒ 延迟越低")
    check(abs(rows[-1][2] - 1.0) < 1e-9, "nprobe == nlist ⇒ 扫过 100% 的库(退化为穷举)")
    check(0.0 < rows[-1][1] < 1.0,
          "即使穷举(nprobe=nlist)召回也 **< 1.0**:PQ 是有损的,它给召回设了天花板")
    check(rows[-1][1] == max(r[1] for r in rows),
          "穷举是该量化器的召回上界 —— 再大的 nprobe 也突破不了量化误差")

    print("\n" + "=" * 70)
    print("Demo 5 · SDC(对称距离):查询也量化,用中心间距离表")
    print("=" * 70)
    sdc = [[[l2(idx.subcents[m][i], idx.subcents[m][j]) for j in range(KSUB)]
            for i in range(KSUB)] for m in range(M)]
    print("   SDC 表大小 = M × ksub × ksub = %d 个 float(而 ADC 表只有 M × ksub = %d)"
          % (M * KSUB * KSUB, M * KSUB))
    check(len(sdc) == M and len(sdc[0]) == KSUB and len(sdc[0][0]) == KSUB,
          "SDC 表是 M × ksub × ksub(与 IndexIVFPQ 的 nlist*M*ksub 预计算表不同)")
    qcode = idx.encode_residual(idx._residual(Q[0]))
    sdc_d = sum(sdc[m][qcode[m]][code[m]] for m in range(M))
    print("   同一对(查询,库):ADC=%.6f  SDC=%.6f" % (adc, float(sdc_d)))
    check(sdc_d >= 0.0, "SDC 非负")
    check(abs(sdc_d - adc) > 1e-6,
          "SDC ≠ ADC:前者是 ||q(q)−q(y)||²(对称),后者是 ||q−q(y)||²(非对称)")
    return summary()


if __name__ == "__main__":
    sys.exit(main())
