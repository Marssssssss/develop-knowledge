"""Broad Phase 自检 —— 四条独立路径对拍 + Box2D/pbrt 常量复算。

最强的证据是**四种算法在随机数据上给出同一个候选对集合**：
朴素两两 / 均匀网格 / 动态 AABB 树 / SAH-BVH。宽相只允许"多报"（漏报就是 bug）。
"""

import random

from main import (
    AABB, union, fatten,
    B2_LINEAR_SLOP, B2_SPECULATIVE_DISTANCE, B2_MAX_AABB_MARGIN,
    B2_AABB_MARGIN_FRACTION, B2_CONTACT_RECYCLE_DISTANCE, B2_TIME_TO_SLEEP,
    PBRT_SAH_BUCKETS, PBRT_TRAVERSAL_COST,
    compute_shape_margin,
    naive_pairs, grid_pairs, DynamicTree,
    build_sah_bvh, bvh_pairs, count_nodes,
)

PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1


def close(a, b, tol=1e-9, msg=""):
    ok(abs(a - b) <= tol, "%s (got %r want %r)" % (msg, a, b))


# ---------------------------------------- 1. 常量逐条复算
close(B2_LINEAR_SLOP, 0.005, msg="linearSlop 0.5cm")
close(B2_SPECULATIVE_DISTANCE, 0.02, msg="speculative = 4*slop")
close(B2_MAX_AABB_MARGIN, 0.05, msg="maxAABB margin 5cm")
close(B2_AABB_MARGIN_FRACTION, 0.125, msg="margin fraction")
close(B2_CONTACT_RECYCLE_DISTANCE, 0.05, msg="contact recycle = 10*slop")
close(B2_TIME_TO_SLEEP, 0.5, msg="time to sleep 0.5s")
close(PBRT_SAH_BUCKETS, 12, msg="pbrt 12 个桶")
close(PBRT_TRAVERSAL_COST, 0.5, msg="pbrt 遍历代价 1/2")

# ---------------------------------------- 2. AABB 余量（shape.c 的两个分支）
# 大物体：0.125 * 0.7071 = 0.0884 > 0.05 -> 被 B2_MAX_AABB_MARGIN 夹住
big_extent = (0.5 ** 2 + 0.5 ** 2) ** 0.5      # 单位正方形质心到顶点
close(compute_shape_margin(big_extent), 0.05, msg="大物体余量被上限 0.05 夹住")
# 小物体：0.1 边长正方形 -> 0.125 * 0.0707 = 0.00884 < 0.05
small_extent = (0.05 ** 2 + 0.05 ** 2) ** 0.5
close(compute_shape_margin(small_extent), B2_AABB_MARGIN_FRACTION * small_extent,
      msg="小物体余量按比例算")
ok(compute_shape_margin(small_extent) < B2_MAX_AABB_MARGIN, "小物体走比例分支")
# 静态体走 speculativeDistance，不是 aabbMargin（注释：不能为 0，TOI 容差需要）
close(compute_shape_margin(big_extent, is_static=True), B2_SPECULATIVE_DISTANCE,
      msg="静态体用 speculativeDistance")
ok(compute_shape_margin(small_extent, is_static=True) >
   compute_shape_margin(small_extent), "小物体上静态体余量反而更大（两条分支的差异）")

# ---------------------------------------- 3. AABB 基本性质
a = AABB((0.0, 0.0), (1.0, 2.0))
close(a.perimeter(), 6.0, msg="周长 2*(1+2)")
close(a.center()[0], 0.5); close(a.center()[1], 1.0)
ok(a.overlaps(AABB((0.9, 1.9), (2.0, 3.0))), "角上相碰算重叠")
# Box2D 的 b2AABB_Overlaps 用的是闭区间 <=，因此"只贴边"**算**重叠
ok(a.overlaps(AABB((1.0, 0.0), (2.0, 1.0))), "贴边算重叠（闭区间，与 Box2D 口径一致）")
ok(not a.overlaps(AABB((1.0000001, 0.0), (2.0, 1.0))), "留一点缝隙才不重叠")
ok(a.contains(AABB((0.1, 0.1), (0.9, 1.9))), "包含")
f = fatten(a, 0.05)
close(f.perimeter(), 2.0 * (1.1 + 2.1), msg="fatten 后周长")

# ---------------------------------------- 4. 四路对拍（随机数据）
rng = random.Random(20260921)
for trial in range(6):
    n = 40
    boxes = []
    for _ in range(n):
        x = rng.uniform(0, 20)
        y = rng.uniform(0, 20)
        w = rng.uniform(0.2, 1.5)
        h = rng.uniform(0.2, 1.5)
        boxes.append(AABB((x, y), (x + w, y + h)))
    truth = naive_pairs(boxes)

    g, per_obj = grid_pairs(boxes, cell=2.0)
    t = DynamicTree()
    for i, b in enumerate(boxes):
        t.insert(fatten(b, compute_shape_margin(0.5)), proxy=i)
    tree = {(i, j) for (i, j) in t.all_pairs() if boxes[i].overlaps(boxes[j])}

    root, ordered, _ = build_sah_bvh(boxes)
    bvh = bvh_pairs(root, ordered, boxes)

    ok(truth <= g, "第 %d 组：网格不得漏报（可多报）" % trial)
    ok(truth <= tree, "第 %d 组：动态树不得漏报" % trial)
    ok(truth <= bvh, "第 %d 组：SAH-BVH 不得漏报" % trial)
    # 三者的"多报"程度都应该远小于朴素枚举的全部 C(n,2)
    total = n * (n - 1) // 2
    for name, cand in (("网格", g), ("动态树", tree), ("BVH", bvh)):
        ok(len(cand) < total, "%s 候选数应少于全枚举（%d < %d）" % (name, len(cand), total))
    # 树的不变量：内部节点必须包住两个孩子
    ok(not t.validate(), "第 %d 组：树不变量（父包住子）成立" % trial)
    ok(t.height() < n, "第 %d 组：树高小于物体数（%d）" % (trial, t.height()))

