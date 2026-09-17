# Forward+ 分块光源剔除(Tiled / Clustered Light Culling)

## 简介

Forward+(tiled forward shading)在前向渲染前面加一个**光源剔除阶段**:把屏幕切成 tile 网格,为每个 tile 预先算出"哪些光源可能与它相交"的索引列表;着色时每个片元只遍历自己所在 tile 的列表,而不是全场景光源。它拿到了接近延迟渲染的光源扩展性,又保留了前向渲染的 MSAA、透明物体与材质多样性。

- **Light Culling Pass**:屏幕 2D tile 网格 + 每 tile 光源索引列表(compute pass)
- **Tile Frustum**:每 tile 的 4 个过视点侧平面构成"微型视锥"
- **球-视锥测试**:点光源按球体对 4 平面做有向距离判定
- **深度区间测试**:tile 的 [zMin, zMax] 替代近/远平面(来自深度 pre-pass 归约)
- **Clustered**:再按深度切片(指数分布)细分,解决 2D tile 的"深度不连续过度指派"

规模口径(3dgep,GTX 680 级硬件 1080p):forward ≈ 100 盏动态光,deferred ≈ 2000-2500 盏,**Forward+ ≈ 5000-6000 盏**。

## 原理详解

### 三 pass 结构

```
① Light Culling(compute)         ② Opaque Pass            ③ Transparent Pass
   每 tile 建两套列表:               正常前向着色:            半透明几何:
   opaque [zMin, zMax]              按 SV_POSITION 查        近端放宽为 0,
   transparent [0, zMax]            tile 光源列表            用 transparent 列表
```

### Tile 视锥四平面推导

视空间约定**视点在原点**(3dgep 的关键简化)→ tile 的 4 个侧面全部过原点 → 平面方程 `N·x + d = 0` 中 **d = 0**:

1. tile 四角(TL/TR/BL/BR)从像素坐标 → NDC → 逆投影到视空间远平面;
2. 两侧面 = 过原点与 tile 一条边两条角射线的平面,法线 = 两角射线**叉积**:
   `N_left = normalize(TL × BL)`,`N_right = BR × TR)`,`N_top = TR × TL`,`N_bottom = BL × BR`;
3. 绕序决定法线方向,统一翻转到指向 tile 中心射线一侧。

### 球-视锥 + 深度区间联合判定

点光源 = 视空间球(球心 C,半径 r = Range):

```
平面判定:任一 i 有 N_i · C < −r  → 整球在该平面外侧 → 剔除
深度判定:保留 ⇔ (C.z − r ≤ zMax) 且 (C.z + r ≥ zNear)
         opaque: zNear = tile zMin;transparent: zNear = 0
```

该判定**保守**:球贴着角点但未进入视锥时会误保留——只损失少量性能,不影响正确性。聚光灯(锥体)另有 Frustum-Cone 精确判定;方向光是全屏四边形,不参与体积剔除。

### Compute Shader 线程组织

`[numthreads(16,16,1)]` → **一个线程组恰好处理一个 16×16 tile**;dispatch 尺寸 = ⌈W/16⌉ × ⌈H/16⌉:

- 组内 256 线程用 `InterlockedMin/Max`(asuint 位模式保序)归约 tile 深度得 zMin/zMax;
- 跨步循环(`i = GI; i += 256`)均匀分摊全部光源;
- 命中光源先写 groupshared 局部列表,线程 0 用单计数 buffer 的 `InterlockedAdd` 在全局索引表预约连续区段,全组再一次性写出两层结构 `LightGrid{offset,count} + LightIndexList`。

### Tiled vs Clustered

2D tile 跨越"近轨道 + 远墙"时,两层深度的光都进列表(**过度指派**);clustered 把 tile 再按指数深度切片 `Z = near·(far/near)^(s/N)` 细分,像素只看自己 cluster 的光。**分块维度与前向/延迟正交**:Battlefield 3 是 tiled deferred,DOOM 2016 是 clustered forward。

