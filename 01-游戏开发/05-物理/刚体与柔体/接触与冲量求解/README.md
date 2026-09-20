# 接触与冲量求解 —— Box2D v3 的接触约束、软约束与摩擦/恢复系数

## 简介

宽相挑出候选对、窄相给出接触点与法线之后，最后一步是**求解器**：把这些接触变成冲量，
让堆叠的箱子不抖、不陷、不穿。这是物理引擎里最讲究"数值手感"的一段代码。

本 demo 完整复刻 Box2D v3.1 的接触求解核心，包括：

- `b2MakeSoft()` 软约束三参数（把"重叠被推开"建模成一个有刚度与阻尼的弹簧）
- **推测性接触**（有限推测距离内提前减速）
- 累加冲量的**非负 clamp**（接触只能推不能拉）
- 库仑**摩擦锥**（摩擦上界 = μ × 当前法向冲量）
- **恢复系数**只在 relax 阶段生效，且有 1 m/s 的速度阈值
- 偏心接触点的**有效质量**（力臂只取垂直于法线的分量）

本目录：`main.py`（模型）+ `selfcheck_contact_solver.py`（74 项断言，实跑全绿）。

## 原理详解

### 一、先记住这批默认值

`src/types.c` 的 `b2DefaultWorldDef()` 与 `include/box2d/constants.h`：

| 量 | 默认值 | 说明 |
| --- | --- | --- |
| `gravity` | `(0, -10)` | |
| `contactHertz` | 30 | 接触"弹簧"的频率 |
| `contactDampingRatio` | 10 | **过阻尼**（远大于 1） |
| `contactSpeed` | 3 m/s | 分离速度上限 |
| `restitutionThreshold` | 1 m/s | 低于此速度不给恢复（防抖） |
| `hitEventThreshold` | 1 m/s | 碰撞事件阈值 |
| `maximumLinearSpeed` | 400 m/s | 注释："faster than the speed of sound" |
| `B2_LINEAR_SLOP` | 0.005 m | 碰撞与约束容差 |
| `B2_SPECULATIVE_DISTANCE` | `4*slop` = 0.02 m | 有限推测碰撞距离 |
| `B2_TIME_TO_SLEEP` | 0.5 s | 静止多久后休眠 |
| `enableSleep` / `enableContinuous` | 均 `true` | |

刚体默认 `safetyFactor = 0.5`、`sleepThreshold = 0.05 m`、`gravityScale = 1`。
`src/solver.c` 里 `ITERATIONS = 1`、`RELAX_ITERATIONS = 1`（v3 用子步/松弛各一轮，
靠软约束而不是堆迭代次数来保证稳定）。

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
| `ω = π/(4h)` | `massScale = π²/(16+π²) ≈ 0.38`、`impulseScale = 16/(16+π²) ≈ 0.62` |
| **任意参数** | `massScale + impulseScale == 1` |

`physics_world.c` 里两套参数：

```c
context.contactSoftness = b2MakeSoft( contactHertz,        world->contactDampingRatio, context.h );
context.staticSoftness  = b2MakeSoft( 2.0f * contactHertz, world->contactDampingRatio, context.h );
```

**静态接触用的是两倍频率** —— 实测静态版的 `massScale` 更大、`impulseScale` 更小、`biasRate` 更大，
也就是说"和静态物体接触时推得更硬"。静态物体没有质量可以退让，这是合理的。

默认阻尼比 10 是**过阻尼**：`massScale ≈ 0.986`、`impulseScale ≈ 0.0136`，
意味着接触几乎是完全刚性的，只有极小一部分冲量被"软"掉。

### 三、间距决定用哪套偏置

每个接触点先算当前间距 `s = baseSeparation + dot(dp + rot(dqB,rB) - rot(dqA,rA), normal)`：
`dp/dq` 是本步的位移与旋转增量，所以间距是**随时间推进被重新计算**的。

```text
s > 0  (还差一点才碰到)：velocityBias = s / h          —— 推测性偏置
s <= 0 (已经重叠)：      velocityBias = max(massScale·biasRate·s, -contactSpeed)
                        并启用 massScale / impulseScale
```

**推测性接触**是关键设计：物体还没真正碰到时就开始减速，目标是解完之后
`vn = -s/h` —— 也就是"这一步刚好走完剩下的距离 s，正好停在接触面上"。
实测：物体以 -1 m/s 下落、距离 0.01 m 时，解完之后速度恰好变成 **-0.6 m/s = -s/h**，
既不提前刹死也不会穿进去。这消除了传统"先穿进去再推出来"造成的抖动。

