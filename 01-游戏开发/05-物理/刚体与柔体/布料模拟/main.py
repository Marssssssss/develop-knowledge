"""布料模拟 —— 三角形网格布料的两类约束（拉伸 + 弯曲）与 XPBD 求解。模型部分。

模块划分：
- `mesh.py`    向量运算 / `findTriNeighbors`（官方版 + 修正版）/ 网格生成
- `bending.py` PBD 2006 §4.1 的二面角弯曲（mixin）
- `main.py`    `Cloth` 类：质量、约束登记、距离约束求解、主循环、度量

两套弯曲模型在本 demo 中对照：
A. Ten Minute Physics（`14-cloth.html` 官方 JS 实读）—— 弯曲退化成
   **对角顶点之间的距离约束**，静长取初始距离，便宜且稳定。
B. Müller 2006 §4.1 —— 弯曲是**二面角** `acos(n1·n2) - φ0`，与拉伸无关。

官方 JS 关键参数与流程：
- `bendingCompliance` 默认 **1.0**，`stretchingCompliance` 默认 **0.0**（布不可伸长）。
- 求解：`alpha = compliance/dt/dt`，`s = -C / (w + alpha)`，
  `pos[id0] += grad*s*w0`，`pos[id1] += grad*(-s*w1)`。
  （**不累加 λ** 的简化 XPBD 变体，与 Macklin 2016 式 18 不同。）
- 主循环 `sdt = dt / numSubsteps`（默认 **15** 子步），每子步 `preSolve → solve → postSolve`。
- `preSolve` 地面碰撞是**位置投影**：`y < 0` 就退回 `prevPos` 再置 `y = 0`。
- `postSolve`：`v = (pos - prevPos) / dt`。

断言在 `selfcheck_cloth.py`。
"""

from mesh import (  # noqa: F401
    vadd, vsub, vmul, vdot, vlen, vnorm, vcross,
    find_tri_neighbors, find_tri_neighbors_robust, make_grid,
)
from bending import DihedralMixin


class Cloth(DihedralMixin):
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

        # 约束登记
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


if __name__ == "__main__":
    p, t = make_grid(3, 3, y0=1.0)
    c = Cloth(p, t, pin_ids=(0, 2))
    c.simulate(1.0 / 60.0, substeps=5)
    print("顶点数 %d 三角形数 %d 拉伸约束 %d 弯曲约束 %d"
          % (c.num_particles, len(t) // 3, len(c.stretch_ids), len(c.bend_ids)))
    print("总质量 %.6f  最大拉伸误差 %.3e" % (c.total_mass(), c.max_stretch_error()))
    print("最低顶点 y = %.6f" % min(q[1] for q in c.pos))
