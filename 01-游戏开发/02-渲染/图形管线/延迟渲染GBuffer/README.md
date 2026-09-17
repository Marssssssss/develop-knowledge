# 延迟渲染 G-Buffer(Deferred Shading)

## 简介

延迟着色把"渲染几何"与"计算光照"解耦成两个 pass:geometry pass 用 MRT 把每个像素的几何信息(位置/法线/反照率)写入 G-buffer,lighting pass 再用一个屏幕大小的四边形逐像素采样 G-buffer 计算光照。它解决的核心问题是**多光源下的计算量**:前向渲染每片元遍历全部光源,延迟渲染把光照降到"每像素一次 + 每光源覆盖区域一次"。

- **Geometry Pass**:渲染场景一次,深度测试保证 G-buffer 里只留最上层片元
- **G-Buffer**:一组屏幕大小的纹理,存位置(RGBA16F)、法线(RGBA16F)、反照率+镜面(RGBA8)
- **Lighting Pass**:逐像素从 G-buffer 重建片元数据,光照数学与前向完全相同
- **Light Volume**:为每个光源渲染一个按作用半径缩放的球体,只处理被照亮的像素
- **O(objects×lights) → O(objects+lights)**:LearnOpenGL 演示了 1847 个点光源的场景

历史:Deering 1988 年提出延迟渲染思想;现代引擎(DICE 寒霜的 Battlefield 3 等)把它与分块剔除结合成 tiled/clustered deferred。

## 原理详解

### 两阶段流程

```
[Geometry Pass]                         [Lighting Pass]
  逐物体光栅化                            全屏四边形逐像素:
  ├─ 深度测试(最上层胜出)                  ├─ texture(gPosition, uv)
  ├─ gPosition = FragPos (RGBA16F)        ├─ texture(gNormal, uv)
  ├─ gNormal   = Normal  (RGBA16F)        ├─ texture(gAlbedoSpec, uv)
  └─ gAlbedoSpec.rgb/a                    └─ 累加各光源 Blinn-Phong
     = albedo / specularIntensity
```

### G-Buffer 布局与带宽

| 纹理 | 格式 | 内容 | 每像素位数 |
| --- | --- | --- | --- |
| gPosition | RGBA16F | 世界空间位置 | 64 |
| gNormal | RGBA16F | 世界空间法线 | 64 |
| gAlbedoSpec | RGBA8 | RGB=反照率, A=镜面强度 | 32 |
| depth | D24 | 深度 | 24 |

- 1080p 下本 demo 的口径 = 184 bit/px ≈ **45.5 MiB**;LearnOpenGL 提醒:一张 32bpp 纹理在 1080p 就要 **8.29 MB**,G-buffer 是"用带宽换光照计算"
- 用 `RGBA16F` 而非 `RGB16F`:GPU 偏好 4 分量对齐,3 分量格式可能无法完成 framebuffer
- 镜面强度打包进 albedo 的 alpha,**省一张纹理**
- 位置可以不存:由 `gl_FragCoord` + 逆投影从深度重建(见"常见坑")

### Light Volume 半径反解

衰减方程 `F = I / (Kc + Kl·d + Kq·d²)` 永不为 0,取阈值 **5/256**(8 位 framebuffer 的 256 级强度中"接近黑"),由最亮分量 Imax 解二次方程:

```
Kq·d² + Kl·d + Kc − Imax·256/5 = 0
d = (−Kl + √(Kl² − 4·Kq·(Kc − 51.2·Imax))) / (2·Kq)
```

球体渲染要点:**只渲染背面**——玩家走进光源内部时,背面剔除会让光源"消失",渲染背面反而正确。

### 朴素 `if(distance < radius)` 为什么无效

GPU shader 高度并行,大批线程执行相同代码时分支**两端都会执行**;在 fragment shader 里按距离判断无法省掉光照计算。Light volume 用**光栅化覆盖范围**在 shader 之前就跳过无关像素,才是真正的减法。

## 对比 / 选型

