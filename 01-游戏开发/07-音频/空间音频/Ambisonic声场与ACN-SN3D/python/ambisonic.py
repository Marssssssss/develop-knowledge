"""Ambisonic 声场：ACN 通道序 + SN3D 归一化 + 一阶编解码 + SA3D 盒。

来源（本 demo 实读，口径均来自原文）：
  * Google《Spatial Audio RFC》`docs/spatial-audio-rfc.md`（spatial-media 仓库 master 分支）
    - ACN: n = l*(l+1) + m
    - SN3D: sqrt((2 - delta(m)) * (l-m)! / (l+m)!)
    - 球谐: N(l,|m|) * P(l,|m|, sin E) * T(m, A)，T(m,x) = sin(-m*x) (m<0) / cos(m*x) (m>=0)
    - 约定: A=0 正前，A∈(0,pi/2) 左前；E=0 水平面，E∈(0,pi/2] 上方
    - 每iphonic 声场通道数 (order+1)^2，order = sqrt(n) - 1
    - SA3D 盒字段与两个 channel_map 示例
所有超出原文的口径（如 N3D 换算、采样解码器 1/K）在 README 中显式标注。
"""

import math
import struct

# ---------------------------------------------------------------- 阶与通道

def acn_index(degree, order):
    """ACN: n = l*(l+1) + m（原文公式）。"""
    return degree * (degree + 1) + order


def acn_components(max_order):
    """按 ACN 升序列出 (n, l, m)。"""
    out = []
    for degree in range(max_order + 1):
        for m in range(-degree, degree + 1):
            out.append((acn_index(degree, m), degree, m))
    out.sort()
    return out


def channels_for_order(order):
    """全向（periphonic）声场通道数 = (order+1)^2。"""
    return (order + 1) ** 2


def order_for_channels(channels):
    """原文：order = sqrt(n) - 1；非完全平方数时返回 None（非法通道数）。"""
    root = int(round(math.sqrt(channels)))
    if root * root != channels:
        return None
    return root - 1


# ---------------------------------------------------------------- 归一化

def sn3d(degree, m):
    """SN3D（Schmidt semi-normalization），原文公式；阶 m 取其绝对值。"""
    m = abs(m)
    delta = 1 if m == 0 else 0
    return math.sqrt((2 - delta) * math.factorial(degree - m) / math.factorial(degree + m))


def n3d_factor(degree):
    """N3D = SN3D * sqrt((2l+1)/4pi)（标准球谐归一化关系，本 demo 口径）。"""
    return math.sqrt((2 * degree + 1) / (4 * math.pi))


def _legendre(degree, m, x):
    """无 Condon-Shortley 相位的缔合勒让德多项式 P(l,m,x)。"""
    if m < 0 or m > degree:
        return 0.0
    pmm = 1.0
    if m > 0:
        somx2 = math.sqrt(max(0.0, 1.0 - x * x))
        fact = 1.0
        for _ in range(m):
            pmm *= -fact * somx2 if False else fact * somx2
            fact += 2.0
    if degree == m:
        return pmm
    pmmp1 = (2 * m + 1) * x * pmm
    if degree == m + 1:
        return pmmp1
    for l in range(m + 2, degree + 1):
        pl = ((2 * l - 1) * x * pmmp1 - (l + m - 1) * pmm) / (l - m)
        pmm, pmmp1 = pmmp1, pl
    return pmmp1


def harmonic(degree, m, elevation, azimuth, normalization="SN3D"):
    """球谐分量 N(l,|m|) * P(l,|m|, sin E) * T(m, A)。"""
    x = math.sin(elevation)
    val = _legendre(degree, abs(m), x)
    if m < 0:
        val *= math.sin(-m * azimuth)
    else:
        val *= math.cos(m * azimuth)
    if normalization == "SN3D":
        return sn3d(degree, m) * val
    if normalization == "N3D":
        return sn3d(degree, m) * n3d_factor(degree) * val
    raise ValueError("unknown normalization: %r" % normalization)


def encode(max_order, elevation, azimuth, gain=1.0, normalization="SN3D"):
    """平面波编码：按 ACN 序输出 B 格式分量。"""
    return [gain * harmonic(l, m, elevation, azimuth, normalization)
            for _, l, m in acn_components(max_order)]


def decode_sampling(b, speakers):
    """采样解码（本 demo 口径：1/K 加权，未做伪逆/近场补偿）。

    speakers: [(elevation, azimuth), ...]；返回每个扬声器的一次增益。
    """
    max_order = order_for_channels(len(b))
    if max_order is None:
        raise ValueError("channels %d is not (order+1)^2" % len(b))
    comps = acn_components(max_order)
    out = []
    for elevation, azimuth in speakers:
        acc = 0.0
        for idx, (_, l, m) in enumerate(comps):
            acc += b[idx] * harmonic(l, m, elevation, azimuth)
        out.append(acc / len(speakers))
    return out


def energy_sum(max_order, elevation, azimuth, normalization="SN3D"):
    """Σ_n Y_n² —— SN3D 下恒等于 order+1（加法定理推论）。"""
    return sum(v * v for v in encode(max_order, elevation, azimuth, 1.0, normalization))


# ---------------------------------------------------------------- channel_map

def build_channel_map(stored_components):
    """原文语义：channel_map[i] = ACN 分量 i 所在的轨道通道下标。

    stored_components: 按轨道通道顺序排列的分量名，如 ["W","X","Y","Z"]。
    非 ambisonic 分量（如 head-locked 立体声 L/R）按轨道顺序接在 4 个 ACN 分量之后，
    这与原文「W,Y,Z,X,L,R -> 0,1,2,3,4,5」的示例一致。
    """
    acn_of = {"W": 0, "Y": 1, "Z": 2, "X": 3}
    position = {name: i for i, name in enumerate(stored_components)}
    head = [position[name] for name in ("W", "Y", "Z", "X") if name in position]
    tail = [i for name, i in position.items() if name not in acn_of]
    tail.sort()
    return head + tail


def apply_channel_map(channels, channel_map):
    """把轨道通道重排成 ACN 序：out[i] = channels[channel_map[i]]。"""
    return [channels[i] for i in channel_map]


# ---------------------------------------------------------------- SA3D 盒

AMBISONIC_TYPE_PERIPHONIC = 0
CHANNEL_ORDERING_ACN = 0
NORMALIZATION_SN3D = 0


class SA3DBox(object):
    """Spatial Audio Box（原文 §Syntax，大端）。"""

    def __init__(self, version=0, ambisonic_type=0, ambisonic_order=1,
                 ordering=0, normalization=0, channel_map=None):
        self.version = version
        self.ambisonic_type = ambisonic_type
        self.ambisonic_order = ambisonic_order
        self.ordering = ordering
        self.normalization = normalization
        self.channel_map = list(channel_map or [])

    def render(self):
        out = struct.pack(">BBIBB I".replace(" ", ""),
                          self.version, self.ambisonic_type,
                          self.ambisonic_order, self.ordering,
                          self.normalization, len(self.channel_map))
        for c in self.channel_map:
            out += struct.pack(">I", c)
        return out

    @staticmethod
    def parse(data):
        if len(data) < 12:
            raise ValueError("SA3D too short")
        version, ambisonic_type, order, ordering, normalization, num = \
            struct.unpack(">BBIBBI", data[:12])
        if len(data) != 12 + 4 * num:
            raise ValueError("SA3D length mismatch: num_channels=%d" % num)
        channel_map = list(struct.unpack(">%dI" % num, data[12:]))
        return SA3DBox(version, ambisonic_type, order, ordering,
                       normalization, channel_map)
