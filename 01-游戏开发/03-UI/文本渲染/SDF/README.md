# SDF 文本渲染（Signed Distance Field）

## 简介

用**有符号距离场**（Signed Distance Field, SDF）编码字形：纹理中每个纹素存储的不是颜色覆盖，而是"到字形轮廓的最短距离"（内部为负、外部为正）。这样一张**低分辨率**纹理经硬件双线性过滤后，`0.5` 等值线依然是平滑曲线，配合 alpha test 即可在**任意放大倍率**下渲染出平滑边缘，运行时几乎零额外开销。该技术由 Valve 的 Chris Green 在 SIGGRAPH 2007 论文中提出，用于《军团要塞 2》（Team Fortress 2），后被 libgdx、Unity TextMeshPro、Godot 等主流引擎/框架广泛采用。

- **有符号距离**：点到轮廓的最短欧氏距离，符号区分内外（本 demo 与 libgdx 约定一致：归一化后内部 > 0.5、外部 < 0.5，**0.5 恰好落在轮廓上**）
- **双线性重建**：硬件双线性过滤对距离场做分段线性插值，"免费"重建出连续的距离函数
- **alpha test 阈值**：`value >= 0.5` 处即为字形边缘，与纹理分辨率解耦
- **spread（扩散范围）**：距离归一化的截断半径，决定可表达的边缘过渡带宽度
- **smoothstep 抗锯齿**：在 shader 中以屏幕空间导数决定过渡带宽，做边缘 AA

历史背景：2007 年前游戏普遍用位图字体，放大即锯齿/模糊；Chris Green 在 SIGGRAPH 2007 课程《Improved Alpha-Tested Magnification Using Vector Textures and Specialized Texture Shaders》中提出 SDF 方案；2014 年 Viktor Chlumsky 在硕士论文中提出多通道 MSDF 修复尖角圆化问题。

## 原理详解

### 1. 构建 SDF 纹理（离线预处理）

1. 取高分辨率字形位图（或矢量轮廓），确定 spread 半径 r
2. 对输出纹理每个纹素中心 p，计算到轮廓的最短距离 d（精确欧氏距离或距离变换近似）
3. 截断到 ±r 内，线性映射到 [0,1]：`value = clamp(0.5 - d/(2r), 0, 1)`——内部（d<0）值 > 0.5，外部值 < 0.5
4. 量化存储为 8-bit 单通道纹理；0.5 处恰好是原字形边缘（最近邻采样下能 1:1 还原输入）

### 2. 运行时采样与重建

```
SDF 纹理(64x64)                     输出(512x512, 8x 放大)
┌─┬─┬─┬─┐   双线性插值对距离场      ┌─────────────┐
│.1│.2│.9│.9│   做分段线性重建       │   value=0.5 │ ← 等值线是
├─┼─┼─┼─┤  ───────────────────▶   │  ╱‾‾‾╲____  │   平滑曲线，
│.2│.5│.8│.9│   (硬件免费提供)      │ ╱ 字形      │   与纹素网格
├─┼─┼─┼─┤                          │╲___╱        │   无关
│.1│.3│.6│.8│                       └─────────────┘
└─┴─┴─┴─┘                           alpha test: value>=0.5
```

- **为什么二值位图不行**：两个 0/1 像素之间双线性插值只能得到线性渐变的灰色——边缘位置和形状都被"糊"掉；而 SDF 纹素间的插值得到的是**距离的线性近似**，`0.5` 等值线的位置依然准确。
- **抗锯齿**：直接 `alpha = value >= 0.5` 输出硬边；论文与 libgdx 的 shader 改用 `alpha = smoothstep(0.5 - w, 0.5 + w, value)`，w 由屏幕空间导数（`fwidth` 类函数）按当前放大倍率计算，边缘过渡恰为约 1 个屏幕像素。
- **特效（specialized effects）**：对同一张 SDF 纹理偏移采样再阈值化，即可得到阴影/描边/发光，无需额外纹理与顶点。

### 3. 本 demo 的关键函数（三语言一致）

