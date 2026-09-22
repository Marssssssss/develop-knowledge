"""演示入口：ACN/SN3D 编码、采样解码、channel_map 与 SA3D 盒。"""

import math

from ambisonic import (acn_components, build_channel_map, channels_for_order,
                       decode_sampling, encode, energy_sum, harmonic,
                       order_for_channels, sn3d, SA3DBox)

DEG = math.pi / 180.0
NAMES = {0: "W", 1: "Y", 2: "Z", 3: "X"}


def main():
    print("== ACN 一阶分量表（n = l*(l+1) + m） ==")
    for n, l, m in acn_components(1):
        print("  ACN %d -> (l=%2d, m=%2d) %s  SN3D=%.6f"
              % (n, l, m, NAMES.get(n, "?"), sn3d(l, m)))

    print("\n== 阶 ↔ 通道数（periphonic） ==")
    for order in (1, 2, 3):
        ch = channels_for_order(order)
        print("  order %d -> %d ch，反解 order=%s" % (order, ch, order_for_channels(ch)))

    print("\n== 平面波编码（SN3D，一阶） ==")
    for label, elev, azim in [("正前", 0.0, 0.0), ("正左", 0.0, 90.0),
                              ("正上", 90.0, 0.0), ("右后 45/-135", 0.0, -135.0)]:
        b = encode(1, elev * DEG, azim * DEG)
        print("  %-12s W=%+.4f Y=%+.4f Z=%+.4f X=%+.4f"
              % (label, b[0], b[1], b[2], b[3]))

    print("\n== Σ Y² 恒等式 ==")
    for order in (1, 2):
        print("  order %d: Σ Y_n² = %.6f（期望 %d）"
              % (order, energy_sum(order, 0.4, 1.1), order + 1))

    print("\n== 采样解码（4 扬声器水平环，1/K 口径） ==")
    spk = [(0.0, 0.0), (0.0, 90.0 * DEG), (0.0, 180.0 * DEG), (0.0, -90.0 * DEG)]
    b = encode(1, 0.0, 0.0)
    gains = decode_sampling(b, spk)
    for (elev, azim), g in zip(spk, gains):
        print("  扬声器 az=%6.1f° -> 增益 %+.4f" % (math.degrees(azim), g))

    print("\n== channel_map：ACN 分量 i 落在哪个轨道通道 ==")
    for layout in (["W", "X", "Y", "Z"], ["W", "Y", "Z", "X"],
                   ["W", "Y", "Z", "X", "L", "R"]):
        print("  轨道 %-24s -> map %s"
              % (",".join(layout), build_channel_map(layout)))

    print("\n== SA3D 盒（Google Spatial Audio RFC 示例） ==")
    box = SA3DBox(0, 0, 1, 0, 0, [0, 2, 3, 1])
    raw = box.render()
    print("  字节数 %d = 12 + 4*%d" % (len(raw), len(box.channel_map)))
    print("  hex:", raw.hex())
    back = SA3DBox.parse(raw)
    print("  往返: order=%d ordering=%d norm=%d map=%s"
          % (back.ambisonic_order, back.ordering, back.normalization, back.channel_map))

    print("\n== 单个球谐分量抽查 ==")
    print("  Y(1,-1) 正左 = %+.6f（约定 A∈(0,pi/2) 为左前）" % harmonic(1, -1, 0.0, 90 * DEG))


if __name__ == "__main__":
    main()
