"""接触与冲量求解自检 —— Box2D 常量复算 + 求解器语义实测，全部实跑。"""

from math import pi

from main import (
    B2_LINEAR_SLOP, B2_SPECULATIVE_DISTANCE, B2_TIME_TO_SLEEP, B2_MAX_ROTATION,
    DEFAULT_GRAVITY, DEFAULT_HIT_EVENT_THRESHOLD, DEFAULT_RESTITUTION_THRESHOLD,
    DEFAULT_CONTACT_SPEED, DEFAULT_CONTACT_HERTZ, DEFAULT_CONTACT_DAMPING_RATIO,
    DEFAULT_MAXIMUM_LINEAR_SPEED, DEFAULT_ENABLE_SLEEP, DEFAULT_ENABLE_CONTINUOUS,
    DEFAULT_SAFETY_FACTOR, DEFAULT_SLEEP_THRESHOLD, DEFAULT_GRAVITY_SCALE,
    ITERATIONS, RELAX_ITERATIONS,
    b2_make_soft, make_contact_softness, make_static_softness,
    Body, ContactPoint, solve_contact, current_separation, relative_velocity,
    effective_mass, compute_restitution_velocity,
    vadd, vsub, vmul, vdot, cross_vv,
)

PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1


def close(a, b, tol=1e-9, msg=""):
    ok(abs(a - b) <= tol, "%s (got %r want %r)" % (msg, a, b))


# ============================ 1. 默认值逐条复算（types.c / constants.h） ============================
close(B2_LINEAR_SLOP, 0.005, msg="linearSlop 0.5cm")
close(B2_SPECULATIVE_DISTANCE, 0.02, msg="speculative = 4*slop")
close(B2_TIME_TO_SLEEP, 0.5, msg="0.5s 后休眠")
close(B2_MAX_ROTATION, 0.25 * pi, msg="每步最大旋转 0.25π")
close(DEFAULT_GRAVITY[1], -10.0, msg="默认重力 -10")
close(DEFAULT_HIT_EVENT_THRESHOLD, 1.0, msg="hitEvent 阈值 1m/s")
close(DEFAULT_RESTITUTION_THRESHOLD, 1.0, msg="恢复系数阈值 1m/s")
close(DEFAULT_CONTACT_SPEED, 3.0, msg="contactSpeed 3m/s")
close(DEFAULT_CONTACT_HERTZ, 30.0, msg="contactHertz 30")
close(DEFAULT_CONTACT_DAMPING_RATIO, 10.0, msg="阻尼比 10（过阻尼）")
close(DEFAULT_MAXIMUM_LINEAR_SPEED, 400.0, msg="最大线速度 400m/s")
ok(DEFAULT_ENABLE_SLEEP is True, "默认开启休眠")
ok(DEFAULT_ENABLE_CONTINUOUS is True, "默认开启连续碰撞")
close(DEFAULT_SAFETY_FACTOR, 0.5, msg="safetyFactor 0.5")
close(DEFAULT_SLEEP_THRESHOLD, 0.05, msg="sleepThreshold 0.05m")
close(DEFAULT_GRAVITY_SCALE, 1.0, msg="gravityScale 1")
close(ITERATIONS, 1, msg="solver.c：ITERATIONS = 1")
close(RELAX_ITERATIONS, 1, msg="solver.c：RELAX_ITERATIONS = 1")

# ============================ 2. b2MakeSoft 的三个特例 ============================
h = 1.0 / 60.0
z = b2_make_soft(0.0, 10.0, h)
ok((z.bias_rate, z.mass_scale, z.impulse_scale) == (0.0, 0.0, 0.0),
   "hertz=0 -> 三项全零（软约束关闭）")

# ζ=0：bias = 1/h
s0 = b2_make_soft(10.0, 0.0, h)
close(s0.bias_rate, 1.0 / h, tol=1e-9, msg="ζ=0 时 biasRate = 1/h")
# 恒等式：massScale + impulseScale == 1
for hz in (5.0, 30.0, 60.0, 240.0):
    for ze in (0.0, 1.0, 10.0):
        s = b2_make_soft(hz, ze, h)
        close(s.mass_scale + s.impulse_scale, 1.0, tol=1e-12,
              msg="massScale+impulseScale=1 (hertz=%g zeta=%g)" % (hz, ze))
