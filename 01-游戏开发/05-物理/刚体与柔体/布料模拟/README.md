# 布料模拟 —— 拉伸约束、弯曲约束与两类求解路线

## 简介

布料是 PBD/XPBD 最经典的应用：一块三角形网格，靠**拉伸约束**维持"布不该被拉长"，靠**弯曲约束**维持"布不该被折平"。

有意思的是，两篇权威资料给出了**两套完全不同的弯曲模型**：

- **Müller 2006（PBD 原论文 §4）**：弯曲用**二面角** `C_bend = acos(n1·n2) - φ0`。优点是**与拉伸完全解耦**，可以做出"拉伸很软但抗弯很强"的布。
- **Ten Minute Physics 第 14 期（Müller 本人后来的做法）**：弯曲**不用二面角**，直接在两个相邻三角形的**对角顶点**之间加一根普通**距离约束** —— 这就是标题里那个"secret"。便宜、稳定、和拉伸约束共用同一段求解代码。

本 demo 两套都实现并对照。另外还实测到一个官方示例代码里的**真实缺陷**（见 §三）。

本目录：`main.py`（模型）+ `selfcheck_cloth.py`（93 项断言，实跑全绿）。

## 原理详解

### 一、表示与质量（PBD 2006 §4.1）

- 输入是任意三角形网格，唯一要求是**流形**（每条边最多被两个三角形共享）。
- 用户输入**面密度** ρ [kg/m²]，顶点质量 = **每个相邻三角形质量的 1/3 之和**。
  实测核对：3×3 网格上逐顶点质量都等于"相邻三角形数 × 三角形质量/3"，且
  **总质量 = 所有三角形质量之和**（3 份 1/3 正好守恒，不重不漏）。
- 每条边一根拉伸约束 `C = |p1-p2| - l0`（equality）。
- 固定/被抓取的顶点把**逆质量置 0**（原文 3.6），这样其它约束就推不动它。

### 二、拉伸求解（Ten Minute Physics 14-cloth.html 实读）

```javascript
solveStretching(compliance, dt) {
    var alpha = compliance / dt / dt;
    ...
    var C = len - restLen;
    var s = -C / (w + alpha);          // w = w0 + w1
    vecAdd(this.pos, id0, this.grads, 0,  s * w0);
    vecAdd(this.pos, id1, this.grads, 0, -s * w1);
}
```

默认值值得注意：**`stretchingCompliance = 0.0`（布不可伸长），`bendingCompliance = 1.0`**。
主循环是 `sdt = dt / numSubsteps`，**默认 15 个子步**，每子步 `preSolve → solve → postSolve`：

| 阶段 | 内容 |
| --- | --- |
| `preSolve` | `v += g·dt`；`prevPos = pos`；`pos += v·dt`；**地面碰撞**：`y < 0` 就退回 `prevPos` 并把 `y` 置 0 |
| `solve` | 先 `solveStretching`，再 `solveBending`（顺序固定） |
| `postSolve` | `v = (pos - prevPos) / dt` |

实测性质：

- `stretchingCompliance = 0` → 最大拉伸误差 < 1e-3，**布确实不可伸长**；
  换成 1e-2 后误差明显变大，且**底边真的垂得更低**（可观测的宏观后果）。
- 子步数 1 → 5 → 15，拉伸误差**单调下降**，15 步比 1 步误差小一半以上。
- 固定点（逆质量 0）跑 200 帧后**逐位不动**。

> 注意：这里的 `s = -C/(w+α)` 是**不累加 λ 的简化 XPBD**，与 Macklin 2016 的式 18
> （`Δλ = (-C - α̃λ)/(∇CM⁻¹∇Cᵀ + α̃)`）不同 —— 官方布料 demo 用的是简化版。

### 三、实测到的官方缺陷：相邻三角形查找会漏配

官方 `findTriNeighbors()` 的做法是：把所有边规范成 `(min,max)` 排序，然后**按下标两两扫**
`(0,1) (2,3) (4,5) ...` 配成对。这个扫描**只在重复边恰好落在偶数/奇数配对内时才成立**。

3×3 规则网格上的实测：

| | 官方扫描 | 字典归桶修正版 |
| --- | --- | --- |
| 配出的共享边条目 | **2**（1 条） | **16**（8 条） |
| 拉伸约束数 | **23**（唯一边只有 16 条，重复登记） | **16** |
| 弯曲约束数 | 明显偏少 | 完整 |