重叠情形的 `max(..., -contactSpeed)` 是个**上限夹取**：重叠再深，推开速度也不会超过 3 m/s。
实测重叠从 1 mm 放大到 1 m（1000 倍），推开速度只从 0.008 m/s 涨到**恰好 3.0 m/s**（373 倍），
再深也不会更快 —— 否则深重叠会引发爆炸性弹开。

### 四、冲量与三个 clamp

```text
impulse     = -normalMass * (massScale * vn + velocityBias) - impulseScale * normalImpulse
newImpulse  = max(normalImpulse + impulse, 0)      // 接触只能推不能拉
```

注意 **`velocityBias` 不乘 `massScale`**（公式里 `massScale` 只乘在 `vn` 上），
所以被夹住时瞬时速度增量**恰好等于** `contactSpeed`。

累加冲量的非负 clamp 是稳定性的核心。实测：一个正在以 +5 m/s 分离的接触点，
带着 2.0 的旧冲量，解完之后冲量被卸回 **0**，只减速不拉扯。

**摩擦**（库仑锥）：

```text
vt          = dot(vrB - vrA, tangent) - tangentSpeed
impulse     = tangentMass * (-vt)
maxFriction = friction * normalImpulse                 // 用**当前**法向冲量
newImpulse  = clamp(tangentImpulse + impulse, -maxFriction, maxFriction)
```

三条实测性质：

1. 法向冲量为 0（没压紧）→ `maxFriction = 0` → **摩擦完全不起作用**，切向速度纹丝不动。
2. 摩擦用的是**本轮 relax 更新之后**的法向冲量，不是 push 阶段的 —— 实测两者差 0.0057，
   拿 push 阶段的值去算上界会算错。
3. 想完全刹住 2 m/s 的侧滑需要切向冲量 2.0，但 μ=0.5 只给得起 0.5 → 只能减速刹不住；
   μ=1.0 明显比 μ=0.1 刹得更狠。

### 五、恢复系数：只在 relax 阶段，且有阈值

`physics_world.c`：

```c
if ( mp->totalNormalImpulse > 0.0f && mp->normalVelocity < -world->restitutionThreshold )
    mp->restitutionVelocity = -contactSim->restitution * mp->normalVelocity;
else
    mp->restitutionVelocity = 0.0f;
```

两个条件缺一不可：**确实在挤压**（累积法向冲量 > 0）**且撞得够快**（法向速度 < -1 m/s）。
这就是经典的 bounce threshold —— 低于阈值的微小弹跳一律抹掉，防止堆叠物体永远抖。

`contact_solver.c` 里恢复只写在 `useBias == false` 分支（即**relax 阶段**）：

```c
if ( useBias == false && cp->restitutionVelocity > 0.0f )
    velocityBias = b2MinFloat( velocityBias, -cp->restitutionVelocity );
```

实测：同一个接触点，push 阶段只把它推到 0.008 m/s，relax 阶段（开了恢复速度 2.0）
把它顶到**恰好 2.0 m/s**。恢复系数被实现成一个"偏置下限"，而不是直接加冲量。

### 六、有效质量：力臂只取垂直于法线的分量

```text
normalMass = 1 / (wA + wB + iA·(rA×n)² + iB·(rB×n)²)
```

**实测踩到的坑**：第一版把锚点偏移设成 `(0, 0.5)`，法线是 `(0,1)`，
结果 `r×n = 0·1 - 0.5·0 = 0` —— 有效质量**完全没变**。
偏移量**平行于法线时不产生任何转动**，必须垂直于法线（这里要 `(0.5, 0)`）才有力臂。
改成 `(0.5, 0)` 后有效质量从 1.0 降到 **0.8**，且施加冲量后**同时**产生角速度与线速度 ——
一部分冲量被"转成了旋转"，接触因此显得更软。

## 对比：软约束 vs 硬约束 vs PBD

| | 冲量法 + 软约束（Box2D v3） | 纯位置投影（PBD/XPBD） |
| --- | --- | --- |
| 状态 | 速度 + 累加冲量 λ | 只有位置（速度由位置反推） |
| 穿透处理 | 速度偏置 `biasRate·s`，被 `contactSpeed` 夹住 | 直接把位置推开 |
| 恢复系数 | relax 阶段的速度偏置 | `velocityUpdate` 里改速度 |
| 刚度控制 | `hertz` / `dampingRatio`（物理单位） | XPBD 的柔度 α |
|  warm start | 累加冲量跨帧复用 | 无（每步 λ 清零） |

两者不是互斥的：Box2D v3 的 `enableContinuous` + 软约束负责刚体接触，
而布/绳/软体通常走 PBD/XPBD（见同目录 [`PBD与XPBD约束求解/`](../PBD与XPBD约束求解/)）。

## 环境

- Python 3.13，仅用标准库 `math`。无第三方依赖、不联网。

## 运行方式

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

