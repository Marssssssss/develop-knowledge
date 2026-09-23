"""向量度量空间自检：hnswlib / faiss 官方定义的可观测后果。"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metrics import (  # noqa: E402
    cosine, from_cosine, inner_product, inner_product_as_simd,
    inner_product_distance, l2, l2sqr, lcg_points, naive_accumulate,
    normalize, norm,
    rank_by_cosine, rank_by_ip, rank_by_l2, simd_route,
)

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


# ============================================ 1. L2Sqr 是平方距离
a, b = [1.0, 2.0, 3.0], [4.0, 5.0, 6.0]
ck(near(l2sqr(a, b), 27.0), "L2Sqr([1,2,3],[4,5,6]) = 27（平方）")
ck(near(l2(a, b), math.sqrt(27.0)), "开根后才是欧氏距离")
ck(near(l2sqr(a, a), 0.0), "自距离 0")

vs = lcg_points(40, 5, seed=3)
q = lcg_points(1, 5, seed=4)[0]
ck(rank_by_l2(q, vs) == [i for _, i in sorted((l2(q, v), i)
                                              for i, v in enumerate(vs))],
   "平方距离与开根距离的排序完全一致（单调变换）")

# ============================================ 2. InnerProductDistance = 1 - IP
ck(near(inner_product_distance(a, b), 1.0 - 32.0), "1 - <a,b>，a·b=32 -> -31")
ck(inner_product_distance([1.0, 0.0], [0.0, 1.0]) == 1.0, "正交 -> 距离 1")
ck(inner_product_distance([2.0, 0.0], [1.0, 0.0]) == -1.0,
   "IP 距离可以为负（<a,b>=2 > 1）")
ck(inner_product_distance([0.0, 0.0], [0.0, 0.0]) == 1.0, "零向量 -> 距离 1")

# 负距离的存在意味着它不能当「距离」用
ck(inner_product_distance([3.0], [1.0]) < 0, "IP 距离无下界")
ck(l2sqr([3.0], [1.0]) >= 0, "L2Sqr 恒非负")

# ============================================ 3. 单位向量下的恒等式
u = normalize([1.0, 2.0, 3.0])
w = normalize([3.0, 1.0, 2.0])
ck(near(norm(u), 1.0), "归一化后模长为 1")
ck(near(l2sqr(u, w), 2.0 - 2.0 * inner_product(u, w)),
   "单位向量: ||u-w||^2 = 2 - 2<u,w>")
ck(near(l2sqr(u, w), from_cosine(u, w)),
   "同样等于 2 - 2cos")
ck(near(cosine(u, w), inner_product(u, w), 1e-12),
   "单位向量下 cosine == inner product")

try:
    normalize([0.0, 0.0])
    ck(False, "零向量归一化应抛错")
except ValueError:
    ck(True, "零向量归一化抛错（cosine 分母为零）")
try:
    cosine([0.0], [1.0])
    ck(False, "零向量的 cosine 应抛错")
except ValueError:
    ck(True, "零向量的 cosine 抛错")

# ============================================ 4. 归一化后三种度量同序
pts = [normalize(v) for v in lcg_points(30, 6, seed=11)]
qs = [normalize(v) for v in lcg_points(10, 6, seed=12)]
same = True
for qq in qs:
    if not (rank_by_l2(qq, pts) == rank_by_ip(qq, pts) == rank_by_cosine(qq, pts)):
        same = False
ck(same, "单位向量下 L2 / IP / cosine 三种排序完全一致")

# 负控：不归一化时 IP 与 L2 排序不同
raw = lcg_points(30, 6, seed=11)
diff = 0
for qq in lcg_points(10, 6, seed=12):
    if rank_by_l2(qq, raw) != rank_by_ip(qq, raw):
        diff += 1
ck(diff > 0, "未归一化时 L2 与 IP 排序不同（%d/10 个查询不同）" % diff)

# 构造一个确定性的「长度主导」反例：远但同向 vs 近但反向
long_same = [10.0, 0.0]
short_opp = [-0.5, 0.0]
query = [1.0, 0.0]
ck(l2sqr(query, short_opp) < l2sqr(query, long_same), "L2 认为短反向更近")
ck(inner_product(query, long_same) > inner_product(query, short_opp),
   "IP 认为长同向更近")

# ============================================ 5. SIMD 派发由 dim 整除性决定
ck(simd_route(16) == "SIMD16Ext", "dim=16 -> SIMD16Ext")
ck(simd_route(32) == "SIMD16Ext", "dim=32 -> SIMD16Ext")
ck(simd_route(8) == "SIMD4Ext", "dim=8 -> SIMD4Ext（不是 %16，走 %4）")
ck(simd_route(12) == "SIMD4Ext", "dim=12 -> SIMD4Ext")
ck(simd_route(20) == "SIMD4Ext", "dim=20 -> SIMD4Ext（20%4==0 先命中，够不到 Residuals）")
ck(simd_route(17) == "SIMD16ExtResiduals", "dim=17 -> SIMD16ExtResiduals")
ck(simd_route(7) == "SIMD4ExtResiduals", "dim=7 -> SIMD4ExtResiduals")
ck(simd_route(5) == "SIMD4ExtResiduals", "dim=5 -> SIMD4ExtResiduals")
ck(simd_route(4) == "SIMD4Ext", "dim=4 -> SIMD4Ext（%4==0 优先）")
ck(simd_route(3) == "scalar", "dim=3 -> 标量")

big = [0.1 * i + 0.37 for i in range(64)]
big2 = [0.07 * i - 0.19 for i in range(64)]
sv = inner_product(big, big2)
ck(abs(inner_product_as_simd(big, big2, "SIMD16Ext") - sv) < 1e-9,
   "SIMD16 与标量结果在容差内一致")
ck(abs(inner_product_as_simd(big, big2, "SIMD4Ext") - sv) < 1e-9,
   "SIMD4 与标量结果在容差内一致")
# 分块求和确实走了不同的浮点路径：块状横向归约会「吞掉」小数
catastrophic = [1e16, 1.0, 1.0, 1.0, -1e16, 1.0, 1.0, 1.0]
ones = [1.0] * 8
naive_v = inner_product(catastrophic, ones)
blocked_v = inner_product_as_simd(catastrophic, ones, "SIMD4Ext")
wide_v = inner_product_as_simd(catastrophic, ones, "SIMD16Ext")
ck(near(naive_v, 3.0), "朴素累加（C++ 标量版语义）-> 3.0")
ck(near(blocked_v, 0.0), "SIMD4 分块 -> 0.0（两块各自 ±1e16，小数被吞）")
ck(near(wide_v, naive_v), "SIMD16 宽度覆盖全长 -> 与标量同序同结果（负控）")
ck(naive_v != blocked_v, "分块与标量的结果确实不同（%.1f vs %.1f）" % (naive_v, blocked_v))

# 另一个 Python 侧的坑：3.12+ 的 sum() 对 float 走补偿求和，与朴素累加不是一回事
builtin_v = sum(catastrophic)
ck(builtin_v != naive_accumulate(catastrophic),
   "Python 3.12+ 的 sum() 与朴素累加不同（%.1f vs %.1f）"
   % (builtin_v, naive_accumulate(catastrophic)))

# ============================================ 6. 三角不等式
p1, p2, p3 = [0.0, 0.0], [1.0, 0.0], [2.0, 0.0]
ck(l2(p1, p3) <= l2(p1, p2) + l2(p2, p3) + 1e-12, "L2 满足三角不等式")
# IP 距离不满足：p1·p3 距离最远，经过中间点的路径反而「更短」
ip_ok = (inner_product_distance(p1, p3)
         <= inner_product_distance(p1, p2) + inner_product_distance(p2, p3))
ck(not ip_ok, "IP 距离不满足三角不等式（负控）")

print("PASS=%d  FAIL=%d" % (PASS, len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