漏配的原因是重复边落在下标 (3,4)、(5,6)、(7,8)、(13,14)、(15,16)、(17,18)、(19,20) 上，
全部**跨在配对边界**上，只有 (10,11) 那对被抓到。

**后果不只是"少几根弯曲约束"**：拉伸约束的去重条件是 `n < 0 || id0 < id1`，
一旦 `n` 被误判成 -1，共享边就会被**两个三角形各登记一次** —— 同一根约束被算两遍，
等价于局部刚度加倍，表现为布料某些区域莫名偏硬。

本 demo 同时保留官方版 `find_tri_neighbors()` 与修正版 `find_tri_neighbors_robust()`
（按 `(min,max)` 字典归桶），并对两者的差异**直接断言**。

### 四、二面角弯曲（PBD 2006 §4.1）

对相邻三角形 `(p1,p3,p2)` 与 `(p1,p2,p4)`（共享边 `p1-p2`）：

```text
C_bend = acos( ((p2-p1)×(p3-p1))/|·| · ((p2-p1)×(p4-p1))/|·| ) - φ0
```

**口径说明（实测确认）**：按上式，两个"对角顶点" p3 与 p4 落在共享边的**两侧**，
所以**平铺**的布 `n1·n2 = -1`，静止二面角是 **π 而不是 0**。这是公式本身决定的。

原文强调这个形式的价值：**它不依赖边长**，因此与拉伸解耦 —— 用户可以做出
"低拉伸刚度 + 高抗弯"的布，而如果用"在对角顶点间加距离约束"的做法，
抗弯会顺带把布也绷紧（原文 Figure 3 给了 `(k_stretch, k_bend) = (1,1) / (1/2,1) / (1/100,1)` 的对照）。

**实测踩到的坑**：四个顶点必须**一起**用同一个缩放因子投影

```text
s = C / Σ_j w_j|∇_{p_j}C|²        Δp_i = -s·w_i·∇_{p_i}C
```

本 demo 第一版是"逐顶点各自做一次完整牛顿步"（分母只算该顶点自己的梯度），
结果折叠角不但没被拉回去，反而从 **0.615 恶化到 1.670** —— 因为每个顶点都按
"自己能独立解决整个约束"来移动，四个人一起过冲。
改成共享 `s` 之后单调收敛。

### 五、地面碰撞：只在 preSolve 处理

官方 `preSolve` 里地面是**位置投影**（`y<0` 退回 `prevPos` 再置 0），
但随后的 `solve()` **不知道有地面**，约束求解可以把顶点重新拽到地面以下。
实测：跑满 400 帧后确实有顶点略微陷入地面（约 **-5.6e-5**，格距的万分之六）。
量级很小所以视觉上看不出来，但如果要严格无穿透，地面必须**也作为约束**参与求解。

## 对比

| | 距离约束抗弯（Ten Minute Physics） | 二面角抗弯（PBD 2006 §4.1） |
| --- | --- | --- |
| 约束形式 | `\|p3-p4\| - l0`，与普通距离约束同构 | `acos(n1·n2) - φ0` |
| 实现成本 | 极低，复用同一段代码 | 要算两个法线 / 梯度，成本高 |
| 与拉伸的关系 | **耦合**（绷紧对角会顺带绷紧布） | **解耦**（不依赖边长） |
| 能否做"软而挺" | 不能 | 能 |
| 静止角口径 | 静长 = 初始对角距离 | 平铺布面 **φ0 = π** |
| 数值稳健性 | 好（无 acos 端点奇点） | `acos` 在 `±1` 附近导数发散 |

## 环境

- Python 3.13，仅用标准库 `math`。无第三方依赖、不联网。

## 运行方式

```bash
cd 01-游戏开发/05-物理/刚体与柔体/布料模拟
python selfcheck_cloth.py      # 输出：布料模拟: 93 项断言全部通过
```

## 关键代码

```python
# 官方求解：拉伸/弯曲共用同一段代码，alpha = compliance/dt/dt
def _solve_distance_set(self, id_pairs, rest, compliance, dt):
    alpha = compliance / dt / dt
    for k, (id0, id1) in enumerate(id_pairs):
        w0, w1 = self.inv_mass[id0], self.inv_mass[id1]
        w = w0 + w1
        if w == 0.0: continue
        grad = vsub(self.pos[id0], self.pos[id1]); ln = vlen(grad)
        if ln == 0.0: continue
        grad = vmul(grad, 1.0/ln)
        s = -(ln - rest[k]) / (w + alpha)
        self.pos[id0] = vadd(self.pos[id0], vmul(grad,  s*w0))
        self.pos[id1] = vadd(self.pos[id1], vmul(grad, -s*w1))

# 二面角弯曲：四个顶点共享同一个缩放因子 s（逐个顶点各做一次牛顿步会过冲）
s = stiffness * c / sum(inv_mass[i] * dot(g_i, g_i) for i, g_i in grads.items())
for i, g_i in grads.items():
    pos[i] -= g_i * s * inv_mass[i]
```

