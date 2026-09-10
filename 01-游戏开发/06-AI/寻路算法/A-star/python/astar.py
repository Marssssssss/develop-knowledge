# -*- coding: utf-8 -*-
"""A* 寻路算法最小实现（4 邻接带权网格）。

对照 Red Blob Games 的参考实现：不使用 decrease-key，
直接向最小堆插入重复条目，出队后依靠 cost_so_far 判断过期。

模式说明：
  mode 0 = Dijkstra        优先级 = g
  mode 1 = A*              优先级 = g + h
  mode 2 = Greedy Best-First 优先级 = h
"""

import heapq

W, H = 24, 12
FOREST_COST = 5  # 森林格的移动代价（普通格为 1）

# '.' 普通格 / 'f' 森林格 / '#' 墙 / 'S' 起点 / 'E' 终点
MAP_ROWS = [
    "..............#.........",
    "..S.......f...#.........",
    "..........f...#....fff..",
    "...ffffff.f...#....fff..",
    "...ffffff.f...#.........",
    ".......fffffffffffffffff",
    "..............#.........",
    "..............#.ffff....",
    "####..........#.........",
    "..............#....E....",
    "..............#.........",
    "..............#.........",
]


def parse_map(rows):
    """解析字符地图 -> (blocked 集合, forest 集合, start, goal)"""
    blocked, forest = set(), set()
    start = goal = None
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "#":
                blocked.add((x, y))
            elif ch == "f":
                forest.add((x, y))
            elif ch == "S":
                start = (x, y)
            elif ch == "E":
                goal = (x, y)
    return blocked, forest, start, goal


def neighbors(loc):
    """4 邻接（曼哈顿世界，对应曼哈顿启发式）"""
    x, y = loc
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < W and 0 <= ny < H:
            yield (nx, ny)


def move_cost(loc, nxt, forest):
    """进入 nxt 格的代价：普通格 1，森林格 5"""
    return FOREST_COST if nxt in forest else 1


def heuristic(a, b):
    """曼哈顿距离：4 邻接单位代价网格的可采纳启发式（不高估）"""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def search(blocked, forest, start, goal, mode):
    """统一搜索：mode 0/1/2 分别为 Dijkstra / A* / Greedy。

    返回 (came_from, cost_so_far, expanded)。
    """
    frontier = [(0, start)]          # 最小堆：(priority, loc)
    came_from = {start: None}         # 父指针表，用于回溯路径
    cost_so_far = {start: 0}          # g 值表
    expanded = 0
    while frontier:
        _, current = heapq.heappop(frontier)
        if current == goal:            # early exit：目标出队即结束
            break
        # 注：堆中可能存在同一节点的重复条目（无 decrease-key），
        # 重复扩展无害——松弛时的 g 值改善检查保证正确性。
        expanded += 1
        for nxt in neighbors(current):
            if nxt in blocked:
                continue
            new_cost = cost_so_far[current] + move_cost(current, nxt, forest)
            if nxt not in cost_so_far or new_cost < cost_so_far[nxt]:
                cost_so_far[nxt] = new_cost
                came_from[nxt] = current
                if mode == 0:          # Dijkstra：只看 g
                    priority = new_cost
                elif mode == 1:        # A*：g + h
                    priority = new_cost + heuristic(goal, nxt)
                else:                 # Greedy：只看 h
                    priority = heuristic(goal, nxt)
                heapq.heappush(frontier, (priority, nxt))
    return came_from, cost_so_far, expanded


def reconstruct_path(came_from, goal):
    """从 goal 沿父指针回溯到 start"""
    if goal not in came_from:
        return None
    path = []
    cur = goal
    while cur is not None:
        path.append(cur)
        cur = came_from[cur]
    path.reverse()
    return path


def render(blocked, forest, start, goal, path):
    """输出带路径的 ASCII 地图：* 路径，# 墙，f 森林"""
    path_set = set(path or [])
    lines = []
    for y in range(H):
        row = []
        for x in range(W):
            loc = (x, y)
            if loc == start:
                row.append("S")
            elif loc == goal:
                row.append("E")
            elif loc in path_set:
                row.append("*")
            elif loc in blocked:
                row.append("#")
            elif loc in forest:
                row.append("f")
            else:
                row.append(".")
        lines.append("".join(row))
    return "\n".join(lines)


def main():
    blocked, forest, start, goal = parse_map(MAP_ROWS)
    names = ["Dijkstra (g)", "A* (g+h)", "Greedy (h)"]
    results = {}
    for mode in range(3):
        came_from, cost_so_far, expanded = search(
            blocked, forest, start, goal, mode
        )
        path = reconstruct_path(came_from, goal)
        results[mode] = (path, cost_so_far.get(goal), expanded)
        cost = cost_so_far.get(goal)
        cost_str = str(cost) if cost is not None else "不可达"
        print("%-16s 代价 = %-4s 扩展节点数 = %d" % (names[mode], cost_str, expanded))
    print()
    print("A* 路径可视化（* 为路径，绕开代价 5 的森林）：")
    print(render(blocked, forest, start, goal, results[1][0]))
    # 一致性验证：A* 与 Dijkstra 代价必须相同（曼哈顿 h 在代价>=1 时可采纳）
    if results[0][1] is not None and results[1][1] is not None:
        assert results[0][1] == results[1][1], "A* 代价应与 Dijkstra 一致！"
        print("\n[check] A* 与 Dijkstra 最优代价一致：%d（h 可采纳性验证通过）" % results[1][1])


if __name__ == "__main__":
    main()
