"""JPS（Jump Point Search）的 Python 转写：按 Harabor & Grastien (AAAI 2011) 的原文实现。

论文原文逐条对应：

- §Neighbour Pruning Rules 的式 (1)（直线移动，``<=``）与式 (2)（对角移动，``<``）
- Definition 1（forced neighbour：不是 natural neighbour 且 ``len(<p(x),x,n>) < len(<p(x),...,n>\\x)``）
- Definition 2（jump point 的三个条件）
- Definition 3（turning point）
- Algorithm 1（Identify Successors）与 Algorithm 2（Function jump）

语言与表述差异显式落地：
- 论文用 1..8 给邻居编号（图 2 的 numpad 布局），本模块保留同一套编号以便对照 Figure 2；
- 论文里 ``len(<p(x),...,n>\\x)`` 是「只用 neighbours(x) 且不经过 x 的最短路」，
  这里用 **3x3 邻域内的 BFS** 精确计算（走不到即视为无穷，正是「forced」的判定依据）；
- 直线代价 1、对角代价 sqrt(2)，与论文一致。
"""

import heapq
import math
from collections import deque

SQRT2 = math.sqrt(2.0)

# 图 2 的邻居编号（numpad 布局，y 轴向下）：
# 1 2 3     1 = 左上  2 = 上  3 = 右上
# 4 x 5     4 = 左          5 = 右
# 6 7 8     6 = 左下  7 = 下  8 = 右下
NB = {
    1: (-1, -1), 2: (0, -1), 3: (1, -1),
    4: (-1, 0),              5: (1, 0),
    6: (-1, 1),  7: (0, 1),  8: (1, 1),
}
IDX = {v: k for k, v in NB.items()}


def step_cost(d):
    return SQRT2 if d[0] != 0 and d[1] != 0 else 1.0


def octile(a, b):
    """8 连通网格上的最短距离（用于启发值与跳跃代价）。"""
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    return SQRT2 * min(dx, dy) + (max(dx, dy) - min(dx, dy))


class Grid:
    def __init__(self, width, height, blocked=()):
        self.w, self.h = width, height
        self.blocked = set(blocked)

    def walkable(self, p):
        x, y = p
        return 0 <= x < self.w and 0 <= y < self.h and p not in self.blocked

    def neighbours(self, x):
        """neighbours(x)：8 邻域里的可通行节点。"""
        out = []
        for i in (1, 2, 3, 4, 5, 6, 7, 8):
            n = (x[0] + NB[i][0], x[1] + NB[i][1])
            if self.walkable(n):
                out.append(i)
        return out


def natural_neighbours(d):
    """natural neighbours：无障碍假设下剪枝后剩下的邻居（按移动方向决定）。"""
    di = IDX[d]
    if d[0] != 0 and d[1] != 0:          # 对角移动：d 本身 + 两个正交分量
        d1 = (d[0], 0)
        d2 = (0, d[1])
        return {di, IDX[d1], IDX[d2]}
    return {di}                           # 直线移动：只有继续前进的那一个


def path_without_x(grid, x, p, n):
    """len(<p(x),...,n>\\x)：只在 neighbours(x) 内、且不经过 x 的最短路；不可达返回 inf。"""
    nodes = [x]
    for i in (1, 2, 3, 4, 5, 6, 7, 8):
        q = (x[0] + NB[i][0], x[1] + NB[i][1])
        if grid.walkable(q):
            nodes.append(q)
    if p not in nodes or n not in nodes:
        return math.inf
    dist = {p: 0.0}
    queue = deque([p])
    while queue:
        cur = queue.popleft()
        if cur == n:
            return dist[cur]
        for other in nodes:
            if other == x or other == cur:
                continue
            dx, dy = abs(other[0] - cur[0]), abs(other[1] - cur[1])
            if dx > 1 or dy > 1:
                continue
            cost = SQRT2 if (dx == 1 and dy == 1) else 1.0
            nd = dist[cur] + cost
            if nd < dist.get(other, math.inf):
                dist[other] = nd
                queue.append(other)
    return math.inf


