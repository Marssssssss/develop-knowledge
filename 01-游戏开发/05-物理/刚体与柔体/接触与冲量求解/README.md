# 接触与冲量求解 —— Box2D v3 的接触约束、软约束与摩擦/恢复系数

## 简介

宽相挑出候选对、窄相给出接触点与法线之后，最后一步是**求解器**：把接触变成冲量，
让堆叠的箱子不抖、不陷、不穿。本 demo 复刻 Box2D v3.1 的接触求解核心：
`b2MakeSoft()` 软约束三参数、**推测性接触**、累加冲量的非负 clamp、库仑**摩擦锥**、
只在 relax 阶段生效且带 1 m/s 阈值的**恢复系数**、偏心接触点的**有效质量**。
本目录：`main.py`（模型）+ `selfcheck_contact_solver.py`（74 项断言，实跑全绿）。

## 原理详解

### 一、先记住这批默认值

`src/types.c` 的 `b2DefaultWorldDef()` 与 `include/box2d/constants.h`：

| 量 | 默认值 | 量 | 默认值 |
| --- | --- | --- | --- |
| `gravity` | `(0, -10)` | `restitutionThreshold` | 1 m/s |
| `contactHertz` | 30 | `hitEventThreshold` | 1 m/s |
| `contactDampingRatio` | 10（过阻尼） | `maximumLinearSpeed` | 400 m/s |
| `contactSpeed` | 3 m/s | `B2_LINEAR_SLOP` | 0.005 m |
| `B2_SPECULATIVE_DISTANCE` | `4*slop` = 0.02 m | `B2_TIME_TO_SLEEP` | 0.5 s |

刚体默认 `safetyFactor = 0.5`、`sleepThreshold = 0.05 m`、`gravityScale = 1`。`src/solver.c` 里
`ITERATIONS = 1`、`RELAX_ITERATIONS = 1`（v3 用子步/松弛各一轮，靠软约束而非堆迭代次数保证稳定）。

### 二、软约束 `b2MakeSoft(hertz, zeta, h)`

```c
float omega = 2.0f * B2_PI * hertz;
float a1 = 2.0f * zeta + h * omega;
float a2 = h * omega * a1;
float a3 = 1.0f / (1.0f + a2);
// biasRate = ω/a1; massScale = a2·a3; impulseScale = a3
```

源码注释给了三个特例，本 demo 全部断言：

| 条件 | 结论 |
| --- | --- |
| `hertz == 0` | 三项全零（软约束关闭） |
| `ζ == 0` | `biasRate = 1/h` |
| `ω → ∞` | `massScale → 1`、`impulseScale → 0`、`bias → 1/h` |
| `ω = π/(4h)` | `massScale ≈ 0.38`、`impulseScale ≈ 0.62` |
| **任意参数** | `massScale + impulseScale == 1` |

`physics_world.c` 里两套参数：

```c
context.contactSoftness = b2MakeSoft( contactHertz,        world->contactDampingRatio, context.h );
context.staticSoftness  = b2MakeSoft( 2.0f * contactHertz, world->contactDampingRatio, context.h );
```

**静态接触用两倍频率** —— 实测静态版 `massScale` 更大、`impulseScale` 更小、`biasRate` 更大，
即"和静态物体接触时推得更硬"（静态体没有质量可以退让）。
默认阻尼比 10 是**过阻尼**：`massScale ≈ 0.986`、`impulseScale ≈ 0.0136`，
接触几乎完全刚性，只有极小一部分冲量被"软"掉。

### 三、间距决定用哪套偏置

每个接触点先算当前间距 `s = baseSeparation + dot(dp + rot(dqB,rB) - rot(dqA,rA), normal)`：
`dp/dq` 是本步位移与旋转增量，所以间距**随时间推进被重新计算**。

```text
s > 0  (还差一点才碰到)：velocityBias = s / h          —— 推测性偏置
s <= 0 (已经重叠)：      velocityBias = max(massScale·biasRate·s, -contactSpeed)
                        并启用 massScale / impulseScale
```

**推测性接触**是关键设计：还没真正碰到就开始减速，目标 `vn = -s/h`（这一步刚好走完剩下距离 s，
正好停在接触面上）。实测：以 -1 m/s 下落、距离 0.01 m 时，解完速度恰为 **-0.6 m/s = -s/h**，
既不提前刹死也不穿进去 —— 消除了"先穿进去再推出来"的抖动。
重叠情形的 `max(..., -contactSpeed)` 是**上限夹取**：实测重叠从 1 mm 放大到 1 m（1000 倍），
推开速度只从 0.008 m/s 涨到**恰好 3.0 m/s**（373 倍），再深也不更快，否则深重叠会炸开。

