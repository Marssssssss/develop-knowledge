# GJK 碰撞检测算法（Gilbert–Johnson–Keerthi）

## 简介

- GJK 是现代物理引擎（Bullet、Box2D、Jolt、PhysX 内部）narrow-phase 的标准算法：**判断任意两个凸形状是否相交**，并可扩展为求最小间距。
- 核心洞察：`A、B 相交 <=> 它们的 Minkowski 差 A−B 包含原点`。GJK 用 support 函数在 A−B 边界上取点，迭代构建 simplex（2D：点→线段→三角形）试探包围原点，**全程不显式构造 A−B**。
- 关键概念：
  - **Minkowski 差**：`A−B = {a−b | a∈A, b∈B}`，两凸形状的差仍是凸集
  - **support 函数**：`S(d)` 返回形状在方向 `d` 上最远的点，是 GJK 唯一的形状接口
  - **simplex**：单纯形，2D 中最多 3 个点；每次迭代向"更接近原点"演化
  - **Voronoi 区域测试**：用点积判断原点落在 simplex 的哪个区域
- 历史背景：由 E.G. Gilbert、D.W. Johnson、S.S. Keerthi 于 1988 年在 IEEE Journal of Robotics and Automation 论文中提出，最初用于机器人运动规划中快速求凸体间距，后被游戏物理引擎广泛采用为通用凸体相交测试。

## 原理详解

### 1. 工作机制（分步）

1. 取初始方向 `d`（任意；用两形状中心连线利于早退），算出 Minkowski 差上的第一个 support 点，加入 simplex，`d` 反向
2. 循环：沿 `d` 取新 support 点 `w = S_A(d) − S_B(−d)`，加入 simplex
3. **分离测试**：若 `w·d ≤ 0`，即沿 `d` 的最远点都没越过原点 ⇒ 整个 A−B 在垂直 `d` 的直线一侧 ⇒ 不含原点 ⇒ **不相交，退出**
4. **演化 simplex**：线段情形取垂直 AB 指向原点的方向；三角形情形做 Voronoi 区域测试，丢弃无用顶点、更新方向
5. 若原点被三角形包围 ⇒ A−B 包含原点 ⇒ **相交，退出**；否则回到 2

### 2. support 函数与 Minkowski 差（核心 API）

```text
S_shape(d)     = argmax{ p·d : p ∈ shape }          # 形状在 d 方向的最远点
S_{A−B}(d)     = S_A(d) − S_B(−d)                   # 差集的 support = 两形状 support 相减

推导（arXiv:2007.12045, Eq.3）:
max_{p∈P,q∈Q} (v·p − v·q) = max_{p∈P}(v·p) − max_{q∈Q}(−v·q)
```

- 参数 `d` 不需要归一化；返回值是差集边界上的一个顶点
- 对多边形是 O(n) 顶点扫描；对圆是 `center + r·d̂`（O(1)）；对凸包可爬山优化到平均 O(√N)
- **正因为只用 support 接口**，GJK 无需修改即可支持任意凸形状（球、胶囊、凸包……）

### 3. simplex 演化与 Voronoi 区域（2D）

```text
                C
               / \
              /   \      abPerp ⊥ AB，指向三角形外侧（背离 C）
        B ···+····· A    acPerp ⊥ AC，指向外侧（背离 B）
             ↑      ↑
        acPerp   abPerp
   （A 总是最后加入的 support 点；AB = B − A，AO = −A）
```

- **线段（2 点）**：`d = (AB × AO) × AB`（三重积），垂直 AB 且指向原点一侧
- **三角形（3 点）**：
  - `abPerp·AO > 0` ⇒ 原点在 AB 外侧区域 ⇒ 丢弃 C，`d = abPerp`
  - `acPerp·AO > 0` ⇒ 原点在 AC 外侧区域 ⇒ 丢弃 B，`d = acPerp`
  - 否则 ⇒ 原点在三角形内 ⇒ 碰撞
- 三重积展开：`(a × b) × c = b(c·a) − a(c·b)`；2D 中退化叉积即标量 `a.x·b.y − a.y·b.x`，展开式避免显式构造 3D 向量
- 3D 中 simplex 为四面体（4 点），垂直向量有无穷多，**必须**用三重积写法

### 4. 终止条件与收敛

| 条件 | 判据 | 结论 |
| --- | --- | --- |
| 分离 | 新 support 点 `w·d ≤ 0` | 不相交 |
| 相交 | simplex 包含原点 | 相交 |
| 收敛（求距离版） | 新点不再比当前最近点更近 | 最近距离 = \|v\| |

- 典型形状 **< 10 次迭代**收敛（arXiv:2007.12045 / dyn4j 均有此量级说明）；warm-start（复用上帧 simplex）可进一步大幅减少迭代

## 对比 / 选型

| 方案 | 适用形状 | 输出 | 每对成本 | 备注 |
| --- | --- | --- | --- | --- |
| **GJK** | 任意凸（含曲边） | 布尔 / 距离 | O(迭代×support) | 通用性最好，可配 EPA 求穿透深度 |
| **SAT**（分离轴定理） | 2D 凸多边形 / 简单 3D | 布尔 + MTV | O(边数和) | 2D 简单直接；3D 轴数爆炸（F_A+F_B+E_A×E_B） |
| 暴力三角化 | 任意（凸/凹） | 布尔 | O(n·m) | 仅适合低频离线检测 |
| GJK + **EPA** | 任意凸 | 穿透深度 + 法线 | GJK 后再扩展 | 布尔相交后求最小平移向量的标准配套 |

