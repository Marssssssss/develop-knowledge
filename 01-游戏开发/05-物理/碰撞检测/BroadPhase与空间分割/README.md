# Broad Phase 与空间分割 —— 均匀网格、动态 AABB 树、SAH-BVH

## 简介

宽相（broad phase）的任务是：在 N 个物体里把**可能**相交的候选对找出来，交给窄相（SAT / GJK-EPA）精确判定。朴素两两枚举是 O(N²)，1 万个物体就是 5×10⁷ 次 AABB 测试 —— 宽相存在的全部意义就是把这个数字压下来。

本 demo 实现并**对拍**三种主流做法：

1. **均匀网格 / 空间哈希**（spatial subdivision）
2. **动态 AABB 树**（Box2D `b2DynamicTree`，增量插入 + 定期重建）
3. **SAH-BVH**（pbrt 第四版 `BVHAggregate`，分桶近似表面积启发式）

正确性证据是四条路径在随机数据上给出**同一个**候选对集合：宽相只允许"多报"，**漏报就是 bug**。

本目录：`main.py`（模型）+ `selfcheck_broadphase.py`（90 项断言，实跑全绿）。

## 原理详解

### 〇、两条必须先说清的口径

**1）"面积"在 2D 里是周长。** pbrt 是 3D 光线追踪，用表面积 `SurfaceArea()`；Box2D 的树注释里写的 `area()` 在 2D 下同样是周长 `2*(w+h)`。本 demo 的 `AABB.perimeter()` 就是那个量。

**2）AABB 重叠是闭区间。** Box2D 的 `b2AABB_Overlaps` 用 `<=`，因此"只贴边"**算**重叠（`selfcheck` 里断言了这一点）。别想当然写成严格小于。

### 一、Box2D 的 AABB 余量（fattening）

Box2D 不是把物体真实 AABB 插入树，而是插一个**加胖过的** AABB —— 物体小幅移动时不必触发树的调整。

`constants.h`：

| 常量 | 值 | 含义 |
| --- | --- | --- |
| `B2_LINEAR_SLOP` | 0.005 m | 碰撞与约束容差，"数值上显著、视觉上不显著" |
| `B2_SPECULATIVE_DISTANCE` | `4*slop` = 0.02 m | 有限推测碰撞距离，抑制抖动 |
| `B2_MAX_AABB_MARGIN` | 0.05 m | 余量上限（注释里说"保持余量小能减少接触数、反而更快"） |
| `B2_AABB_MARGIN_FRACTION` | 0.125 | 小物体的余量按最大尺度的这个比例取 |
| `B2_TIME_TO_SLEEP` | 0.5 s | 静止多久后休眠 |

`shape.c` 的 `b2ComputeShapeMargin()`：多边形取**质心到顶点最大距离**、圆取半径、胶囊取"半长 + 半径"、线段取半长，最后统一

```text
margin = min(B2_MAX_AABB_MARGIN, B2_AABB_MARGIN_FRACTION * margin)
```

实测两个分支都会命中：单位正方形（质心到顶点 0.7071）算出来 0.0884 被上限夹成 **0.05**；边长 0.1 的小正方形算出 **0.00884**，走比例分支。

**一个容易搞反的点**：`b2UpdateShapeAABBs()` 里**静态体用的是 `speculativeDistance`（0.02）而不是 `aabbMargin`**，注释写明"Smaller margin for static bodies. Cannot be zero due to TOI tolerance."。因此在小物体上，**静态体的余量反而比动态体更大**。

### 二、均匀网格 / 空间哈希

把空间切成边长 `cell` 的格子，每个物体登记进它**覆盖的所有格子**，同格内的物体两两测试。

代价：O(N) 登记 + 每个格子内部的局部两两。物体尺度与 `cell` 相当时效果最好；**尺度差异大**（一个大物体横跨几十个格子）会退化 —— 本 demo 断言了"跨格物体被登记进多个格子"，也就是说**同一对会被多个格子重复命中，必须显式去重**，不去重就会拿到重复对。

### 三、Box2D 动态 AABB 树

`dynamic_tree.c` 是一棵二叉 AABB 树，叶子是代理（proxy），内部节点存孩子的联合盒。

**插入**：走贪心下潜，对每个候选兄弟算

```text
直接代价 = area(union(兄弟, 新盒))
继承代价 = 新盒上溯后各祖先面积的增量
代价     = 直接代价 + 继承代价
```

当候选是**内部节点**时，还要再取一个下界（"插到它的某个后代旁边"可能更便宜）：`继承 + (直接 - 该孩子自身面积) + 新盒面积`。源码注释写得很直白：除了 B/C 是内部节点的情况，其余代价都能直接用兄弟代价公式算出来，所以必须贪心。

