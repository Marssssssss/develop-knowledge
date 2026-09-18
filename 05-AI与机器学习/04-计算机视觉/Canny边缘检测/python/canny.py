# -*- coding: utf-8 -*-
"""Canny 边缘检测 — 五阶段流水线的最小实现(纯标准库)。

权威口径(docs.opencv.org 4.x `tutorial_py_canny`、OpenCV 3.4 `tutorial_canny_detector`、
Stanford EE368 讲义《Canny edge detector》,三者对同一算法的描述一致):

1. **去噪**:5x5 高斯滤波。3.4 教程给出除数 159 的整数核(本文件 GAUSSIAN_5X5_INT)。
2. **求梯度**:一对 3x3 Sobel 核;幅值 `G = sqrt(Gx^2+Gy^2)`(cv.Canny 的 `L2gradient=True`)
   或近似 `G = |Gx| + |Gy|`(默认 `L2gradient=False`)。
3. **非极大值抑制**:梯度方向量化到 0/45/90/135 四个角度,与该方向上两个邻居比较,
   只留局部极大 —— 结果是「细边」二值图。
4. **双阈值 + 滞后**:`> high` 必为边;`< low` 必非边;落在两者之间**只有与确定边连通**才保留。
5. **阈值比**:Canny 建议 high:low 在 **2:1 ~ 3:1** 之间(OpenCV 教程的 trackbar 例子用 ratio=3)。

工程取舍(与 OpenCV 的差异,已在 README「注意事项」中写明):
- 边界用 **clamp 复制**,OpenCV 默认是 `BORDER_DEFAULT`(reflect_101);差异只出现在图像
  最外 1~2 像素,不影响内部结构。
- 只实现 `apertureSize=3`(OpenCV 还支持 5/7,由 getDerivKernels 生成)。
- 滞后连通性用 **8 连通**(OpenCV 教程原文 "connected to sure-edge pixels" 未限定邻域)。
"""
import math

# OpenCV Canny 注释里给出的标准 3x3 Sobel 核(docs.opencv.org Sobel Derivatives 同款)
SOBEL_X = ((-1, 0, 1), (-2, 0, 2), (-1, 0, 1))
SOBEL_Y = ((-1, -2, -1), (0, 0, 0), (1, 2, 1))

# OpenCV 3.4 Canny 教程原文:K = 1/159 * [[2,4,5,4,2],[4,9,12,9,4],[5,12,15,12,5],...]
GAUSSIAN_5X5_INT = (
    (2, 4, 5, 4, 2),
    (4, 9, 12, 9, 4),
    (5, 12, 15, 12, 5),
    (4, 9, 12, 9, 4),
    (2, 4, 5, 4, 2),
)
GAUSSIAN_5X5_DIVISOR = 159

# 非极大值抑制:把梯度方向量化成 4 档后,沿该方向取的两个邻居偏移
# (图像坐标 y 向下;角度以 +x 为 0 逆时针到 y 轴为 90,即 atan2(gy, gx))
NMS_OFFSETS = {
    0: ((1, 0), (-1, 0)),      # 水平梯度 -> 垂直边缘
    45: ((1, 1), (-1, -1)),
    90: ((0, 1), (0, -1)),     # 垂直梯度 -> 水平边缘
    135: ((-1, 1), (1, -1)),
}


def gaussian_kernel_2d(size=5, sigma=1.4):
    """归一化的 2D 高斯核(均值 0,标准差 sigma)。size 必须为奇数。"""
    if size % 2 == 0:
        raise ValueError("kernel size must be odd")
    half = size // 2
    flat = [math.exp(-(i * i) / (2.0 * sigma * sigma)) for i in range(-half, half + 1)]
    total = sum(flat)
    flat = [v / total for v in flat]
    return tuple(tuple(fy * fx for fx in flat) for fy in flat)


def correlate(img, kernel):
    """通用奇数尺寸相关运算(clamp 边界),kernel 为等宽方形元组。"""
    h, w = len(img), len(img[0])
    k = len(kernel)
    half = k // 2
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        oy = out[y]
        for x in range(w):
            acc = 0.0
            for ky in range(k):
                yy = y + ky - half
                yy = 0 if yy < 0 else (h - 1 if yy >= h else yy)
                krow = kernel[ky]
                irow = img[yy]
                for kx in range(k):
                    xx = x + kx - half
                    xx = 0 if xx < 0 else (w - 1 if xx >= w else xx)
                    acc += krow[kx] * irow[xx]
            oy[x] = acc
    return out


def blur(img, size=5, sigma=1.4):
    """5x5 高斯平滑(第 1 阶段:去噪)。"""
    return correlate(img, gaussian_kernel_2d(size, sigma))


def sobel_gradients(img, aperture=3):
    """第 2 阶段:Gx / Gy。本实现只支持 aperture=3(OpenCV 还支持 5/7)。"""
    if aperture != 3:
        raise ValueError("this demo implements apertureSize=3 only")
    return correlate(img, SOBEL_X), correlate(img, SOBEL_Y)


