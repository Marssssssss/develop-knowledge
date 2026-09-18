# -*- coding: utf-8 -*-
"""Harris 角点检测(结构张量 + 角点响应)的最小实现(纯标准库)。

权威口径(docs.opencv.org 4.x `tutorial_py_features_harris`,与 Harris & Stephens 1988
《A Combined Corner and Edge Detector》一致):

1. 把"窗口平移 (u,v) 后的强度变化"写成
   `E(u,v) = Σ w(x,y) [ I(x+u, y+v) - I(x,y) ]²`
   窗口函数 `w` 可以是**矩形窗或高斯窗**(教程原文)。
2. 对 E 做 Taylor 展开,得到 `E(u,v) ≈ [u v] M [u v]ᵀ`,其中结构张量
   `M = Σ w(x,y) [[Ix², IxIy], [IxIy, Iy²]]`。
3. 不显式求特征值,改用响应函数 `R = det(M) - k * trace(M)²`,
   `det = λ1λ2`、`trace = λ1+λ2`,`k` 是经验常数(0.04~0.06)。
4. 判别:λ1、λ2 都小 → 平坦;一个远大于另一个 → 边缘(R<0);两个都大且接近 → 角点。

两条本 demo 显式写明的口径:
- **`k` 的取值范围 0.04~0.06 与"从 0.04 起调"** 来自 OpenCV 教程示例(`cv.cornerHarris(gray,2,3,0.04)`)
  与官方参数说明;本模块默认 0.04。
- **亚像素精化**:OpenCV 的 `cv.cornerSubPix()` 是"迭代梯度加权质心法"。本 demo 用**响应图上的
  二次曲面拟合**做同一件事(把整数峰修正到亚像素),思路一致但不是同一算法,已在 README 标注。
"""
import math

SOBEL_X = ((-1, 0, 1), (-2, 0, 2), (-1, 0, 1))
SOBEL_Y = ((-1, -2, -1), (0, 0, 0), (1, 2, 1))


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def correlate(img, kernel):
    """通用奇数尺寸相关运算(clamp 边界)。"""
    h, w = len(img), len(img[0])
    k = len(kernel)
    half = k // 2
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            acc = 0.0
            for ky in range(k):
                irow = img[clamp(y + ky - half, 0, h - 1)]
                krow = kernel[ky]
                for kx in range(k):
                    acc += krow[kx] * irow[clamp(x + kx - half, 0, w - 1)]
            out[y][x] = acc
    return out


def sobel_gradients(img):
    """Ix / Iy(3x3 Sobel,OpenCV 教程用 cv.Sobel 求偏导)。"""
    return correlate(img, SOBEL_X), correlate(img, SOBEL_Y)


def window_weights(block_size, mode="rect", sigma=None):
    """窗口函数权重。mode='rect' 为矩形窗(等权),'gaussian' 为高斯窗。

    OpenCV 教程原文:E 里的 w(x,y) "is either a rectangular window or a Gaussian window"。
    两种窗都**归一化到总和 1**,避免不同窗之间只剩尺度差异可比。
    """
    r = block_size
    size = 2 * r + 1
    if mode == "rect":
        w = [[1.0] * size for _ in range(size)]
    elif mode == "gaussian":
        s = sigma if sigma is not None else max(1.0, r / 2.0)
        w = [[math.exp(-((x - r) ** 2 + (y - r) ** 2) / (2.0 * s * s))
              for x in range(size)] for y in range(size)]
    else:
        raise ValueError("window mode must be 'rect' or 'gaussian'")
    total = sum(sum(row) for row in w)
    return [[v / total for v in row] for row in w]


def structure_tensor(gx, gy, block_size=2, mode="rect", sigma=None):
    """结构张量的三个分量 (Sxx, Sxy, Syy) = Σ w * (Ix², IxIy, Iy²)。"""
    w = window_weights(block_size, mode, sigma)
    r = block_size
    h, wd = len(gx), len(gx[0])
    sxx = [[0.0] * wd for _ in range(h)]
    sxy = [[0.0] * wd for _ in range(h)]
    syy = [[0.0] * wd for _ in range(h)]
    for y in range(h):
        for x in range(wd):
            a = b = c = 0.0
            for dy in range(-r, r + 1):
                yy = clamp(y + dy, 0, h - 1)
                wr = w[dy + r]
                for dx in range(-r, r + 1):
                    xx = clamp(x + dx, 0, wd - 1)
                    ixx, iyy = gx[yy][xx], gy[yy][xx]
                    wv = wr[dx + r]
                    a += wv * ixx * ixx
                    b += wv * ixx * iyy
                    c += wv * iyy * iyy
            sxx[y][x], sxy[y][x], syy[y][x] = a, b, c
    return sxx, sxy, syy


def eigenvalues(sxx, sxy, syy):
    """对称正定 2x2 矩阵的特征值,返回 (λmax, λmin)。

    结构张量是若干 `[Ix,Iy]ᵀ[Ix,Iy]` 加权和,所以必为半正定 → λmin ≥ 0。
    """
    mid = 0.5 * (sxx + syy)
    dif = 0.5 * (sxx - syy)
    rad = math.sqrt(dif * dif + sxy * sxy)
    return mid + rad, mid - rad