**旋转（rotation）**：`b2InsertLeaf()` 有个 `shouldRotate` 参数 —— **逐个插入时传 `true`、整树重建时传 `false`**。重建那轮刚排好序，再旋转纯属浪费。本 demo 未实现旋转，README 标注。

**refit 与 rebuild（两阶段）**：

```c
static inline bool b2NeedsRebuild( const b2DynamicTree* tree ) {
    return b2IsNodeMoved( tree->nodes + B2_ROOT_NODE ) || tree->dfsOrdered == false;
}
```

两个触发条件缺一不可，而 `b2DynamicTree_Rebuild()` 里还藏着一句关键注释：

> An unordered tree is rebuilt even when nothing moved, the sweep refit needs the order.

也就是说：**`dfsOrdered == false` 时即使一个物体都没动，也要重建**。本 demo 实测：增量插入把 `dfsOrdered` 打成 `False`，此时 `rebuild(fullBuild=false)` 明明"没动"却照样重建；重建之后 `rebuild(false)` 才返回 0（不排序）。

**移动标记**：`mark_moved` 沿父链一路传到根，所以判据只看根。`broad_phase.c` 随后收集"有节点移动过的兄弟对"，把这些子树两两碰撞 —— 源码注释点名参考 *Real-time collision detection* §6.3.2，并说这比"对每个移动过的代理都查一遍"更快。

**重建阶段踩到的坑（实测）**：重建时必须**复用原来的叶子节点对象**。本 demo 第一版新建了叶子节点，结果外部持有的旧叶子全部变成孤儿 —— 之后再 `mark_moved()` 根本传不到树上，`needs_rebuild()` 永远是 `False`。这是"重建"这类操作最典型的建模陷阱：树的连通性换了，但外部引用还指着旧对象。

### 四、pbrt 的 SAH-BVH

与网格（空间 subdivision）相对，BVH 是**图元 subdivision**：每个图元在层次里**只出现一次**，因此内存有界 —— 每叶一个图元的二叉 BVH 节点总数是 **2n-1**（n 个叶 + n-1 个内部节点），本 demo 在 n=8 上断言了这一点。

建树（pbrt 第四版 `buildRecursive`）：

1. 取所有图元质心的包围盒，选**跨度最大的那个轴** `MaxDimension` 做分裂维度；
2. **质心包围盒跨度为 0 → 直接成叶**（源码注释：这种情况下没有任何分裂方式有效）；
3. 否则用**分桶近似 SAH**：`nBuckets = 12`，前向扫累加下侧 `countBelow * areaBelow`，后向扫累加上侧，得到每个候选分裂的代价；
4. `minCost = 1/2 + minCost / bounds.SurfaceArea()`（遍历代价常数 1/2，相对求交代价 1）；
5. `leafCost = n`，只有 `n > maxPrimsInNode || minCost < leafCost` 才继续分裂。

pbrt 明确说 BVH 比 kd-tree **建得更快、数值更稳健、更不容易因舍入误差漏掉交点**，因此是 pbrt 的默认加速结构。

**叶内枚举是必须的（实测漏报）**：本 demo 的 `bvh_pairs()` 第一版只做"左子树 × 右子树"的兄弟交叉，结果**所有图元完全重合**时（质心全同 → 根就是一个装着全部 5 个图元的叶子）一个候选对都产不出来。真实引擎必须在叶子内部再两两枚举。这条不是理论边角 —— 堆叠在一起的一堆物体就是这种构型。

## 对比

| | 均匀网格 | 动态 AABB 树 | SAH-BVH |
| --- | --- | --- | --- |
| 归属 | 空间 subdivision | 空间 subdivision | **图元** subdivision |
| 图元是否重复登记 | 会（跨格时） | 不会 | 不会 |
| 适合 | 尺度相近、分布均匀 | **持续移动**的物体（增量更新） | 静态/批量重建、求交代价高 |
| 更新方式 | 每帧重算桶 | 增量插入 + 定期 refit/rebuild | 通常整体重建 |
| 尺度差异大时 | 退化明显 | 稳健 | 稳健 |
| 典型出处 | 粒子/流体空间哈希 | Box2D `b2DynamicTree` | pbrt `BVHAggregate` |

四路对拍结论（6 组随机数据，每组 40 个盒子）：三者的候选集都**真包含**朴素枚举的真值，且都远小于 C(n,2) —— 即"不漏报，且确实省了"。

## 环境

- Python 3.13，仅用标准库 `math` / `random`。无第三方依赖、不联网。

## 运行方式

```bash
cd 01-游戏开发/05-物理/碰撞检测/BroadPhase与空间分割
python selfcheck_broadphase.py    # 输出：BroadPhase与空间分割: 90 项断言全部通过
```