# ω → ∞：massScale → 1、impulseScale → 0
big = b2_make_soft(1e7, 0.0, h)
ok(big.mass_scale > 1.0 - 1e-6, "ω→∞：massScale → 1（%.12f）" % big.mass_scale)
ok(big.impulse_scale < 1e-6, "ω→∞：impulseScale → 0（%.3e）" % big.impulse_scale)
close(big.bias_rate, 1.0 / h, tol=1e-6, msg="ω→∞：bias → 1/h")
# ω = π/(4h)：源码注释给出的两个数值
w_quarter = b2_make_soft(1.0 / (4.0 * 2.0 * h), 0.0, h)   # hertz = (π/4)/h / (2π) = 1/(8h)
close(w_quarter.mass_scale, pi * pi / (16.0 + pi * pi), tol=1e-12,
      msg="ω=π/(4h)：massScale = π²/(16+π²) ≈ 0.38")
close(w_quarter.impulse_scale, 16.0 / (16.0 + pi * pi), tol=1e-12,
      msg="ω=π/(4h)：impulseScale = 16/(16+π²) ≈ 0.62")

# ============================ 3. contact vs static 软约束（后者用 2 倍 hertz） ============================
cs = make_contact_softness(h)
ss = make_static_softness(h)
ok(ss.mass_scale > cs.mass_scale,
   "静态接触更硬：massScale %.6f > %.6f" % (ss.mass_scale, cs.mass_scale))
ok(ss.impulse_scale < cs.impulse_scale,
   "静态接触 impulseScale 更小（%.6f < %.6f）" % (ss.impulse_scale, cs.impulse_scale))
ok(ss.bias_rate > cs.bias_rate,
   "静态接触 biasRate 更大（%.4f > %.4f）" % (ss.bias_rate, cs.bias_rate))
# 阻尼比 10 是**过阻尼**：massScale 接近 1、impulseScale 很小
ok(cs.mass_scale > 0.9, "默认阻尼比 10 下 massScale 已接近 1（%.6f）" % cs.mass_scale)
ok(cs.impulse_scale < 0.1, "impulseScale 很小（%.6f）" % cs.impulse_scale)

# ---------------------------------------------------------------------------
# 约定（与 Box2D 一致）：法线 normal 从 A 指向 B。
# 下面统一让 **A 为静态地面、B 为动态物体**，于是"分离"= B 沿 +y 被推上去。
# ---------------------------------------------------------------------------

# ============================ 4. 推测性接触：正好停在接触面上 ============================
normal = (0.0, 1.0)
h = 1.0 / 60.0
soft = make_contact_softness(h)


def ground_and_body(v=(0.0, 0.0), inv_I=0.0, **cpkw):
    """返回 (静态地面 A, 动态物体 B, 接触点)。"""
    a = Body(v=(0.0, 0.0), inv_mass=0.0, inv_I=0.0)
    b = Body(v=v, inv_mass=1.0, inv_I=inv_I)
    cp = ContactPoint((0.0, 0.0), (0.0, 0.0), **cpkw)
    return a, b, cp


a, b, cp = ground_and_body(v=(0.0, -1.0), base_separation=0.01)
mass = effective_mass(a, b, cp, normal)
close(mass, 1.0, tol=1e-12, msg="纯线性接触的有效质量 = 1/(wA+wB)")
solve_contact(a, b, cp, normal, h, soft, use_bias=True)
# 推测性偏置 s/h 让物体在一步内刚好走完 s：解后 vn 应为 -s/h
close(b.v[1], -0.01 / h, tol=1e-9,
      msg="推测性接触后的法向速度 = -s/h（%.6f）" % b.v[1])
ok(b.v[1] > -1.0, "推测性接触提前减速了（从 -1 变成 %.6f）" % b.v[1])
ok(cp.normal_impulse > 0.0, "推测性接触照样累积了正冲量 %.6f" % cp.normal_impulse)

