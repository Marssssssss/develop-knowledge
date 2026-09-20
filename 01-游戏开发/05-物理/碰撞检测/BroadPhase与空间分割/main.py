"""Broad Phase 与空间分割 —— 均匀网格、动态 AABB 树、SAH-BVH。

判据与公式全部取自下列**实读**资料：

* Box2D v3.1 源码（`src/`、`include/box2d/`）
  - `constants.h`：`B2_LINEAR_SLOP = 0.005m`、`B2_SPECULATIVE_DISTANCE = 4*slop`、
    `B2_MAX_AABB_MARGIN = 0.05m`、`B2_AABB_MARGIN_FRACTION = 0.125`、
    `B2_CONTACT_RECYCLE_DISTANCE = 10*slop`、`B2_TIME_TO_SLEEP = 0.5s`。
  - `shape.c` 的 `b2ComputeShapeMargin()`：多边形取"质心到顶点最大距离"、
    圆取半径、胶囊取半长加半径、线段取半长，最后统一
    `margin = min(B2_MAX_AABB_MARGIN, B2_AABB_MARGIN_FRACTION * margin)`；
    `b2UpdateShapeAABBs()` 里**静态体用的是 speculativeDistance 而不是 aabbMargin**
    （注释："Smaller margin for static bodies. Cannot be zero due to TOI tolerance."）。
  - `dynamic_tree.c` 的插入代价：兄弟代价 = `area(union(H, D)) + 祖先增加的代价`，
    对内部节点还要取"插在其下"的下界代价；`b2InsertLeaf(..., shouldRotate)` 在
    逐个插入时传 `true`、整树重建时传 `false`。
  - `dynamic_tree.h` 的 `b2NeedsRebuild()`：`root 被标记 moved || dfsOrdered == false`。
    注意注释："An unordered tree is rebuilt even when nothing moved"。
  - `broad_phase.c`：收集"有节点移动过的兄弟对"两两碰撞子树，
    注释点名参考 Real-time collision detection 6.3.2。
* pbrt 第四版《Bounding Volume Hierarchies》
  - 二叉 BVH 每叶一个图元时节点总数是 **2n-1**（n 个叶 + n-1 个内部节点）。
  - 分桶近似 SAH：`nBuckets = 12`，前向扫累加下侧、后向扫累加上侧，
    `costs[i] = countBelow*areaBelow + countAbove*areaAbove`，
    最终 `minCost = 1/2 + minCost / bounds.SurfaceArea()`，`leafCost = n`；
    `n > maxPrimsInNode || minCost < leafCost` 才继续分裂。
  - 图元只出现一次（primitive subdivision），与空间 subdivision 相对。

**口径说明**：pbrt 是 3D，用表面积；本 demo 是 2D 物理场景，等价量是**周长**
`2*(w+h)`。所有"面积"字样在 2D 下按周长理解，README 也这么标注。

本文件只放模型，断言在 `selfcheck_broadphase.py`。

(本文件是第 2 段：Box2D 动态 AABB 树 + pbrt SAH-BVH。常量、AABB、网格在 `spatial.py`，
这里用 `from spatial import *` 再导出，故 `selfcheck_broadphase.py` 的导入无需改动。)
"""

from math import sqrt, inf

from spatial import *   # noqa: F401,F403  常量 / AABB / 朴素两两 / 均匀网格
from tree import *      # noqa: F401,F403  Box2D 动态 AABB 树

# ------------------------------------- 4. pbrt 分桶近似 SAH 的 BVH -------------------------------------

class BVHNode:
    __slots__ = ("bounds", "left", "right", "first", "count")

    def __init__(self, bounds, first=0, count=0, left=None, right=None):
        self.bounds = bounds
        self.left = left
        self.right = right
        self.first = first
        self.count = count


