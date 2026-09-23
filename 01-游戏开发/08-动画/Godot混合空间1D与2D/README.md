# Godot 混合空间 1D / 2D 的权重计算

## 这是什么

混合空间（BlendSpace）把「走 / 跑 / 转向」这类连续量（速度、方向）映射成若干动画的**权重**。Godot 的实现里真正有工程价值的，是两处**平局取向相反**的比较、以及 2D 的重心坐标与「点在三角形外」的回退。本 demo 把这两个文件逐行转写并断言。

## 权威来源（实际读过）

- godotengine/godot@master `scene/animation/animation_blend_space_1d.cpp`（24614 B）
- godotengine/godot@master `scene/animation/animation_blend_space_2d.cpp`（35094 B）
- `core/math/math_defs.h`：`CMP_EPSILON = 0.00001`

## 一维：找最近的两侧，线性插值

```cpp
if (pos <= blend_pos) { if (point_lower == -1 || pos > pos_lower)  { point_lower  = i; pos_lower  = pos; } }
else                  { if (point_higher == -1 || pos < pos_higher){ point_higher = i; pos_higher = pos; } }
```

- 源码是**线性扫描**，所以点位**不需要排序**（demo 用乱序点位验证）
- 只有一侧有点位时，那一侧拿满权重；两侧都有时 `p = (blend_pos - pos_lower) / (pos_higher - pos_lower)`
- 点位非均匀时插值按**实际间距**：点位 `0` 与 `3`、`blend_pos = 1` 给出 `2/3 : 1/3`

## 平局取向：插值取「下标大者」，离散取「下标小者」

`closest`（用于返回时间信息）在两种模式下的比较符号不同：

| 模式 | 判定式 | 平局时 |
| --- | --- | --- |
| `BLEND_MODE_INTERPOLATED` | `if (weights[i] >= max_weight)` | **下标更大者**胜（`>=` 会持续刷新） |
| `BLEND_MODE_DISCRETE`（1D） | `if (d < new_closest_dist)` | **下标更小者**胜（严格 `<`） |
| `BLEND_MODE_DISCRETE`（2D） | `if (d < new_closest_dist)`，`d` 用**平方距离** | 同上 |

实测：点位 `[-1, 1]`、`blend_pos = 0` 时，插值模式 `closest = 1`，离散模式 `closest = 0`。差一个比较符号，行为就相反——这是成对断言才能钉住的性质。

## 时间同步（sync）

`SYNC_MODE_CYCLIC_MUTABLE` 下，混合后的目标长度是**加权平均**，播放速率按它的倒数缩放：

```cpp
target_length += weights[i] * cached_lengths[i];   // 只统计 weights[i] > 0 且 length > CMP_EPSILON
total_weight  += weights[i];
target_length /= total_weight;
inv_target_length = (target_length > CMP_EPSILON) ? (1.0 / target_length) : 0.0;
```

实测：两个点长度分别为 `2.0` 与 `1.0`、`blend_pos = 0.5` 时目标长度 `1.5`、缩放 `1/1.5`；若某个点长度为 0，它会被**排除**（`> CMP_EPSILON` 的门），而不是把目标长度往 0 拉。`SYNC_MODE_NONE` 下源码直接把 `deltas[i]` 置为 `NAN`（不同步）。

## 二维：重心坐标与退化处理

`_blend_triangle` 用点积形式解重心坐标，先判三个顶点的精确命中，再判退化：

```
v0 = p1 - p0;  v1 = p2 - p0;  v2 = pos - p0
d00 = v0·v0;  d01 = v0·v1;  d11 = v1·v1;  d20 = v2·v0;  d21 = v2·v1
denom = d00·d11 - d01·d01
v = (d11·d20 - d01·d21) / denom      → 顶点 1 的权重
w = (d00·d21 - d01·d20) / denom      → 顶点 2 的权重
u = 1 - v - w                        → 顶点 0 的权重
```

- 三点共线时 `denom == 0`，源码**直接给 `[1,0,0]`**（不是报错）
- 三角形 `(0,0) (1,0) (0,1)` 上，`(0.25, 0.25)` 的权重是 `(0.5, 0.25, 0.25)`，重心处是各 `1/3`
- 点**不在**任何三角形内时回退：遍历所有三角形的三条边求最近点，按 `c = |a - closest| / |a - b|` 在边的两端点间插值，第三个顶点权重置 **0**。实测 `(2,0)` 投影到顶点 1 得 `[0,1,0]`，`(1,1)` 投影到 1-2 边中点得 `[0,0.5,0.5]`
- 三角形来自 `Delaunay2D::triangulate`（自动剖分），也可以手动指定；**没有三角形时插值模式直接返回空权重**

## 目录结构

```
python/blendspace.py            1D/2D 权重模型（约 200 行）
python/selfcheck_blendspace.py  59 条断言，全部实跑通过
python/main.py                  演示入口
go/main.go                      Go 侧同协议实现（人工审查 + 静态检查）
```

## 运行

```bash
cd python
python selfcheck_blendspace.py   # 断言总数 59，失败 0
python main.py
```

## 自检覆盖

1D：段内/点位上/区间外/乱序点位/非均匀间距/单点 · 插值与离散的平局取向对照 · 离散的 nearest 与边界 · sync 的加权平均、零长度点排除、FIXED 模式、`SYNC_NONE` 返回空 · 2D：重心坐标（含顶点命中、重心、共线退化、权重和为 1）、内部点、外部点回退（三个方向）、空三角形 · 离散 2D 的平方距离 nearest 与平局 · 权重和恒为 1 与非负的不变式扫描。

## 踩坑记录

1. **离散 2D 的平局用例要挑真正等距的点**：`(0.4,0.4)` 到三个顶点的平方距离是 `0.32 / 0.52 / 0.52`，最近的是 `(0,0)` 而不是我以为的等距两点；要测平局得用 `(1,1)`（到 `(1,0)` 与 `(0,1)` 都为 1）。
2. **`closest` 的平局方向靠比较符号决定**：`>=` 与 `<` 差一个字符，行为完全相反，必须用同一份配置做**成对断言**。
3. **sync 的零长度点是「排除」不是「拉低」**：判据是 `cached_lengths[i] > CMP_EPSILON`，权重仍参与 `total_weight` 吗？——不参与，两个 `if` 是同一个块，权重也没进 `total_weight`。
4. **外部点的回退会遍历所有三角形的所有边**：`best_point` 只在第一次赋值时用 `first` 放行，之后一律严格 `<`，所以平局取先遇到的边。
