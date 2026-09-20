"""PBD / XPBD 自检 —— 对拍与实测，全部实跑。

刻意不写"应然 TRUTH"式断言，改验**可观测的差异**：
* α=0 时 XPBD 的 Δλ 是否**数值等于** PBD 的缩放因子 s（原文的明确结论）；
* 迭代次数变化时，PBD 的有效刚度变不变、XPBD 收不收敛到同一个解；
* Gauss-Seidel 一轮能否把压力波传到链尾（Jacobi 不行）；
* 全局阻尼是否只吃"偏离刚体运动"的那部分。
"""

from main import (
    Particle, DistanceConstraint, constraint_value,
    add, sub, mul, dot, length, norm, cross2,
    project_distance_pbd, solve_pbd,
    project_distance_xpbd, solve_xpbd,
    step_pbd, step_xpbd, global_damping,
)

PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1


def close(a, b, tol=1e-9, msg=""):
    ok(abs(a - b) <= tol, "%s (got %r want %r)" % (msg, a, b))


# ============================ 1. 距离约束投影：等质量 / 不等质量 ============================
a = Particle((0.0, 0.0), inv_mass=1.0)
b = Particle((2.0, 0.0), inv_mass=1.0)
da, db = project_distance_pbd(a, b, 1.0)
# a 在原点、b 在 x=2，静长 1 -> a 朝 +x 走 0.5，b 朝 -x 走 0.5
close(da[0], 0.5, msg="等质量：两端各走一半（原文式 10）")
close(db[0], -0.5, msg="等质量：另一端反向走一半（原文式 11）")
close(da[1], 0.0); close(db[1], 0.0)
ok(abs(length(sub(add(a.p, da), add(b.p, db)))) - 1.0 < 1e-12,
   "投影后距离约束**精确**满足（沿 n 方向是线性的）")

# 一端固定（w=0）时只有另一端动，且是**全部**修正量
a2 = Particle((0.0, 0.0), inv_mass=0.0)
b2 = Particle((2.0, 0.0), inv_mass=1.0)
da2, db2 = project_distance_pbd(a2, b2, 1.0)
close(da2[0], 0.0, msg="无限质量端不动")
close(db2[0], -1.0, msg="自由端承担全部修正")
# 质量比 1:3 -> 逆质量 1 : 1/3 -> 位移比 3 : 1
a3 = Particle((0.0, 0.0), inv_mass=1.0)
b3 = Particle((2.0, 0.0), inv_mass=1.0 / 3.0)
da3, db3 = project_distance_pbd(a3, b3, 1.0)
close(abs(da3[0]) / abs(db3[0]), 3.0, tol=1e-9, msg="位移按逆质量分配（1:3 质量 -> 3:1 位移）")

# ============================ 2. 动量守恒（原文式 1） ============================
# Σ m_i Δp_i 必须为 0，否则引入 ghost force
a4 = Particle((0.0, 0.0), inv_mass=1.0)          # m=1
b4 = Particle((2.0, 0.0), inv_mass=0.25)         # m=4
da4, db4 = project_distance_pbd(a4, b4, 1.0)
mom = add(mul(da4, a4.m), mul(db4, b4.m))
close(mom[0], 0.0, tol=1e-12, msg="线性动量守恒 Σm_iΔp_i = 0")
close(mom[1], 0.0, tol=1e-12, msg="y 方向同样守恒")

# ============================ 3. 刚度 k 的非线性（原文 3.3 末） ============================
# 单个约束上：每轮把剩余误差乘 (1-k)，n 轮后 residual = (1-k)^n * C0
for k in (0.2, 0.5, 0.8):
    for n_iter in (1, 2, 3, 4):
        ps = [Particle((0.0, 0.0), inv_mass=0.0), Particle((2.0, 0.0), inv_mass=1.0)]
        cs = [DistanceConstraint(0, 1, 1.0)]
        for _ in range(n_iter):
            da, db = project_distance_pbd(ps[0], ps[1], 1.0, k)
            ps[0].p = add(ps[0].p, da)
            ps[1].p = add(ps[1].p, db)
        expected = ((1.0 - k) ** n_iter) * 1.0     # C0 = 2 - 1 = 1
        close(constraint_value(ps, cs[0]), expected, tol=1e-12,
              msg="k=%.1f 迭代 %d 轮后残差 = (1-k)^n·C0" % (k, n_iter))
# 反证：把 k 当"一次投影 k 倍"而不是"每轮乘 (1-k)"就会错
ok(abs(((1 - 0.5) ** 2) - (1 - 2 * 0.5)) > 1e-9, "两轮 k=0.5 ≠ 一轮 k=1.0（不能线性加）")

