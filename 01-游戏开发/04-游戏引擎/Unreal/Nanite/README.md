# Nanite 虚拟化几何

## 简介

- Nanite 是 UE5 的虚拟化微多边形几何系统：让美术**直接导入影视级三角面数**的模型，引擎自动做 LOD、剔除与流式加载，"三角形数量"第一次不再是需要手工预算的资源。
- 官方口径：「导入时网格被分析并分解成**层级化的三角形簇（hierarchical clusters）**；渲染时按摄像机视图在**不同细节层级间实时切换簇**，同一物体内相邻簇无缝衔接不产生裂缝；数据按需流式载入，只有可见细节需要驻留内存。Nanite 跑在自己的渲染 pass 里，完全绕开传统 draw call。」
- 本 demo 用 Python + Go 复刻 SIGGRAPH 2021《Nanite A Deep Dive》里最核心的四块机制：**cluster 层级构建、误差单调与组决策、可并行的 LOD cut 选择、visibility buffer 的材质解耦**。

## 原理详解

### 1. 一切都以 cluster 为单位

一个 cluster **恰好 128 个三角形**（原文："In our case a cluster is 128 triangles"）。选 128 的理由是光栅器/primitive shader 的友好粒度——**分组只对 cluster 做，绝不对单个三角形做**（"We do not form groups with individual triangles"），这样才能正好填满 128 三角形的 cluster。

### 2. 构建：group → merge → simplify 50% → split

```text
while NumClusters > 1:
    ① 用图划分（METIS）把 cluster 分组，边权 = 两簇共享的三角形边数
       → 最小割 = 最少的「锁定边」
    ② 把组内三角形合并成一个共享列表
    ③ 简化到 50% 三角形（边折叠，优先折叠误差最小的边）
    ④ 再切成 128 三角形一簇  →  簇数减半
```

重复到只剩 1 个 cluster 为止。本 demo 的 64 叶簇产出层级：**64 → 32 → 16 → 8 → 4 → 2 → 1**。

结构最终是一张 **DAG 而不是树**：组里的每个 cluster 都是该组所有 parent 的孩子（4 个孩子 → 2 个 parent，不是二叉）。

### 3. 裂缝（LOD cracks）与"组决策"

独立 cluster 各自决定 LOD 就会在边界上出现裂缝。Naive 解是**锁定共享边界边**，但边界会在多层之间持续存在、堆积"密集垃圾"（dense cruft），导致连"每层减半"都做不到。Nanite 的解法：

- **把必须一致的 cluster 分成一组，强制它们做同一个 LOD 决策**；
- 怎么在 GPU 上并行做到？不通信——**同输入同输出**：让组内所有 cluster 共享**同一份 unioned error 与包围球**；
- 边界锁定范围**逐层交替**（这一层的边界在下一层变成内部），垃圾就堆不起来。

### 4. 运行时：一次局部判断切出 cut

选哪一层？按**屏幕空间投影误差**：简化器给出物体空间标量误差，投影到屏幕时取包围球内**投影误差最大的那一点**。渲染判据完全局部：

```text
绘制 cluster c  ⟺  ParentError(c) > 阈值  &&  ClusterError(c) <= 阈值
剔除             ⟺  ParentError(c) <= 阈值 ||  ClusterError(c) > 阈值
```

"局部"意味着可以**在 GPU 上并行求值**，不必从根遍历 DAG。代价是要保证 cut 唯一：构建期强制**误差单调**（parent 的 error 与 bounds 都必须 ≥ 孩子），这样从根到叶的判定只会从"否"翻到"是"一次。本 demo 断言了 4 个阈值 × 全部 2048 条路径，每条恰好命中 1 个 cluster。

阈值取 **1 像素**：误差亚像素时人眼分不出，TAA 会把这点差异当锯齿抹平——所以 Nanite **不需要** geomorphing 或交叉淡入淡出。

剪枝用的树按 **ParentError**（孩子 ParentError 的最大值）而不是 ClusterError 组织：某个节点的 ParentError 已足够小 ⇒ 整棵子树都不可能被选中。

### 5. 剔除与 visibility buffer

- 三级剔除：cluster 包围盒**视锥剔除**、对 **Hierarchical Z-Buffer** 的遮挡剔除（可用上一帧深度做两遍剔除）、背面 cluster 剔除。
- 求交结果写 **visibility buffer**：`Depth : InstanceID : TriangleID`（原文强调"Object ID + Triangle ID 才叫 visibility buffer，带 UV/法线的那是 deferred texturing"）。
- 好处是**把可见性与材质解耦**：三角形每视图只光栅化一次，材质每像素只求值一次，overdraw 不再放大材质开销。

### 6. 流式加载

任何一层 cut 都能被标成叶，更深的数据不必驻留；需要时按缺请求，久未绘制则驱逐——和 virtual texturing 一个思路。

## 对比 / 选型