def gradient_magnitude(gx, gy, l2gradient=False):
    """L2gradient=True 走 sqrt(Gx^2+Gy^2);默认 False 走 |Gx|+|Gy|(OpenCV 教程)。"""
    h, w = len(gx), len(gx[0])
    if l2gradient:
        return [[math.hypot(gx[y][x], gy[y][x]) for x in range(w)] for y in range(h)]
    return [[abs(gx[y][x]) + abs(gy[y][x]) for x in range(w)] for y in range(h)]


def gradient_angle(gx, gy):
    """梯度方向,归一到 [0,180):方向与方向+180 是同一条直线,无需区分。"""
    a = math.degrees(math.atan2(gy, gx))
    if a < 0.0:
        a += 180.0
    if a >= 180.0:
        a -= 180.0
    return a


def direction_sector(angle_deg):
    """把 [0,180) 量化到 4 个方向之一:0 / 45 / 90 / 135(OpenCV 3.4 教程原文)。"""
    if angle_deg < 22.5 or angle_deg >= 157.5:
        return 0
    if angle_deg < 67.5:
        return 45
    if angle_deg < 112.5:
        return 90
    return 135


def non_max_suppression(mag, sectors, eps_ratio=1e-9):
    """第 3 阶段:沿梯度方向只保留局部极大。

    平局规则必须显式写死:理想阶跃边缘两侧的梯度幅值**在数学上严格相等**
    (见 README「注意事项」),用 `>=` 比较会留下两列、用 `>` 比较会一列不留。
    这里取「先出现的邻居(NMS_OFFSETS 的次序,即 +1 侧)必须严格更小、后出现的
    邻居允许相等」,保证任何情况下每行只留 1 列。

    `eps_ratio` 是**浮点残差护栏**:平坦区的 |Gx|、|Gy| 因累加顺序不同会留下
    1e-14 量级的残差(实测 2.84e-14),用 `m == 0.0` 判断"无梯度"会被它骗过,
    于是在纯平坦区留下伪边。这里按"地图峰值 × eps_ratio"设一个相对下限;
    真实管线里这一层由双阈值兜住,但 NMS 自身不该输出噪声。取 0 可关掉护栏。
    """
    h, w = len(mag), len(mag[0])
    peak = max(v for row in mag for v in row)
    eps = peak * eps_ratio
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            m = mag[y][x]
            if m <= eps:
                continue
            (d1x, d1y), (d2x, d2y) = NMS_OFFSETS[sectors[y][x]]
            n1 = n2 = 0.0
            if 0 <= x + d1x < w and 0 <= y + d1y < h:
                n1 = mag[y + d1y][x + d1x]
            if 0 <= x + d2x < w and 0 <= y + d2y < h:
                n2 = mag[y + d2y][x + d2x]
            if m > n1 and m >= n2:
                out[y][x] = m
    return out


def hysteresis(mag, low, high):
    """第 4 阶段:双阈值 + 滞后。返回 0/1 边缘图。

    OpenCV 教程的判据:> high 为「确定边」;< low 直接丢弃;两者之间的像素
    只有**与确定边连通**才算边,否则丢弃(这就是"滞后")。
    """
    if low > high:
        raise ValueError("low threshold must not exceed high threshold")
    h, w = len(mag), len(mag[0])
    WEAK, STRONG = 1, 2
    state = [[0] * w for _ in range(h)]
    stack = []
    for y in range(h):
        for x in range(w):
            v = mag[y][x]
            if v >= high:
                state[y][x] = STRONG
                stack.append((x, y))
            elif v >= low:
                state[y][x] = WEAK
    while stack:
        x, y = stack.pop()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                xx, yy = x + dx, y + dy
                if 0 <= xx < w and 0 <= yy < h and state[yy][xx] == WEAK:
                    state[yy][xx] = STRONG
                    stack.append((xx, yy))
    return [[1 if v == STRONG else 0 for v in row] for row in state]


def canny(img, low, high, aperture=3, l2gradient=False, sigma=1.4):
    """完整流水线:平滑 -> 梯度 -> NMS -> 滞后阈值。返回 0/1 边缘图。"""
    src = blur(img, 5, sigma)
    gx, gy = sobel_gradients(src, aperture)
    h, w = len(gx), len(gx[0])
    mag = gradient_magnitude(gx, gy, l2gradient)
    sectors = [
        [direction_sector(gradient_angle(gx[y][x], gy[y][x])) for x in range(w)]
        for y in range(h)
    ]
    return hysteresis(non_max_suppression(mag, sectors), low, high)


def auto_thresholds(img, sigma=0.33):
    """中位数启发式阈值(工程惯例,非 Canny 原文/Canny 官方口径)。

    low = (1-sigma)*median, high = (1+sigma)*median —— 目的是省掉逐图调参;
    与 cv.Canny 一样,阈值作用于**梯度幅值**而非灰度,这里仅按灰度中位数标定尺度。
    """
    vals = sorted(v for row in img for v in row)
    n = len(vals)
    med = vals[n // 2] if n % 2 else 0.5 * (vals[n // 2 - 1] + vals[n // 2])
    return (1.0 - sigma) * med, (1.0 + sigma) * med
