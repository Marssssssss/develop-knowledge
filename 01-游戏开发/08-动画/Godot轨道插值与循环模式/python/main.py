"""Godot 轨道求值演示入口。"""

import math

from track import Track, Key, pingpong_index

k2 = [Key(0.0, 0.0), Key(0.5, 10.0)]

if __name__ == "__main__":
    for name, mode in (("LOOP_NONE", Track.LOOP_NONE), ("LOOP_LINEAR", Track.LOOP_LINEAR),
                       ("LOOP_PINGPONG", Track.LOOP_PINGPONG)):
        tr = Track(k2, length=1.0, loop_mode=mode)
        vals = [round(tr.interpolate(t / 8.0), 4) for t in range(9)]
        print(f"[{name:13}] t=0..1 取值: {vals}")

    ang = Track([Key(0.0, 0.1), Key(1.0, math.tau - 0.1)], length=1.0,
                interpolation=Track.LINEAR_ANGLE)
    print("[LINEAR_ANGLE] 跨 2π 中点:", round(ang.interpolate(0.5), 6),
          "（朴素线性会给", round((0.1 + math.tau - 0.1) / 2, 6), "）")
    # 关键帧覆盖 [0, length] 时 pingpong 才呈现三角波（只有两个点会退化成平顶）
    k3 = [Key(0.0, 0.0), Key(0.5, 10.0), Key(1.0, 20.0)]
    pp3 = Track(k3, length=1.0, loop_mode=Track.LOOP_PINGPONG)
    print("[LOOP_PINGPONG 3 帧] t=0..1:", [round(pp3.interpolate(t / 8.0), 4) for t in range(9)])
    print("[pingpong 下标] len=5:", [pingpong_index(i, 5) for i in range(10)])