| 函数 | 签名 | 说明 |
| --- | --- | --- |
| `point_in_glyph` | `(px, py) -> bool` | 射线法点在多边形内判定，决定距离符号 |
| `dist_point_segment` | `(p, a, b) -> float` | 点到线段最短距离（含投影 clamp 到 [0,1]） |
| `signed_distance` | `(px, py) -> float` | 对所有轮廓边取 min，内部取负——**精确**有符号距离 |
| `bilinear` | `(tex, fx, fy) -> float` | 双线性采样，等价 GPU 的 `GL_LINEAR` |
| `smoothstep` | `(a, b, x) -> float` | `t*t*(3-2t)` 平滑阶梯，等价 GLSL 内建函数 |

工程上真实工具链：libgdx Hiero 的 "Distance field" filter（spread ≈ 最粗笔画宽度的一半，padding = spread，字距补偿 XY = -2×spread）；msdfgen 的 `generateSDF` / `generateMSDF`（从 FreeType/SVG 轮廓生成）。

## 对比 / 选型

| 方案 | 放大质量 | 旋转/任意变换 | 纹理内存 | 运行时开销 | 彩色支持 | 锐利尖角 |
| --- | --- | --- | --- | --- | --- | --- |
| 位图字体（最近邻） | 锯齿 | 差 | 小 | 最低 | 支持 | 好 |
| 位图字体（双线性） | 模糊 | 差 | 小 | 最低 | 支持 | 差 |
| **SDF（单通道）** | 平滑 | 好 | **极小**（64² 可放大数十倍） | 低（alpha test + 采样） | 仅单色/渐变上色 | **圆化** |
| MSDF（三通道） | 平滑 | 好 | SDF 的 3 倍 | 低 + median-of-three | 单色 | 几乎完美 |
| 矢量直接渲染（GLyphy） | 完美 | 好 | 无纹理 | 高（CPU/复杂 shader） | 支持 | 完美 |

选型建议：UI 大字号文本、需要任意缩放/旋转/描边阴影特效 → SDF/MSDF；静态彩色艺术字 → 普通纹理；极端清晰度要求 → 矢量渲染。

## 环境准备

- 操作系统：任意（纯 CPU 计算，无图形 API 依赖）
- 语言版本：C（C99+，仅 libm）/ Python 3.8+ / Go 1.21+
- 依赖：无第三方依赖；输出为 PPM(P6) 图片，可用 GIMP、IrfanView、`magick xxx.ppm xxx.png` 查看

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -o sdf_demo c/sdf_demo.c -lm
./sdf_demo
```

### Python

```bash
python3 python/sdf_demo.py
```

### Go

```bash
cd go && go run sdf_demo.go
```

均输出 5 张 PPM 到当前目录：`sdf_texture.ppm`（64² 距离场可视化）、`nearest.ppm`、`bilinear_mask.ppm`、`bilinear_sdf.ppm`、`effects.ppm`（512²）。

## 关键代码片段（以 Python 版为例）

```python
def signed_distance(px, py):
    """精确有符号距离：对全部轮廓边取最短，内部取负。"""
    best = float("inf")
    for i in range(len(GLYPH)):                 # 遍历闭合多边形每条边
        ax, ay = GLYPH[i]
        bx, by = GLYPH[(i + 1) % len(GLYPH)]
        best = min(best, dist_point_segment(px, py, ax, ay, bx, by))
    return -best if point_in_glyph(px, py) else best  # 符号：内负外正

def build_texture():
    """步骤 1：64x64 网格采样距离，归一化——0.5 恰好在轮廓上。"""
    sdf = [[0.0] * TEX_N for _ in range(TEX_N)]
    for j in range(TEX_N):
        for i in range(TEX_N):
            px = (i + 0.5) / TEX_N * DOMAIN
            py = (j + 0.5) / TEX_N * DOMAIN
            d = signed_distance(px, py)
            sdf[j][i] = clamp01(0.5 - d / (2.0 * RANGE))   # 截断到 ±RANGE
    return sdf

def render_sdf(sdf, out):
    """步骤 2/3：双线性采样 + smoothstep，8x 放大仍平滑。"""
    for y in range(OUT_N):
        for x in range(OUT_N):
            fx = (x + 0.5) / OUT_N * TEX_N - 0.5           # 映射到纹素坐标
            fy = (y + 0.5) / OUT_N * TEX_N - 0.5
            v = bilinear(sdf, fx, fy)                       # GPU 的 GL_LINEAR
            a = smoothstep(0.5 - AA_W, 0.5 + AA_W, v)       # 边缘 AA
            put_pixel(out, x, y, a)                         # a 即字形覆盖率