# 间距决定偏置：s>0 推测性，s<=0 重叠（被 -contactSpeed 夹住）
if s > 0.0:
    velocity_bias = s / h
elif use_bias:
    velocity_bias = max(softness.mass_scale * softness.bias_rate * s, -contact_speed)
    mass_scale, impulse_scale = softness.mass_scale, softness.impulse_scale

impulse = -normal_mass * (mass_scale * vn + velocity_bias) - impulse_scale * cp.normal_impulse
cp.normal_impulse = max(cp.normal_impulse + impulse, 0.0)     # 只能推不能拉
```

## 性能边界

- **每接触点代价**：两次有效质量（法向 + 切向）+ 若干次向量运算，常数时间。
  Box2D v3 用 SIMD 一次处理 4 个接触（`b2SolveContacts_Wide`），并用**图着色**
  （`B2_GRAPH_COLOR_COUNT = 24`）把约束分色并行。
- **迭代次数**：v3 的 `ITERATIONS = 1`、`RELAX_ITERATIONS = 1` —— 靠软约束与子步，
  而不是堆迭代。这是它与 Box2D v2（8 次速度迭代 + 3 次位置迭代）最大的差别。
- **累加冲量是 warm start 的基础**：跨帧复用让堆叠在几帧内就"记住"正确的支撑力，
  这也是为什么改接触点匹配（contact recycling，`B2_CONTACT_RECYCLE_DISTANCE = 10*slop`）
  会显著影响堆叠稳定性。
- 深重叠时推开速度被夹在 `contactSpeed`，代价是**恢复需要多帧**；
  这是有意的取舍（宁可慢一点也不要炸）。

## 注意事项与常见坑

1. **法线方向约定**：`normal` 从 A 指向 B。搞反会把"分离"算成"接近"，
   所有冲量符号跟着反。本 demo 统一 A=静态地面、B=动态物体。
2. **力臂要垂直于法线**：锚点偏移平行于法线时 `r×n = 0`，有效质量不变，
   看起来像"旋转没生效"。
3. **摩擦用的是当前法向冲量**，不是上一帧的，也不是 push 阶段的 —— 拿错值会让摩擦偏大/偏小。
4. **恢复系数有阈值**：撞得不够快（< 1 m/s）一律不弹，这是防抖设计，不是 bug。
5. **恢复只在 relax 阶段**：写在 push 阶段会让物体在还没分离时就被弹开。
6. **偏置不乘 massScale**：公式是 `-m*(massScale*vn + bias)`，别顺手把两者都乘上。
7. **累加冲量必须 clamp 到非负**：接触只能推不能拉，否则会把物体粘住。
8. **改 `B2_LINEAR_SLOP` / `B2_SPECULATIVE_DISTANCE`** 要慎重 —— 源码注释挂了
   `@warning modifying this can have a significant impact on stability`。

## 参考资料

（以下均为本轮**实读**并落盘核对的 Box2D v3.1 源码，MIT 许可）

- `src/types.c` — `b2DefaultWorldDef()` / `b2DefaultBodyDef()`：<https://github.com/erincatto/box2d/blob/main/src/types.c>
- `include/box2d/constants.h` — `B2_LINEAR_SLOP` / `B2_SPECULATIVE_DISTANCE` / `B2_TIME_TO_SLEEP` / `B2_MAX_AABB_MARGIN` 等：<https://github.com/erincatto/box2d/blob/main/include/box2d/constants.h>
- `src/solver.h` — `b2MakeSoft()` 与其三个特例注释：<https://github.com/erincatto/box2d/blob/main/src/solver.h>
- `src/solver.c` — `ITERATIONS` / `RELAX_ITERATIONS`、阶段编排（warm start → solve → integrate positions → relax）
- `src/contact_solver.c` — `b2SolveContacts()`：间距计算、推测性/重叠偏置、冲量 clamp、恢复、滚动阻力、摩擦锥
- `src/physics_world.c` — 软约束装配（`2 * contactHertz`）、恢复速度计算与阈值判断
- Erin Catto 历年 GDC 讲稿 *Box2D: Soft Constraints*（本轮未实读，仅列作延伸）

## 待研究

- [ ] 滚动阻力（`rollingResistance`，`contact_solver.c` 的 relax 分支）
- [ ] 接触点回收（contact recycling，`B2_CONTACT_RECYCLE_DISTANCE` / `B2_CONTACT_RECYCLE_COS_ANGLE`）对堆叠稳定性的影响
- [ ] 图着色并行（`B2_GRAPH_COLOR_COUNT = 24`）与 Gauss-Seidel 顺序依赖的关系
- [ ] 连续碰撞（TOI）与 `B2_MAX_ROTATION = 0.25π` 的耦合
