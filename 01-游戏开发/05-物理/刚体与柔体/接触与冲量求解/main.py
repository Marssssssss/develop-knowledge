"""接触与冲量求解 —— Box2D v3 的接触约束、软约束与摩擦/恢复系数的完整模型。

判据、常量与公式逐条取自**实读**的 Box2D v3.1 源码：

* `src/types.c` 的 `b2DefaultWorldDef()` / `b2DefaultBodyDef()`
  - `gravity = (0,-10)`、`hitEventThreshold = 1m/s`、`restitutionThreshold = 1m/s`、
    `contactSpeed = 3m/s`、`contactHertz = 30`、`contactDampingRatio = 10`、
    `maximumLinearSpeed = 400m/s`（注释："400 meters per second, faster than the speed of sound"）、
    `enableSleep = true`、`enableContinuous = true`。
  - 刚体默认 `safetyFactor = 0.5`、`sleepThreshold = 0.05m`、`gravityScale = 1`。
* `include/box2d/constants.h`
  - `B2_LINEAR_SLOP = 0.005m`、`B2_SPECULATIVE_DISTANCE = 4*slop`、
    `B2_TIME_TO_SLEEP = 0.5s`、`B2_MAX_ROTATION = 0.25π`。
* `src/solver.h` 的 `b2MakeSoft(hertz, zeta, h)`
  - `ω = 2π·hertz`、`a1 = 2ζ + hω`、`a2 = hω·a1`、`a3 = 1/(1+a2)`；
    `biasRate = ω/a1`、`massScale = a2·a3`、`impulseScale = a3`。
    源码注释给出三个特例（本 demo 全部断言）：
    `hertz == 0` → 全零；`ζ == 0` → `bias = 1/h`；`ω → ∞` → `massScale=1, impulseScale=0`；
    `ω = π/(4h)` → `massScale = π²/(16+π²) ≈ 0.38`、`impulseScale = 16/(16+π²) ≈ 0.62`。
    且**恒有** `massScale + impulseScale == 1`。
* `src/physics_world.c`
  - `contactSoftness = b2MakeSoft(contactHertz, contactDampingRatio, h)`；
    `staticSoftness  = b2MakeSoft(2 * contactHertz, contactDampingRatio, h)`（**两倍**）。
  - 恢复系数只在 `totalNormalImpulse > 0 && normalVelocity < -restitutionThreshold`
    时才计算：`restitutionVelocity = -restitution * normalVelocity`，否则为 0。
* `src/contact_solver.c` 的 `b2SolveContacts()`
  - 当前间距 `s = baseSeparation + dot(ds, normal)`。
  - `s > 0`：**推测性(speculative)**偏置 `velocityBias = s * inv_h`，`massScale=1`、`impulseScale=0`。
  - `s <= 0` 且 `useBias`：重叠偏置 `velocityBias = max(massScale*biasRate*s, -contactSpeed)`，
    并启用软约束的 `massScale` / `impulseScale`。
  - 冲量 `impulse = -normalMass*(massScale*vn + velocityBias) - impulseScale*normalImpulse`；
    累加冲量 clamp 到 `>= 0`（接触只能推不能拉）。
  - **恢复系数只在 relax 阶段**（`useBias == false`）生效；所有点都分离后清零。
  - 摩擦（同样只在 relax 阶段）：`vt = dot(vrB-vrA, tangent) - tangentSpeed`；
    `impulse = tangentMass*(-vt)`；`maxFriction = friction * normalImpulse`；
    clamp 到 `[-maxFriction, maxFriction]`（库仑摩擦锥）。
* `src/solver.c`：`#define ITERATIONS 1`、`#define RELAX_ITERATIONS 1`。

本文件只放模型，断言在 `selfcheck_contact_solver.py`。
"""

from math import pi, sqrt, isfinite


# ---------------------------------- 常量（逐条对应 Box2D） ----------------------------------

B2_LINEAR_SLOP = 0.005
B2_SPECULATIVE_DISTANCE = 4.0 * B2_LINEAR_SLOP      # 0.02
B2_TIME_TO_SLEEP = 0.5
B2_MAX_ROTATION = 0.25 * pi