## 对比 / 选型

| 维度 | Forward | Deferred | Forward+ / Clustered |
| --- | --- | --- | --- |
| 1080p 动态光上限 | ~100 | ~2500(仅不透明) | 5000-6000 |
| G-buffer 带宽 | 无 | 8.29MB×N 张 | 无 |
| MSAA / 透明 | 原生支持 | 受限 | 原生支持(透明用 [0,zMax] 列表) |
| 材质模型 | 逐物体 | 共用一套 | 逐物体 |
| 额外成本 | — | 带宽 | 每帧一次 culling compute pass |

## 环境准备

- Python 3.8+ / Go 1.18+,无第三方依赖(纯算法模拟)

## 运行方式

```bash
python3 python/main.py
go run go/main.go
```

## 关键代码片段

```python
def build_tile_frustum(tx, ty):
    """角射线叉积 → 4 侧平面,过视点(d=0),法线统一指向视锥内部。"""
    tl, tr = pixel_ray(tx * TILE, ty * TILE), pixel_ray((tx + 1) * TILE, ty * TILE)
    bl, br = pixel_ray(tx * TILE, (ty + 1) * TILE), pixel_ray((tx + 1) * TILE, (ty + 1) * TILE)
    planes = [cross(tl, bl), cross(br, tr), cross(tr, tl), cross(bl, br)]
    out = []
    for n in planes:                       # 翻转到指向 tile 中心射线
        n = normalize(n)
        if dot(n, center) < 0:
            n = (-n[0], -n[1], -n[2])
        out.append(n)
    return out

def cull_tile(lights, planes, zmin, zmax, transparent=False):
    near = 0.0 if transparent else zmin     # 透明列表近端放宽为 0
    for i, l in enumerate(lights):
        if not sphere_in_frustum(l.c, l.r, planes):   # 任一平面 ρ < −r → 剔除
            continue
        if l.c[2] - l.r > zmax or l.c[2] + l.r < near:  # 深度区间不相交 → 剔除
            continue
        out.append(i)
```

## 性能与边界

- 剔除判定 O(4) 每光源每 tile;culling pass 本身是每帧一次的轻量 compute
- 深度不连续 tile 的实测(本 demo):近墙像素在 2D tiled 下列表 4 盏,clustered 为 0——过度指派被深度切片消除
- 光源数据放 `StructuredBuffer` 而非 constant buffer:后者 64KB 上限只够 ~570 盏,纹理内存放 10000 盏约 1.12MB

## 注意事项与常见坑

- **"clustered = deferred"是常见误读**:分块维度与渲染架构正交,四种组合都存在
- **深度归约用 asuint 整型原子**:IEEE754 正浮点的位模式保序,直接 InterlockedMin/Max 即可
- **透明列表必须把近端放宽到 0**:半透明几何可能出现在相机与最浅不透明表面之间
- **不要引用统一的"最大光源数"**:DOOM 的 256/cluster×3072 clusters 是单作配置
- **方向光不进剔除列表**(全屏四边形、无 Range),要单独合并进着色

## 参考资料(实际阅读过的权威来源)

- [3dgep — Forward+ Rendering](https://www.3dgep.com/forward-plus/) — 两阶段架构、双列表设计、光源数据量(112B/盏)、StructuredBuffer vs ConstantBuffer、性能口径(5000-6000 盏 @1080p)
- [Deferred from scratch — Tiled & Clustered](https://mightyprofessionalgaming.com/tutorials/deferred-from-scratch) — tiled/clustered 与 deferred 的正交性、指数深度切片公式、DOOM/BF3 组合实例
- [MegSesh/Project5-WebGL-Clustered-Deferred-Forward-Plus(UPenn CIS 565)](https://github.com/MegSesh/Project5-WebGL-Clustered-Deferred-Forward-Plus) — cluster 光照列表的 WebGL 实现描述