def forced_neighbours(grid, x, p, d):
    """Definition 1：不是 natural neighbour 且 len(<p,x,n>) < len(<p,...,n>\\x)。"""
    natural = natural_neighbours(d)
    out = []
    for i in grid.neighbours(x):
        if i in natural:
            continue
        n = (x[0] + NB[i][0], x[1] + NB[i][1])
        with_x = octile(p, x) + octile(x, n)
        without_x = path_without_x(grid, x, p, n)
        if with_x < without_x:
            out.append(i)
    return out


def jump(grid, x, d, start, goal):
    """Algorithm 2：沿 d 一直跳，直到碰到跳点或撞墙。"""
    n = (x[0] + d[0], x[1] + d[1])
    if not grid.walkable(n):
        return None
    if n == goal:                                    # 条件 1
        return n
    if forced_neighbours(grid, n, x, d):             # 条件 2
        return n
    if d[0] != 0 and d[1] != 0:                      # 条件 3：先试两个正交方向
        for di in ((d[0], 0), (0, d[1])):
            if jump(grid, n, di, start, goal) is not None:
                return n
    return jump(grid, n, d, start, goal)


def identify_successors(grid, x, parent, goal):
    """Algorithm 1：先剪枝，再对每个幸存的邻居做 jump。"""
    out = []
    for i in (1, 2, 3, 4, 5, 6, 7, 8):
        n = (x[0] + NB[i][0], x[1] + NB[i][1])
        if not grid.walkable(n):
            continue
        d = NB[i]
        if parent is not None:
            travel = (x[0] - parent[0], x[1] - parent[1])
            travel_d = (0 if travel[0] == 0 else (1 if travel[0] > 0 else -1),
                        0 if travel[1] == 0 else (1 if travel[1] > 0 else -1))
            natural = natural_neighbours(travel_d)
            if i not in natural and i not in forced_neighbours(grid, x, parent, travel_d):
                continue
        jp = jump(grid, x, d, None, goal)
        if jp is not None:
            out.append(jp)
    return out


def jps_search(grid, start, goal):
    """在跳点图上跑 A*；返回 (路径, 展开次数)。"""
    g_score = {start: 0.0}
    parent = {start: None}
    counter = 0
    heap = [(octile(start, goal), counter, start)]
    expanded = 0
    while heap:
        _, _, x = heapq.heappop(heap)
        if x == goal:
            path = []
            while x is not None:
                path.append(x)
                x = parent[x]
            return list(reversed(path)), expanded
        expanded += 1
        for y in identify_successors(grid, x, parent[x], goal):
            ng = g_score[x] + octile(x, y)
            if ng < g_score.get(y, math.inf):
                g_score[y] = ng
                parent[y] = x
                counter += 1
                heapq.heappush(heap, (ng + octile(y, goal), counter, y))
    return None, expanded


def astar_search(grid, start, goal):
    """普通 8 连通 A*，用来对照最优性与展开次数。"""
    g_score = {start: 0.0}
    parent = {start: None}
    counter = 0
    heap = [(octile(start, goal), counter, start)]
    expanded = 0
    while heap:
        _, _, x = heapq.heappop(heap)
        if x == goal:
            path = []
            while x is not None:
                path.append(x)
                x = parent[x]
            return list(reversed(path)), expanded
        expanded += 1
        for i in (1, 2, 3, 4, 5, 6, 7, 8):
            n = (x[0] + NB[i][0], x[1] + NB[i][1])
            if not grid.walkable(n):
                continue
            ng = g_score[x] + step_cost(NB[i])
            if ng < g_score.get(n, math.inf):
                g_score[n] = ng
                parent[n] = x
                counter += 1
                heapq.heappush(heap, (ng + octile(n, goal), counter, n))
    return None, expanded
