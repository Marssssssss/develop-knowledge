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