| 维度 | 传统 LOD（每网格几档） | Nanite |
| --- | --- | --- |
| LOD 粒度 | 整个网格 | 128 三角形的 cluster，同一物体内可混合 |
| 切换 | 需要 morph/淡入，否则跳变 | 误差 < 1 像素 ⇒ 不可察觉，TAA 兜底 |
| 裂缝 | 靠人工对齐 | 组决策 + 逐层交替锁定边 |
| 绘制提交 | 每网格若干 draw call | 自己的 pass，1 次 DrawIndirect 起步 |
| 材质 | 深度 prepass 或 overdraw | visibility buffer：可见性一次、材质一次/像素 |

官方提醒的适用边界：实例数、单网格三角形数、材质复杂度、输出分辨率仍需实测；天空球这类"三角形在屏幕上很大、不遮挡任何东西"的物体开 Nanite 收益不大。

## 环境准备

- Python 3.8+（标准库，零依赖）
- Go 1.21+（零依赖）

## 运行方式

```bash
python3 python/selfcheck_nanite.py   # 27 条断言
cd go && go run nanite.go
```

## 关键代码片段

构建循环里"组内共享 + 强制单调"这两步（Python 版）：

```python
center, radius = sphere_union(group)
union_err = max(c.error for c in group)
for c in group:                       # 同组必须做同样的 LOD 决策
    c.center, c.radius, c.error = center, radius, union_err
...
err = max(raw_error(level, len(nxt), gi), union_err)   # 强制 parent 误差 >= 孩子
pc.children = list(group)             # 组内每个 cluster 都是它的孩子 ⇒ DAG
for c in group:
    c.parents.append(pc)
```

并行 LOD 选择与 ParentError 剪枝：

```python
if parent_view_error(c, camera) > threshold and view_error(c, camera) <= threshold:
    selected.append(c)
...
bound = max(parent_view_error(ch, camera) for ch in c.children)
if bound <= threshold:                # 子树里不可能再有被选中的
    return
```

## 性能与边界

- 目标：**渲染成本随屏幕分辨率缩放，而不是随场景复杂度缩放**（原文："should scale with screen resolution, not scene complexity"）。
- 本 demo 实测（64 叶簇、相机 z=10、阈值 1 像素）：阈值放大时选中数 `64 → 32 → 12 → 4`（更粗）；相机从 6 拉到 40 时 `56 → 8`；ParentError 剪枝把评估从 127 个降到 63 个且结果完全一致。
- 材质求值：1920×1080、一半像素 4 倍 overdraw 时，forward 需要 5 184 000 次，visibility buffer 恒为 2 073 600 次（= 像素数）。
- LOD 选择、剔除、光栅化都在 GPU 上用 **persistent threads**（自己写的 mini job system）跑，避免每个阶段一次 dispatch。

## 注意事项与常见坑

- **误差必须单调，否则 cut 不唯一**：不强制时同一个阈值下一条路径上可能出现多个"合格"cluster，画面就会出现重叠/空洞。本 demo 里 55 个 parent 的误差是被强制抬高的——说明这一步不是可有可无的。
- **别把 DAG 当树遍历**：同一个 cluster 有多个 parent，朴素递归会重复访问（实测不去重时评估数膨胀到 512，是 cluster 总数的 4 倍）。
- **剪枝判据用 ParentError 而不是 ClusterError**：用错会得到不一样的选中集合。
- **组内共享的是"并集"误差与包围球**，不是平均值——取平均会让不同簇做出不同决策，裂缝回来。
- **leaf（LOD0）误差为 0**：这保证任何阈值下都至少有叶子可选，也就是官方说的"相机凑得够近就会画出导入的原始三角形"。
- **误差是物体空间标量**：位置误差可能有方向性、属性误差混进来后很难估准，原文承认这是难点——估计不准会直接表现为可见跳变。
- visibility buffer ≠ deferred texturing：只存 `ObjectID + TriangleID`，材质需要的属性在着色阶段按 ID 反查。

## 参考资料（实际阅读过的权威来源）

- [Brian Karis / Epic — Nanite A Deep Dive（SIGGRAPH 2021 Advances in Real-Time Rendering，PDF）](https://advances.realtimerendering.com/s2021/Karis_Nanite_SIGGRAPH_Advances_2021_final.pdf) — 128 三角形 cluster、构建操作六步、图划分(METIS)最小割、DAG 而非树、裂缝与逐层交替锁定边、投影误差与阈值 1 像素、唯一 cut 与误差单调、`ParentError > thr && ClusterError <= thr` 判据、ParentError 剪枝树、visibility buffer 定义、persistent threads、按需 streaming。
- [Epic — Nanite virtualized geometry in Unreal Engine](https://dev.epicgames.com/documentation/en-us/unreal-engine/nanite-virtualized-geometry-in-unreal-engine) — 官方口径的 cluster/LOD/流式说明、适用性建议（天空球之类例外）、Fallback Mesh 与可视化模式。
