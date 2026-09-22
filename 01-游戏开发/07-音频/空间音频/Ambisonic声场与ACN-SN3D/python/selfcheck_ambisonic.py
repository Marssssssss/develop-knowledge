"""ambisonic.py 自检：ACN / SN3D / 编解码 / channel_map / SA3D。实跑版。"""

import math
import struct

from ambisonic import (acn_components, acn_index, apply_channel_map,
                       build_channel_map, channels_for_order, decode_sampling,
                       encode, energy_sum, harmonic, n3d_factor,
                       order_for_channels, sn3d, SA3DBox)

TOL = 1e-9
_ok = 0
_bad = []


def check(name, got, want, tol=TOL):
    global _ok
    if isinstance(want, float) or isinstance(got, float):
        good = abs(got - want) <= tol
    else:
        good = got == want
    if good:
        _ok += 1
    else:
        _bad.append("%s: got %r want %r" % (name, got, want))


def near(name, got, want, tol=TOL):
    check(name, got, want, tol)


def _raises(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


# --- ACN 序 -------------------------------------------------------------
check("acn(0,0)", acn_index(0, 0), 0)
check("acn(1,-1)", acn_index(1, -1), 1)
check("acn(1,0)", acn_index(1, 0), 2)
check("acn(1,1)", acn_index(1, 1), 3)
check("acn(2,-2)", acn_index(2, -2), 4)
check("acn(2,2)", acn_index(2, 2), 8)
check("一阶分量表", acn_components(1),
      [(0, 0, 0), (1, 1, -1), (2, 1, 0), (3, 1, 1)])
check("二阶共 9 分量", len(acn_components(2)), 9)

# --- 通道数 ↔ 阶 -------------------------------------------------------
check("order1 -> 4ch", channels_for_order(1), 4)
check("order2 -> 9ch", channels_for_order(2), 9)
check("order3 -> 16ch", channels_for_order(3), 16)
check("4ch -> order1", order_for_channels(4), 1)
check("9ch -> order2", order_for_channels(9), 2)
check("5ch 非法", order_for_channels(5), None)
check("16ch -> order3", order_for_channels(16), 3)

# --- SN3D ---------------------------------------------------------------
near("sn3d(0,0)", sn3d(0, 0), 1.0)
near("sn3d(1,0)", sn3d(1, 0), 1.0)
near("sn3d(1,1)", sn3d(1, 1), 1.0)
near("sn3d(2,0)", sn3d(2, 0), 1.0)
near("sn3d(2,1)", sn3d(2, 1), math.sqrt(1.0 / 3.0))
near("sn3d(2,2)", sn3d(2, 2), math.sqrt(1.0 / 12.0))
near("sn3d(3,1)", sn3d(3, 1), math.sqrt(2.0 * 2.0 / 24.0))
near("n3d_factor(0)", n3d_factor(0), math.sqrt(1.0 / (4 * math.pi)))
near("n3d_factor(1)", n3d_factor(1), math.sqrt(3.0 / (4 * math.pi)))

# --- 方向语义（原文约定）----------------------------------------------
near("W 全向", harmonic(0, 0, 0.3, 1.2), 1.0)
near("正前方 X=1", harmonic(1, 1, 0.0, 0.0), 1.0)
near("正左方 Y=1", harmonic(1, -1, 0.0, math.pi / 2), 1.0)
near("正右方 Y=-1", harmonic(1, -1, 0.0, -math.pi / 2), -1.0)
near("正上方 Z=1", harmonic(1, 0, math.pi / 2, 0.0), 1.0)
near("正后方 X=-1", harmonic(1, 1, 0.0, math.pi), -1.0)
near("左前象限 X>0 且 Y>0", harmonic(1, 1, 0.0, math.pi / 4) > 0
     and harmonic(1, -1, 0.0, math.pi / 4) > 0, True)
near("仰角 45 度 Z=sinE", harmonic(1, 0, math.pi / 4, 1.0), math.sin(math.pi / 4))
near("二阶 P(2,0,1)=1", harmonic(2, 0, math.pi / 2, 0.0), 1.0)
near("二阶 P(2,0,0)=-0.5", harmonic(2, 0, 0.0, 0.0), -0.5)
near("二阶 m=-2 用 sin(2A)", harmonic(2, -2, 0.0, math.pi / 4),
     3.0 * sn3d(2, 2) * math.sin(2 * math.pi / 4))
near("N3D 的 W = 1/sqrt(4pi)", harmonic(0, 0, 0.7, 2.1, "N3D"),
     n3d_factor(0))

# --- 编码 ---------------------------------------------------------------
b_front = encode(1, 0.0, 0.0)
near("正前方编码 W", b_front[0], 1.0)
near("正前方编码 Y", b_front[1], 0.0)
near("正前方编码 Z", b_front[2], 0.0)
near("正前方编码 X", b_front[3], 1.0)
b_left = encode(1, 0.0, math.pi / 2)
near("正左方编码 Y", b_left[1], 1.0)
near("正左方编码 X", b_left[3], 0.0)
b_gain = encode(1, 0.0, 0.0, gain=0.25)
near("增益线性", b_gain[3], 0.25)

# --- Σ Y² = order+1（SN3D 加法定理）-----------------------------------
for elev, azim in [(0.0, 0.0), (0.4, 1.1), (-0.9, -2.0), (1.2, 3.0)]:
    near("SN3D 一阶能量和 @%s" % ((elev, azim),), energy_sum(1, elev, azim), 2.0)
near("SN3D 二阶能量和", energy_sum(2, 0.5, 0.5), 3.0)
near("N3D 一阶能量和 = 4/(4pi)", energy_sum(1, 0.5, 0.5, "N3D"),
     4.0 / (4 * math.pi))

# --- 采样解码 -----------------------------------------------------------
spk = [(0.0, 0.0), (0.0, math.pi / 2), (0.0, math.pi), (0.0, -math.pi / 2)]
d_front = decode_sampling(b_front, spk)
near("解码命中方向最大", max(d_front), d_front[0])
near("解码命中 = 2/K", d_front[0], 2.0 / 4)
near("解码背向为 0", d_front[2], 0.0)
near("解码侧向 = 1/K（比命中低 6dB）", d_front[1], 1.0 / 4)
d_left = decode_sampling(b_left, spk)
near("左向源在左扬声器最大", max(d_left), d_left[1])
near("左向源右扬声器为 0", d_left[3], 0.0)
check("非法通道数报错", _raises(decode_sampling, [1.0, 2.0, 3.0], spk), True)


# --- channel_map（原文两个示例）---------------------------------------
check("WXYZ 轨道 -> map", build_channel_map(["W", "X", "Y", "Z"]), [0, 2, 3, 1])
check("WYZX 轨道 -> map", build_channel_map(["W", "Y", "Z", "X"]), [0, 1, 2, 3])
check("重排 WXYZ 得 ACN 序",
      apply_channel_map(["w", "x", "y", "z"], [0, 2, 3, 1]),
      ["w", "y", "z", "x"])
check("head-locked 立体声 6 通道",
      build_channel_map(["W", "Y", "Z", "X", "L", "R"]),
      [0, 1, 2, 3, 4, 5])
# 原文第三个示例（布局 L,R,W,Y,Z,X 写作 4,5,0,1,2,3）与前两例语义不自洽：
# 按「map[acn] = 轨道通道」应为 [2,3,4,5,0,1]，此处记录实测值而非照抄原文。
check("原文第三例按同一语义的重算值",
      build_channel_map(["L", "R", "W", "Y", "Z", "X"]),
      [2, 3, 4, 5, 0, 1])

# --- SA3D 盒 ------------------------------------------------------------
box = SA3DBox(0, 0, 1, 0, 0, [0, 2, 3, 1])
raw = box.render()
check("SA3D 长度 = 12 + 4*n", len(raw), 12 + 16)
check("SA3D 魔术字段前 6 字节", struct.unpack(">BBIBBI", raw[:12]),
      (0, 0, 1, 0, 0, 4))
back = SA3DBox.parse(raw)
check("SA3D 往返 channel_map", back.channel_map, [0, 2, 3, 1])
check("SA3D 往返 order", back.ambisonic_order, 1)
check("SA3D 整字节读 ambisonic_type",
      SA3DBox.parse(struct.pack(">BBIBBI", 0, 1, 1, 0, 0, 0)).ambisonic_type, 1)
check("SA3D 长度不符报错", _raises(SA3DBox.parse, raw[:-4]), True)
check("SA3D 过短报错", _raises(SA3DBox.parse, raw[:8]), True)

print("assertions ok: %d, failed: %d" % (_ok, len(_bad)))
for line in _bad:
    print("FAILED", line)
if _bad:
    raise SystemExit(1)
print("ALL GREEN")