```

`nearest.ppm` 与 `bilinear_mask.ppm` 用同一循环换采样方式即可，三者对比即可直观看到：最近邻 → 阶梯锯齿；二值双线性 → 边缘糊成灰带；SDF 双线性 → 平滑锐利。

## 性能与边界

- **内存**：64×64 单通道 = 4 KB/字形；一张 2048² 图集可容纳约 1000 个字形，缩放到数百像素高度依然平滑（libgdx 示例使用 32×32 纹理即可获得平滑结果）
- **离线构建成本**：精确距离为 O(纹素数 × 边数)；工业界用距离变换（Felzenszwalb 两遍算法等）降到近似线性。本 demo 为教学用精确计算，64² 网格瞬时完成
- **精度边界**：8-bit 量化 + 双线性（二次多项式）意味着每个纹素格内距离是**二次**函数，导数不能突变——这正是尖角被圆化的数学根源（Chlumsky 的分析）
- **缩放下限**：缩小到远小于纹理分辨率时同样会失真，libgdx 建议开启 mipmap 改善下采样质量

## 注意事项与常见坑

1. **尖角圆化**：现象——字形尖角（如 "7" 的下端尖角、衬线）放大后变圆。原因——双线性插值得到的距离场是二次多项式，无法表示导数突变。规避——用 MSDF：三通道各存一组边的距离，渲染时 `median(r, g, b)` 重构，尖角几乎完美（Chlumsky msdfgen）。
2. **spread 选错**：现象——阴影/描边偏移稍大就被截断，或边缘过渡带不足仍有锯齿。原因——spread 决定 ±r 的截断范围。规避——libgdx 经验值：spread ≈ 字体最粗笔画宽度的一半，纹理四周 padding ≥ spread，生成时字距补偿 XY = -2×spread。
3. **只支持单色**：SDF 编码的是几何覆盖，不能直接表示彩色位图；彩色需用渐变/贴图上色或退回普通纹理（libgdx 明确列为该技术的主要缺点）。
4. **mipmap 下的阈值漂移**：预过滤的 mip 层混合了内外距离，直接 alpha test 会出现细笔画断裂；要么限制最小缩放，要么在 shader 中按导数补偿（同抗锯齿做法）。
5. **量化条纹**：8-bit 通道在极端放大（数百倍）时距离梯度会出现可见台阶；用 16-bit 纹理或加大 spread 缓解。

## 参考资料（实际阅读过的权威来源）

- [Improved Alpha-Tested Magnification Using Vector Textures and Specialized Texture Shaders — Chris Green, ACM SIGGRAPH 2007 Courses, pp. 9-18, DOI 10.1145/1281500.1281665](https://valvearchive.com/archive/Other%20Files/Publications/SIGGRAPH2007_AlphaTestedMagnification.pdf) — 奠基论文（提出者本人撰写）；本轮 PDF 各镜像均返回空文件，技术要点经下方两个来源交叉确认
- [Distance field fonts — libgdx 官方 Wiki](https://github.com/libgdx/libgdx/wiki/Distance-field-fonts) — 全文阅读：预处理约定（0.5 在边缘、内正外负）、spread/padding 经验值、linear filter + mipmap 设置、单色局限
- [msdfgen — Viktor Chlumsky (GitHub)](https://github.com/Chlumsky/msdfgen) — MSDF 原作者项目；经检索快照阅读其 README 与 API：`generateSDF`/`generateMSDF`、median-of-three 重构
- [Signed Distance Fields: How are different colour channels used to improve output of sharp corners? — Chlumsky 在 gamedev.stackexchange 的回答](https://gamedev.stackexchange.com/questions/92265/signed-distance-fields-how-are-different-colour-channels-used-to-improve-output) — 原作者解释双线性二次多项式无法表示尖角、MSDF 用 AND/OR(max/min) 组合重构角点的原理
- [Better contour rendering — ghostinthecode.net](https://ghostinthecode.net/2010/11/22/contours.html) — 对 Valve 论文核心思想（双线性过滤做距离场分段线性重建）的独立复述，用于交叉验证
