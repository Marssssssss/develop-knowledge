"""sdf_demo.py — 有符号距离场(SDF)字形渲染原理演示

镜像 C 版实现（流程说明见 ../README.md）：
  1. "7" 字形闭合多边形作为矢量轮廓（单位坐标 [0,16]，y 向下）
  2. 64x64 网格采样精确有符号距离构建 SDF 纹理（0.5 恰在轮廓上）
  3. 8x 放大(512x512)对比三种采样：最近邻 / 二值双线性 / SDF 双线性+smoothstep
  4. 基于 SDF 的阴影 + 描边特效（Valve 论文 specialized effects）

输出为二进制 PPM(P6)，可用 GIMP / IrfanView / magick 查看。
运行: python3 sdf_demo.py
"""

import math

DOMAIN = 16.0   # 形状定义域边长（单位坐标系）
TEX_N = 64      # SDF / coverage 纹理分辨率
OUT_N = 512     # 输出图像分辨率（8x 纹理放大）
RANGE = 4.0     # 距离归一化截断半径(spread)，单位坐标
AA_W = 0.01     # smoothstep 半宽（值域单位），约 2 个输出像素

# "7" 字形闭合多边形轮廓（屏幕坐标，y 向下）
GLYPH = [
    (2.0, 2.0),    # 左上
    (14.0, 2.0),   # 右上
    (14.0, 4.5),   # 顶横条下缘（右）
    (9.0, 14.0),   # 斜笔右下端
    (5.5, 14.0),   # 斜笔左下端
    (10.5, 4.5),   # 斜笔上端（左）
    (2.0, 4.5),    # 顶横条下缘（左）
]


def clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def smoothstep(a, b, x):
    """GLSL 同名内建函数的等价实现。"""
    t = clamp01((x - a) / (b - a))
    return t * t * (3.0 - 2.0 * t)


def point_in_glyph(px, py):
    """射线法点在多边形内判定：向 +x 发射线，穿越边次数为奇数则在内。"""
    inside = False
    n = len(GLYPH)
    for i in range(n):
        j = (i + 1) % n
        xi, yi = GLYPH[i]
        xj, yj = GLYPH[j]
        if (yi > py) != (yj > py):
            x_at = xi + (py - yi) * (xj - xi) / (yj - yi)
            if px < x_at:
                inside = not inside
    return inside


def dist_point_segment(px, py, ax, ay, bx, by):
    """点到线段最短欧氏距离（投影 clamp 到 [0,1]）。"""
    dx, dy = bx - ax, by - ay
    len2 = dx * dx + dy * dy
    t = 0.0
    if len2 > 0.0:
        t = clamp01(((px - ax) * dx + (py - ay) * dy) / len2)
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def signed_distance(px, py):
    """精确有符号距离：对所有轮廓边取最短；内部取负（内负外正约定）。"""
    best = float("inf")
    n = len(GLYPH)
    for i in range(n):
        ax, ay = GLYPH[i]
        bx, by = GLYPH[(i + 1) % n]
        best = min(best, dist_point_segment(px, py, ax, ay, bx, by))
    return -best if point_in_glyph(px, py) else best


def build_textures():
    """步骤 1：构建 SDF 纹理与二值 coverage 纹理。"""
    sdf = [[0.0] * TEX_N for _ in range(TEX_N)]
    mask = [[0.0] * TEX_N for _ in range(TEX_N)]
    for j in range(TEX_N):
        for i in range(TEX_N):
            px = (i + 0.5) / TEX_N * DOMAIN
            py = (j + 0.5) / TEX_N * DOMAIN
            d = signed_distance(px, py)
            sdf[j][i] = clamp01(0.5 - d / (2.0 * RANGE))  # 0.5 即轮廓
            mask[j][i] = 1.0 if d < 0.0 else 0.0          # 传统位图字体
    return sdf, mask


def bilinear(tex, fx, fy):
    """双线性采样（等价 GPU 的 GL_LINEAR）；fx/fy 为连续纹素坐标。"""
    x0, y0 = math.floor(fx), math.floor(fy)
    tx, ty = fx - x0, fy - y0
    ix0, iy0 = int(x0), int(y0)
    ix1, iy1 = min(ix0 + 1, TEX_N - 1), min(iy0 + 1, TEX_N - 1)
    ix0, iy0 = max(ix0, 0), max(iy0, 0)
    v00, v10 = tex[iy0][ix0], tex[iy0][ix1]
    v01, v11 = tex[iy1][ix0], tex[iy1][ix1]
    return ((v00 * (1.0 - tx) + v10 * tx) * (1.0 - ty) +
            (v01 * (1.0 - tx) + v11 * tx) * ty)


