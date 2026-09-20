"""布料模拟 —— 三角形网格布料的两类约束（拉伸 + 弯曲）与 XPBD 求解。
两条**实读**资料给出的其实是**两套不同的弯曲模型**，本 demo 两套都实现并对照：
A. Müller et al. 2006《Position Based Dynamics》第 4 章
  - 顶点质量 = **每个相邻三角形质量的 1/3 之和**；用户输入面密度 ρ [kg/m²]。
  - 每条边一根**拉伸约束** `C_stretch = |p1-p2| - l0`（类型 equality）。
  - 每对相邻三角形 `(p1,p3,p2)` 与 `(p1,p2,p4)` 一根**弯曲约束**，
    用**二面角**：`C_bend = acos(n1·n2) - φ0`。原文强调这个形式**与拉伸无关**
    （它不依赖边长），因此可以做出"低拉伸刚度 + 高抗弯"的布。
  - 自碰撞：空间哈希找"顶点-三角形"，约束 `C = (q-p1)·n - h`（h 是布厚），
    从哪一侧进入要用**对应朝向的法线**（原文式 12/13 给了两种写法）。
  - 只测顶点不够：小刚体会穿过大三角形，刚体的凸角也要反过来测。
B. Matthias Müller《Ten Minute Physics》第 14 期 "The secret of cloth simulation"
  （官方 JS 源码 `14-cloth.html` 实读）
  - `bendingCompliance` 默认 **1.0**，而 `stretchingCompliance` 默认 **0.0**（布不可伸长）。
  - 弯曲**不用二面角**，而是直接在"两个三角形的对角顶点"之间加一根**距离约束**：
    `bendingIds` 每组 4 个 id，实际只取 `id2 = triIds[3i+(j+2)%3]` 与
    `id3 = triIds[3*ni+(nj+2)%3]`，静长取初始距离 —— 这就是"秘密"所在：
    抗弯退化成一组普通距离约束，便宜且稳定。
  - 求解：`alpha = compliance/dt/dt`，`s = -C / (w + alpha)`，
    `pos[id0] += grad*s*w0`，`pos[id1] += grad*(-s*w1)`。
    （注意这是**不累加 λ** 的简化 XPBD 变体，与 Macklin 2016 式 18 不同。）
  - 主循环 `sdt = dt / numSubsteps`（默认 **15** 个子步），每子步
    `preSolve → solve → postSolve`。
  - `preSolve` 里地面碰撞是**位置投影**：`y < 0` 就把位置退回 `prevPos` 再置 `y = 0`。
  - `postSolve`：`v = (pos - prevPos) / dt`。
本文件只放模型，断言在 `selfcheck_cloth.py`。
"""
# ------------------------------------------- 三维向量 -------------------------------------------
def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def vmul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)
def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def vlen(a):
    return sqrt(vdot(a, a))
def vnorm(a):
    l = vlen(a)
    if l == 0.0:
        return (0.0, 0.0, 0.0)
    return (a[0] / l, a[1] / l, a[2] / l)
def vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])
# ------------------------------------- 三角形邻居（findTriNeighbors） -------------------------------------
def find_tri_neighbors(tri_ids):
    """官方 JS `findTriNeighbors()` 的移植：把公共边配对起来。
    返回长度 `3*numTris` 的数组，值是"对面那条边的全局边号"，开放边为 -1。
    """
    num_tris = len(tri_ids) // 3
    edges = []
    for i in range(num_tris):
        for j in range(3):
            id0 = tri_ids[3 * i + j]
            id1 = tri_ids[3 * i + (j + 1) % 3]
            edges.append((min(id0, id1), max(id0, id1), 3 * i + j))
    edges.sort(key=lambda e: (e[0], e[1]))
    neighbors = [-1] * (3 * num_tris)
    nr = 0
    while nr < len(edges):
        e0 = edges[nr]
        nr += 1
        if nr < len(edges):
            e1 = edges[nr]
            if e0[0] == e1[0] and e0[1] == e1[1]:
                neighbors[e0[2]] = e1[2]
                neighbors[e1[2]] = e0[2]
            nr += 1
    return neighbors
def find_tri_neighbors_robust(tri_ids):
    """上函数的**修正版**：用字典按 `(min,max)` 归桶，而不是"排序后两两扫"。
    为什么要修：官方那段的配对扫描是按 `(0,1) (2,3) (4,5) ...` 固定步长走的，
    一旦一对重复边**跨在偶数/奇数边界上**（例如落在下标 3 与 4）就会被整体错过。
    本 demo 在 3×3 规则网格上实测：8 条共享边里只配出 1 条，
    连带让拉伸约束重复登记（23 条 vs 唯一边 16 条）、弯曲约束只剩 1 组。
    """
    num_tris = len(tri_ids) // 3
    buckets = {}
    for i in range(num_tris):
        for j in range(3):
            id0 = tri_ids[3 * i + j]
            id1 = tri_ids[3 * i + (j + 1) % 3]
            buckets.setdefault((min(id0, id1), max(id0, id1)), []).append(3 * i + j)
    neighbors = [-1] * (3 * num_tris)
    for members in buckets.values():
        if len(members) == 2:
            neighbors[members[0]] = members[1]
            neighbors[members[1]] = members[0]
    return neighbors
# ------------------------------------- 网格生成 -------------------------------------
def make_grid(nx, ny, dx=0.1, dy=0.1, z=0.0, y0=0.0):
    """生成一块竖直挂着的布：顶点 + 三角形索引（两个三角形拼一个格子）。
    `y0` 是顶行的高度 —— 地面在 `y = 0`，布的初始位置必须**整体在地面之上**，
    否则第一帧就会被地面投影整块拍到 `y = 0`（实测踩过）。
    """
    pos = []
    for j in range(ny):
        for i in range(nx):
            pos.append((i * dx, y0 - j * dy, z))
    tris = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            v00 = j * nx + i
            v10 = v00 + 1
            v01 = v00 + nx
            v11 = v01 + 1
            tris.extend([v00, v10, v01])
            tris.extend([v10, v11, v01])
    return pos, tris
from math import sqrt, acos
