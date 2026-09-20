# PBD 与 XPBD —— 基于位置的动力学及其扩展

## 简介

传统刚体/柔体仿真走"力 → 加速度 → 速度 → 位置"的链条，显式积分在大刚度下会炸。
**PBD（Position Based Dynamics）**干脆跳过力：直接预测位置、再把位置**投影**回约束流形上，无条件稳定。
**XPBD（Extended PBD）**补上了 PBD 最大的短板 —— **刚度不再依赖时间步和迭代次数**。

本 demo 实现两者并**对拍**，最强的一条证据是：

> 同一个"顶端固定 + 恒定载荷"的场景，PBD（k=0.2）的稳态伸长量随迭代次数一路下降
> （0.0667 → 0.0081 → 0.00019 → 2.2e-6，永远不收敛）；
> 而 XPBD（α=1e-5）在 1 / 5 / 20 / 40 次迭代下给出**完全相同**的 0.000600000000，
> 且伸长量与柔度 α 严格成正比（α 放大 10 倍 → 伸长放大 10.000000 倍）。

本目录：`main.py`（模型）+ `selfcheck_pbd_xpbd.py`（56 项断言，实跑全绿）。

## 原理详解

### 一、PBD 主循环（Müller 2006 Algorithm 1，17 行）

```text
(1)-(3)  初始化 x_i, v_i, w_i = 1/m_i
(4)  loop
(5)      v_i += dt · w_i · f_ext(x_i)        # 外力（只用不到的力才走这里，典型是重力）
(6)      dampVelocities(v_1..v_N)            # 全局阻尼，见 §四
(7)      p_i = x_i + dt · v_i                # 显式欧拉预测
(8)      generateCollisionConstraints(x_i → p_i)
(9)-(11) 重复 solverIterations 次：projectConstraints(...)
(13)     v_i = (p_i - x_i) / dt              # 速度由位置反推
(14)     x_i = p_i
(16)     velocityUpdate(...)                 # 摩擦与恢复系数
(17) endloop
```

原文特别点出 (13)(14) 与 **Verlet 积分精确对应**（Verlet 把速度隐含在"当前位置减上一位置"里），但显式保留速度更好操作。

### 二、约束投影：一次牛顿-拉弗森步

对约束 `C(p) = 0`，一阶展开 `C(p+Δp) ≈ C(p) + ∇C·Δp = 0`，取 `Δp = λ∇C(p)` 解得

```text
Δp = -C(p) / |∇C(p)|² · ∇C(p)
```

带逆质量时

```text
s   = C(p) / Σ_j w_j |∇_{p_j} C|²
Δp_i = -s · w_i · ∇_{p_i} C
```

**距离约束** `C(p1,p2) = |p1-p2| - d` 的特例就是原文式 (10)(11)：

```text
Δp1 = -w1/(w1+w2) · (|p1-p2| - d) · n
Δp2 = +w2/(w1+w2) · (|p1-p2| - d) · n      n = (p1-p2)/|p1-p2|
```

实测性质：

| 场景 | 结果 |
| --- | --- |
| 两端等质量、距离 2、静长 1 | 各走 **0.5**，投影后距离**精确**等于 1（沿 n 方向是线性的） |
| 一端 `w=0`（静态/被抓住） | 该端不动，另一端承担**全部**修正量 |
| 质量比 1:3 | 位移比 **3:1**（按逆质量分配） |
| 动量守恒 | `Σ m_i Δp_i = 0`（式 1），否则引入 ghost force |

**不等式约束**（如碰撞 `C ≥ 0`）只在 `C < 0` 时才投影。

### 三、刚度 k 的非线性，以及 XPBD 的修正

PBD 里 `k ∈ [0,1]` 被**直接乘在修正量上**。原文立刻指出这不是线性的：单约束上每轮把剩余误差乘 `(1-k)`，n 轮后残差是 `(1-k)^n · C₀`（本 demo 对 k=0.2/0.5/0.8 × n=1..4 全部断言通过）。但**多约束、多迭代时 k 的效果既依赖迭代次数也依赖时间步**，且"多约束下不收敛到一个确定解"。Müller 2007 的指数缩放只解决迭代次数，不解决时间步 —— 这是 XPBD 要解决的。

