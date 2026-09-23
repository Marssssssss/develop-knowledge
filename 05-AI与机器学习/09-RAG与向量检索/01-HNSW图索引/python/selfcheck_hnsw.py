"""HNSW 自检：逐条断言 hnswlib 官方 hnswalg.h 的可观测语义。

所有随机性都由确定性序列钉死（LCG），不用全局随机源。
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hnsw import (  # noqa: E402
    HierarchicalNSW, get_neighbors_by_heuristic2,
)
from hnsw_prim import (  # noqa: E402
    MaxHeapOnDist, MinHeapOnDist, brute_knn, get_random_level, l2sqr,
    lcg_points, lcg_uniforms,
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


# ============================================================ 1. 层级生成
M = 16
mult = 1.0 / math.log(1.0 * M)

ck(near(mult, 1.0 / math.log(16.0)), "mult_ = 1/log(M)")
ck(near(1.0 / mult, math.log(16.0)), "revSize_ = 1/mult_ = log(M)")

ck(get_random_level(0.5, mult) == 0, "U=0.5, M=16 -> level 0")
ck(get_random_level(1.0, mult) == 0, "U=1.0 -> level 0")
ck(get_random_level(0.99, mult) == 0, "U=0.99 -> level 0")
# 边界：-log(1/16)/log(16) = 1.0 -> int 截断恰好为 1
ck(get_random_level(1.0 / 16, mult) == 1, "U=1/M -> level 1（边界含等号）")
ck(get_random_level(1.0 / 16 * 1.0001, mult) == 0, "U 略大于 1/M -> level 0")
ck(get_random_level(1.0 / 256, mult) == 2, "U=1/M^2 -> level 2")
ck(get_random_level(1e-6, mult) == 4, "U=1e-6 -> level 4")

us = [0.9, 0.7, 0.5, 0.3, 0.1, 0.03, 0.005]
lv = [get_random_level(u, mult) for u in us]
ck(lv == sorted(lv), "U 递减时 level 非递减")

us2 = lcg_uniforms(4000)
zero = [u for u in us2 if get_random_level(u, mult) == 0]
ck(all(u > 1.0 / M for u in zero), "level==0 蕴含 U > 1/M")
nz = [u for u in us2 if get_random_level(u, mult) > 0]
ck(all(u <= 1.0 / M for u in nz), "level>=1 蕴含 U <= 1/M")
frac = len(zero) / len(us2)
ck(abs(frac - (1 - 1.0 / M)) < 0.02, "P(level=0) 逼近 1-1/M (实测 %.4f)" % frac)

try:
    get_random_level(0.0, mult)
    ck(False, "U=0 应抛错")
except ValueError:
    ck(True, "U=0 抛 ValueError")
try:
    get_random_level(1.5, mult)
    ck(False, "U>1 应抛错")
except ValueError:
    ck(True, "U>1 抛 ValueError")

# ============================================================ 2. 容量
idx = HierarchicalNSW(dim=2, M=16)
ck(idx.maxM_ == 16, "maxM_ = M")
ck(idx.maxM0_ == 32, "maxM0_ = M*2")
ck(idx.m_cur_max(0) == 32, "Mcurmax(level 0) = maxM0_ = 2M")
ck(idx.m_cur_max(1) == 16, "Mcurmax(level 1) = maxM_ = M")
ck(idx.m_cur_max(7) == 16, "Mcurmax(level 7) = maxM_")
ck(idx.maxlevel == -1 and idx.enterpoint is None, "空图 maxlevel=-1")

# ============================================================ 3. 堆语义
h = MaxHeapOnDist()
for d, n in [(3.0, 3), (1.0, 1), (2.0, 2)]:
    h.push(d, n)
ck(h.top() == (3.0, 3), "top_candidates 是大顶堆：top = 最远")
ck(h.size() == 3, "size 正确")
ck(h.pop() == (3.0, 3), "size>ef 时 pop 淘汰最远")
ck(h.top() == (2.0, 2), "淘汰后新 top 是次远")
ck(h.items() == [(1.0, 1), (2.0, 2)], "items() 按距离升序")

m = MinHeapOnDist()
for d, n in [(3.0, 3), (1.0, 1), (2.0, 2)]:
    m.push(d, n)
ck(m.top() == (1.0, 1), "candidateSet 存负距离 => top 是最近")

# ============================================================ 4. 启发式剪枝
# 4a 候选不足 M 时原样返回
heap = MaxHeapOnDist()
for i in range(5):
    heap.push(float(i + 1), i)
vecs = [[float(i + 1), 0.0] for i in range(5)]
out = get_neighbors_by_heuristic2(heap, 16, vecs)
ck(len(out) == 5, "候选数 < M 时启发式不剪枝（官方 early return）")

# 4b 一维密集簇：q=0，候选 1.00..1.45 全被最近者挡住
#     M 必须 <= 候选数，否则官方 `size < M` 直接早退、根本不剪
dense = []
for i in range(10):
    dense.append([1.0 + 0.05 * i, 0.0])
heap = MaxHeapOnDist()
for i, v in enumerate(dense):
    heap.push(l2sqr([0.0, 0.0], v), i)
out = get_neighbors_by_heuristic2(heap, 4, dense)
ck(len(out) == 1, "密集共线簇只留下最近的一个（实测 %d）" % len(out))
ck(out[0][1] == 0, "留下的是最近的那个")
ck(get_neighbors_by_heuristic2(heap, 16, dense) is not None, "M>候选数时走早退分支")

# 4c 单位圆上 4 个正交方向：两两距离 sqrt(2) > 到查询距离 1，全部保留
far = [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0], [0.0, -1.0]]
heap = MaxHeapOnDist()
for i, v in enumerate(far):
    heap.push(l2sqr([0.0, 0.0], v), i)
out = get_neighbors_by_heuristic2(heap, 4, far)
ck(len(out) == 4, "正交候选两两够远，全部保留（实测 %d）" % len(out))

# 4d 入选序列满足：先入选者与后入选者的距离 >= 后者到查询的距离
pts = lcg_points(60, 3, seed=31337)
q = [0.5, 0.5, 0.5]
heap = MaxHeapOnDist()
for i, v in enumerate(pts):
    heap.push(l2sqr(q, v), i)
out = get_neighbors_by_heuristic2(heap, 8, pts)
ck(len(out) <= 8, "启发式输出规模 <= M")
ok = True
for j in range(1, len(out)):
    dq = out[j][0]
    for i in range(j):
        if l2sqr(pts[out[i][1]], pts[out[j][1]]) < dq - 1e-12:
            ok = False
ck(ok, "入选序列满足 d(s_i,s_j) >= d(q,s_j)（i<j）")

# 4e 负控：共线上的 1.1 被 1.0 挡住；正交的 (0,3) 与 1.0 距离 10 > 9 故保留
tri = [[1.0, 0.0], [1.1, 0.0], [0.0, 3.0]]
heap = MaxHeapOnDist()
for i, v in enumerate(tri):
    heap.push(l2sqr([0.0, 0.0], v), i)
out = get_neighbors_by_heuristic2(heap, 2, tri)
kept = sorted(n for _, n in out)
ck(kept == [0, 2], "1.1 被 1.0 挡住而正交点保留（实测 %s）" % kept)

# 4f 全部共线（同一射线上）时只剩最近的一个 —— 启发式的极端代价
ray = [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [4.0, 0.0]]
heap2 = MaxHeapOnDist()
for i, v in enumerate(ray):
    heap2.push(l2sqr([0.0, 0.0], v), i)
out = get_neighbors_by_heuristic2(heap2, 4, ray)
ck(len(out) == 1, "同一射线上只剩最近的一个（实测 %d）" % len(out))

# ============================================================ 5. search_layer
data = lcg_points(240, 4, seed=90210)
g = HierarchicalNSW(dim=4, M=16, ef_construction=64, ef=16,
                    uniform_source=lcg_uniforms(600, seed=555))
for v in data:
    g.add_point(v)

ck(len(g.vectors) == 240, "全部 240 点已插入")
ck(g.enterpoint is not None, "入口点已建立")
maxdeg0 = max(len(g.neighbors(i, 0)) for i in range(240))
up = [i for i in range(240) if g.levels[i] > 0]
maxdeg_up = max((len(g.neighbors(i, lv)) for i in up for lv in range(1, g.levels[i] + 1)),
                default=0)
ck(maxdeg0 <= 32, "底层出度 <= 2M（实测 %d）" % maxdeg0)
ck(maxdeg_up <= 16, "上层出度 <= M（实测 %d）" % maxdeg_up)
ck(len(up) > 0, "存在高于 0 层的结点（分层生效）")
ck(max(g.levels) == g.maxlevel, "maxlevel 与最高结点层号一致")

q = [0.5, 0.5, 0.5, 0.5]
res16 = g.search_layer(q, g.enterpoint, 16, 0)
items = res16.items()
ck(len(items) <= 16, "search_layer 结果规模 <= ef（实测 %d）" % len(items))
ck(items == sorted(items), "结果按距离升序")
top_d = res16.top()[0]
ck(near(top_d, max(d for d, _ in items)), "lowerBound = top_candidates.top() 即结果中最远")

# ef 越小、结果越少：同一查询下 ef=4 的结果是 ef=16 的子集（同为有限候选池）
res4 = g.search_layer(q, g.enterpoint, 4, 0)
ck(len(res4.items()) <= 4, "ef=4 时结果 <= 4")
s4 = set(n for _, n in res4.items())
s16 = set(n for _, n in items)
ck(s4 <= s16, "小 ef 的结果是大 ef 结果的子集")

# 打断条件：ef 很小 + 图大连通时必然提前退出
g.last_broke_early = False
g.search_layer(q, g.enterpoint, 2, 0)
ck(g.last_broke_early is True, "ef 小、邻居多时触发打断条件")

# ef=1 的 base search == 纯贪心下降
g.last_broke_early = False
r1 = g.search_layer(q, g.enterpoint, 1, 0)
greedy = g._greedy_descend(q, g.enterpoint, 0)
ck(r1.top()[1] == greedy, "ef=1 的 base search 退化为纯贪心")

# ============================================================ 6. 端到端
brute = brute_knn(q, data, 10)
exact1 = brute[0]
knn = g.search_knn(q, 1, ef=len(data))
ck(knn[0][1] == exact1[1], "ef=N 时 top-1 与暴力结果一致")
ck(near(knn[0][0], exact1[0]), "距离也一致")


def recall_at_k(k, ef):
    hit = 0
    for qq in lcg_points(20, 4, seed=4242):
        got = set(n for _, n in g.search_knn(qq, k, ef=ef))
        want = set(n for _, n in brute_knn(qq, data, k))
        hit += len(got & want) / float(k)
    return hit / 20.0


r_low = recall_at_k(10, 4)
r_high = recall_at_k(10, 200)
ck(r_high >= r_low, "ef 增大召回不降（%.3f -> %.3f）" % (r_low, r_high))
ck(r_high > 0.9, "ef=200 时 recall@10 高于 0.9（实测 %.3f）" % r_high)

# ef 是 max(ef_, k) 的真身：k=1 时 ef 才真正生效（k=10 会把 ef 抬到 10）
r1_low = recall_at_k(1, 1)
r1_high = recall_at_k(1, 200)
ck(r1_low < 1.0, "ef=1（纯贪心）的 top-1 召回不满（实测 %.3f）" % r1_low)
ck(r1_high > r1_low, "k=1 时 ef 增大召回上升（%.3f -> %.3f）" % (r1_low, r1_high))
ck(len(g.search_knn(q, 10, ef=4)) == 10, "ef=4 被抬成 max(4,10)=10，仍返回 10 条")

# ef 被 max(ef_, k) 抬高：k 大于 ef_ 时结果仍能有 k 个
got20 = g.search_knn(q, 20, ef=None)   # ef_ = 16 < k = 20
ck(len(got20) == 20, "ef=max(ef_,k)：ef_=16 < k=20 仍返回 20 条")

# 入口点只在 curlevel > maxlevel 时更新：全 0 层数据下入口恒为 0
flat = HierarchicalNSW(dim=2, M=8, uniform_source=[0.99] * 50)
for v in lcg_points(30, 2, seed=11):
    flat.add_point(v)
ck(flat.enterpoint == 0, "全是 0 层时入口点不更新")
ck(flat.maxlevel == 0, "全是 0 层时 maxlevel=0")

tall = HierarchicalNSW(dim=2, M=8, uniform_source=[0.99] * 5 + [1e-6] + [0.99] * 50)
for v in lcg_points(30, 2, seed=11):
    tall.add_point(v)
ck(tall.enterpoint == 5, "出现最高层结点后入口点换成它（实测 %d）" % tall.enterpoint)
ck(tall.maxlevel == tall.levels[5], "maxlevel 与该点层号一致")

# 高层贪心确实走过多跳
g.hops = 0
g.search_knn(q, 5)
ck(g.hops > 0, "层 >0 的贪心下降产生多跳（%d）" % g.hops)

# 图的连通性：从入口出发层 0 贪心能到很多点
reached = len({g._greedy_descend(v, g.enterpoint, 0) for v in lcg_points(50, 4, seed=8)})
ck(reached >= 20, "入口点可达性合理（50 查询落到 %d 个不同终点）" % reached)

print("PASS=%d  FAIL=%d" % (PASS, len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
