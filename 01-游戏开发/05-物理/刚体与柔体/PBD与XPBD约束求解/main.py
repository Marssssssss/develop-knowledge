"""PBD 与 XPBD —— 基于位置的动力学与扩展版（柔度约束）。

公式逐条取自下列**实读**论文，代码与原文伪代码同构：

* Müller et al. 2006《Position Based Dynamics》（VRIPHYS / JVCIR 2007）
  - Algorithm 1 主循环 17 行：`v += dt*w*f_ext` → `dampVelocities` → `p = x + dt*v`
    → `generateCollisionConstraints` → 迭代投影 → `v = (p-x)/dt` → `x = p` → `velocityUpdate`。
    原文点明 (13)(14) 与 Verlet 积分"精确对应"，但显式保留速度更好操作。
  - 约束投影（3.3）：`C(p+Δp) ≈ C(p) + ∇C·Δp = 0`，取 `Δp = λ∇C(p)` 得
    `Δp = -C(p)/|∇C(p)|² · ∇C(p)`，即单个约束上的**牛顿-拉弗森步**。
    带逆质量：`s = C(p) / Σ_j w_j|∇_{p_j}C|²`，`Δp_i = -s·w_i·∇_{p_i}C`。
    距离约束的特例（原文式 10/11）：
    `Δp1 = -w1/(w1+w2)·(|p1-p2|-d)·n`，`Δp2 = +w2/(w1+w2)·(|p1-p2|-d)·n`。
  - 动量守恒（式 1/2）：内部约束必须满足 `Σ m_iΔp_i = 0` 与 `Σ r_i×m_iΔp_i = 0`，
    否则会引入 ghost force。
  - Gauss-Seidel（3.2）：逐个约束投影、修改**立刻对后续可见**，压力波能在一轮内传遍材料，
    因此比 Jacobi 收敛快得多；代价是**结果依赖求解顺序**，顺序不稳定会振荡。
  - 不等式约束：只有 `C < 0` 时才投影。
  - 全局阻尼（3.5）：先算 `xcm/vcm/L/I/ω`，再只阻尼各点速度**偏离整体运动**的部分
    `Δv_i = vcm + ω×r_i - v_i`，`k_damping = 1` 时整体退化成刚体。
* Macklin et al. 2016《XPBD: Position-Based Simulation of Compliant Constrained Dynamics》
  - 指出 PBD 把修正量直接乘 `k` 的副作用：**有效刚度同时依赖时间步与投影次数**；
    Müller 2007 的指数缩放只解决迭代次数、不解决时间步，且多约束下不收敛到确定解。
  - 能量势 `U = ½ Cᵀα⁻¹C`，`α` 是柔度（刚度的倒数），`α̃ = α/Δt²`。
  - Schur 补后：
    `Δλ_j = (-C_j(x) - α̃_j λ_j) / (∇C_j M⁻¹ ∇C_jᵀ + α̃_j)`（式 18）
    `Δx   = M⁻¹ ∇C(x)ᵀ Δλ`（式 17）
  - Algorithm 1 相对原 PBD 只多了 3 行：初始化 `λ=0`、按式 18 算 `Δλ`、累加 `λ`。
  - **α_j = 0 时 Δλ 恰好退化为原 PBD 的缩放因子 s_j**（原文明确写出）。
  - 带阻尼的扩展（式 26）：`γ_j = α̃_j β̃_j / Δt`，分母多乘 `(1+γ_j)`。

本文件只放模型，断言在 `selfcheck_pbd_xpbd.py`。
"""

from math import sqrt


# ------------------------------------------- 二维向量 -------------------------------------------

