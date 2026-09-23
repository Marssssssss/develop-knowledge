"""Godot 混合空间演示入口。"""

from blendspace import BlendSpace1D, BlendSpace2D, DISCRETE, SYNC_CYCLIC_MUTABLE

if __name__ == "__main__":
    bs = BlendSpace1D([0.0, 1.0, 2.0])
    for p in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0):
        r = bs.process(p)
        print(f"[1D 插值] pos={p}: weights={[round(w, 4) for w in r['weights']]} closest={r['closest']}")

    bsd = BlendSpace1D([0.0, 1.0, 2.0], blend_mode=DISCRETE)
    for p in (0.4, 0.6, 1.4):
        print(f"[1D 离散] pos={p}: weights={bsd.process(p)['weights']} closest={bsd.process(p)['closest']}")

    bss = BlendSpace1D([0.0, 1.0], lengths=[2.0, 1.0], sync_mode=SYNC_CYCLIC_MUTABLE)
    for p in (0.0, 0.5, 1.0):
        print(f"[1D sync] pos={p}: 时间缩放={round(bss.process(p)['delta_scale'], 6)}")

    bs2 = BlendSpace2D([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], triangles=[(0, 1, 2)])
    for p in ((0.25, 0.25), (0.7, 0.2), (2.0, 0.0), (1.0, 1.0)):
        r = bs2.process(p)
        print(f"[2D 插值] pos={p}: weights={[round(w, 4) for w in r['weights']]} closest={r['closest']}")
