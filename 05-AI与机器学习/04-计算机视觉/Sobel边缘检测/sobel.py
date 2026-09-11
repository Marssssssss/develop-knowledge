# -*- coding: utf-8 -*-
"""Sobel 边缘检测 — 图像梯度近似的最小实现(纯标准库)。

依据 OpenCV 官方教程:
- Gx/Gy 为 3x3 核,Gx 响应水平方向强度变化(垂直边缘),
  Gy 响应垂直方向强度变化(水平边缘)
- 梯度幅值 G = sqrt(Gx^2 + Gy^2),工程上常用近似 |Gx| + |Gy|
- Sobel = 高斯平滑 + 微分,对噪声比纯差分更鲁棒

4 个 demo:
1. 垂直阶跃边缘 → 仅 Gx 响应
2. 水平阶跃边缘 → 仅 Gy 响应
3. 方块轮廓 → Gx/Gy 联合 + 阈值二值化
4. CV_8U 截断陷阱(OpenCV 官方"One Important Matter")→ 负梯度被吞
"""
import math

# OpenCV 文档给出的标准 3x3 Sobel 核(docs.opencv.org 2.4 Sobel Derivatives)
GX = ((-1, 0, 1),
      (-2, 0, 2),
      (-1, 0, 1))
GY = ((-1, -2, -1),
      (0, 0, 0),
      (1, 2, 1))


def correlate3x3(img, kernel):
    """3x3 相关(不翻转核)计算,零填充边界,返回浮点梯度图。

    OpenCV 的 filter2D/Sobel 实际执行的是相关运算,核按文档原样使用;
    Gx 核不对称,相关 vs 卷积差一个符号,这里与 OpenCV 行为保持一致。
    """
    h, w = len(img), len(img[0])
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            acc = 0.0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w:  # 零填充:越界按 0
                        acc += img[yy][xx] * kernel[dy + 1][dx + 1]
            out[y][x] = acc
    return out


def sobel(img):
    """返回 (gx, gy, magnitude, direction)。

    direction = atan2(gy, gx),图像坐标系 y 轴向下(OpenCV 约定)。
    """
    gx = correlate3x3(img, GX)
    gy = correlate3x3(img, GY)
    h, w = len(img), len(img[0])
    mag = [[0.0] * w for _ in range(h)]
    ang = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            mag[y][x] = math.hypot(gx[y][x], gy[y][x])
            ang[y][x] = math.atan2(gy[y][x], gx[y][x])
    return gx, gy, mag, ang


def render(values, levels=" .:-=+*#%@"):
    """把 2D 数值矩阵渲染成 ASCII 灰度图,便于终端直读。"""
    lo = min(min(r) for r in values)
    hi = max(max(r) for r in values)
    span = (hi - lo) or 1.0
    lines = []
    for row in values:
        lines.append("".join(levels[min(int((v - lo) / span * (len(levels) - 1)),
                                        len(levels) - 1)] for v in row))
    return "\n".join(lines)


def threshold(mag, t):
    """梯度幅值二值化:t 以上为边缘(1),否则 0。"""
    return [[1 if v >= t else 0 for v in row] for row in mag]


def step_image(width, height, vertical=True, split=None, lo=0, hi=10):
    """生成阶跃边缘测试图:一半 lo、一半 hi。"""
    if split is None:
        split = width // 2 if vertical else height // 2
    img = []
    for y in range(height):
        row = []
        for x in range(width):
            if vertical:
                row.append(lo if x < split else hi)
            else:
                row.append(lo if y < split else hi)
        img.append(row)
    return img


def square_image(width, height, x0, y0, x1, y1, bg=0, fg=10):
    """生成实心方块图:方块内部 fg,背景 bg。"""
    return [[fg if x0 <= x <= x1 and y0 <= y <= y1 else bg
             for x in range(width)] for y in range(height)]


def trunc_u8(v):
    """模拟 OpenCV 里 CV_8U 输出的截断:负值全部变 0(而非取绝对值)。"""
    return 0 if v < 0 else min(255, int(v))


def demo1_vertical_edge():
    print("=== demo 1: 垂直阶跃边缘(左暗右亮)→ 仅 Gx 响应 ===")
    img = step_image(12, 8, vertical=True, split=6)
    gx, gy, mag, _ = sobel(img)
    print(render(img), "<- 原图(x=5/6 之间黑→白跳变)")
    print(render(gx), "<- Gx:边界列出现强响应(正),其余为 0")
    print(render(gy), "<- Gy:全 0(垂直边缘在 y 方向无变化)")
    assert max(max(r) for r in gy) == 0, "垂直边缘不应触发 Gy"
    assert max(gx[4][5], gx[4][6]) > 0
    print("PASS: Gx>0 at edge, Gy==0 everywhere\n")


def demo2_horizontal_edge():
    print("=== demo 2: 水平阶跃边缘(上暗下亮)→ 仅 Gy 响应 ===")
    img = step_image(12, 8, vertical=False, split=4)
    gx, gy, mag, _ = sobel(img)
    print(render(img), "<- 原图")
    print(render(gy), "<- Gy:边界行出现强响应")
    print(render(gx), "<- Gx:全 0")
    assert max(max(r) for r in gx) == 0, "水平边缘不应触发 Gx"
    print("PASS: Gy>0 at edge, Gx==0 everywhere\n")


def demo3_square():
    print("=== demo 3: 实心方块 → 四条边 + 阈值二值化 ===")
    img = square_image(16, 10, x0=4, y0=2, x1=11, y1=7)
    gx, gy, mag, ang = sobel(img)
    print(render(img), "<- 原图 16x10 方块")
    print(render(threshold(mag, 10.0)), "<- |G|>=10 的边缘图:'#'=边缘")
    # 方块左边缘(黑→白,x 增大方向)Gx>0;右边缘(白→黑)Gx<0 —— 方向信息可用
    assert gx[4][3] > 0 and gx[4][12] < 0, "左右边缘 Gx 符号应相反"
    assert gy[2][7] > 0 and gy[8][7] < 0, "上下边缘 Gy 符号应相反"
    print("PASS: 左/右边缘 Gx 符号相反(方向可判),四边均被检出\n")


def demo4_u8_truncation():
    print("=== demo 4: CV_8U 截断陷阱(OpenCV 'One Important Matter') ===")
    img = step_image(12, 6, vertical=True, split=6)  # 右亮左暗区域间有一条垂直边
    gx, _, _, _ = sobel(img)
    bad = [[trunc_u8(v) for v in row] for row in gx]
    good = [[min(255, int(abs(v))) for v in row] for row in gx]
    print(render(bad), "<- 直接存 uint8:负梯度全部变 0 → 一条边消失!")
    print(render(good), "<- 先取绝对值再转 uint8:两侧边缘都保留")
    assert sum(trunc_u8(v) for row in gx for v in row) < sum(
        min(255, int(abs(v))) for row in gx for v in gx)
    print("PASS: 负斜率边缘需 abs 后再转 8 位,否则丢失(官方教程原话)\n")


def main():
    demo1_vertical_edge()
    demo2_horizontal_edge()
    demo3_square()
    demo4_u8_truncation()
    print("all 4 demos PASS")


if __name__ == "__main__":
    main()
