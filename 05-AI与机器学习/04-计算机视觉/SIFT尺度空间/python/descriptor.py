# -*- coding: utf-8 -*-
"""SIFT 第 5~6 步:方向分配 + 128 维描述子 + 0.8 比率匹配。

论文口径(Lowe IJCV 2004 §5、§6):
- 方向直方图 **36 个 bin 覆盖 360°**(即每 bin 10°),样本**按梯度幅值加权**,
  并且乘一个 **σ = 1.5 × 关键点尺度** 的高斯圆形窗(离中心越远权重越小)。
- 取最高峰;任何**达到最高峰 80%** 的局部峰也各生成一个关键点(同位置同尺度、不同方向)。
  论文实测只有约 **15%** 的点被赋多个方向,但它们显著提升匹配稳定性。
- 最后对每个峰附近的 3 个直方图值**拟合抛物线**插值,提高方向精度。
- 描述子:关键点周围 **16x16** 采样,分成 **4x4 = 16 个子块**,每块 **8 个方向 bin**,
  合起来 **4x4x8 = 128** 维;子块内用 **三线性插值**(每维权重 `1-d`)避免边界突变。
- 高斯窗:σ 取**描述子窗口宽度的一半**,目的是抑制离中心远的梯度(它们受配准误差影响最大)。
- 光照不变性:向量先**归一化到单位长度**(抵消对比度线性变化;亮度整体平移本就不影响梯度),
  再把每个分量**截到 0.2** 后**重新归一化**(抑制非线性光照/饱和造成的大梯度),0.2 由实验确定。
- 匹配:最近邻/次近邻距离比 **> 0.8 则拒绝**,论文称可剔除约 **90% 的错误匹配** 而只丢弃不到 5% 的正确匹配。
"""
import math

ORIENTATION_BINS = 36        # 360° / 10° per bin
PEAK_RATIO = 0.8             # 达到最高峰 80% 的局部峰也生成关键点
DESC_WIDTH = 4               # 4x4 子块
DESC_BINS = 8                # 每块 8 个方向
DESC_SAMPLES = 16            # 16x16 采样
DESC_CLAMP = 0.2             # 截断阈值
MATCH_RATIO = 0.8            # 最近邻/次近邻距离比