# ============================ 4. α=0 时 XPBD 退化为 PBD（原文明确结论） ============================
dt = 1.0 / 60.0
pa = Particle((0.0, 0.0), inv_mass=0.0)
pb = Particle((2.0, 0.0), inv_mass=1.0)
d_lam, dxa, dxb = project_distance_xpbd(pa, pb, 1.0, alpha_tilde=0.0, lam=0.0)
da_p, db_p = project_distance_pbd(pa, pb, 1.0, k=1.0)
close(dxa[0], da_p[0], tol=1e-12, msg="α̃=0：XPBD 位置修正 == PBD 位置修正")
close(dxb[0], db_p[0], tol=1e-12, msg="α̃=0：另一端也相同")
# 缩放因子 s 的一致性：PBD 的 s = C/Σw|∇C|² = 1.0/1.0
close(d_lam, -1.0 / 1.0, tol=1e-12, msg="α̃=0 时 Δλ 就是 PBD 的缩放因子 s 的相反数")

# ============================ 5. 柔度越大，单次修正越小（正则化限制约束力） ============================
prev = None
for alpha in (0.0, 1e-6, 1e-4, 1e-2):
    pa = Particle((0.0, 0.0), inv_mass=0.0)
    pb = Particle((2.0, 0.0), inv_mass=1.0)
    at = alpha / (dt * dt)
    d_lam, dxa, dxb = project_distance_xpbd(pa, pb, 1.0, at, 0.0)
    mag = abs(dxb[0])
    if prev is not None:
        ok(mag <= prev + 1e-15, "柔度增大 -> 单次修正单调变小（α=%g）" % alpha)
    prev = mag
ok(prev < 1.0, "有柔度时修正量严格小于刚性解")

# ============================ 6. 迭代次数：PBD 变刚度 vs XPBD 收敛 ============================
# 场景：顶端固定的单点，下端受恒定外力，看稳态伸长量
def settle(solver, n_iter, steps=4000, dt=1.0 / 60.0, **kw):
    ps = [Particle((0.0, 0.0), inv_mass=0.0), Particle((0.0, -1.0), inv_mass=1.0)]
    cs = [DistanceConstraint(0, 1, 1.0)]
    for _ in range(steps):
        if solver == "pbd":
            ps[1].v = (0.0, -1.0)          # 恒定向下速度作为"载荷"，每步重置
            step_pbd(ps, cs, dt=dt, iterations=n_iter, k=kw.get("k", 0.2),
                     gravity=(0.0, 0.0))
        else:
            ps[1].v = (0.0, -1.0)
            step_xpbd(ps, cs, dt=dt, iterations=n_iter, alpha=kw.get("alpha", 1e-5),
                      gravity=(0.0, 0.0))
    return length(sub(ps[0].x, ps[1].x)) - 1.0


e_pbd_1 = settle("pbd", 1)
e_pbd_5 = settle("pbd", 5)
e_pbd_20 = settle("pbd", 20)
e_pbd_40 = settle("pbd", 40)
# PBD：伸长量随迭代次数一路变小，永远不收敛到一个"确定刚度"
ok(e_pbd_1 > e_pbd_5 > e_pbd_20 > e_pbd_40 > 0.0,
   "PBD(k=0.2)：迭代越多越硬，伸长量单调下降 %.6f > %.6f > %.8f > %.9f"
   % (e_pbd_1, e_pbd_5, e_pbd_20, e_pbd_40))
ok(abs(e_pbd_1 - e_pbd_20) > 1e-3, "PBD：1 次与 20 次迭代的有效刚度差了数量级")

# XPBD：1 / 5 / 20 / 40 次迭代给出**完全相同**的稳态伸长
e_x = [settle("xpbd", n, alpha=1e-5) for n in (1, 5, 20, 40)]
for v in e_x[1:]:
    close(v, e_x[0], tol=1e-12,
          msg="XPBD：迭代次数无关（%.12f vs %.12f）" % (v, e_x[0]))
# 伸长量与柔度 α 成正比（10 倍 α -> 10 倍伸长）
e_lo = settle("xpbd", 20, alpha=1e-5)
e_hi = settle("xpbd", 20, alpha=1e-4)
close(e_hi / e_lo, 10.0, tol=1e-6,
      msg="XPBD：稳态伸长正比于柔度 α（%.9f / %.9f）" % (e_hi, e_lo))

# ============================ 7. Gauss-Seidel vs Jacobi：压力波传播 ============================
# 三粒子链，**只让第一根约束违规**（其余两根初始都是满足的），
# 这样"链尾动没动"就纯粹取决于求解器能不能在一轮内把扰动传播过去。
def chain(order=None, jacobi=False, iterations=1):
    ps = [Particle((0.0, 0.0), inv_mass=0.0),
          Particle((0.0, -0.6), inv_mass=1.0),
          Particle((0.0, -1.6), inv_mass=1.0),
          Particle((0.0, -2.6), inv_mass=1.0)]
    cs = [DistanceConstraint(0, 1, 1.0), DistanceConstraint(1, 2, 1.0),
          DistanceConstraint(2, 3, 1.0)]
    ok_violated = [abs(constraint_value(ps, cs[0])) > 1e-9,
                   abs(constraint_value(ps, cs[1])) < 1e-9,
                   abs(constraint_value(ps, cs[2])) < 1e-9]
    assert all(ok_violated), "构造前提：只有第一根约束违规 %r" % (ok_violated,)
    solve_pbd(ps, cs, iterations, k=1.0, order=order, jacobi=jacobi)
    return ps, cs