## 性能边界

- **约束数量**：`拉伸 ≈ 3V`（V 为顶点数，网格边数约 3V-ish），`弯曲 ≈ 边数-边界边数`；
  官方称这个方案能在手机上跑 6400 个三角形 30+ fps。
- **子步 vs 迭代**：15 个子步是官方默认；子步越多越收敛（实测误差单调下降），
  但线性增加开销。Müller 2020 的结论是总预算相同时**加子步比加迭代划算**。
- **二面角弯曲的成本**：本 demo 用中心差分求数值梯度，每约束 24 次 `acos`；
  工程上应换成原文附录 A 的解析梯度，或改用对角距离约束。
- **自碰撞**：原文用**空间哈希**找"顶点-三角形"，代价随布厚 `h` 与三角形尺度变化；
  本 demo 未实现（见待研究）。

## 注意事项与常见坑

1. **布初始位置必须在地面之上** —— 地面在 `y=0`，若初始网格 y ≤ 0，第一帧就会被
   `preSolve` 整块拍到 `y=0`（本 demo 实测踩到，加了 `y0` 参数解决）。
2. **相邻三角形查找用"排序后两两扫"** —— 会漏配跨偶数/奇数边界的重复边，
   连带让拉伸约束重复登记（实测 23 条 vs 唯一边 16 条）。用字典归桶。
3. **二面角弯曲逐顶点各做一次牛顿步** —— 四个顶点一起过冲，实测角度从 0.615 恶化到 1.670。
   必须用共享缩放因子 `s`。
4. **`acos` 的端点** —— `n1·n2` 必须 clamp 到 `[-1, 1]`，否则 `NaN`；且在完全平铺/完全对折时导数发散。
5. **地面只在 preSolve 处理** —— 约束求解会把点重新拽到地面以下（实测 -5.6e-5）。
6. **静止二面角是 π 不是 0** —— 取决于公式里两个叉积的取向，改公式就要改 φ0。
7. **只测顶点不够** —— 原文提醒：小刚体会穿过大三角形，刚体的**凸角**也要反过来对三角形测试。
8. **自碰撞的法线方向** —— 顶点从哪一侧进入要用对应朝向的法线（原文式 12 与式 13 是两种写法）。
9. **纯约束求解不该移动质心** —— 内部约束必须动量守恒；本 demo 断言平铺布求解后质心漂移 < 1e-9。

## 参考资料

（以下均为本轮**实读**并落盘核对的原文）

- Matthias Müller, *Ten Minute Physics* 第 14 期 —— *The secret of cloth simulation*
  - 交互演示与完整 JS 源码：<https://matthias-research.github.io/pages/tenMinutePhysics/14-cloth.html>
    （`class Cloth` 的构造、`findTriNeighbors()`、`solveStretching/solveBending`、
    `preSolve/solve/postSolve`、默认 `bendingCompliance = 1.0` 与 `stretchingCompliance = 0.0`）
  - 讲义 PDF：<https://matthias-research.github.io/pages/tenMinutePhysics/14-cloth.pdf>
- Müller, Heidelberger, Hennix, Ratcliff (2006). *Position Based Dynamics*，第 4 章 Cloth Simulation
  <https://matthias-research.github.io/pages/publications/posBasedDyn.pdf>
  （顶点质量规则、拉伸约束、二面角弯曲约束、自碰撞、刚体双向作用、气球压力约束）
- Macklin, Müller, Chentanez (2016). *XPBD*（式 17/18，与本 demo 的简化版对照）
  <https://matthias-research.github.io/pages/publications/XPBD.pdf>

## 待研究

- [ ] 自碰撞：空间哈希 + 顶点-三角形约束 `C = (q-p1)·n - h`（原文 §4.3）
- [ ] 刚体与布的双向作用：`m_i·Δp_i/dt` 的冲量回传（原文 §4.2）
- [ ] 长程约束（Long Range Attachments, Kim 2012）解决不可伸长布的过度拉伸
- [ ] 二面角弯曲的解析梯度（原文附录 A）替换本 demo 的数值差分