def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def mul(a, s):
    return (a[0] * s, a[1] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def length(a):
    return sqrt(dot(a, a))


def norm(a):
    l = length(a)
    if l == 0.0:
        return (0.0, 0.0)
    return (a[0] / l, a[1] / l)


def cross2(a, b):
    """2D 叉积的 z 分量。"""
    return a[0] * b[1] - a[1] * b[0]


def cross_wv(w, r):
    """2D：`ω × r`（把 ω 当作 z 轴标量）。"""
    return (-w * r[1], w * r[0])


# ------------------------------------------- 粒子与约束 -------------------------------------------

class Particle:
    __slots__ = ("x", "p", "v", "w")

    def __init__(self, x, inv_mass=1.0):
        self.x = tuple(x)
        self.p = tuple(x)
        self.v = (0.0, 0.0)
        self.w = inv_mass

    @property
    def m(self):
        return 0.0 if self.w == 0.0 else 1.0 / self.w


class DistanceConstraint:
    """`C(p1,p2) = |p1-p2| - d`，类型为 equality。"""

    def __init__(self, i, j, d):
        self.i = i
        self.j = j
        self.d = d
        self.lam = 0.0        # XPBD 的拉格朗日乘子
        self.type = "equality"


def constraint_value(ps, c):
    return length(sub(ps[c.i].p, ps[c.j].p)) - c.d


# --------------------------------------- PBD 投影（原文式 10/11） ---------------------------------------

def project_distance_pbd(a, b, d, k=1.0):
    """返回 `(Δp_a, Δp_b)`。`k ∈ [0,1]` 作为修正量的乘子（原文 3.3 末）。"""
    delta = sub(a.p, b.p)
    dist = length(delta)
    if dist < 1e-12:
        return ((0.0, 0.0), (0.0, 0.0))
    n = (delta[0] / dist, delta[1] / dist)
    wsum = a.w + b.w
    if wsum == 0.0:
        return ((0.0, 0.0), (0.0, 0.0))
    c = dist - d
    da = mul(n, -k * c * a.w / wsum)
    db = mul(n, k * c * b.w / wsum)
    return (da, db)


def solve_pbd(ps, cs, iterations, k=1.0, order=None, jacobi=False):
    """Gauss-Seidel（默认）或 Jacobi 迭代投影。返回本轮求解的约束遍历次数。"""
    idx = list(range(len(cs))) if order is None else list(order)
    steps = 0
    for _ in range(iterations):
        if jacobi:
            accum = [(0.0, 0.0)] * len(ps)
            for ci in idx:
                c = cs[ci]
                da, db = project_distance_pbd(ps[c.i], ps[c.j], c.d, k)
                accum[c.i] = add(accum[c.i], da)
                accum[c.j] = add(accum[c.j], db)
                steps += 1
            for pi, dv in enumerate(accum):
                ps[pi].p = add(ps[pi].p, dv)
        else:
            for ci in idx:
                c = cs[ci]
                da, db = project_distance_pbd(ps[c.i], ps[c.j], c.d, k)
                ps[c.i].p = add(ps[c.i].p, da)
                ps[c.j].p = add(ps[c.j].p, db)
                steps += 1
    return steps


# ---------------------------------------- XPBD 投影（原文式 17/18） ----------------------------------------

def project_distance_xpbd(a, b, d, alpha_tilde, lam):
    """返回 `(Δλ, Δp_a, Δp_b)`。

    `alpha_tilde` 就是原文的 `α̃ = α/Δt²`；`alpha_tilde = 0` 时退化为 PBD 的缩放因子。
    """
    delta = sub(a.p, b.p)
    dist = length(delta)
    if dist < 1e-12:
        return (0.0, (0.0, 0.0), (0.0, 0.0))
    n = (delta[0] / dist, delta[1] / dist)
    c = dist - d
    # ∇C_a = n, ∇C_b = -n  ->  ∇C M⁻¹ ∇Cᵀ = w_a + w_b
    denom = (a.w + b.w) + alpha_tilde
    if denom == 0.0:
        return (0.0, (0.0, 0.0), (0.0, 0.0))
    d_lam = (-c - alpha_tilde * lam) / denom      # 式 18
    da = mul(n, a.w * d_lam)                      # 式 17：Δx = M⁻¹∇CᵀΔλ
    db = mul(n, -b.w * d_lam)
    return (d_lam, da, db)


def solve_xpbd(ps, cs, iterations, alpha=0.0, dt=1.0 / 60.0):
    """XPBD：每步开始把 λ 清零，迭代中累加（原文 Algorithm 1 第 4/7/9 行）。"""
    alpha_tilde = alpha / (dt * dt)
    for c in cs:
        c.lam = 0.0
    for _ in range(iterations):
        for c in cs:
            d_lam, da, db = project_distance_xpbd(ps[c.i], ps[c.j], c.d, alpha_tilde, c.lam)
            c.lam += d_lam
            ps[c.i].p = add(ps[c.i].p, da)
            ps[c.j].p = add(ps[c.j].p, db)
    return [c.lam for c in cs]


# ------------------------------------- 主循环（原文 Algorithm 1） -------------------------------------

def step_pbd(ps, cs, dt=1.0 / 60.0, iterations=1, k=1.0, gravity=(0.0, -10.0),
             damping=0.0, jacobi=False):
    # (5) v += dt*w*f_ext
    for p in ps:
        if p.w > 0.0:
            p.v = add(p.v, mul(gravity, dt))
    # (6) dampVelocities
    if damping > 0.0:
        global_damping(ps, damping)
    # (7) p = x + dt*v
    for p in ps:
        p.p = add(p.x, mul(p.v, dt))
    # (9)-(11) 迭代投影约束
    solve_pbd(ps, cs, iterations, k, jacobi=jacobi)
    # (13)(14) v = (p-x)/dt ; x = p
    for p in ps:
        p.v = mul(sub(p.p, p.x), 1.0 / dt)
        p.x = p.p


def step_xpbd(ps, cs, dt=1.0 / 60.0, iterations=1, alpha=0.0,
              gravity=(0.0, -10.0), substeps=1):
    """带子步的 XPBD：原文/Müller 2020 推荐用多个小步代替多次迭代。"""
    h = dt / substeps
    for _ in range(substeps):
        for p in ps:
            if p.w > 0.0:
                p.v = add(p.v, mul(gravity, h))
        for p in ps:
            p.p = add(p.x, mul(p.v, h))
        solve_xpbd(ps, cs, iterations, alpha, h)
        for p in ps:
            p.v = mul(sub(p.p, p.x), 1.0 / h)
            p.x = p.p


# ------------------------------------- 全局阻尼（原文 3.5） -------------------------------------

def global_damping(ps, k_damping):
    """只阻尼"偏离整体刚体运动"的那部分速度；`k_damping=1` 时退化成刚体。"""
    if not ps:
        return (0.0, 0.0), 0.0
    total_m = sum(p.m for p in ps)
    if total_m == 0.0:
        return (0.0, 0.0), 0.0
    xcm = mul((sum(p.m * p.x[0] for p in ps), sum(p.m * p.x[1] for p in ps)), 1.0 / total_m)
    vcm = mul((sum(p.m * p.v[0] for p in ps), sum(p.m * p.v[1] for p in ps)), 1.0 / total_m)
    r = [sub(p.x, xcm) for p in ps]
    L = sum(p.m * cross2(r[i], ps[i].v) for i, p in enumerate(ps))
    I = sum(p.m * dot(r[i], r[i]) for i, p in enumerate(ps))
    omega = 0.0 if I == 0.0 else L / I
    for i, p in enumerate(ps):
        rigid_v = add(vcm, cross_wv(omega, r[i]))
        dv = sub(rigid_v, p.v)
        p.v = add(p.v, mul(dv, k_damping))
    return vcm, omega