def build_sah_bvh(boxes, max_prims_in_node=1, n_buckets=PBRT_SAH_BUCKETS):
    """pbrt 第四版 BVHAggregate::buildRecursive 的 2D 复刻（周长代替表面积）。"""
    prims = list(range(len(boxes)))
    ordered = []
    stats = {"nodes": 0}

    def bounds_of(idx):
        b = None
        for i in idx:
            b = union(b, boxes[i])
        return b

    def rec(idxs, bounds):
        # pbrt：质心包围盒体积为 0 -> 直接成叶
        cmin = [min(boxes[i].center()[d] for i in idxs) for d in (0, 1)]
        cmax = [max(boxes[i].center()[d] for i in idxs) for d in (0, 1)]
        extents = [cmax[d] - cmin[d] for d in (0, 1)]
        if max(extents) <= 0.0 or len(idxs) <= max_prims_in_node:
            return make_leaf(idxs, bounds)
        dim = 0 if extents[0] >= extents[1] else 1   # MaxDimension

        if len(idxs) == 2:
            s = sorted(idxs, key=lambda i: boxes[i].center()[dim])
            mid = 1
            left, right = s[:mid], s[mid:]
        else:
            # 分桶：12 个桶，前向扫 + 后向扫
            buckets = [{"count": 0, "bounds": None} for _ in range(n_buckets)]
            span = cmax[dim] - cmin[dim]
            for i in idxs:
                c = boxes[i].center()[dim]
                b = int(n_buckets * (c - cmin[dim]) / span)
                if b >= n_buckets:
                    b = n_buckets - 1
                buckets[b]["count"] += 1
                buckets[b]["bounds"] = union(buckets[b]["bounds"], boxes[i])
            n_splits = n_buckets - 1
            costs = [0.0] * n_splits
            below, count_below = None, 0
            for i in range(n_splits):
                below = union(below, buckets[i]["bounds"])
                count_below += buckets[i]["count"]
                costs[i] += count_below * (below.perimeter() if below else 0.0)
            above, count_above = None, 0
            for i in range(n_splits, 0, -1):
                above = union(above, buckets[i]["bounds"])
                count_above += buckets[i]["count"]
                costs[i - 1] += count_above * (above.perimeter() if above else 0.0)
            min_cost = min(costs)
            split = costs.index(min_cost)
            min_cost = PBRT_TRAVERSAL_COST + min_cost / bounds.perimeter()
            leaf_cost = float(len(idxs))
            if leaf_cost <= min_cost:
                return make_leaf(idxs, bounds)
            pivot = cmin[dim] + span * (split + 1) / n_buckets
            left = [i for i in idxs if boxes[i].center()[dim] < pivot]
            right = [i for i in idxs if boxes[i].center()[dim] >= pivot]
            if not left or not right:
                return make_leaf(idxs, bounds)

        stats["nodes"] += 1
        node = BVHNode(bounds)
        node.left = rec(left, bounds_of(left))
        node.right = rec(right, bounds_of(right))
        return node

    def make_leaf(idxs, bounds):
        stats["nodes"] += 1
        node = BVHNode(bounds, first=len(ordered), count=len(idxs))
        ordered.extend(idxs)
        return node

    root = rec(prims, bounds_of(prims))
    return root, ordered, stats["nodes"]


def bvh_pairs(root, ordered, boxes):
    out = set()

    def walk(n):
        # 叶子内也要两两枚举：叶子里有多个图元时它**没有兄弟可交叉**，
        # 只做兄弟交叉会整片漏报（全重合场景下根就是一个叶子）。
        if n.left is None and n.right is None:
            idx = [ordered[k] for k in range(n.first, n.first + n.count)]
            for x in range(len(idx)):
                for y in range(x + 1, len(idx)):
                    i, j = idx[x], idx[y]
                    if i > j:
                        i, j = j, i
                    if boxes[i].overlaps(boxes[j]):
                        out.add((i, j))
            return
        for pair in _cross(n.left, n.right, ordered, boxes):
            out.add(pair)
        walk(n.left)
        walk(n.right)

    def _cross(a, b, ordered, boxes):
        res = []
        ia = _leaves(a, ordered)
        ib = _leaves(b, ordered)
        for x in ia:
            for y in ib:
                i, j = (x, y) if x < y else (y, x)
                if boxes[x].overlaps(boxes[y]):
                    res.append((i, j))
        return res

    def _leaves(n, ordered):
        if n.left is None and n.right is None:
            return [ordered[k] for k in range(n.first, n.first + n.count)]
        return _leaves(n.left, ordered) + _leaves(n.right, ordered)

    walk(root)
    return out


def count_nodes(root):
    if root is None:
        return 0
    return 1 + count_nodes(root.left) + count_nodes(root.right)
