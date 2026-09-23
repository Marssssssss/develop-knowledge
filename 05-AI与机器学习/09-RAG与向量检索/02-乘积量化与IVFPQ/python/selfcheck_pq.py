"""IVF-PQ 自检：逐条断言 faiss 官方 ProductQuantizer / IndexIVFPQ 的可观测语义。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ivfpq import (  # noqa: E402
    IndexIVFPQ, MAX_POINTS_PER_CENTROID, MIN_POINTS_PER_CENTROID,
    NITER, PRECOMPUTED_TABLE_MAX_BYTES,
)
from pq import ProductQuantizer, l2sqr, lcg_points  # noqa: E402

PASS = 0
FAIL = []


def ck(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(msg)


def near(a, b, tol=1e-9):
    return abs(a - b) <= tol


# ==================================================== 1. 派生值
pq = ProductQuantizer(8, 4, 8)
ck(pq.dsub == 2, "dsub = d/M")
ck(pq.ksub == 256, "ksub = 1 << nbits")
ck(pq.code_size == 4, "code_size = (nbits*M+7)/8 = 4")

pq2 = ProductQuantizer(12, 3, 6)
ck(pq2.dsub == 4, "dsub=4（M=3, d=12）")
ck(pq2.ksub == 64, "ksub = 2^6 = 64")
ck(pq2.code_size == 3, "code_size = ceil(6*3/8) = 3（不是 2）")

pq3 = ProductQuantizer(16, 8, 4)
ck(pq3.code_size == 4, "nbits=4,M=8 -> code_size=4")

try:
    ProductQuantizer(10, 3, 8)
    ck(False, "d 不能被 M 整除应抛错")
except ValueError as e:
    ck("multiple of" in str(e), "d%M!=0 抛错且消息提到 multiple")

try:
    ProductQuantizer(8, 4, 25)
    ck(False, "nbits>24 应抛错")
except ValueError as e:
    ck("not practical" in str(e), "nbits>24 抛 'not practical'")

try:
    ProductQuantizer(8, 0, 8)
    ck(False, "M=0 应抛错")
except ValueError as e:
    ck("M must be > 0" in str(e), "M=0 抛错")

# ==================================================== 2. 编码/打包
data = lcg_points(600, 8, seed=1234)
p = ProductQuantizer(8, 4, 8).train(data, niter=8, seed=7)
ck(len(p.centroids) == 4, "centroids 第一维 = M")
ck(all(len(c) == 256 for c in p.centroids), "每个子空间 ksub 个质心")
ck(all(len(c) == 2 for c in p.centroids[0]), "每个质心 dsub 维")
ck(len(p.centroids_sq_lengths) == 4, "centroids_sq_lengths 布局 (M, ksub)")

x = data[0]
code = p.compute_code(x)
ck(len(code) == 4, "code 长度 = M")
ck(all(0 <= c < 256 for c in code), "code 每个索引落在 [0, ksub)")

# compute_code 取的是各子空间最近质心
ok = True
for m in range(4):
    xm = x[m * 2:(m + 1) * 2]
    dsel = l2sqr(xm, p.get_centroids(m, code[m]))
    for j in range(256):
        if l2sqr(xm, p.get_centroids(m, j)) < dsel - 1e-12:
            ok = False
ck(ok, "compute_code 逐子空间取最近质心")

dec = p.decode(code)
ck(len(dec) == 8, "decode 长度 = d")
ck(all(near(dec[m * 2 + t], p.get_centroids(m, code[m])[t])
       for m in range(4) for t in range(2)), "decode = 各子空间质心拼接")

raw = p.pack_code(code)
ck(len(raw) == p.code_size, "pack_code 长度 = code_size")
ck(p.unpack_code(raw) == code, "pack/unpack 往返一致")

# ==================================================== 3. 距离表与 ADC
tbl = p.compute_distance_table(x)
ck(len(tbl) == 4 and all(len(r) == 256 for r in tbl), "距离表形状 M×ksub")
ok = True
for m in range(4):
    xm = x[m * 2:(m + 1) * 2]
    for j in range(256):
        if not near(tbl[m][j], l2sqr(xm, p.get_centroids(m, j)), 1e-12):
            ok = False
ck(ok, "dis_table(m,j) = ||x_m - c_(m,j)||^2")

# ADC 的核心恒等式：对距离表求和 == 还原后算 L2（不还原也能算）
adc = p.adc_l2(tbl, code)
ck(near(adc, l2sqr(x, p.decode(code)), 1e-12),
   "ADC 求和 == ||x - decode(code)||^2（恒等式）")
# 负控：换一个 code，两侧仍相等，说明不是巧合
other = [(c + 7) % 256 for c in code]
ck(near(p.adc_l2(tbl, other), l2sqr(x, p.decode(other)), 1e-12),
   "换 code 后恒等式仍成立")

ip = p.compute_inner_prod_table(x)
ok = True
for m in range(4):
    xm = x[m * 2:(m + 1) * 2]
    for j in range(256):
        c = p.get_centroids(m, j)
        if not near(ip[m][j], sum(a * b for a, b in zip(xm, c)), 1e-12):
            ok = False
ck(ok, "内积表 = <x_m, c_(m,j)>（不是 L2）")

asym = p.adc_ip_asymmetric(x, ip, code)
ck(near(asym, l2sqr(x, p.decode(code)), 1e-9),
   "内积 ADC 经 ||x||^2+||y||^2-2<x,y> 还原出同一个 L2")
ck(near(asym, adc, 1e-9), "两条 ADC 路线给出同一距离")

# ==================================================== 4. 量化误差
err = p.reconstruction_error(data)
ck(err > 0, "PQ 是有损的，重构误差 > 0")
# 固定 nbits 时，M 增大 = 码长变长，误差单调下降
err_m2 = ProductQuantizer(8, 2, 8).train(data, niter=8, seed=7).reconstruction_error(data)
err_m8 = ProductQuantizer(8, 8, 8).train(data, niter=8, seed=7).reconstruction_error(data)
ck(err_m2 > err > err_m8,
   "固定 nbits，M 增大误差下降（%.6f > %.6f > %.6f）" % (err_m2, err, err_m8))

# 固定 nbits 时，nbits 增大（ksub 变多）误差下降
err_n4 = ProductQuantizer(8, 4, 4).train(data, niter=8, seed=7).reconstruction_error(data)
ck(err_n4 > err, "固定 M，nbits 增大误差下降（%.6f > %.6f）" % (err_n4, err))

# 固定码长（code_size=4）时 M 与 nbits 的取舍不是单调的：
# 本 8 维数据上 M=4/nbits=8 优于 M=8/nbits=4（dsub=2 用 256 个质心已过饱和）
e_4x8 = err
e_8x4 = ProductQuantizer(8, 8, 4).train(data, niter=8, seed=7).reconstruction_error(data)
ck(e_4x8 < e_8x4,
   "同码长下 M=4/nbits=8 优于 M=8/nbits=4（%.6f < %.6f）" % (e_4x8, e_8x4))
ck(err < 0.05, "8 维 / M=4 / nbits=8 的重构误差量级合理（%.5f）" % err)

# ==================================================== 5. IVF-PQ
idx = IndexIVFPQ(d=8, nlist=16, M=4, nbits=8)
ck(idx.code_size == idx.pq.code_size, "code_size = pq.code_size")
ck(idx.by_residual is True, "by_residual 默认 True（官方 IndexIVFPQ 构造）")
ck(idx.use_precomputed_table == 0, "use_precomputed_table 初始 0")
ck(idx.scan_table_threshold == 0, "scan_table_threshold 初始 0")
ck(idx.nprobe == 1, "nprobe 默认 1")
ck(NITER == 25, "ClusteringParameters.niter = 25")
ck(MAX_POINTS_PER_CENTROID == 256, "max_points_per_centroid = 256")
ck(MIN_POINTS_PER_CENTROID == 39, "min_points_per_centroid = 39")
ck(PRECOMPUTED_TABLE_MAX_BYTES == 2147483648, "precomputed_table_max_bytes = 2 GiB")
ck(idx.train_encoder_num_vectors() == 256 * 256,
   "train_encoder_num_vectors = 256 * ksub")

idx.train(data, seed=5)
ck(len(idx.coarse) == 16, "粗量化出 nlist 个质心")
idx.add(data)
ck(sum(len(l) for l in idx.lists) == len(data), "全部向量入倒排")

# nprobe 被 min(nlist, nprobe) 钳住
ck(idx.effective_nprobe(32) == 16, "nprobe=32 > nlist=16 -> 钳到 16")
ck(idx.effective_nprobe(4) == 4, "nprobe=4 -> 4")
try:
    idx.effective_nprobe(0)
    ck(False, "nprobe=0 应抛错")
except ValueError:
    ck(True, "nprobe=0 抛错（官方 FAISS_THROW_IF_NOT(cur_nprobe > 0)）")

qs = lcg_points(20, 8, seed=99)
r1 = idx.recall_at_k(qs, 5, nprobe=1)
r4 = idx.recall_at_k(qs, 5, nprobe=4)
r16 = idx.recall_at_k(qs, 5, nprobe=16)
ck(r16 >= r4 >= r1, "nprobe 增大召回不降（%.3f / %.3f / %.3f）" % (r1, r4, r16))
ck(r16 <= 1.0, "召回不超过 1")
ck(r1 < 1.0, "nprobe=1 召回不满分（%.3f），倒排是真实剪枝" % r1)

# 压缩比
ck(near(idx.compression_ratio(), 32 / 4.0), "压缩比 = d*4 / code_size = 8.0")

# ==================================================== 6. 预计算表决策
ix = IndexIVFPQ(d=8, nlist=100, M=4, nbits=8)
ix.use_precomputed_table = 0
ck(ix.precompute_table() == 1, "L2 + by_residual 且表不大 -> type 1")
ck(ix.precomputed_table_size == 4 * 256 * 100 * 4, "表大小 = M*ksub*nlist*4 字节")

iy = IndexIVFPQ(d=8, nlist=100, M=4, nbits=8, metric="IP")
iy.use_precomputed_table = 0
ck(iy.precompute_table() == 0, "非 L2 度量 -> 不预计算")

iz = IndexIVFPQ(d=8, nlist=100, M=4, nbits=8, by_residual=False)
iz.use_precomputed_table = 0
ck(iz.precompute_table() == 0, "by_residual=False -> 不预计算")

iw = IndexIVFPQ(d=8, nlist=1 << 21, M=4, nbits=8)   # 4*256*2^21*4 = 8.6e9 > 2^31
iw.use_precomputed_table = 0
ck(iw.precompute_table() == 0, "表超过 2 GiB -> 不预计算")

iv = IndexIVFPQ(d=8, nlist=100, M=4, nbits=8)
iv.use_precomputed_table = -1
ck(iv.precompute_table() == 0, "use_precomputed_table=-1 直接关闭")

print("PASS=%d  FAIL=%d" % (PASS, len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
