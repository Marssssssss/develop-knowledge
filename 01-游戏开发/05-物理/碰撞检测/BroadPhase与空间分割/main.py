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
"""

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


# ------------------------------ 3. Box2D 动态 AABB 树（增量插入） ------------------------------

class Node:
    __slots__ = ("aabb", "parent", "child1", "child2", "height", "moved", "proxy")

    def __init__(self, aabb, proxy=None):
        self.aabb = aabb
        self.parent = None
        self.child1 = None
        self.child2 = None
        self.height = 0
        self.moved = False
        self.proxy = proxy

    def is_leaf(self):
        return self.child1 is None


class DynamicTree:
    """`b2DynamicTree` 的 2D 教学复刻：按"联合周长 + 祖先增量"选插入位置。"""

    def __init__(self):
        self.root = None
        self.nodes = []
        self.dfs_ordered = True

    # -- 代价：联合周长（2D 对应 pbrt/Box2D 的 area） --
    @staticmethod
    def _direct_cost(sibling, leaf_box):
        return union(sibling, leaf_box).perimeter()

    def _inherited_cost(self, node):
        """从 node 往上走到根，累计"盒子上溯后祖先周长的增量"。"""
        box = node.aabb
        cost = 0.0
        cur = node.parent
        while cur is not None:
            old = cur.aabb.perimeter()
            new = union(cur.aabb, box).perimeter()
            cost += new - old
            box = union(cur.aabb, box)
            cur = cur.parent
        return cost

    def insert(self, box, proxy=None):
        leaf = Node(box, proxy)
        self.nodes.append(leaf)
        self._attach(leaf)
        return leaf

    def insert_existing(self, leaf):
        """重建专用：**复用**原来那个叶子对象，不能新建。

        重建阶段若新建叶子对象，外部持有的旧叶子就变成孤儿，
        之后对它的 `mark_moved()` 不会传到树上（这是本模型实测踩到的坑）。
        """
        leaf.parent = None
        leaf.child1 = None
        leaf.child2 = None
        leaf.height = 0
        leaf.moved = False
        self.nodes.append(leaf)
        self._attach(leaf)
        return leaf

    def _attach(self, leaf):
        box = leaf.aabb
        if self.root is None:
            self.root = leaf
            self.dfs_ordered = True
            return leaf
        if self.root.is_leaf():
            parent = Node(union(self.root.aabb, box))
            self.nodes.append(parent)
            parent.child1 = self.root
            parent.child2 = leaf
            parent.height = 1
            self.root.parent = parent
            leaf.parent = parent
            self.root = parent
            return leaf

        best_cost = inf
        best_sibling = None
        # 贪心下潜：每层比较"左/右子树作为兄弟"或"插到其下"的最小代价
        stack = [self.root]
        while stack:
            node = stack.pop()
            direct = self._direct_cost(node.aabb, box)
            inherited = self._inherited_cost(node)
            cost = direct + inherited
            if cost < best_cost:
                best_cost = cost
                best_sibling = node
            if node.is_leaf():
                continue
            for child in (node.child1, node.child2):
                if child is None:
                    continue
                c_direct = self._direct_cost(child.aabb, box)
                c_cost = c_direct + inherited
                # 内部节点还要考虑"其某个后代做兄弟"的下界
                c_lower = inherited + (c_direct - child.aabb.perimeter()) + box.perimeter()
                if min(c_cost, c_lower) < best_cost:
                    stack.append(child)
        self._insert_leaf(box, leaf, best_sibling)
        return leaf

    def _insert_leaf(self, box, leaf, sibling):
        parent = sibling.parent
        new_parent = Node(union(sibling.aabb, box))
        self.nodes.append(new_parent)
        new_parent.parent = parent
        new_parent.height = sibling.height + 1
        if parent is None:
            new_parent.child1 = sibling
            new_parent.child2 = leaf
            sibling.parent = new_parent
            leaf.parent = new_parent
            self.root = new_parent
        else:
            if parent.child1 is sibling:
                parent.child1 = new_parent
            else:
                parent.child2 = new_parent
            new_parent.child1 = sibling
            new_parent.child2 = leaf
            sibling.parent = new_parent
            leaf.parent = new_parent
        self._refit(new_parent.parent)
        self.dfs_ordered = False   # 增量插入会破坏 DFS 顺序 -> 触发重建

    def _refit(self, node):
        cur = node
        while cur is not None:
            if cur.child1 is None:
                cur = cur.parent
                continue
            h = 1 + max(cur.child1.height, cur.child2.height)
            box = union(cur.child1.aabb, cur.child2.aabb)
            cur.height = h
            cur.aabb = box
            cur = cur.parent

    def mark_moved(self, node):
        """Box2D 把 moved 标记沿父链一路传到根。"""
        cur = node
        while cur is not None:
            cur.moved = True
            cur = cur.parent

    def needs_rebuild(self):
        """`b2NeedsRebuild()`：root 被标记 moved 或 dfsOrdered 为 False。"""
        if self.root is None:
            return False
        return self.root.moved or (self.dfs_ordered is False)

    def rebuild(self, full_build=False):
        """`b2DynamicTree_Rebuild()`：非 fullBuild 时"既没动又有序"则直接返回 0。"""
        if self.root is None:
            return 0
        if full_build is False and (self.root.moved is False) and self.dfs_ordered:
            return 0
        # 复用原叶子对象（重建阶段 shouldRotate=False，本模型未实现旋转）
        leaves = [n for n in self.nodes if n.is_leaf()]
        self.nodes = []
        self.root = None
        for leaf in leaves:
            self.insert_existing(leaf)
        self.dfs_ordered = True
        self.root.moved = False
        return len(leaves)

    def query(self, box):
        out = []
        if self.root is None:
            return out
        stack = [self.root]
        while stack:
            n = stack.pop()
            if not n.aabb.overlaps(box):
                continue
            if n.is_leaf():
                out.append(n)
            else:
                if n.child1 is not None:
                    stack.append(n.child1)
                if n.child2 is not None:
                    stack.append(n.child2)
        return out

    def all_pairs(self):
        """树 query 得到的候选对（可能与真实相交对不等，需再过一次精确测试）。"""
        leaves = [n for n in self.nodes if n.is_leaf()]
        out = set()
        for i, a in enumerate(leaves):
            for b in self.query(a.aabb):
                if b is a:
                    continue
                ia, ib = a.proxy, b.proxy
                if ia is None or ib is None:
                    continue
                if ia > ib:
                    ia, ib = ib, ia
                if ia != ib and a.aabb.overlaps(b.aabb):
                    out.add((ia, ib))
        return out

    def height(self):
        return 0 if self.root is None else self.root.height

    def validate(self):
        """返回内部节点是否都包住自己的孩子（不变量）。"""
        bad = []
        for n in self.nodes:
            if n.is_leaf():
                continue
            if not n.aabb.contains(n.child1.aabb):
                bad.append(("child1", n))
            if not n.aabb.contains(n.child2.aabb):
                bad.append(("child2", n))
        return bad


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