def to_texel(x, y):
    """输出像素 -> 连续纹素坐标。"""
    return ((x + 0.5) / OUT_N * TEX_N - 0.5,
            (y + 0.5) / OUT_N * TEX_N - 0.5)


def write_ppm(path, rgb):
    """写出二进制 PPM(P6) 文件；rgb 为 OUT_N*OUT_N*3 字节。"""
    with open(path, "wb") as f:
        f.write(f"P6\n{OUT_N} {OUT_N}\n255\n".encode("ascii"))
        f.write(bytes(rgb))


def render_gray(out, sample):
    """按 sample(x, y) -> [0,1] 的灰度渲染整帧。"""
    for y in range(OUT_N):
        for x in range(OUT_N):
            g = int(sample(x, y) * 255.0 + 0.5)
            idx = (y * OUT_N + x) * 3
            out[idx] = g
            out[idx + 1] = g
            out[idx + 2] = g


def main():
    sdf, mask = build_textures()
    print(f"SDF texture {TEX_N}x{TEX_N} built (range +/-{RANGE} units)")

    # 5a. 距离场可视化
    out = bytearray(OUT_N * OUT_N * 3)

    def sample_field(x, y):
        ix = min(int((x + 0.5) / OUT_N * TEX_N), TEX_N - 1)
        iy = min(int((y + 0.5) / OUT_N * TEX_N), TEX_N - 1)
        return sdf[iy][ix]

    render_gray(out, sample_field)
    write_ppm("sdf_texture.ppm", out)

    # 5b. 最近邻 + alpha test：阶梯锯齿
    def sample_nearest(x, y):
        fx, fy = to_texel(x, y)
        ix = max(0, min(TEX_N - 1, round(fx)))
        iy = max(0, min(TEX_N - 1, round(fy)))
        return smoothstep(0.5 - AA_W, 0.5 + AA_W, sdf[iy][ix])

    render_gray(out, sample_nearest)
    write_ppm("nearest.ppm", out)

    # 5c. 二值 coverage 双线性：边缘线性模糊
    def sample_mask(x, y):
        fx, fy = to_texel(x, y)
        return bilinear(mask, fx, fy)  # 直接输出覆盖率 -> 可见模糊

    render_gray(out, sample_mask)
    write_ppm("bilinear_mask.ppm", out)

    # 5d. SDF 双线性 + smoothstep：平滑边缘（主角）
    def sample_sdf(x, y):
        fx, fy = to_texel(x, y)
        v = bilinear(sdf, fx, fy)
        return smoothstep(0.5 - AA_W, 0.5 + AA_W, v)

    render_gray(out, sample_sdf)
    write_ppm("bilinear_sdf.ppm", out)

    # 5e. 特效：阴影（偏移采样）+ 描边（阈值带），均来自同一张 SDF
    for y in range(OUT_N):
        for x in range(OUT_N):
            fx, fy = to_texel(x, y)
            v = bilinear(sdf, fx, fy)
            sv = bilinear(sdf, fx + 3.0, fy + 3.0)  # 右下偏移 3 纹素
            shadow = smoothstep(0.5 - AA_W, 0.5 + AA_W, sv)
            glyph = smoothstep(0.5 - AA_W, 0.5 + AA_W, v)
            outline = 0.5 - 0.08 < v < 0.5 + 0.02   # 外描边带
            # 合成顺序：深蓝背景 -> 灰色阴影 -> 黄色描边 -> 白色字形
            r, g, b = 24.0, 28.0, 56.0
            r += (96.0 - r) * shadow
            g += (102.0 - g) * shadow
            b += (120.0 - b) * shadow
            if outline:
                r, g, b = 232.0, 180.0, 48.0
            if glyph:
                r, g, b = 245.0, 245.0, 245.0
            idx = (y * OUT_N + x) * 3
            out[idx] = int(r + 0.5)
            out[idx + 1] = int(g + 0.5)
            out[idx + 2] = int(b + 0.5)
    write_ppm("effects.ppm", out)

    print(f"wrote: sdf_texture.ppm nearest.ppm bilinear_mask.ppm "
          f"bilinear_sdf.ppm effects.ppm ({OUT_N}x{OUT_N})")


if __name__ == "__main__":
    main()
