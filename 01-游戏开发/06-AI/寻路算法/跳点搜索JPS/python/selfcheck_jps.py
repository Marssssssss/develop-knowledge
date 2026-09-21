"""JPS 自检：把论文的三条定义与两个算法逐条变成断言。

运行：``python selfcheck_jps.py``（当前目录 = python/）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    IDX, NB, Grid, astar_search, forced_neighbours, identify_successors,
    jump, jps_search, natural_neighbours, octile, path_without_x, step_cost,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1


def path_cost(path):
    return sum(octile(a, b) for a, b in zip(path, path[1:]))


# ---------- E1..E2 直线移动的剪枝与 forced（对应 Figure 2(a)(b)） ----------
g = Grid(5, 5)
x, p = (2, 2), (1, 2)                    # p(x) = 4 → 向右直行
ok(natural_neighbours((1, 0)) == {5}, "E1-1 直线移动只剩前进方向那一个 natural neighbour（图 2(a) 的 n=5）")
ok(forced_neighbours(g, x, p, (1, 0)) == [], "E1-2 无障碍时没有 forced neighbour")

g2 = Grid(5, 5, blocked={(2, 1)})        # 正上方放障碍（图 2(b)）
ok(forced_neighbours(g2, x, p, (1, 0)) == [3], "E2-1 上方被挡 ⇒ 右上(3) 成为 forced（图 2(b) 的 n=3）")
ok(path_without_x(g2, x, p, (3, 1)) > octile(p, x) + octile(x, (3, 1)),
   "E2-2 forced 的判据：绕开 x 的路比经过 x 的路更长")

# ---------- E3..E4 对角移动的剪枝与 forced（对应 Figure 2(c)(d)） ----------
p_diag = (1, 3)                          # p(x) = 6 → 向右上对角
ok(natural_neighbours((1, -1)) == {2, 3, 5}, "E3-1 对角移动保留 d 与两个正交分量（图 2(c) 的 2/3/5）")
ok(forced_neighbours(g, x, p_diag, (1, -1)) == [], "E3-2 无障碍时对角也没有 forced")

g3 = Grid(5, 5, blocked={(1, 2)})        # 正左方放障碍（图 2(d)）
ok(forced_neighbours(g3, x, p_diag, (1, -1)) == [1], "E4-1 左方被挡 ⇒ 左上(1) 成为 forced（图 2(d) 的 n=1）")

# ---------- E5..E7 jump（Algorithm 2） ----------
g4 = Grid(6, 5)
ok(jump(g4, (0, 2), (1, 0), None, (5, 2)) == (5, 2), "E5-1 直线上没有跳点时一路跳到目标（条件 1）")
g5 = Grid(6, 5, blocked={(3, 2)})
ok(jump(g5, (0, 2), (1, 0), None, (5, 2)) is None, "E6-1 直线被墙截断 ⇒ 返回 null")

g6 = Grid(6, 5, blocked={(2, 1)})        # (2,2) 的右上是 forced ⇒ (2,2) 成为跳点
ok(jump(g6, (0, 2), (1, 0), None, (5, 2)) == (2, 2), "E7-1 跳点在障碍上邻的下一格停下（条件 2）")

# 条件 3：对角步进前必须先试两个正交方向
g7 = Grid(6, 6, blocked={(0, 2), (1, 2)})   # 从 (0,1) 往右下走，(2,1) 右侧有跳点
ok(jump(g7, (0, 0), (1, 1), None, (5, 5)) is not None, "E7-2 对角搜索会因正交方向存在跳点而停下（条件 3）")

# ---------- E8 Identify Successors：起点不剪枝、其余剪枝 ----------
g8 = Grid(6, 5)
succ_start = identify_successors(g8, (0, 2), None, (5, 2))
ok(len(succ_start) >= 1 and (5, 2) in succ_start, "E8-1 起点（父为空）不剪枝，能一路跳到目标")
succ_mid = identify_successors(g8, (2, 2), (1, 2), (5, 2))
ok(succ_mid == [(5, 2)], "E8-2 有父节点时只保留 natural/forced 方向（直线前进）⇒ 单个后继")

# ---------- E9..E11 与朴素 A* 对拍：最优性与展开次数 ----------
obstacles = set()
for y in range(2, 8):
    obstacles.add((4, y))            # 一堵竖墙，中间留缺口
obstacles.discard((4, 5))
grid = Grid(12, 12, blocked=obstacles)
start, goal = (1, 5), (10, 5)

jp_path, jp_expanded = jps_search(grid, start, goal)
a_path, a_expanded = astar_search(grid, start, goal)
ok(jp_path is not None and a_path is not None, "E9-1 两种方法都找到路径")
ok(jp_path[0] == start and jp_path[-1] == goal, "E9-2 JPS 路径端点正确")
ok(abs(path_cost(jp_path) - path_cost(a_path)) < 1e-9,
   "E9-3 JPS 与朴素 A* 的路径代价相等（最优性保持）")
ok(jp_expanded < a_expanded, "E9-4 JPS 展开的跳点数远少于 A* 展开的格子数（%d < %d）" % (jp_expanded, a_expanded))
ok(len(jp_path) <= len(a_path), "E9-5 跳点路径的节点数也不多于逐格路径")

# 空旷地图：对称路径被剪掉的效果最明显
grid2 = Grid(15, 15)
jp2, exp2 = jps_search(grid2, (0, 0), (14, 14))
a2, exp_a2 = astar_search(grid2, (0, 0), (14, 14))
ok(abs(path_cost(jp2) - path_cost(a2)) < 1e-9, "E10-1 空旷地图上仍然最优")
ok(exp2 <= 2 and exp2 < exp_a2, "E10-2 空旷地图上 JPS 展开更少的节点（%d vs A* 的 %d）" % (exp2, exp_a2))
ok(len(jp2) == 2, "E10-3 空旷对角：JPS 一次跳到终点，路径只有起终点两个节点")

# ---------- E11 不可达 ----------
grid3 = Grid(6, 5, blocked={(3, 0), (3, 1), (3, 2), (3, 3), (3, 4)})
ok(jps_search(grid3, (0, 2), (5, 2))[0] is None, "E11-1 被完全隔断时返回 None")
ok(astar_search(grid3, (0, 2), (5, 2))[0] is None, "E11-2 朴素 A* 同样 None（对照组）")

# ---------- E12 代价口径 ----------
ok(step_cost((1, 0)) == 1.0 and abs(step_cost((1, 1)) - 2 ** 0.5) < 1e-12, "E12-1 直线 1 / 对角 sqrt(2)")
ok(NB[IDX[(-1, 1)]] == (-1, 1), "E12-2 邻居编号与方向向量一一对应（6 = 左下）")

# ---------- E13 更大地形上的对拍（30x30 空旷 / 15% 障碍） ----------
big = Grid(30, 30)
jp3, exp3 = jps_search(big, (0, 0), (29, 29))
a3, exp_a3 = astar_search(big, (0, 0), (29, 29))
ok(abs(path_cost(jp3) - path_cost(a3)) < 1e-9, "E13-1 30x30 空旷：代价仍然相等")
ok(exp3 == 1 and exp_a3 == 29, "E13-2 30x30 空旷：JPS 展开 1 个节点，A* 展开 29 个")

import random  # noqa: E402
random.seed(7)
blocks = {(x, y) for y in range(30) for x in range(30) if random.random() < 0.15}
blocks.discard((0, 0))
blocks.discard((29, 29))
rand_grid = Grid(30, 30, blocked=blocks)
jp4, exp4 = jps_search(rand_grid, (0, 0), (29, 29))
a4, exp_a4 = astar_search(rand_grid, (0, 0), (29, 29))
ok(jp4 is not None and a4 is not None, "E13-3 15% 随机障碍上两者都有解")
ok(abs(path_cost(jp4) - path_cost(a4)) < 1e-9, "E13-4 15% 随机障碍上代价仍然相等")
ok(exp4 < exp_a4, "E13-5 随机障碍(密度0.15)上 JPS 展开更少（{} < {}）".format(exp4, exp_a4))

print("PASS =", PASS)
print("JPS expanded=%d  A* expanded=%d  (12x12 带缺口竖墙)" % (jp_expanded, a_expanded))
print("JPS expanded=%d  A* expanded=%d  (15x15 空旷)" % (exp2, exp_a2))