| 维度 | Forward | Deferred |
| --- | --- | --- |
| 每片元光照成本 | O(lights) | 每像素 1 次 + 每光源覆盖像素 |
| 少光源(1~2 盏) | 更快 | G-buffer 写入是纯开销(本 demo 断言 6) |
| 多光源 | 不可行 | 数百~数千(1847 盏演示) |
| 混合(透明) | 支持 | **不支持**(G-buffer 每像素只存一个片元) |
| MSAA | 支持 | 失效(需要 per-sample G-buffer) |
| 材质多样性 | 逐物体 shader | 被迫共用一套光照模型 |

透明物体的标准解法:**混合渲染器**——延迟渲染不透明部分,再用 `glBlitFramebuffer` 把深度复制到默认 framebuffer,前向渲染透明物体与特殊效果。

## 环境准备

- 操作系统:任意(Python 3.8+;Go 1.18+ 仅需标准库)
- 依赖:无(纯软件模拟,不需要 GPU/OpenGL)

## 运行方式

```bash
python3 python/main.py    # 6 组断言 + 计算量对比输出
go run go/main.go         # 同口径的 Go 复刻
```

## 关键代码片段

```python
def light_volume_radius(light):
    """按 5/256 阈值反解光源作用半径(LearnOpenGL 原文推导)。"""
    imax = max(light.color)
    a, b, c = light.kq, light.kl, light.kc - (256.0 / 5.0) * imax
    disc = b * b - 4.0 * a * c
    return (-b + math.sqrt(disc)) / (2.0 * a) if disc >= 0 else 0.0

# geometry pass 的深度测试:更小的 depth 胜出(等价 early-z)
if (x, y) not in gb.depth or q.depth < gb.depth[(x, y)]:
    gb.depth[(x, y)] = q.depth
    gb.position[(x, y)] = (float(x), float(y), q.depth)   # → gPosition
    gb.albedo_spec[(x, y)] = (r, g, b, q.spec)             # → gAlbedoSpec
```

## 性能与边界

- 计算量口径(16×16 模拟屏):32 盏小体积光源下 forward 8192 次光照 vs deferred(volume)2163 次;单盏大体积亮光源下 deferred 含 geometry pass 共 512 次 > forward 256 次——**交叉点存在,少光源时前向更快**
- 1847 盏点光源是 LearnOpenGL 的演示规模;更大的光源数需要 tiled/clustered 扩展(见姊妹 demo「ForwardPlus分块光源剔除」)
- G-buffer 带宽随分辨率线性放大,4K 下三张 16F 纹理的代价不可忽视

## 注意事项与常见坑

- **别存位置纹理(如果带宽紧张)**:教程存了 RGBA16F 位置;工业实现普遍从深度 + 逆投影重建,Vulkan 的 z 已是 0..1 无需 ×2−1
- **light volume 必须开面剔除且渲染背面**,否则光源体积内部会失效
- **透明物体永远走单独的前向 pass**,无论主管线选什么架构
- **sRGB 只作用于 base color / emissive**:normal、metallic、roughness 是线性数据
- Vulkan 1.3 只保证 4 个 color attachment:G-buffer 设计要 ≤4 + depth 或先查询上限

## 参考资料(实际阅读过的权威来源)

- [LearnOpenGL — Deferred Shading](https://learnopengl.com/Advanced-Lighting/Deferred-Shading) — G-buffer 布局、light volume 半径推导、混合渲染器与深度 blit、1847 光源演示
- [Khronos Vulkan Tutorial — Engine Architecture: Rendering Pipeline](https://github.khronos.org/Vulkan-Site/tutorial/latest/Building_a_Simple_Engine/Engine_Architecture/05_rendering_pipeline.html) — Vulkan 版 geometry pass / lighting pass 的 render pass 组织
- [MegSesh/Project5-WebGL-Clustered-Deferred-Forward-Plus(UPenn CIS 565)](https://github.com/MegSesh/Project5-WebGL-Clustered-Deferred-Forward-Plus) — 延迟 vs 前向的优缺点清单与透明物体限制