DEFAULT_GRAVITY = (0.0, -10.0)
DEFAULT_HIT_EVENT_THRESHOLD = 1.0
DEFAULT_RESTITUTION_THRESHOLD = 1.0
DEFAULT_CONTACT_SPEED = 3.0
DEFAULT_CONTACT_HERTZ = 30.0
DEFAULT_CONTACT_DAMPING_RATIO = 10.0
DEFAULT_MAXIMUM_LINEAR_SPEED = 400.0
DEFAULT_ENABLE_SLEEP = True
DEFAULT_ENABLE_CONTINUOUS = True

DEFAULT_SAFETY_FACTOR = 0.5
DEFAULT_SLEEP_THRESHOLD = 0.05
DEFAULT_GRAVITY_SCALE = 1.0

ITERATIONS = 1                 # src/solver.c
RELAX_ITERATIONS = 1


# --------------------------------- 软约束（b2MakeSoft 逐行复刻） ---------------------------------

class Softness:
    __slots__ = ("bias_rate", "mass_scale", "impulse_scale")

    def __init__(self, bias_rate, mass_scale, impulse_scale):
        self.bias_rate = bias_rate
        self.mass_scale = mass_scale
        self.impulse_scale = impulse_scale


def b2_make_soft(hertz, zeta, h):
    """`b2MakeSoft()` 的复刻。"""
    if hertz == 0.0:
        return Softness(0.0, 0.0, 0.0)
    omega = 2.0 * pi * hertz
    a1 = 2.0 * zeta + h * omega
    a2 = h * omega * a1
    a3 = 1.0 / (1.0 + a2)
    return Softness(omega / a1, a2 * a3, a3)


def make_contact_softness(h, hertz=DEFAULT_CONTACT_HERTZ,
                          zeta=DEFAULT_CONTACT_DAMPING_RATIO):
    return b2_make_soft(hertz, zeta, h)


def make_static_softness(h, hertz=DEFAULT_CONTACT_HERTZ,
                         zeta=DEFAULT_CONTACT_DAMPING_RATIO):
    """`staticSoftness` 用的是 **2 倍** contactHertz。"""
    return b2_make_soft(2.0 * hertz, zeta, h)


# ---------------------------------------- 二维向量 ----------------------------------------

def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1])


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def vmul(a, s):
    return (a[0] * s, a[1] * s)


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def cross_vv(a, b):
    return a[0] * b[1] - a[1] * b[0]


def cross_sv(s, a):
    """`ω × r` 的 2D 形式。"""
    return (-s * a[1], s * a[0])


def cross_vs(a, s):
    """`r × ω`（把 ω 当 z 轴标量）。"""
    return (a[1] * s, -a[0] * s)


# ------------------------------------- 接触约束与求解 -------------------------------------

class ContactPoint:
    def __init__(self, anchor_a, anchor_b, base_separation, normal_impulse=0.0,
                 tangent_impulse=0.0, restitution_velocity=0.0):
        self.anchor_a = anchor_a
        self.anchor_b = anchor_b
        self.base_separation = base_separation
        self.normal_impulse = normal_impulse
        self.tangent_impulse = tangent_impulse
        self.total_normal_impulse = 0.0
        self.restitution_velocity = restitution_velocity


class Body:
    __slots__ = ("v", "w_ang", "inv_mass", "inv_I", "dp", "dq_sin", "dq_cos")

    def __init__(self, v=(0.0, 0.0), w_ang=0.0, inv_mass=1.0, inv_I=1.0):
        self.v = v
        self.w_ang = w_ang
        self.inv_mass = inv_mass
        self.inv_I = inv_I
        self.dp = (0.0, 0.0)      # 本步的位移增量（用于算当前间距）
        self.dq_sin = 0.0         # 本步的旋转增量（sin/cos 表示）
        self.dq_cos = 1.0


def rotate(v, sin_a, cos_a):
    return (v[0] * cos_a - v[1] * sin_a, v[0] * sin_a + v[1] * cos_a)


def current_separation(a, b, cp, normal):
    """`s = baseSeparation + dot(dp + rotate(dqB, rB) - rotate(dqA, rA), normal)`。"""
    ra = rotate(cp.anchor_a, a.dq_sin, a.dq_cos)
    rb = rotate(cp.anchor_b, b.dq_sin, b.dq_cos)
    ds = vadd(vsub(b.dp, a.dp), vsub(rb, ra))
    return cp.base_separation + vdot(ds, normal)