### 四、冲量与三个 clamp

```text
impulse     = -normalMass * (massScale * vn + velocityBias) - impulseScale * normalImpulse
newImpulse  = max(normalImpulse + impulse, 0)      // 接触只能推不能拉
```

注意 **`velocityBias` 不乘 `massScale`**（`massScale` 只乘在 `vn` 上），
所以被夹住时瞬时速度增量**恰好等于** `contactSpeed`。
累加冲量的非负 clamp 是稳定性核心。实测：以 +5 m/s 分离、带 2.0 旧冲量的接触点，
解完冲量被卸回 **0**，只减速不拉扯。

**摩擦**（库仑锥）：

```text
vt          = dot(vrB - vrA, tangent) - tangentSpeed
impulse     = tangentMass * (-vt)
maxFriction = friction * normalImpulse                 // 用**当前**法向冲量
newImpulse  = clamp(tangentImpulse + impulse, -maxFriction, maxFriction)
```

1. 法向冲量为 0（没压紧）→ `maxFriction = 0` → **摩擦完全不起作用**，切向速度纹丝不动。
2. 摩擦用的是**本轮 relax 更新之后**的法向冲量，不是 push 阶段的 —— 实测两者差 0.0057。
3. 完全刹住 2 m/s 侧滑需切向冲量 2.0，μ=0.5 只给得起 0.5 → 只能减速刹不住。
### 五、恢复系数：只在 relax 阶段，且有阈值

`physics_world.c`：`if (totalNormalImpulse > 0 && normalVelocity < -restitutionThreshold)`
才置 `restitutionVelocity = -restitution * normalVelocity`，否则置 0 —— 两个条件缺一不可：
**确实在挤压**且**撞得够快**。这就是 bounce threshold，低于阈值的微小弹跳一律抹掉以防抖。

`contact_solver.c` 里恢复只写在 `useBias == false` 分支（即 **relax 阶段**）：
`velocityBias = b2MinFloat(velocityBias, -cp->restitutionVelocity)`。
实测：同一接触点 push 阶段只推到 0.008 m/s，relax 阶段（恢复速度 2.0）顶到**恰好 2.0 m/s** ——
恢复被实现成"偏置下限"，不是直接加冲量。

### 六、有效质量：力臂只取垂直于法线的分量

`normalMass = 1 / (wA + wB + iA·(rA×n)² + iB·(rB×n)²)`。

**实测踩到的坑**：第一版把锚点偏移设成 `(0, 0.5)`、法线 `(0,1)`，结果
`r×n = 0·1 - 0.5·0 = 0` —— 有效质量**完全没变**。偏移**平行于法线时不产生任何转动**，
必须垂直于法线（这里要 `(0.5, 0)`）才有力臂。改后有效质量从 1.0 降到 **0.8**，施加冲量后
**同时**产生角速度与线速度 —— 一部分冲量被"转成旋转"，接触因此显得更软。

## 对比：软约束 vs 硬约束 vs PBD

| | 冲量法 + 软约束（Box2D v3） | 纯位置投影（PBD/XPBD） |
| --- | --- | --- |
| 状态 | 速度 + 累加冲量 λ | 只有位置（速度由位置反推） |
| 穿透处理 | 速度偏置 `biasRate·s`，被 `contactSpeed` 夹住 | 直接把位置推开 |
| 恢复系数 | relax 阶段的速度偏置 | `velocityUpdate` 里改速度 |
| 刚度控制 | `hertz` / `dampingRatio`（物理单位） | XPBD 的柔度 α |

两者不互斥：Box2D v3 的 `enableContinuous` + 软约束负责刚体接触，
布/绳/软体通常走 PBD/XPBD（见 [`PBD与XPBD约束求解/`](../PBD与XPBD约束求解/)）。

## 环境 / 运行方式

Python 3.13，仅用标准库 `math`，无第三方依赖、不联网。

```bash
cd 01-游戏开发/05-物理/刚体与柔体/接触与冲量求解
python selfcheck_contact_solver.py   # 输出：接触与冲量求解: 74 项断言全部通过
```

## 关键代码