XPBD（Macklin 2016）引入**柔度** `α`（刚度倒数）与能量势 `U = ½ Cᵀ α⁻¹ C`，取 `α̃ = α/Δt²`，Schur 补后：

```text
Δλ_j = (-C_j(x) - α̃_j λ_j) / (∇C_j M⁻¹ ∇C_jᵀ + α̃_j)      # 式 18
Δx   = M⁻¹ ∇C(x)ᵀ Δλ                                        # 式 17
λ   += Δλ        （每个子步开始时 λ 清零）
```

Algorithm 1 相对原 PBD **只多了 3 行**：初始化 `λ=0`、按式 18 算 `Δλ`、累加 `λ`。

两条原文明确给出的性质，本 demo 都验到了：

1. **`α_j = 0` 时 `Δλ` 恰好退化为原 PBD 的缩放因子 `s_j`** —— 实测数值完全相等（1e-12 内）。
2. 柔度越大，单次修正**单调变小**（正则化限制了约束力）。

### 四、Gauss-Seidel vs Jacobi：压力波传播

原文 3.2：GS 逐个约束投影，修改**立刻对后续可见**，"压力波能在一轮之内传遍材料，这个效果**依赖约束求解顺序**"；代价是过约束情况下顺序不稳定会振荡。

实测（四粒子链，**只让第一根约束违规**）：一轮后 Gauss-Seidel 的链尾位移为 **-0.1**
（扰动已传到链尾），Jacobi 为 **0**（纹丝不动，各约束只看旧值）。同时断言：同一个过约束系统
换求解顺序会得到**不同**结果，但顺序固定时**可复现** —— 这正是"必须保持顺序不变"的工程含义。

### 五、全局阻尼（原文 3.5）

难点是"既要阻尼抖动，又不能把物体整体平移/旋转也吃掉"。原文做法是先解出整体运动，再只阻尼**偏离**它的部分：

```text
(1) xcm = Σ x_i m_i / Σ m_i
(2) vcm = Σ v_i m_i / Σ m_i
(3) L   = Σ r_i × (m_i v_i)
(4) I   = Σ m_i r̃_i r̃_iᵀ
(5) ω   = I⁻¹ L
(6)-(8) Δv_i = vcm + ω×r_i - v_i ;  v_i += k_damping · Δv_i
```

`k_damping = 1` 时"只有整体运动幸存，整组顶点表现得像刚体"。

实测三条：

- 纯刚体平移 → 阻尼后速度**逐位不变**；
- 纯刚体旋转 → 从 `L` 与 `I` 反解出的 `ω` 与设定的完全一致，速度也逐位不变；
- 非刚体速度 → `k_damping=1` 后残差 < 1e-9，只剩整体刚体运动；`k_damping=0` 时完全不动。

（2D 下 `I` 退化成标量 `Σ m_i|r_i|²`，`ω × r = (-ω·r_y, ω·r_x)`。）

## 对比

| | PBD | XPBD |
| --- | --- | --- |
| 刚度参数 | `k ∈ [0,1]`，乘在修正量上 | 柔度 `α`（物理单位），进入 `α̃ = α/Δt²` |
| 有效刚度依赖 | **时间步 + 迭代次数** | 只依赖 α |
| 多次迭代 | 越迭代越硬，不收敛到确定解 | 收敛到柔度决定的确定解 |
| 额外状态 | 无 | 每个约束一个 `λ`（每步清零） |
| 相对原 PBD | — | 只多 3 行 |
| `α = 0` | — | 完全退化为 PBD |

工程上的常见组合是 **XPBD + 子步（substepping）**：用多个小步替代多次迭代（Müller 2020《Detailed Rigid Body Simulation with XPBD》的路线），本 demo 的 `step_xpbd(substeps=...)` 就是这个接口。

## 环境

- Python 3.13，仅用标准库 `math`。无第三方依赖、不联网。

## 运行方式

```bash
cd 01-游戏开发/05-物理/刚体与柔体/PBD与XPBD约束求解
python selfcheck_pbd_xpbd.py    # 输出：PBD与XPBD约束求解: 56 项断言全部通过
```

## 关键代码