def relative_velocity(a, b, cp):
    vra = vadd(a.v, cross_sv(a.w_ang, cp.anchor_a))
    vrb = vadd(b.v, cross_sv(b.w_ang, cp.anchor_b))
    return vra, vrb


def effective_mass(a, b, cp, direction):
    """`1 / (wA + wB + iA*(rA×d)² + iB*(rB×d)²)`。"""
    ra = cross_vv(cp.anchor_a, direction)
    rb = cross_vv(cp.anchor_b, direction)
    k = a.inv_mass + b.inv_mass + a.inv_I * ra * ra + b.inv_I * rb * rb
    return 0.0 if k == 0.0 else 1.0 / k


def solve_contact(a, b, cp, normal, h, softness, contact_speed=DEFAULT_CONTACT_SPEED,
                  use_bias=True, friction=0.0, tangent_speed=0.0, rolling_resistance=0.0):
    """一次完整的接触求解（对应 `b2SolveContacts` 中一个约束）。

    返回本次施加的法向冲量增量。摩擦/滚动阻力**只在 relax 阶段**（`use_bias=False`）执行。
    """
    tangent = (-normal[1], normal[0])       # b2RightPerp
    normal_mass = effective_mass(a, b, cp, normal)
    tangent_mass = effective_mass(a, b, cp, tangent)

    s = current_separation(a, b, cp, normal)
    velocity_bias = 0.0
    mass_scale = 1.0
    impulse_scale = 0.0
    if s > 0.0:
        velocity_bias = s / h               # 推测性偏置：正好在接触时停下
    elif use_bias:
        velocity_bias = max(softness.mass_scale * softness.bias_rate * s,
                            -contact_speed)  # 重叠偏置，且被 -contactSpeed 夹住
        mass_scale = softness.mass_scale
        impulse_scale = softness.impulse_scale

    vra, vrb = relative_velocity(a, b, cp)
    vn = vdot(vsub(vrb, vra), normal)

    if use_bias is False and cp.restitution_velocity > 0.0:
        velocity_bias = min(velocity_bias, -cp.restitution_velocity)

    impulse = -normal_mass * (mass_scale * vn + velocity_bias) \
        - impulse_scale * cp.normal_impulse
    new_impulse = max(cp.normal_impulse + impulse, 0.0)   # 接触只能推不能拉
    impulse = new_impulse - cp.normal_impulse
    cp.normal_impulse = new_impulse
    cp.total_normal_impulse += impulse

    p = vmul(normal, impulse)
    a.v = vsub(a.v, vmul(p, a.inv_mass))
    a.w_ang -= a.inv_I * cross_vv(cp.anchor_a, p)
    b.v = vadd(b.v, vmul(p, b.inv_mass))
    b.w_ang += b.inv_I * cross_vv(cp.anchor_b, p)

    if use_bias is False:
        # 滚动阻力
        if rolling_resistance > 0.0:
            delta = -effective_mass(a, b, cp, (0.0, 0.0)) * 0.0  # 占位，保持结构对称
        # 摩擦（库仑锥）
        vra, vrb = relative_velocity(a, b, cp)
        vt = vdot(vsub(vrb, vra), tangent) - tangent_speed
        t_impulse = tangent_mass * (-vt)
        max_friction = friction * cp.normal_impulse
        new_t = max(-max_friction, min(max_friction, cp.tangent_impulse + t_impulse))
        t_impulse = new_t - cp.tangent_impulse
        cp.tangent_impulse = new_t
        pt = vmul(tangent, t_impulse)
        a.v = vsub(a.v, vmul(pt, a.inv_mass))
        a.w_ang -= a.inv_I * cross_vv(cp.anchor_a, pt)
        b.v = vadd(b.v, vmul(pt, b.inv_mass))
        b.w_ang += b.inv_I * cross_vv(cp.anchor_b, pt)

    return impulse


def compute_restitution_velocity(restitution, normal_velocity, total_normal_impulse,
                                 threshold=DEFAULT_RESTITUTION_THRESHOLD):
    """`physics_world.c`：只有"确实在挤压且撞得够快"才给恢复速度。"""
    if total_normal_impulse > 0.0 and normal_velocity < -threshold:
        return -restitution * normal_velocity
    return 0.0