# ============================ 5. 重叠：偏置被 -contactSpeed 夹住 ============================
def overlap_bias(sep):
    aa, bb, cpp = ground_and_body(v=(0.0, 0.0), base_separation=sep)
    solve_contact(aa, bb, cpp, normal, h, soft, use_bias=True)
    return bb.v[1], cpp


shallow, _ = overlap_bias(-0.001)      # 1mm
deep, _ = overlap_bias(-1.0)           # 1m 的极端重叠
ok(shallow > 0.0, "浅重叠被推开（Δv = %.6f）" % shallow)
ok(deep > 0.0, "深重叠也被推开（Δv = %.6f）" % deep)
# 注意：偏置**不乘** massScale（公式是 -m*(massScale*vn + bias)），
# 所以被夹住后的瞬时速度增量恰好等于 contactSpeed
close(deep, DEFAULT_CONTACT_SPEED, tol=1e-9,
      msg="深重叠的推开速度恰好被夹在 contactSpeed = 3 m/s")
ok(deep / shallow < 1000.0,
   "重叠放大 1000 倍，推开速度只放大 %.1f 倍 —— 正是这个夹取在起作用" % (deep / shallow))

# ============================ 6. 累加冲量只能推不能拉 ============================
a, b, cp = ground_and_body(v=(0.0, 5.0), base_separation=0.001, normal_impulse=2.0)
imp = solve_contact(a, b, cp, normal, h, soft, use_bias=True)
close(cp.normal_impulse, 0.0, tol=1e-12,
      msg="分离中的接触：累加冲量被 clamp 回 0（接触不能拉）")
ok(imp < 0.0, "这一次施加的是负增量（卸掉之前的冲量 %.6f）" % imp)
ok(0.0 < b.v[1] < 5.0, "卸掉冲量后物体仍在分离但被减速（v=%.6f）" % b.v[1])

# ============================ 7. 摩擦：库仑锥 + 依赖当前法向冲量 ============================
def friction_case(friction, tangent_v):
    # 物体既在**下落**又在**侧滑**：法向要先被约束住，摩擦才有法向冲量可用。
    # （若 v.y 已经是 0，relax 阶段 use_bias=False 会把法向冲量卸回 0，摩擦随之失效 ——
    #   这正是"必须先压紧才有摩擦"的直接体现）
    aa, bb, cpp = ground_and_body(v=(tangent_v, -1.0), base_separation=-0.001)
    solve_contact(aa, bb, cpp, normal, h, soft, use_bias=True)      # push：建立法向冲量
    n_push = cpp.normal_impulse
    v_before = bb.v[0]
    solve_contact(aa, bb, cpp, normal, h, soft, use_bias=False, friction=friction)
    # 摩擦用的是**本轮 relax 更新之后**的法向冲量，不是 push 阶段的
    n_friction = cpp.normal_impulse
    return n_push, n_friction, v_before, bb.v[0], cpp.tangent_impulse


n_push, n_fric, vb, va, ti = friction_case(0.5, 2.0)
ok(n_push > 0.0, "push 阶段建立了法向冲量 %.6f" % n_push)
ok(n_fric > n_push, "relax 阶段把法向冲量补到刚好抵消 vn（%.6f -> %.6f）" % (n_push, n_fric))
ok(va < vb, "摩擦把切向速度降下来了（%.6f -> %.6f）" % (vb, va))
ok(abs(ti) <= 0.5 * n_fric + 1e-9,
   "切向冲量不超过 μ·法向冲量（|%.6f| <= %.6f）" % (abs(ti), 0.5 * n_fric))
# 想要完全刹住 2 m/s 的侧滑，需要的切向冲量是 2.0，摩擦锥给不起 -> 只能减速
ok(va > 0.0, "μ=0.5 只能减速，刹不住（残余 %.6f）" % va)
# μ 越大越能刹住
_, _, vb1, va1, _ = friction_case(0.1, 2.0)
_, _, vb2, va2, _ = friction_case(1.0, 2.0)
ok(va2 < va1, "μ=1.0 比 μ=0.1 刹得更狠（%.6f < %.6f）" % (va2, va1))
# 法向冲量为 0（没有压紧）-> 摩擦完全不起作用
a0, b0, cp0 = ground_and_body(v=(2.0, 0.0), base_separation=0.5)
solve_contact(a0, b0, cp0, normal, h, soft, use_bias=False, friction=0.9)
close(cp0.tangent_impulse, 0.0, tol=1e-12,
      msg="法向冲量为 0 时摩擦冲量被夹到 0（没有压紧就没有摩擦）")
