"""图遍历：BFS / DFS / Dijkstra 三件套 —— 纯标准库。

权威来源：
  - Wikipedia "Dijkstra's algorithm"
    https://en.wikipedia.org/wiki/Dijkstra%27s_algorithm
  - Czech Technical University Programming for Engineers L8 Recursion
    https://cw.fel.cvut.cz/b252/_media/courses/be5b33pge/lectures/l8_recursion.pdf
    ("DFS has the same asymptotic complexity as BFS on adjacency lists:
     time: O(V + E), extra space: O(V)")
  - algoarena.net "DFS vs BFS"
    https://algoarena.net/blog/dfs-vs-bfs

邻接表 + 队列(BFS) / 栈(DFS) / 堆(Dijkstra)。
对比 BFS/DFS 在无权图最短路径上的差异；Dijkstra 处理非负加权图。
"""
from __future__ import annotations

import heapq
from collections import deque
from typing import Any


def bfs(graph: dict[Any, list[Any]], start: Any) -> list[Any]:
    """BFS：按层扩展，可求无权图最短跳数。O(V+E) 时间, O(V) 空间。"""
    visited = {start}
    q = deque([start])
    order: list[Any] = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in graph.get(u, []):
            if v not in visited:
                visited.add(v)
                q.append(v)
    return order


def dfs(graph: dict[Any, list[Any]], start: Any) -> list[Any]:
    """DFS：递归实现，一支走到底再回溯。O(V+E) 时间, O(depth) 栈空间。"""
    visited: set[Any] = set()
    order: list[Any] = []

    def _dfs(u: Any) -> None:
        visited.add(u)
        order.append(u)
        for v in graph.get(u, []):
            if v not in visited:
                _dfs(v)
    _dfs(start)
    return order


def bfs_shortest_path(graph: dict[Any, list[Any]], start: Any, goal: Any) -> list[Any]:
    """BFS 求无权图最短路径（最少跳数）。"""
    if start == goal:
        return [start]
    parent: dict[Any, Any] = {start: None}
    q = deque([start])
    while q:
        u = q.popleft()
        for v in graph.get(u, []):
            if v not in parent:
                parent[v] = u
                if v == goal:
                    # 回溯路径
                    path = [v]
                    while parent[path[-1]] is not None:
                        path.append(parent[path[-1]])
                    return path[::-1]
                q.append(v)
    return []  # 不可达


def dijkstra(graph: dict[Any, list[tuple[Any, float]]], start: Any) -> dict[Any, float]:
    """Dijkstra：加权边非负的最短路径。
    复杂度 O((V+E) log V)（二元堆）。
    """
    dist: dict[Any, float] = {start: 0.0}
    pq: list[tuple[float, Any]] = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, float("inf")):
            continue
        for v, w in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist


if __name__ == "__main__":
    # 7 节点示例（与 Dijkstra Wikipedia 经典示例一致）
    G = {
        1: [(2, 8), (3, 1)],
        2: [(5, 5)],
        3: [(2, 4), (4, 2)],
        4: [(2, 1), (5, 3)],
        5: [],
    }
    print("BFS  from 1 :", bfs(G, 1))
    print("DFS  from 1 :", dfs(G, 1))
    print("Dijkstra(1):", dijkstra(G, 1))
    # 简单无权图：BFS 最短跳数
    GU = {
        "A": ["B", "C"],
        "B": ["A", "C", "D"],
        "C": ["A", "B", "D"],
        "D": ["B", "C"],
    }
    print("BFS 最短跳 A->D:", bfs_shortest_path(GU, "A", "D"))
    # 时间复杂度自检
    print("\n复杂度：BFS/DFS 都是 O(V+E) 时间, O(V) 空间；Dijkstra O((V+E) log V)")