## 关键代码

```python
# Box2D 插入代价：直接代价(联合周长) + 继承代价(祖先周长增量)
direct    = union(sibling_box, new_box).perimeter()
inherited = 0.0
cur, box = sibling.parent, new_box
while cur is not None:
    inherited += union(cur.aabb, box).perimeter() - cur.aabb.perimeter()
    box = union(cur.aabb, box)
    cur = cur.parent
cost = direct + inherited

# 重建判据：root 被标记 moved 或 dfsOrdered 为 False（后者即使没动也要重建）
def needs_rebuild(self):
    return self.root.moved or (self.dfs_ordered is False)

# pbrt 分桶 SAH：12 桶，前向扫 + 后向扫，代价 1/2 + Σ/面积
min_cost = PBRT_TRAVERSAL_COST + min_cost / bounds.perimeter()
leaf_cost = float(len(idxs))
if leaf_cost <= min_cost:
    return make_leaf(idxs, bounds)
```

## 性能边界

- **网格**：`cell` 取物体平均尺度的 1~2 倍最好；物体尺度跨几个数量级时，大物体会横跨海量格子，退化到接近 O(N²)。时间 O(N + Σ格子内 k²)，空间哈希可以避免为空白格子开数组。
- **动态树**：单次插入 O(log N) 期望、最坏 O(N)（贪心下潜每层可能都要算继承代价）；`refit` 是 O(移动数 × 树高)。Box2D 用**余量**换取"小移动不触发调整"，这是它比逐帧重建快的关键。
- **SAH-BVH**：建树 O(N log N)（每层一次分桶扫描），查询 O(log N)；但**物体一动就得重建**，因此只适合静态几何或低频重建。pbrt 用 12 个桶近似全 SAH（精确 SAH 要对每个候选分裂都扫一遍，代价高得多）。
- 三者都只做 AABB 级剔除；**真正的相交判定**交给窄相（见同目录 [`SAT与EPA/`](./SAT与EPA/) 与 [`GJK/`](./GJK/)）。

## 注意事项与常见坑

1. **宽相漏报 = 穿模**。任何加速结构的正确性底线是"候选集 ⊇ 真值"，本 demo 用四路对拍来守住。
2. **网格忘记去重** —— 跨格物体会在多个格子里重复命中同一对。
3. **重建阶段新建叶子对象** —— 外部持有的旧引用变孤儿，`mark_moved` 传不到根，`needs_rebuild()` 恒假，树再也不更新（本 demo 实测踩到）。
4. **误以为"没动就不用重建"** —— `dfsOrdered == false` 本身就触发重建，源码注释写得很清楚。
5. **把静态体的余量也用 `aabbMargin`** —— 静态体走的是 `speculativeDistance`，而且不能为 0（TOI 容差需要）。
6. **BVH 只交叉兄弟子树** —— 叶子内多图元时必须额外两两枚举，否则"全部重合"这类构型**整片漏报**。
7. **质心包围盒退化为 0** —— pbrt 直接成叶；此时任何分裂策略都无效，不要试图强行分裂。
8. **改 `B2_LINEAR_SLOP` 之类常量** —— 源码注释挂了 `@warning modifying this can have a significant impact on stability`，改之前先跑基准。

## 参考资料

（以下均为本轮**实读**并落盘核对的原文/源码）

- Box2D v3.1 源码（MIT）：
  - `include/box2d/constants.h` — <https://github.com/erincatto/box2d/blob/main/include/box2d/constants.h>
  - `src/shape.c` — `b2ComputeShapeMargin()` / `b2UpdateShapeAABBs()`
  - `src/dynamic_tree.c` / `src/dynamic_tree.h` — 插入代价、`b2InsertLeaf(shouldRotate)`、`b2NeedsRebuild()`、`b2DynamicTree_Rebuild()`
  - `src/broad_phase.c` — 移动兄弟对收集（引用 *Real-time collision detection* §6.3.2）
- pbrt 第四版 — *Bounding Volume Hierarchies*（7.3）：
  <https://pbr-book.org/4ed/Primitives_and_Intersection_Acceleration/Bounding_Volume_Hierarchies>
  （2n-1 节点数、`SplitMethod{SAH, HLBVH, Middle, EqualCounts}`、`nBuckets = 12` 分桶、遍历代价 1/2）
- Christer Ericson, *Real-Time Collision Detection* §6.3.2（Box2D 源码注释引用）

## 待研究

- [ ] Sweep and Prune（一维排序扫描，Ten Minute Physics 第 23 期专题）
- [ ] `b2RotateNodes()` 的具体旋转判据（本模型未实现旋转）
- [ ] Morton 码 / LBVH 与 HLBVH（pbrt 的 `SplitMethod::HLBVH`）
