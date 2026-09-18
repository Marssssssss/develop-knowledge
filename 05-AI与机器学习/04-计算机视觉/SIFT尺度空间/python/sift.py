# -*- coding: utf-8 -*-
"""SIFT 第 1~4 步:尺度空间极值 → 关键点定位 → 剔除边缘响应。

权威口径:D.Lowe《Distinctive Image Features from Scale-Invariant Keypoints》
(IJCV 2004,本机下载全文后逐节精读)+ OpenCV 4.x `tutorial_py_sift_intro`。

论文中的可验证结论(本 demo 用断言把它们钉住):
- 尺度空间核只能是高斯(Koenderink 1984 / Lindeberg 1994);`L(x,y,σ) = G(x,y,σ) * I(x,y)`
- 用 DoG 近似 **尺度归一化** LoG:`D(x,y,σ) = L(x,y,kσ) - L(x,y,σ) ≈ (k-1)σ²∇²G * I`
  (由热方程 `∂G/∂σ = σ∇²G` 推出;因子 `(k-1)` 与尺度无关,不影响极值位置)
- 每个 octave 被分成 `s` 个间隔,`k = 2^(1/s)`;**必须生成 `s+3` 张模糊图**,
  这样极值检测才能覆盖完整的一个 octave;处理后把 σ 加倍的图按行列隔点重采样得下一个 octave
- 极值判据:与**同层 8 个邻居 + 上下层各 9 个邻居**共 26 个比较
- 亚像素定位:对 D 做 Taylor 展开解 3x3 线性系统;偏移 > 0.5 则换到相邻采样点重做
- 低对比度剔除:`|D(x̂)| < 0.03`(像素值在 [0,1])
- 边缘剔除:2x2 Hessian,用 `Tr(H)²/Det(H) < (r+1)²/r` 避开显式求特征值,论文取 `r = 10`
- 经验参数:每 octave 采样 3 个尺度时重复性最高;初始 σ = 1.6;推荐 octaves = 4
"""
import math

SIFT_SIGMA = 1.6            # 论文:initial σ = 1.6(把输入图预模糊到该尺度)
SIFT_ASSUMED_BLUR = 0.5     # 假设输入图本身已被 σ=0.5 的高斯模糊
SCALES_PER_OCTAVE = 3       # 论文实验:每 octave 采样 3 个尺度时重复性最高
CONTRAST_THRESHOLD = 0.03   # 论文:|D(x̂)| < 0.03 的极值丢弃
EDGE_RATIO = 10.0           # 论文:主曲率比 > 10 的关键点丢弃
DEFAULT_OCTAVES = 4         # 论文给出的经验值


def gaussian_1d(sigma, radius=None):
    """归一化 1D 高斯核。radius 默认取 3σ(截断到 0.3% 能量之外)。"""
    r = int(math.ceil(3.0 * sigma)) if radius is None else radius
    k = [math.exp(-(i * i) / (2.0 * sigma * sigma)) for i in range(-r, r + 1)]
    total = sum(k)
    return [v / total for v in k]


def blur1d(img, kernel, axis):
    """沿 axis(0=行/y,1=列/x)做一维卷积,clamp 边界。"""
    h, w = len(img), len(img[0])
    r = len(kernel) // 2
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            acc = 0.0
            for i, kv in enumerate(kernel):
                if axis == 0:
                    yy = min(max(y + i - r, 0), h - 1)
                    acc += kv * img[yy][x]
                else:
                    xx = min(max(x + i - r, 0), w - 1)
                    acc += kv * img[y][xx]
            out[y][x] = acc
    return out


def gaussian_blur(img, sigma):
    """可分离 2D 高斯模糊(先列后行)。"""
    if sigma <= 0.0:
        return [list(r) for r in img]
    k = gaussian_1d(sigma)
    return blur1d(blur1d(img, k, 1), k, 0)


