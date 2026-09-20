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

(本文件是第 1 段：常量、AABB、朴素两两、均匀网格；动态树与 BVH 见 `main.py`。)
"""

from math import sqrt, inf


from math import sqrt, inf


# ------------------------------- 常量（逐条对应 Box2D constants.h） -------------------------------

B2_LINEAR_SLOP = 0.005
B2_SPECULATIVE_DISTANCE = 4.0 * B2_LINEAR_SLOP          # 0.02
B2_MAX_AABB_MARGIN = 0.05
B2_AABB_MARGIN_FRACTION = 0.125
B2_CONTACT_RECYCLE_DISTANCE = 10.0 * B2_LINEAR_SLOP     # 0.05
B2_TIME_TO_SLEEP = 0.5

# pbrt 第四版 BVH：遍历代价常数 1/2（相对求交代价 1）
PBRT_TRAVERSAL_COST = 0.5
PBRT_SAH_BUCKETS = 12


# ------------------------------------------- AABB -------------------------------------------

class AABB:
    __slots__ = ("lo", "hi")   # lo=(x,y) hi=(x,y)

    def __init__(self, lo, hi):
        self.lo = (min(lo[0], hi[0]), min(lo[1], hi[1]))
        self.hi = (max(lo[0], hi[0]), max(lo[1], hi[1]))

    @property
    def w(self):
        return self.hi[0] - self.lo[0]

    @property
    def h(self):
        return self.hi[1] - self.lo[1]

    def perimeter(self):
        """2D 下等价于 pbrt 的 SurfaceArea()。"""
        return 2.0 * (self.w + self.h)

    def center(self):
        return ((self.lo[0] + self.hi[0]) / 2.0, (self.lo[1] + self.hi[1]) / 2.0)

    def overlaps(self, o):
        return not (self.hi[0] < o.lo[0] or o.hi[0] < self.lo[0] or
                    self.hi[1] < o.lo[1] or o.hi[1] < self.lo[1])

    def contains(self, o, tol=1e-12):
        return (self.lo[0] <= o.lo[0] + tol and self.lo[1] <= o.lo[1] + tol and
                o.hi[0] <= self.hi[0] + tol and o.hi[1] <= self.hi[1] + tol)

    def __repr__(self):
        return "AABB(%r,%r)" % (self.lo, self.hi)


def union(a, b):
    if a is None:
        return AABB(b.lo, b.hi)
    if b is None:
        return AABB(a.lo, a.hi)
    return AABB((min(a.lo[0], b.lo[0]), min(a.lo[1], b.lo[1])),
                (max(a.hi[0], b.hi[0]), max(a.hi[1], b.hi[1])))


def fatten(box, margin):
    return AABB((box.lo[0] - margin, box.lo[1] - margin),
                (box.hi[0] + margin, box.hi[1] + margin))


# ------------------------------- Box2D 的 AABB 余量（shape.c 复刻） -------------------------------

def compute_shape_margin(max_extent, is_static=False):
    """`b2ComputeShapeMargin()` + `b2UpdateShapeAABBs()` 的余量选择。

    `max_extent` 对多边形是"质心到顶点最大距离"、圆是半径、胶囊是半长加半径、
    线段是半长。静态体走 speculativeDistance 分支。
    """
    if is_static:
        return B2_SPECULATIVE_DISTANCE
    return min(B2_MAX_AABB_MARGIN, B2_AABB_MARGIN_FRACTION * max_extent)


# ----------------------------------------- 1. 朴素两两 -----------------------------------------

def naive_pairs(boxes):
    out = set()
    n = len(boxes)
    for i in range(n):
        for j in range(i + 1, n):
            if boxes[i].overlaps(boxes[j]):
                out.add((i, j))
    return out


# ------------------------------------- 2. 均匀网格 / 空间哈希 -------------------------------------

def grid_pairs(boxes, cell):
    """把每个盒子登记进它覆盖的所有格子；同一对可能被多个格子重复命中。"""
    buckets = {}
    per_object = {}
    for i, b in enumerate(boxes):
        x0 = int(b.lo[0] // cell)
        x1 = int(b.hi[0] // cell)
        y0 = int(b.lo[1] // cell)
        y1 = int(b.hi[1] // cell)
        per_object[i] = (x1 - x0 + 1) * (y1 - y0 + 1)
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                buckets.setdefault((cx, cy), []).append(i)
    seen = set()
    for members in buckets.values():
        m = sorted(members)
        for a in range(len(m)):
            for b in range(a + 1, len(m)):
                i, j = m[a], m[b]
                if i > j:
                    i, j = j, i
                if boxes[i].overlaps(boxes[j]):
                    seen.add((i, j))
    return seen, per_object