# ---------------------------------------- 5. 网格：跨格物体会重复命中
b0 = AABB((0.0, 0.0), (5.0, 0.5))     # 横跨多个格子
b1 = AABB((4.5, 0.0), (5.5, 0.5))
boxes = [b0, b1]
_, per_obj = grid_pairs(boxes, cell=1.0)
ok(per_obj[0] > 1, "跨格物体被登记进多个格子（%d 个）" % per_obj[0])
ok(per_obj[1] >= 1, "另一个物体至少登记 1 个格子")
# 重复登记意味着：不去重就会得到重复对，必须显式去重
pairs_no_dedup = grid_pairs([b0, b1], cell=1.0)[0]
ok(pairs_no_dedup == {(0, 1)}, "去重后只剩一对")

# ---------------------------------------- 6. 动态树：refit / rebuild 判据
t = DynamicTree()
leaves = []
for i in range(8):
    box = AABB((float(i), 0.0), (float(i) + 0.4, 0.4))
    leaves.append(t.insert(box, proxy=i))
ok(t.needs_rebuild() is True or t.dfs_ordered is False,
   "增量插入会把 dfsOrdered 打成 False -> 即使没动也要重建")
t.rebuild(full_build=False)
ok(t.dfs_ordered is True, "重建后恢复有序")
ok(t.needs_rebuild() is False, "重建后不再需要重建")
# 什么都没动时非 fullBuild 直接返回 0（不排序）
ok(t.rebuild(full_build=False) == 0, "既没动又有序 -> 返回 0")
ok(t.rebuild(full_build=True) > 0, "fullBuild 强制重建")
# 标记移动：沿父链传到根 -> needs_rebuild 为真
t.mark_moved(leaves[3])
ok(t.root.moved is True, "moved 标记一路传到根")
ok(t.needs_rebuild() is True, "root.moved -> 需要重建")
t.rebuild(full_build=False)
ok(t.needs_rebuild() is False, "重建清掉 moved")

# ---------------------------------------- 7. SAH-BVH：节点数与 2n-1
boxes = [AABB((float(i), 0.0), (float(i) + 0.5, 0.5)) for i in range(8)]
root, ordered, nodes = build_sah_bvh(boxes, max_prims_in_node=1)
ok(count_nodes(root) == 2 * len(boxes) - 1,
   "每叶一个图元时节点总数 = 2n-1（实得 %d，n=%d）" % (count_nodes(root), len(boxes)))
ok(len(ordered) == len(boxes), "每个图元恰好出现一次（primitive subdivision）")
# 父节点必须包住孩子


def validate_bvh(n):
    bad = []
    if n.left is None and n.right is None:
        return bad
    if not n.bounds.contains(n.left.bounds):
        bad.append("left")
    if not n.bounds.contains(n.right.bounds):
        bad.append("right")
    return bad + validate_bvh(n.left) + validate_bvh(n.right)


ok(not validate_bvh(root), "BVH 父节点包住孩子")

# maxPrimsInNode 放宽后节点数变少
root2, ordered2, nodes2 = build_sah_bvh(boxes, max_prims_in_node=4)
ok(nodes2 < nodes, "放宽 maxPrimsInNode 后节点更少（%d < %d）" % (nodes2, nodes))
ok(len(ordered2) == len(boxes), "放宽后图元仍各出现一次")
ok(bvh_pairs(root2, ordered2, boxes) >= naive_pairs(boxes), "放宽后仍不漏报")

# ---------------------------------------- 8. 极端：全部重合
same = [AABB((0.0, 0.0), (1.0, 1.0)) for _ in range(5)]
truth = naive_pairs(same)
ok(len(truth) == 10, "5 个全重合 -> C(5,2)=10 对")
r, o, _ = build_sah_bvh(same)
ok(bvh_pairs(r, o, same) >= truth, "全重合时 BVH 也不漏报")
tt = DynamicTree()
for i, b in enumerate(same):
    tt.insert(b, proxy=i)
ok(truth <= {(i, j) for (i, j) in tt.all_pairs()}, "全重合时动态树不漏报")
# 质心全同 -> pbrt 里直接成叶（没有任何分裂方式有效）


def is_leaf(n):
    return n.left is None and n.right is None


ok(is_leaf(r), "质心包围盒为零体积 -> 直接成叶")

print("BroadPhase与空间分割: %d 项断言全部通过" % PASS)