def half_size(img):
    """按行列隔点重采样(论文:after each octave, down-sample by 2)。"""
    h, w = len(img), len(img[0])
    return [[img[2 * y][2 * x] for x in range(w // 2)] for y in range(h // 2)]


def incremental_sigma(base_octave_sigma, target_sigma):
    """把已模糊到 base 的图再模糊到 target 所需的增量 σ:sqrt(target² - base²)。"""
    d = target_sigma * target_sigma - base_octave_sigma * base_octave_sigma
    return math.sqrt(d) if d > 0.0 else 0.0


def build_gaussian_pyramid(img, octaves=DEFAULT_OCTAVES, s=SCALES_PER_OCTAVE,
                           sigma=SIFT_SIGMA, assumed_blur=SIFT_ASSUMED_BLUR):
    """高斯金字塔:每个 octave 内 s+3 张模糊图,octave 间 σ 与尺寸各翻倍。

    每个 octave 的 σ 序列是 `σ, σk, σk², …, σk^(s+2)`,其中 `k = 2^(1/s)`;
    降采样取第 `s` 张(σ 恰为 2σ),降采样后其有效 σ 回到 σ,于是下一个 octave
    "start of the previous octave 的采样精度不变"而计算量大幅下降(论文原话)。
    """
    k = 2.0 ** (1.0 / s)
    sigmas = [sigma * (k ** i) for i in range(s + 3)]
    current = gaussian_blur(img, incremental_sigma(assumed_blur, sigma))
    pyramid = []
    for _ in range(octaves):
        stack = [current]
        for i in range(1, s + 3):
            stack.append(gaussian_blur(stack[i - 1], incremental_sigma(sigmas[i - 1], sigmas[i])))
        pyramid.append(stack)
        current = half_size(stack[s])
    return pyramid


def build_dog_pyramid(pyramid):
    """DoG:同一 octave 内相邻两张模糊图相减,得 s+2 张。"""
    dogs = []
    for stack in pyramid:
        h, w = len(stack[0]), len(stack[0][0])
        octave = []
        for i in range(len(stack) - 1):
            a, b = stack[i], stack[i + 1]
            octave.append([[b[y][x] - a[y][x] for x in range(w)] for y in range(h)])
        dogs.append(octave)
    return dogs


def find_extrema(dogs):
    """尺度空间极值:与 3x3x3 的 26 个邻居比较(自身上下层各 9 个 + 同层 8 个)。"""
    found = []
    for oi, octave in enumerate(dogs):
        n = len(octave)
        h, w = len(octave[0]), len(octave[0][0])
        for s in range(1, n - 1):          # 首尾层无法凑齐 26 邻居,天然排除
            for y in range(1, h - 1):
                for x in range(1, w - 1):
                    v = octave[s][y][x]
                    is_max, is_min = True, True
                    for ds in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            for dx in (-1, 0, 1):
                                if ds == 0 and dy == 0 and dx == 0:
                                    continue
                                nv = octave[s + ds][y + dy][x + dx]
                                if nv >= v:
                                    is_max = False
                                if nv <= v:
                                    is_min = False
                    if is_max or is_min:
                        found.append((oi, s, y, x, v))
    return found


def _d1(vol, s, y, x):
    """Taylor 展开用的一阶差分(论文:derivatives approximated by differences of neighbours)。"""
    return (vol[s + 1][y][x] - vol[s - 1][y][x]) * 0.5


def localize(vol, s, y, x, contrast_threshold=CONTRAST_THRESHOLD):
    """亚像素定位 + 低对比度剔除。

    返回 dict:ok / offset(ds,dy,dx) / need_resample / D_hat / contrast_ok。
    `x̂ = -H⁻¹ (∂D/∂x)`,`D(x̂) = D + ½ (∂D/∂x)ᵀ x̂`(论文式 (2)(3))。
    """
    g = [_d1(vol, s, y, x),
         (vol[s][y + 1][x] - vol[s][y - 1][x]) * 0.5,
         (vol[s][y][x + 1] - vol[s][y][x - 1]) * 0.5]
    dss = vol[s + 1][y][x] - 2.0 * vol[s][y][x] + vol[s - 1][y][x]
    dyy = vol[s][y + 1][x] - 2.0 * vol[s][y][x] + vol[s][y - 1][x]
    dxx = vol[s][y][x + 1] - 2.0 * vol[s][y][x] + vol[s][y][x - 1]
    dsy = (vol[s + 1][y + 1][x] - vol[s + 1][y - 1][x] - vol[s - 1][y + 1][x] + vol[s - 1][y - 1][x]) * 0.25
    dsx = (vol[s + 1][y][x + 1] - vol[s + 1][y][x - 1] - vol[s - 1][y][x + 1] + vol[s - 1][y][x - 1]) * 0.25
    dyx = (vol[s][y + 1][x + 1] - vol[s][y + 1][x - 1] - vol[s][y - 1][x + 1] + vol[s][y - 1][x - 1]) * 0.25
    h = [[dss, dsy, dsx], [dsy, dyy, dyx], [dsx, dyx, dxx]]
    off = _solve3(h, [-v for v in g])
    if off is None:
        return {"ok": False, "offset": None, "need_resample": False, "D_hat": vol[s][y][x],
                "contrast_ok": False}
    need_resample = max(abs(v) for v in off) > 0.5
    d_hat = vol[s][y][x] + 0.5 * sum(g[i] * off[i] for i in range(3))
    return {"ok": True, "offset": tuple(off), "need_resample": need_resample,
            "D_hat": d_hat, "contrast_ok": abs(d_hat) >= contrast_threshold}


def _solve3(a, b):
    """3x3 线性方程组的高斯消元(无 numpy)。奇异返回 None。"""
    m = [list(a[i]) + [b[i]] for i in range(3)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-18:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(3):
            if r == col:
                continue
            f = m[r][col] / m[col][col]
            for c in range(col, 4):
                m[r][c] -= f * m[col][c]
    return [m[i][3] / m[i][i] for i in range(3)]


def principal_curvature_ratio(hxx, hxy, hyy):
    """Tr(H)²/Det(H) = (r+1)²/r,只依赖主曲率比 r。Det <= 0 表示曲率异号。"""
    tr = hxx + hyy
    det = hxx * hyy - hxy * hxy
    if det <= 0.0:
        return None
    return tr * tr / det


def edge_ratio_limit(r=EDGE_RATIO):
    """论文的判据阈值 (r+1)²/r;r=10 时为 12.1。"""
    return (r + 1.0) ** 2 / r


def is_edge_response(hxx, hxy, hyy, r=EDGE_RATIO):
    """True 表示应作为边缘响应剔除。"""
    ratio = principal_curvature_ratio(hxx, hxy, hyy)
    if ratio is None:
        return True
    return ratio >= edge_ratio_limit(r)


def hessian_at(vol, s, y, x):
    """D 的 2x2 Hessian(论文式 (4))。"""
    hxx = vol[s][y][x + 1] - 2.0 * vol[s][y][x] + vol[s][y][x - 1]
    hyy = vol[s][y + 1][x] - 2.0 * vol[s][y][x] + vol[s][y - 1][x]
    hxy = (vol[s][y + 1][x + 1] - vol[s][y + 1][x - 1]
           - vol[s][y - 1][x + 1] + vol[s][y - 1][x - 1]) * 0.25
    return hxx, hxy, hyy


def detect(vol, contrast_threshold=CONTRAST_THRESHOLD, r=EDGE_RATIO):
    """对单个 octave 的 DoG 体积做完整的关键点筛选(定位 -> 对比度 -> 边缘)。"""
    out = []
    for oi, s, y, x, v in find_extrema([vol]):
        loc = localize(vol, s, y, x, contrast_threshold)
        if not loc["ok"]:
            out.append((oi, s, y, x, v, "singular"))
            continue
        if loc["need_resample"]:
            out.append((oi, s, y, x, v, "offset>0.5"))
            continue
        if not loc["contrast_ok"]:
            out.append((oi, s, y, x, v, "low_contrast"))
            continue
        hxx, hxy, hyy = hessian_at(vol, s, y, x)
        if is_edge_response(hxx, hxy, hyy, r):
            out.append((oi, s, y, x, v, "edge"))
            continue
        out.append((oi, s, y, x, v, "kept"))
    return out
