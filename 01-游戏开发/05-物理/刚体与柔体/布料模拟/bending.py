"""布料模拟 —— PBD 2006 §4.1 的**二面角弯曲**约束（与 Ten Minute Physics 的距离式弯曲对照）。

Müller et al. 2006《Position Based Dynamics》第 4 章给布料用的是二面角形式：
每对相邻三角形 `(p1,p3,p2)` 与 `(p1,p2,p4)` 共享边 `p1-p2`，约束为

    C_bend = acos(n1 · n2) - φ0 ，  n1 = normalize(e × (p3-p1))， n2 = normalize(e × (p4-p1))， e = p2-p1

原文强调这个形式**与拉伸无关**（它不依赖边长），因此可以做出
"低拉伸刚度 + 高抗弯" 的布 —— 这是它相对"对角顶点距离约束"的核心优势。

数值要点（本 demo 实测）：
1. 梯度用**中心差分**（h = 1e-6）求，而不是手推解析式 —— 二面角梯度的解析形式
   冗长易错，差分在 1e-6 步长下精度足够。
2. 投影必须按 §3.3 的**共享缩放因子**：`s = C / Σ_j w_j|∇_{p_j}C|²`，
   `Δp_i = -s·w_i·∇_{p_i}C`。四个顶点**共用同一个 s**；逐顶点各做一次完整牛顿步
   会严重过冲（实测折叠角从 0.615 恶化到 1.670，越解越坏）。
3. 初始二面角 φ0 **不是 0**：规则网格上相邻三角形位于公共边两侧，
   n1·n2 = -1 → φ0 = π。这个口径必须显式记下来，否则会以为弯折被"拉平"了。
"""


class DihedralMixin:
    """挂到 `Cloth` 上的二面角弯曲能力。假设宿主已提供 `bend_ids` / `pos` / `inv_mass`。"""

    def _phi_of(self, k):
        p1, p2, p3, p4 = self.bend_ids[k]
        e = vsub(self.pos[p2], self.pos[p1])
        n1 = vnorm(vcross(e, vsub(self.pos[p3], self.pos[p1])))
        n2 = vnorm(vcross(e, vsub(self.pos[p4], self.pos[p1])))
        return acos(max(-1.0, min(1.0, vdot(n1, n2))))

    def _init_dihedral_rest(self):
        self._dihedral_rest = [self._phi_of(k) for k in range(len(self.bend_ids))]

    def _dihedral(self, k, pos=None):
        """返回 `(C, {顶点: 数值梯度})`，梯度用中心差分（h = 1e-6）。"""
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
        """按 §3.3 的**共享缩放因子**做一次二面角投影。

        `s = stiffness · C / Σ_j w_j|∇_{p_j}C|²`，`Δp_i = -s·w_i·∇_{p_i}C`。
        注意 `dt` 在此未参与：这是**纯 PBD**（无 compliance）投影，每子步调用一次，
        刚度随子步数变化 —— 与 XPBD 的距离约束（α = compliance/dt²）不是同一族。
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


# 依赖 mesh.py 的向量运算；放在文件末尾可避免 `from bending import *` 时的循环噪声
from mesh import vsub, vmul, vdot, vlen, vnorm, vcross  # noqa: E402,F401
from math import acos  # noqa: E402
