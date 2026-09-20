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

from math import sqrt, acos, pi


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


# ------------------------------------- 布料 -------------------------------------

class Cloth:
    """官方 JS `class Cloth` 的 Python 移植 + PBD 2006 的二面角弯曲（可选）。"""

    def __init__(self, pos, tris, density=1.0, bending_compliance=1.0,
                 stretching_compliance=0.0, pin_ids=(),
                 neighbor_finder=find_tri_neighbors):
        self.num_particles = len(pos)
        self.pos = list(pos)
        self.prev = list(pos)
        self.vel = [(0.0, 0.0, 0.0)] * self.num_particles
        self.tris = list(tris)

        # 质量：PBD 2006 §4.1 —— 顶点质量 = 每个相邻三角形质量的 1/3 之和
        self.mass = [0.0] * self.num_particles
        for t in range(len(self.tris) // 3):
            a, b, c = self.tris[3 * t], self.tris[3 * t + 1], self.tris[3 * t + 2]
            area = 0.5 * vlen(vcross(vsub(self.pos[b], self.pos[a]),
                                     vsub(self.pos[c], self.pos[a])))
            tri_mass = density * area
            for v in (a, b, c):
                self.mass[v] += tri_mass / 3.0
        self.inv_mass = [0.0 if m == 0.0 else 1.0 / m for m in self.mass]
        for pid in pin_ids:
            self.inv_mass[pid] = 0.0        # 原文 3.6：抓取/固定点把逆质量置 0

        # 约束
        neighbors = neighbor_finder(self.tris)
        self.stretch_ids = []
        self.bend_ids = []
        num_tris = len(self.tris) // 3
        for i in range(num_tris):
            for j in range(3):
                id0 = self.tris[3 * i + j]
                id1 = self.tris[3 * i + (j + 1) % 3]
                n = neighbors[3 * i + j]
                if n < 0 or id0 < id1:      # 每条边只登记一次
                    self.stretch_ids.append((id0, id1))
                if n >= 0:                  # 相邻三角形对 -> 弯曲约束
                    ni, nj = n // 3, n % 3
                    id2 = self.tris[3 * i + (j + 2) % 3]
                    id3 = self.tris[3 * ni + (nj + 2) % 3]
                    self.bend_ids.append((id0, id1, id2, id3))
        self.stretch_rest = [vlen(vsub(self.pos[a], self.pos[b]))
                             for a, b in self.stretch_ids]
        self.bend_rest = [vlen(vsub(self.pos[c], self.pos[d]))
                          for _a, _b, c, d in self.bend_ids]

        self.stretching_compliance = stretching_compliance
        self.bending_compliance = bending_compliance
        self.num_substeps = 15              # 官方默认值

    # ---------------- 求解 ----------------

    def _solve_distance_set(self, id_pairs, rest, compliance, dt):
        alpha = compliance / dt / dt
        for k, (id0, id1) in enumerate(id_pairs):
            w0 = self.inv_mass[id0]
            w1 = self.inv_mass[id1]
            w = w0 + w1
            if w == 0.0:
                continue
            grad = vsub(self.pos[id0], self.pos[id1])
            ln = vlen(grad)
            if ln == 0.0:
                continue
            grad = vmul(grad, 1.0 / ln)
            c = ln - rest[k]
            s = -c / (w + alpha)
            self.pos[id0] = vadd(self.pos[id0], vmul(grad, s * w0))
            self.pos[id1] = vadd(self.pos[id1], vmul(grad, -s * w1))

    def solve(self, dt):
        """官方 `solve()`：先拉伸后弯曲，顺序固定。"""
        self._solve_distance_set(self.stretch_ids, self.stretch_rest,
                                 self.stretching_compliance, dt)
        bend_pairs = [(c, d) for _a, _b, c, d in self.bend_ids]
        self._solve_distance_set(bend_pairs, self.bend_rest,
                                 self.bending_compliance, dt)

    def _dihedral(self, k, pos=None):
        """返回 `(C, {顶点: 数值梯度})`，梯度用中心差分（h=1e-6）。"""
        p1, p2, p3, p4 = self.bend_ids[k]
        phi0 = self._dihedral_rest[k]
        saved = dict((v, self.pos[v]) for v in (p1, p2, p3, p4))
        if pos is not None:
            for v, val in pos.items():
                self.pos[v] = val
        c = self._phi_of(k) - phi0
        grads = {}
        h = 1e-6
        for idx in (p1, p2, p3, p4):
            base = self.pos[idx]
            g = []
            for axis in range(3):
                plus = list(base)
                minus = list(base)
                plus[axis] += h
                minus[axis] -= h
                self.pos[idx] = tuple(plus)
                cp = self._phi_of(k) - phi0
                self.pos[idx] = tuple(minus)
                cm = self._phi_of(k) - phi0
                self.pos[idx] = base
                g.append((cp - cm) / (2.0 * h))
            grads[idx] = tuple(g)
        for v, val in saved.items():
            self.pos[v] = val
        return c, grads

    def solve_dihedral(self, dt, stiffness=1.0):
        """PBD 2006 §4.1 的二面角弯曲，按 §3.3 的**共享缩放因子**投影：

        `s = C / Σ_j w_j|∇_{p_j}C|²`，`Δp_i = -s·w_i·∇_{p_i}C`。
        四个顶点必须**一起**用同一个 s —— 逐顶点各自做一次完整牛顿步会严重过冲
        （本 demo 实测：折叠角不但没被拉回去，反而从 0.615 恶化到 1.670）。
        """
        for k in range(len(self.bend_ids)):
            c, grads = self._dihedral(k)
            if abs(c) < 1e-12:
                continue
            denom = 0.0
            for idx, g in grads.items():
                denom += self.inv_mass[idx] * vdot(g, g)
            if denom == 0.0:
                continue
            s = stiffness * c / denom
            for idx, g in grads.items():
                self.pos[idx] = vsub(self.pos[idx], vmul(g, s * self.inv_mass[idx]))

    def _phi_of(self, k):
        p1, p2, p3, p4 = self.bend_ids[k]
        e = vsub(self.pos[p2], self.pos[p1])
        n1 = vnorm(vcross(e, vsub(self.pos[p3], self.pos[p1])))
        n2 = vnorm(vcross(e, vsub(self.pos[p4], self.pos[p1])))
        return acos(max(-1.0, min(1.0, vdot(n1, n2))))

    def _init_dihedral_rest(self):
        self._dihedral_rest = [self._phi_of(k) for k in range(len(self.bend_ids))]

    # ---------------- 主循环 ----------------

    def pre_solve(self, dt, gravity=(0.0, -9.81, 0.0)):
        for i in range(self.num_particles):
            if self.inv_mass[i] == 0.0:
                continue
            self.vel[i] = vadd(self.vel[i], vmul(gravity, dt))
            self.prev[i] = self.pos[i]
            self.pos[i] = vadd(self.pos[i], vmul(self.vel[i], dt))
            if self.pos[i][1] < 0.0:            # 地面：位置投影
                self.pos[i] = self.prev[i]
                self.pos[i] = (self.pos[i][0], 0.0, self.pos[i][2])

    def post_solve(self, dt):
        for i in range(self.num_particles):
            if self.inv_mass[i] == 0.0:
                continue
            self.vel[i] = vmul(vsub(self.pos[i], self.prev[i]), 1.0 / dt)

    def simulate(self, dt, substeps=None, gravity=(0.0, -9.81, 0.0),
                 dihedral=False):
        n = self.num_substeps if substeps is None else substeps
        if dihedral and not hasattr(self, "_dihedral_rest"):
            self._init_dihedral_rest()
        sdt = dt / n
        for _ in range(n):
            self.pre_solve(sdt, gravity)
            if dihedral:
                self._solve_distance_set(self.stretch_ids, self.stretch_rest,
                                         self.stretching_compliance, sdt)
                self.solve_dihedral(sdt)
            else:
                self.solve(sdt)
            self.post_solve(sdt)

    # ---------------- 度量 ----------------

    def max_stretch_error(self):
        e = 0.0
        for k, (a, b) in enumerate(self.stretch_ids):
            e = max(e, abs(vlen(vsub(self.pos[a], self.pos[b])) - self.stretch_rest[k]))
        return e

    def max_bend_error(self):
        e = 0.0
        for k, (_a, _b, c, d) in enumerate(self.bend_ids):
            e = max(e, abs(vlen(vsub(self.pos[c], self.pos[d])) - self.bend_rest[k]))
        return e

    def total_mass(self):
        return sum(self.mass)