```python
# PBD 距离约束投影（原文式 10/11）
def project_distance_pbd(a, b, d, k=1.0):
    delta = sub(a.p, b.p); dist = length(delta)
    n = (delta[0]/dist, delta[1]/dist)
    wsum = a.w + b.w
    c = dist - d
    return mul(n, -k*c*a.w/wsum), mul(n, k*c*b.w/wsum)

# XPBD（原文式 17/18）：α̃=0 时退化为上面的缩放因子
def project_distance_xpbd(a, b, d, alpha_tilde, lam):
    delta = sub(a.p, b.p); dist = length(delta)
    n = (delta[0]/dist, delta[1]/dist)
    c = dist - d
    denom = (a.w + b.w) + alpha_tilde          # ∇C M⁻¹ ∇Cᵀ = w_a + w_b
    d_lam = (-c - alpha_tilde*lam) / denom     # 式 18
    return d_lam, mul(n, a.w*d_lam), mul(n, -b.w*d_lam)   # 式 17
```

## 性能边界

- **单轮代价**：PBD/XPBD 每约束每次投影 O(基数)；本 demo 的距离约束是常数时间。
- **收敛速度**：GS 一轮能传遍链条（实测），Jacobi 不能 —— 长链条用 GS 划算得多，但 GS 顺序
  依赖、难以并行；工程上常见做法是**图着色**分色（Box2D v3 `B2_GRAPH_COLOR_COUNT = 24`）。
- **迭代次数 vs 子步**：XPBD 下"多次迭代"不改变有效刚度（实测），但会改善收敛；Müller 2020 的结论是**在总预算相同时，多加子步比多加迭代更划算**。
- **过约束系统**：GS 结果依赖顺序，务必固定顺序，否则会看到莫名的抖动/振荡。

## 注意事项与常见坑

1. **把 k 当线性量用** —— `k=0.5` 跑两轮 ≠ `k=1.0` 跑一轮，残差是 `(1-k)^n` 不是 `1-nk`。
2. **忘记动量守恒** —— 投影必须沿 `∇C` 方向并按逆质量加权，否则引入 ghost force（原文式 1/2）。碰撞/抓取类约束**不要求**守恒，因为它们是外部约束。
3. ** Jacobi 当 GS 用** —— 一轮传不远，长链条要很多轮才收敛。
4. **每步不清零 λ** —— XPBD 的 `λ` 是**每个子步**重新开始累加的，跨步复用会让柔度失效。
5. **用全局阻尼去压抖动** —— 它只吃"偏离刚体运动"的部分，对整体漂移无效；整体漂移要在 `velocityUpdate` 或外力层处理。
6. **α̃ 的量纲** —— `α̃ = α/Δt²`，子步长度变了 `α̃` 跟着变，别把 α 当成"每步修正比例"来调。
7. **2D 的 I 是标量** —— 别照抄 3D 的 `Σ m_i r̃_i r̃_iᵀ`（3×3 矩阵），2D 退化成 `Σ m_i|r_i|²`；
   **`w=0` 端点**分母 `w1+w2` 为 0 时直接返回零修正，别做除法。

## 参考资料

（以下均为本轮**实读**并落盘提取全文的论文）

- Müller, Heidelberger, Hennix, Ratcliff (2006). *Position Based Dynamics*（VRIPHYS 2006 / JVCIR 2007）
  <https://matthias-research.github.io/pages/publications/posBasedDyn.pdf>
  （Algorithm 1、3.2 Gauss-Seidel、3.3 约束投影与式 1-11、3.5 全局阻尼、第 4 章布料）
- Macklin, Müller, Chentanez (2016). *XPBD: Position-Based Simulation of Compliant Constrained Dynamics*
  <https://matthias-research.github.io/pages/publications/XPBD.pdf>（式 17/18、Algorithm 1、α=0 退化为 s_j、式 26 带阻尼扩展）
- Müller et al. (2020). *Detailed Rigid Body Simulation with XPBD*：<https://matthias-research.github.io/pages/publications/PBDBodies.pdf>（XPBD 刚体化与子步路线）
- Müller. *Ten Minute Physics* 第 09 期 XPBD 讲义：<https://matthias-research.github.io/pages/tenMinutePhysics/09-xpbd.pdf>

## 待研究

- [ ] 小步长（Small Steps，Macklin 2019）与子步数的定量取舍；XPBD 阻尼扩展（式 26 的 `γ_j = α̃_j β̃_j / Δt`）实测
- [ ] 图着色并行（Box2D `B2_GRAPH_COLOR_COUNT`）与本 demo GS 的顺序依赖如何共存
