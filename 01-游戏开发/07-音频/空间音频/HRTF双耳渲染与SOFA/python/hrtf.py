"""HRTF 双耳渲染：SOFA(SimpleFreeFieldHRIR) 约定 + libmysofa 的最近邻/反距离插值。

实读来源：
  * SOFA conventions《SimpleFreeFieldHRIR》v1.2 表（sofaconventions.org）
    - ListenerPosition [0 0 0]、ListenerView [1 0 0]、ListenerUp [0 0 1]（笛卡尔，米）
    - ReceiverPosition [0 0.09 0; 0 -0.09 0]（R=2，头半径默认 0.09 m）
    - SourcePosition 球坐标，单位 degree, degree, metre，默认 [0 0 1]
    - Data.IR 维度 mRn、Data.SamplingRate 默认 48000（hertz）、Data.Delay 维度 IR/MR（单位：样本）
  * libmysofa README（hoene/libmysofa）
    - X 轴 (1 0 0) 为听音正前方，Y (0 1 0) 为左侧，Z (0 0 1) 向上
    - phi = 方位角（度，自 X 轴逆时针）、theta = 仰角（度，自 X-Y 平面向上）
    - mysofa_getfilter_short 的 delay 单位是样本；mysofa_getfilter_float 的 delay 单位是秒
    - mysofa_getfilter_float_nointerp 用「最近点坐标覆盖请求坐标」来绕过插值
  * libmysofa 源码 src/hrtf/tools.h：fequals(a,b) = fabs(a-b) < 0.00001；distance 为欧氏距离宏
  * libmysofa 源码 src/hrtf/interpolate.c：命中即直取；否则 6 邻域按对择优 + 1/d 加权后归一化
  * libmysofa 源码 src/hrtf/spherical.c + tools.c：mysofa_s2c / mysofa_c2s
"""

import math

FEQ_EPS = 0.00001  # tools.h: (fabs(a-b) < 0.00001)

HEAD_RADIUS = 0.09  # SimpleFreeFieldHRIR 默认接收器位置 [0 0.09 0; 0 -0.09 0]


def fequals(a, b):
    return abs(a - b) < FEQ_EPS