```python
def b2_make_soft(hertz, zeta, h):
    if hertz == 0.0:
        return Softness(0.0, 0.0, 0.0)
    omega = 2.0 * pi * hertz
    a1 = 2.0 * zeta + h * omega
    a2 = h * omega * a1
    a3 = 1.0 / (1.0 + a2)
    return Softness(omega / a1, a2 * a3, a3)      # massScale + impulseScale == 1

if s > 0.0:                       # 推测性：目标 vn = -s/h
    velocity_bias = s / h
elif use_bias:                    # 重叠：被 -contactSpeed 夹住
    velocity_bias = max(softness.mass_scale * softness.bias_rate * s, -contact_speed)
    mass_scale, impulse_scale = softness.mass_scale, softness.impulse_scale
impulse = -normal_mass * (mass_scale * vn + velocity_bias) - impulse_scale * cp.normal_impulse
cp.normal_impulse = max(cp.normal_impulse + impulse, 0.0)     # 只能推不能拉
```

## 性能边界

- **每接触点代价**：两次有效质量（法向 + 切向）+ 若干向量运算，常数时间。Box2D v3 用 SIMD
  一次处理 4 个接触（`b2SolveContacts_Wide`），并用**图着色**（`B2_GRAPH_COLOR_COUNT = 24`）分色并行。
- **迭代次数**：v3 的 `ITERATIONS = 1`、`RELAX_ITERATIONS = 1` —— 靠软约束与子步，不堆迭代。
  这是它与 Box2D v2（8 次速度迭代 + 3 次位置迭代）最大的差别。
- **累加冲量是 warm start 的基础**：跨帧复用让堆叠几帧内"记住"正确支撑力，这也是为什么
  接触点回收（`B2_CONTACT_RECYCLE_DISTANCE = 10*slop`）会显著影响堆叠稳定性。
- 深重叠时推开速度被夹在 `contactSpeed`，代价是**恢复需要多帧** —— 有意取舍（宁慢勿炸）。

## 注意事项与常见坑

1. **法线方向约定**：`normal` 从 A 指向 B，搞反会把"分离"算成"接近"、冲量符号全反。
   本 demo 统一 A=静态地面、B=动态物体。
2. **力臂要垂直于法线**：锚点偏移平行于法线时 `r×n = 0`，有效质量不变，像"旋转没生效"。
3. **摩擦用当前法向冲量**，不是上一帧、也不是 push 阶段的值 —— 拿错会让摩擦偏大/偏小。
4. **恢复系数有阈值**：撞得不够快（< 1 m/s）一律不弹（防抖设计，不是 bug）；**恢复只在 relax
   阶段**，写在 push 阶段会让物体还没分离就被弹开。
5. **偏置不乘 massScale**：公式是 `-m*(massScale*vn + bias)`，别顺手把两者都乘上。
6. **累加冲量必须 clamp 到非负**：接触只能推不能拉，否则会把物体粘住。改 `B2_LINEAR_SLOP` /
   `B2_SPECULATIVE_DISTANCE` 要慎重 —— 源码挂了 `@warning ... significant impact on stability`。

## 参考资料

（均为本轮**实读**并落盘核对的 Box2D v3.1 源码，MIT 许可）

- `src/types.c`（`b2DefaultWorldDef()`/`b2DefaultBodyDef()`）：<https://github.com/erincatto/box2d/blob/main/src/types.c>
- `include/box2d/constants.h`（`B2_LINEAR_SLOP`/`B2_SPECULATIVE_DISTANCE`/`B2_TIME_TO_SLEEP`）：<https://github.com/erincatto/box2d/blob/main/include/box2d/constants.h>
- `src/solver.h`（`b2MakeSoft()` 及三个特例注释）、`src/solver.c`（`ITERATIONS`、阶段编排）、
  `src/contact_solver.c`（`b2SolveContacts()`：间距、偏置、clamp、恢复、摩擦锥、滚动阻力）、
  `src/physics_world.c`（软约束装配 `2 * contactHertz`、恢复速度阈值判断）
- Erin Catto 历年 GDC 讲稿 *Box2D: Soft Constraints*（本轮未实读，仅列作延伸）

## 待研究

- [ ] 滚动阻力（`rollingResistance`，`contact_solver.c` 的 relax 分支）
- [ ] 接触点回收（`B2_CONTACT_RECYCLE_DISTANCE` / `B2_CONTACT_RECYCLE_COS_ANGLE`）与图着色并行
      （`B2_GRAPH_COLOR_COUNT = 24`）分别对堆叠稳定性和 Gauss-Seidel 顺序依赖的影响