- GJK 只解决"是否相交/距离"；求**穿透深度与接触流形**需要 EPA（Expanding Polytope Algorithm）在 GJK 最终 simplex 基础上向 A−B 表面扩展
- 凹形状需先凸分解（HACD/VHACD）再逐对调用 GJK

## 环境准备

- 操作系统：任意（纯计算，无平台依赖）
- 语言版本：C ≥ C99（需 `-lm`）；Python ≥ 3.8；Go ≥ 1.21
- 依赖：无（全部标准库 / 零依赖）

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra gjk.c -o gjk -lm
./gjk
```

### Python

```bash
python3 gjk.py
```

### Go

```bash
go run gjk.go
```

三个版本输出相同：5 组测试用例（重叠 / 远离 / 有间隙 / 薄重叠 / 五边形 vs 三角形），每组打印判定结果、迭代次数与 PASS/FAIL。

## 关键代码片段

Minkowski 差上的 support（三语言一致的核心）：

```c
/* C：S_{A-B}(d) = S_A(d) - S_B(-d)，无需构造整个差集 */
static Vec2 mink_support(Poly a, Poly b, Vec2 d)
{
    return vec_sub(support_poly(a, d), support_poly(b, vec_neg(d)));
}
```

主循环的两个终止条件：

```python
# Python
new_pt = mink_support(a, b, d)
simplex.append(new_pt)
if dot(new_pt, d) <= 0:      # ① 最远点未越过原点 → 整个差集在一侧 → 分离
    return False, iters
if handle_simplex(...):       # ② simplex 包围原点 → 相交
    return True, iters
```

三角形 Voronoi 区域测试（对应"原理详解"第 3 步）：

```go
// Go
abPerp := tripleProduct(ac, ab, ab) // ⊥ AB，背离 C
acPerp := tripleProduct(ab, ac, ac) // ⊥ AC，背离 B
if abPerp.dot(ao) > 0 { /* 原点在 AB 外侧：丢弃 C */ }
if acPerp.dot(ao) > 0 { /* 原点在 AC 外侧：丢弃 B */ }
return true                          // 否则原点在三角形内
```

## 性能与边界

- 单次相交测试成本 ≈ `迭代数 × (|A|+|B|)` 次点积；典型 < 10 次迭代，多边形顶点数少时近似常数时间（mysimulator.uk 综述给出 GJK+EPA 微秒级/对的量级）
- Minkowski 差显式构造是 O(|A|·|B|) 个点——GJK 通过 support 抽象完全绕开
- 本实现仅做布尔测试；求距离需保留"最近点 v"并在收敛判据 `|w−v| < ε` 时返回 `|v|`（arXiv 论文 Algorithm 1）
- 迭代上限（本实现 64）之外仍未收敛 ⇒ 浮点退化，保守返回分离

## 注意事项与常见坑

- **`AB = B − A` 而非 `A − B`**：dyn4j 教程早期版本写反，评论区已修正；方向弄反会导致区域判断全部失效
- **原点落在 simplex 边上**：三重积得到零向量 → 死循环或误判。处理：检测 `len2(d) < ε`，视为"接触"，按业务决定算不算碰撞（本实现按碰撞处理）
- **边缘接触（恰好 touching）**：`w·d ≤ 0` 用 `≤` 会把"恰好到 0"判为分离——接触语义需调用者自定义
- **浮点精度**：接近共线/极薄三角形会退化；生产实现需要 ε 容差、最大迭代数、simplex 退化检测（inferensys 综述列出的三类保护）
- **warm-start**：物理引擎每帧复用上一帧的 simplex 作初值，缓慢移动的形状可 1-2 次迭代出结果
- **只有凸形状适用**：凹形状先凸分解；绕序（winding）如果用行列式判断左右，注意 simplex 每轮缠绕方向可能变化，需每次重算

## 参考资料（实际阅读过的权威来源）

- [Collision Detection for Convex Shapes — dyn4j (William Bittle)](https://dyn4j.org/2010/04/gjk-gilbert-johnson-keerthi/) — GJK 2D 实现权威教程：support/simplex 演化/Voronoi 区域/终止条件/伪代码全文精读，含评论区对 AB 方向、零三重积等勘误
- [High Precision Real Time Collision Detection (arXiv:2007.12045v2)](https://arxiv.org/pdf/2007.12045v2) — Minkowski 差数学定义、support 函数分解推导（Eq.3）、GJK 距离算法（Algorithm 1）与收敛条件
- [What is the GJK Algorithm? — Inferensys Glossary](https://inferensys.com/glossary/software-defined-manufacturing-automation/industrial-robotics-path-planning/gilbert-johnson-keerthi-gjk-algorithm) — GJK 在机器人窄相位中的应用定位、Johnson 距离子算法、EPA 配套关系与数值鲁棒性要点
- 背景引用：E.G. Gilbert, D.W. Johnson, S.S. Keerthi, "A fast procedure for computing the distance between complex objects in three-dimensional space", IEEE J. Robotics and Automation, 1988（原始论文，经上述来源转述）
