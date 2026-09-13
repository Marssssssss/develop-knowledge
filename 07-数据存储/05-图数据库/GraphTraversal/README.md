# 图遍历：BFS / DFS / Dijkstra 三件套

## 简介

图遍历是图算法的基石。本 demo 用 **C + Python + Go** 三种语言实现：
- **BFS（广度优先搜索）**：队列 (FIFO) 实现，按层扩展，可求无权图最短跳数。
- **DFS（深度优先搜索）**：栈 / 递归实现，一支走到底再回溯，适合路径枚举、环检测、拓扑排序。
- **Dijkstra（最短路径）**：加权边非负时用最小堆实现，O((V+E) log V)。

> 关键观察：BFS 和 DFS 在邻接表表示下都是 O(V+E) 时间，但内存模式截然不同（BFS 按宽度吃内存，DFS 按深度吃内存）。Wikipedia 与多家高校讲义均印证。

## 原理详解

### 1. BFS
```
BFS(graph, start):
    visited = {start}; queue = [start]
    while queue:
        u = queue.pop_front()
        for v in graph[u]:
            if v not in visited: visited.add(v); queue.append(v)
```
- 时间 O(V+E)，空间 O(V)
- 求无权图最短跳数：第一次到达目标即最短

### 2. DFS（递归版）
```
DFS(graph, u):
    visited.add(u)
    for v in graph[u]:
        if v not in visited: DFS(graph, v)
```
- 时间 O(V+E)，空间 O(V)（递归栈 = 图深度）
- 适合：路径枚举、环检测（白色/灰色/黑色三色）、拓扑排序

### 3. Dijkstra（最小堆版）
```
Dijkstra(graph, start):
    dist[start] = 0
    pq = [(0, start)]
    while pq:
        d, u = heap.pop_min()
        if d > dist[u]: continue        # 跳过陈旧条目
        for v, w in graph[u]:
            nd = d + w
            if nd < dist[v]: dist[v] = nd; heap.push((nd, v))
    return dist
```
- 时间 O((V+E) log V)，空间 O(V+E)
- **前提**：所有边权 ≥ 0；负权见 Bellman-Ford

### 4. 对比

| 算法 | 数据结构 | 时间 | 空间 | 适用场景 |
|---|---|---|---|---|
| BFS | 队列 | O(V+E) | O(V) | 无权图最短跳、连通分量 |
| DFS | 栈 / 递归 | O(V+E) | O(V) | 路径枚举、环检测、拓扑序 |
| Dijkstra | 最小堆 | O((V+E) log V) | O(V) | 非负加权最短路 |

> "DFS has the same asymptotic complexity as BFS on adjacency lists: time O(V+E), extra space O(V)" — Czech Technical University Programming for Engineers L8 Recursion

## 环境准备

- Python ≥ 3.8
- Go ≥ 1.21
- gcc (任意支持 C99 的版本)

## 运行方式

```bash
# Python
cd python && python graph_traversal_demo.py

# Go
cd go && go run graph_traversal_demo.go

# C
cd c && gcc -O2 -Wall -Wextra graph_traversal_demo.c -o demo && ./demo
```

## 关键代码片段

Python BFS 求无权图最短跳：
```python
def bfs_shortest_path(graph, start, goal):
    parent = {start: None}
    q = deque([start])
    while q:
        u = q.popleft()
        for v in graph.get(u, []):
            if v not in parent:
                parent[v] = u
                if v == goal:
                    path = [v]
                    while parent[path[-1]] is not None:
                        path.append(parent[path[-1]])
                    return path[::-1]
                q.append(v)
    return []
```

Python Dijkstra：
```python
def dijkstra(graph, start):
    dist = {start: 0.0}
    pq = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, float("inf")): continue
        for v, w in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist
```

C 手写最小堆（Dijkstra 用）：
```c
static void hswap(int i, int j) { HNode t = heap[i]; heap[i] = heap[j]; heap[j] = t; }
static void hup(int i) { while (i > 0) {
    int p = (i - 1) >> 1;
    if (heap[p].dist <= heap[i].dist) break;
    hswap(p, i); i = p;
}}
```

## 性能与边界

- BFS/DFS：邻接表 O(V+E)，邻接矩阵会变成 O(V²)。
- Dijkstra：O((V+E) log V)；用 Fibonacci 堆可优化到 O(E + V log V)，但常数大。
- 递归 DFS 在深链图（如链表）可能爆栈；可改显式栈版本。
- 7 节点加权示例来自 Wikipedia "Dijkstra's algorithm" 经典图。

## 注意事项与常见坑

- **BFS 求无权图最短跳**：一定要在**入队**时标记 visited，而不是出队时；否则同一节点可能被多次入队，复杂度退化。
- **DFS 环检测**：仅靠 `visited` 集合无法区分"沿 forward edge 重访"和"沿 back edge 成环"，需要三色（white/gray/black）法。
- **Dijkstra 负权失效**：边权出现负数时 Dijkstra 会给错答案，需要 Bellman-Ford 或 SPFA。
- **Python heap 跳过陈旧条目**：节点可能以较大 dist 入堆多次，出堆时 `if d > dist[u]: continue` 保证每个节点只被"真正确定"时处理一次。

## 参考资料

- [Wikipedia - Dijkstra's algorithm](https://en.wikipedia.org/wiki/Dijkstra%27s_algorithm) — 经典伪代码 + 优先级队列优化版
- [Czech Technical University - Programming for Engineers L8 Recursion](https://cw.fel.cvut.cz/b252/_media/courses/be5b33pge/lectures/l8_recursion.pdf) — BFS/DFS 复杂度对比与 grid search demo
- [algoarena.net - DFS vs BFS](https://algoarena.net/blog/dfs-vs-bfs) — frontier 视角（BFS=queue FIFO / DFS=stack LIFO）的对比
- [TIRA MOOC - 6. Shortest paths](https://tira.mooc.fi/autumn-2026-part2/chap06/) — Python heapq 实现 Dijkstra 的教学范例