def distance(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


# ------------------------------------------------------------ 坐标换算

def s2c(values):
    """球坐标 (phi, theta, r) -> 笛卡尔 (x, y, z)，与 mysofa_s2c 逐式对齐。"""
    phi = values[0] * (math.pi / 180)
    theta = values[1] * (math.pi / 180)
    r = values[2]
    x = math.cos(theta) * r
    z = math.sin(theta) * r
    return [math.cos(phi) * x, math.sin(phi) * x, z]


def c2s(values):
    """笛卡尔 -> 球坐标，方位角规整到 [0, 360)，与 mysofa_c2s 逐式对齐。"""
    x, y, z = values[0], values[1], values[2]
    r = math.sqrt(x * x + y * y + z * z)
    theta = math.atan2(z, math.sqrt(x * x + y * y))
    phi = math.atan2(y, x)
    return [math.fmod(phi * (180 / math.pi) + 360, 360), theta * (180 / math.pi), r]


# ------------------------------------------------------------ 数据模型

class HRTF(object):
    """SimpleFreeFieldHRIR 的最小可运行模型。"""

    def __init__(self, source_spherical, irs, sampling_rate=48000.0, delays=None):
        self.M = len(source_spherical)          # 测量点数
        self.R = 2                              # 接收器（耳）数
        self.C = 3                              # 每个坐标的分量数
        self.N = len(irs[0][0])                 # 每条 IR 的采样数
        self.sampling_rate = sampling_rate
        # mysofa_tocartesian 会把 SourcePosition 就地转成笛卡尔
        self.source_spherical = list(source_spherical)
        self.source_cartesian = [s2c(p) for p in source_spherical]
        self.ir = irs                           # irs[m] = [left_taps, right_taps]
        # DataDelay 维度：MR（每测量点一份）或 R（全体共享）
        self.delays = delays if delays is not None else [[0.0, 0.0] for _ in range(self.M)]
        # easy.c：SourcePosition.elements 必须等于 C*M
        self.source_elements = len(source_spherical) * self.C

    def delay_pair(self, m):
        """interpolate.c：DataDelay.elements > R 时按测量点取，否则取 [0],[1]。"""
        if len(self.delays) > self.R:
            return list(self.delays[m])
        return [self.delays[0][0], self.delays[0][1]]

    def validate(self):
        """easy.c：SourcePosition.elements != C*M 时报 MYSOFA_INVALID_FORMAT。"""
        return self.source_elements == self.C * self.M


def nearest_index(hrtf, coordinate):
    """最近测量点（libmysofa 用 k-d tree 加速，语义等价于线性最短路）。"""
    best, best_d = -1, None
    for i in range(hrtf.M):
        d = distance(coordinate, hrtf.source_cartesian[i])
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def build_neighborhood(hrtf, nearest, coordinate, candidate_slots):
    """把 6 个候选槽位填成测量点下标（-1 表示空缺）。

    candidate_slots: 长度 6 的列表，元素为测量点下标或 -1。槽位按 (0,1)(2,3)(4,5) 成对。
    """
    if len(candidate_slots) != 6:
        raise ValueError("neighborhood needs 6 slots")
    return list(candidate_slots)


def interpolate(hrtf, coordinate, nearest, neighborhood):
    """与 mysofa_interpolate 同口径：命中直取，否则按对择优 + 1/d 加权归一化。

    返回 (fir_left, fir_right, delay_left_sec, delay_right_sec)。
    """
    size = hrtf.N * hrtf.R
    d = distance(coordinate, hrtf.source_cartesian[nearest])
    flat_nearest = hrtf.ir[nearest][0] + hrtf.ir[nearest][1]

    if fequals(d, 0.0):
        dl, dr = hrtf.delay_pair(nearest)
        return list(hrtf.ir[nearest][0]), list(hrtf.ir[nearest][1]), dl, dr

    use = [False] * 6
    d6 = [1.0] * 6
    for a, b in ((0, 1), (2, 3), (4, 5)):
        ia, ib = neighborhood[a], neighborhood[b]
        if ia >= 0 and ib >= 0:
            d6[a] = distance(coordinate, hrtf.source_cartesian[ia])
            d6[b] = distance(coordinate, hrtf.source_cartesian[ib])
            if not fequals(d6[a], d6[b]):
                use[a if d6[a] < d6[b] else b] = True
        elif ia >= 0:
            d6[a] = distance(coordinate, hrtf.source_cartesian[ia])
            use[a] = True
        elif ib >= 0:
            d6[b] = distance(coordinate, hrtf.source_cartesian[ib])
            use[b] = True

    weight = 1.0 / d
    fir = [v * weight for v in flat_nearest]
    dl, dr = hrtf.delay_pair(nearest)
    dl *= weight
    dr *= weight
    for i in range(6):
        if use[i]:
            w = 1.0 / d6[i]
            src = hrtf.ir[neighborhood[i]][0] + hrtf.ir[neighborhood[i]][1]
            fir = [fir[k] + src[k] * w for k in range(size)]
            dlm, drm = hrtf.delay_pair(neighborhood[i])
            dl += dlm * w
            dr += drm * w
            weight += w
    norm = 1.0 / weight
    fir = [v * norm for v in fir]
    return fir[:hrtf.N], fir[hrtf.N:], dl * norm, dr * norm


def getfilter(hrtf, coordinate, neighborhood=None, interp=True, unit="float"):
    """mysofa_getfilter_* 的等价实现。unit='short' 时延迟换算为样本。"""
    if not hrtf.validate():
        raise ValueError("MYSOFA_INVALID_FORMAT")
    coord = list(coordinate)
    nearest = nearest_index(hrtf, coord)
    if not interp:
        # mysofa_getfilter_float_nointerp：用最近点坐标覆盖请求坐标
        coord = list(hrtf.source_cartesian[nearest])
    nb = neighborhood or [-1] * 6
    left, right, dl, dr = interpolate(hrtf, coord, nearest, nb)
    if unit == "short":
        # getfilter_short：delay(秒) * DataSamplingRate -> 样本
        return (left, right,
                int(dl * hrtf.sampling_rate), int(dr * hrtf.sampling_rate))
    return left, right, dl, dr


# ------------------------------------------------------------ 双耳线索

def itd_seconds(left_delay_samples, right_delay_samples, sampling_rate):
    """耳间时间差（秒）：延迟以**样本**计（mysofa_getfilter_short 口径）。

    正值 = 右耳更晚 = 声音先到左耳 = 源在左侧。
    """
    return (right_delay_samples - left_delay_samples) / sampling_rate


def ild_db(left_ir, right_ir):
    """耳间强度差：20*log10(rms_right / rms_left)。"""
    def rms(x):
        return math.sqrt(sum(v * v for v in x) / len(x)) if x else 0.0
    rl, rr = rms(left_ir), rms(right_ir)
    if rl == 0.0:
        return float("inf") if rr > 0 else 0.0
    return 20.0 * math.log10(rr / rl)


def render_mono(hrtf, coordinate, samples, neighborhood=None):
    """把单声道信号与选出的左右 IR 做 FIR 卷积（双耳渲染的最小闭环）。"""
    left, right, dl, dr = getfilter(hrtf, coordinate, neighborhood, True, "short")
    total = len(samples) + hrtf.N - 1 + max(dl, dr)
    out_l = [0.0] * total
    out_r = [0.0] * total
    for n, s in enumerate(samples):
        for k in range(hrtf.N):
            out_l[n + dl + k] += s * left[k]
            out_r[n + dr + k] += s * right[k]
    return out_l, out_r