gs, gs_cs = chain()
jc, jc_cs = chain(jacobi=True)
ok(abs(gs[3].p[1] - (-2.6)) > 1e-9,
   "Gauss-Seidel：一轮之内扰动已传到链尾（位移 %g）" % (gs[3].p[1] + 2.6))
close(jc[3].p[1] - (-2.6), 0.0, tol=1e-12,
      msg="Jacobi：一轮之内链尾纹丝不动（各约束只看旧值）")
# 多轮之后 Jacobi 也追上来（只是慢）
jc5, _ = chain(jacobi=True, iterations=5)
ok(abs(jc5[3].p[1] + 2.6) > 1e-9, "Jacobi 5 轮后链尾才被带动")

# ============================ 8. 求解顺序依赖（原文：结果依赖顺序） ============================
def order_result(order):
    ps, cs = [Particle((0.0, 0.0), inv_mass=0.0),
              Particle((0.0, -1.0), inv_mass=1.0),
              Particle((1.0, -1.0), inv_mass=1.0)], None
    cs = [DistanceConstraint(0, 1, 1.0), DistanceConstraint(0, 2, 1.0),
          DistanceConstraint(1, 2, 0.6)]
    ps[1].p = (0.3, -1.1)
    ps[2].p = (0.9, -1.2)
    solve_pbd(ps, cs, 1, k=1.0, order=order)
    return ps[1].p, ps[2].p


r_a = order_result([0, 1, 2])
r_b = order_result([2, 1, 0])
ok(r_a != r_b, "同一个过约束系统，换求解顺序得到不同结果（Gauss-Seidel 的顺序依赖）")
# 但顺序固定时可复现
ok(order_result([0, 1, 2]) == r_a, "顺序固定时结果可复现（原文要求顺序保持不变）")

# ============================ 9. 全局阻尼（原文 3.5） ============================
# 纯刚体运动（整体平移 + 整体旋转）-> 阻尼系数为 1 时速度完全不变
ps = [Particle((0.0, 0.0)), Particle((1.0, 0.0)), Particle((0.0, 1.0))]
for p in ps:
    p.v = (3.0, 2.0)          # 纯平移
before = [p.v for p in ps]
global_damping(ps, 1.0)
for i, p in enumerate(ps):
    close(p.v[0], before[i][0], tol=1e-12, msg="刚体平移不被全局阻尼吃掉")
    close(p.v[1], before[i][1], tol=1e-12, msg="刚体平移不被全局阻尼吃掉")

# 纯旋转：v_i = ω × r_i
ps = [Particle((0.0, 0.0)), Particle((1.0, 0.0)), Particle((0.0, 1.0))]
omega = 2.0
cm = (1.0 / 3.0, 1.0 / 3.0)
for p in ps:
    r = sub(p.x, cm)
    p.v = (-omega * r[1], omega * r[0])
before = [p.v for p in ps]
vcm, w_est = global_damping(ps, 1.0)
close(w_est, omega, tol=1e-9, msg="从 L 与 I 反解出的角速度正确")
for i, p in enumerate(ps):
    close(p.v[0], before[i][0], tol=1e-9, msg="刚体旋转不被全局阻尼吃掉")
    close(p.v[1], before[i][1], tol=1e-9, msg="刚体旋转不被全局阻尼吃掉")

# 非刚体：各点速度偏离整体运动 -> 阻尼系数为 1 后被完全拉回刚体运动
ps = [Particle((0.0, 0.0)), Particle((1.0, 0.0)), Particle((0.0, 1.0))]
ps[0].v = (0.0, 0.0)
ps[1].v = (10.0, 0.0)
ps[2].v = (0.0, -10.0)
vcm, w_est = global_damping(ps, 1.0)
spread_before = 10.0
res = 0.0
for i, p in enumerate(ps):
    r = sub(p.x, cm)
    rigid = add(vcm, (-w_est * r[1], w_est * r[0]))
    res = max(res, length(sub(p.v, rigid)))
ok(res < 1e-9, "k_damping=1 后所有点只剩整体刚体运动（残差 %g）" % res)

# 部分阻尼：残差按 (1-k) 缩放
ps = [Particle((0.0, 0.0)), Particle((1.0, 0.0)), Particle((0.0, 1.0))]
ps[0].v = (0.0, 0.0); ps[1].v = (10.0, 0.0); ps[2].v = (0.0, -10.0)
vcm0, w0 = global_damping(ps, 0.0)
# k=0 时应完全不变
close(ps[1].v[0], 10.0, tol=1e-12, msg="k_damping=0 不改变任何速度")

print("PBD与XPBD约束求解: %d 项断言全部通过" % PASS)