close(b0.v[0], 2.0, tol=1e-12, msg="切向速度纹丝不动")

# ============================ 8. 恢复系数：阈值 + 只在 relax 阶段 ============================
close(compute_restitution_velocity(0.8, -5.0, 1.0), 4.0, tol=1e-12,
      msg="撞得够快 -> 恢复速度 = -e·vn = 4.0")
close(compute_restitution_velocity(0.8, -0.5, 1.0), 0.0, tol=1e-12,
      msg="速度低于 1m/s 阈值 -> 不给恢复（防抖）")
close(compute_restitution_velocity(0.8, -5.0, 0.0), 0.0, tol=1e-12,
      msg="没有累积法向冲量 -> 不给恢复")
a1, b1, cp1 = ground_and_body(base_separation=-0.001, restitution_velocity=2.0)
solve_contact(a1, b1, cp1, normal, h, soft, use_bias=True)
v_push = b1.v[1]
a2, b2, cp2 = ground_and_body(base_separation=-0.001, restitution_velocity=2.0)
solve_contact(a2, b2, cp2, normal, h, soft, use_bias=False)
v_relax = b2.v[1]
ok(v_relax > v_push,
   "恢复只在 relax 阶段生效（relax %.6f > push %.6f）" % (v_relax, v_push))
close(v_relax, 2.0, tol=1e-9, msg="relax 阶段把法向速度顶到恢复速度 2.0")

# ============================ 9. 有效质量：偏心锚点会让接触更"软" ============================
a, b, cp_center = ground_and_body(inv_I=1.0, base_separation=0.0)
cp_center.anchor_b = (0.0, 0.0)
cp_off = ContactPoint((0.0, 0.0), (0.5, 0.0), base_separation=0.0)
m_center = effective_mass(a, b, cp_center, normal)
m_offset = effective_mass(a, b, cp_off, normal)
close(m_center, 1.0, tol=1e-12, msg="锚点在质心：有效质量就是 1/(wA+wB)")
ok(m_offset < m_center,
   "锚点偏离质心后有效质量变小（%.6f < %.6f）—— 一部分冲量转成了旋转" % (m_offset, m_center))
close(m_offset, 1.0 / (1.0 + 1.0 * 0.5 * 0.5), tol=1e-12,
      msg="有效质量 = 1/(wA + wB + iB·(r×n)²)")
a3, b3, cp3 = ground_and_body(inv_I=1.0, base_separation=-0.001)
cp3.anchor_b = (0.5, 0.0)
solve_contact(a3, b3, cp3, normal, h, soft, use_bias=True)
ok(abs(b3.w_ang) > 1e-9, "偏心冲量产生了角速度（%.6f）" % b3.w_ang)
ok(b3.v[1] > 0.0, "同时被推开（v.y = %.6f）" % b3.v[1])

# ============================ 10. 多轮松弛：重叠逐步消失 ============================
a, b, cp = ground_and_body(base_separation=-0.02)
seps = []
for _ in range(6):
    solve_contact(a, b, cp, normal, h, soft, use_bias=True)
    b.dp = (b.dp[0], b.dp[1] + b.v[1] * h)     # 位置积分（Box2D 在独立阶段做）
    seps.append(current_separation(a, b, cp, normal))
ok(all(seps[i + 1] > seps[i] for i in range(len(seps) - 1)),
   "每轮松弛间距都在增大（从 %.5f 到 %.5f）" % (seps[0], seps[-1]))
ok(seps[-1] > seps[0], "重叠确实在被消除")
ok(seps[-1] < 0.02, "分离速度被 contactSpeed 限制，不会瞬间弹开（%.5f）" % seps[-1])

print("接触与冲量求解: %d 项断言全部通过" % PASS)