def response_from_tensor(sxx, sxy, syy, k=0.04, method="harris"):
    """R = det(M) - k*trace(M)²(Harris)/ R = λmin(Shi-Tomasi)。"""
    if method == "harris":
        return sxx * syy - sxy * sxy - k * (sxx + syy) ** 2
    if method == "shi_tomasi":
        return min(eigenvalues(sxx, sxy, syy))
    raise ValueError("method must be 'harris' or 'shi_tomasi'")


def response_image(img, block_size=2, ksize=3, k=0.04, mode="rect", method="harris"):
    """从灰度图直接算响应图。ksize 仅作记录(Sobel 固定 3x3),保留以对齐 cv.cornerHarris 签名。"""
    if ksize != 3:
        raise ValueError("this demo implements ksize=3 only")
    gx, gy = sobel_gradients(img)
    sxx, sxy, syy = structure_tensor(gx, gy, block_size, mode)
    h, w = len(img), len(img[0])
    return [[response_from_tensor(sxx[y][x], sxy[y][x], syy[y][x], k, method)
             for x in range(w)] for y in range(h)]


def response_ratio_threshold(resp, ratio=0.01):
    """相对阈值:resp > ratio * max(resp)。教程示例用的是 `dst > 0.01*dst.max()`。"""
    peak = max(v for row in resp for v in row)
    return ratio * peak


def find_corners(resp, ratio=0.01, min_distance=1):
    """相对阈值 + 邻域非极大值抑制,返回 [(x, y, value)] 按响应降序。"""
    thr = response_ratio_threshold(resp, ratio)
    h, w = len(resp), len(resp[0])
    cand = []
    for y in range(h):
        for x in range(w):
            v = resp[y][x]
            if v <= thr:
                continue
            local = True
            for dy in range(-min_distance, min_distance + 1):
                yy = y + dy
                if yy < 0 or yy >= h or not local:
                    continue
                for dx in range(-min_distance, min_distance + 1):
                    xx = x + dx
                    if xx < 0 or xx >= w:
                        continue
                    if resp[yy][xx] > v:
                        local = False
                        break
            if local:
                cand.append((x, y, v))
    cand.sort(key=lambda t: -t[2])
    return cand


def subpixel_peak(resp, x, y):
    """响应图上的二次曲面拟合:把整数峰 (x,y) 修正到亚像素。

    一维形式(沿 x):δ = 0.5*(f(-1) - f(+1)) / (f(-1) - 2f(0) + f(+1)),
    对抛物线精确、对一般光滑峰是一阶近似。
    """
    h, w = len(resp), len(resp[0])
    if not (1 <= x < w - 1 and 1 <= y < h - 1):
        return float(x), float(y)
    fm, f0, fp = resp[y][x - 1], resp[y][x], resp[y][x + 1]
    denx = fm - 2.0 * f0 + fp
    dx = 0.0 if denx == 0.0 else 0.5 * (fm - fp) / denx
    fm, fp = resp[y - 1][x], resp[y + 1][x]
    deny = fm - 2.0 * f0 + fp
    dy = 0.0 if deny == 0.0 else 0.5 * (fm - fp) / deny
    return x + dx, y + dy


def cluster_candidates(resp, ratio=0.01):
    """把候选像素按 8 连通合并成「角点簇」,返回 [(质心x, 质心y, 峰值, 像素数)]。

    为什么需要这一步:理想角点周围的响应是一个**平台**(本 demo 实测 4x4=16 个像素
    响应完全相同),直接当角点会得到一团重复点。OpenCV 教程在 cornerSubPix 一节
    明说:"There may be a bunch of pixels at a corner, we take their centroid"
    —— 教程自己的做法也是先 `connectedComponentsWithStats` 再取质心。
    """
    thr = response_ratio_threshold(resp, ratio)
    h, w = len(resp), len(resp[0])
    seen = [[False] * w for _ in range(h)]
    clusters = []
    for sy in range(h):
        for sx in range(w):
            if seen[sy][sx] or resp[sy][sx] <= thr:
                continue
            stack = [(sx, sy)]
            seen[sy][sx] = True
            pts = []
            while stack:
                x, y = stack.pop()
                pts.append((x, y))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        xx, yy = x + dx, y + dy
                        if 0 <= xx < w and 0 <= yy < h and not seen[yy][xx] and resp[yy][xx] > thr:
                            seen[yy][xx] = True
                            stack.append((xx, yy))
            n = len(pts)
            clusters.append((sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n,
                             max(resp[y][x] for x, y in pts), n))
    clusters.sort(key=lambda t: -t[2])
    return clusters


def quadratic_peak_offset(f_minus, f0, f_plus):
    """一维亚像素峰位公式的独立入口(自检直接喂解析抛物线)。"""
    den = f_minus - 2.0 * f0 + f_plus
    return 0.0 if den == 0.0 else 0.5 * (f_minus - f_plus) / den