def orientation_histogram(mags, angs, weights):
    """36 个 bin 的方向直方图。angs 为**梯度方向(度)**,样本可带权重。"""
    hist = [0.0] * ORIENTATION_BINS
    for m, a, w in zip(mags, angs, weights):
        idx = int((a % 360.0) // (360.0 / ORIENTATION_BINS)) % ORIENTATION_BINS
        hist[idx] += m * w
    return hist


def gaussian_circle_weights(offsets, sigma):
    """σ = 1.5 × scale 的圆形高斯窗(论文 §5)。offsets 为 [(dx,dy), ...]。"""
    return [math.exp(-(dx * dx + dy * dy) / (2.0 * sigma * sigma)) for dx, dy in offsets]


def parabolic_peak_offset(left, center, right):
    """对峰值附近 3 个直方图值拟合抛物线求亚 bin 偏移(论文 §5 末段)。"""
    den = left - 2.0 * center + right
    return 0.0 if den == 0.0 else 0.5 * (left - right) / den


def assign_orientations(hist, peak_ratio=PEAK_RATIO):
    """返回 [(bin_index, 精化后的角度, 峰值高度)];最高峰必在,其余需 >= peak*ratio。"""
    n = len(hist)
    peak = max(hist)
    if peak <= 0.0:
        return []
    out = []
    for i in range(n):
        if hist[i] < peak * peak_ratio or hist[i] <= 0.0:
            continue
        if hist[i] < hist[(i - 1) % n] or hist[i] < hist[(i + 1) % n]:
            continue                      # 必须是局部峰
        off = parabolic_peak_offset(hist[(i - 1) % n], hist[i], hist[(i + 1) % n])
        deg = (i + off) * (360.0 / n) % 360.0
        out.append((i, deg, hist[i]))
    out.sort(key=lambda t: -t[2])
    return out


def trilinear_weights(fx, fy, fa):
    """把一个样本分配到 4x4x8 直方图相邻 bin 的三线性权重,返回 [((i,j,k), w)]。

    论文:"each entry into a bin is multiplied by a weight of 1-d for each dimension,
    where d is the distance of the sample from the central value of the bin"。
    """
    out = []
    for di in (0, 1):
        wi = fx if di == 1 else 1.0 - fx
        for dj in (0, 1):
            wj = fy if dj == 1 else 1.0 - fy
            for dk in (0, 1):
                wk = fa if dk == 1 else 1.0 - fa
                out.append(((di, dj, dk), wi * wj * wk))
    return out


def normalize_descriptor(vec, clamp=DESC_CLAMP):
    """单位化 -> 截断到 clamp -> 再单位化(论文 §6 的光照不变化两步)。"""
    n = math.sqrt(sum(v * v for v in vec))
    if n == 0.0:
        return list(vec)
    unit = [v / n for v in vec]
    clipped = [min(v, clamp) for v in unit]
    n2 = math.sqrt(sum(v * v for v in clipped))
    return [v / n2 for v in clipped] if n2 > 0.0 else clipped


def compute_descriptor(samples, keypoint_angle_deg, width=DESC_WIDTH, bins=DESC_BINS,
                       sample_grid=DESC_SAMPLES, window_sigma_ratio=0.5):
    """从 16x16 旋转对齐后的梯度样本算 128 维描述子。

    samples: [(u, v, magnitude, gradient_angle_deg)] —— 已经按关键点方向旋转到
    关键点局部坐标系,u/v 为**采样格坐标**(窗口中心为原点),与论文 Fig.7 一致。
    窗口高斯权重的 σ 取窗口宽度的一半(window_sigma_ratio=0.5)。
    """
    sigma = window_sigma_ratio * sample_grid
    vec = [0.0] * (width * width * bins)
    half = sample_grid / 2.0
    per = sample_grid / float(width)          # 每个子块覆盖的采样格数(16/4 = 4)
    binw = 360.0 / bins
    for u, v, mag, ang in samples:
        w = math.exp(-(u * u + v * v) / (2.0 * sigma * sigma))
        rel = (ang - keypoint_angle_deg) % 360.0
        # 采样格坐标 u,v ∈ [-half, half] 映射到子块网格:第 0 个子块覆盖 [-8,-4)
        # 对应采样格 0..3,故网格坐标 g = (u + half) / per ∈ (0, width),不做半格平移
        gi = (u + half) / per
        gj = (v + half) / per
        ga = rel / binw
        i0, j0, a0 = int(math.floor(gi)), int(math.floor(gj)), int(math.floor(ga))
        wx, wy, wa = gi - i0, gj - j0, ga - a0
        for (di, dj, dk), tw in trilinear_weights(wx, wy, wa):
            ii, jj = i0 + di, j0 + dj
            kk = (a0 + dk) % bins
            if 0 <= ii < width and 0 <= jj < width:
                vec[(jj * width + ii) * bins + kk] += mag * w * tw
    return normalize_descriptor(vec)


def descriptor_distance(a, b):
    """欧氏距离(论文用最近邻/次近邻距离比做匹配筛选)。"""
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def ratio_test(query, candidates, ratio=MATCH_RATIO):
    """返回 (最近邻索引, 距离比)。距离比 > ratio 视为无匹配(论文 §7.1)。"""
    ds = sorted(((descriptor_distance(query, c), i) for i, c in enumerate(candidates)))
    if len(ds) < 2:
        return (ds[0][1], 0.0) if ds else (None, float("inf"))
    best, second = ds[0][0], ds[1][0]
    if second == 0.0:
        return ds[0][1], float("inf")
    return ds[0][1], best / second
