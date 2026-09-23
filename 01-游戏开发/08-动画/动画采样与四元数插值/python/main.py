"""glTF 动画采样器演示入口：三种插值 + 四元数最短路 + CUBICSPLINE 切线口径。"""

import math

from sampler import Sampler, slerp, slerp_naive, smoothstep

q0 = [0.0, 0.0, 0.0, 1.0]
q90 = [0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4)]


def show(tag, v):
    if isinstance(v, list):
        print(tag, [round(c, 6) for c in v])
    else:
        print(tag, round(v, 6))


if __name__ == "__main__":
    step = Sampler([0.0, 1.0, 2.0], [0.0, 10.0, 20.0], "STEP", "weights")
    show("[STEP ] t=1.5 ->", step.sample(1.5))
    show("[STEP ] t=5.0 ->", step.sample(5.0))

    lin = Sampler([0.0, 2.0], [[0.0, 0.0, 0.0], [10.0, 20.0, 0.0]], "LINEAR", "translation")
    show("[LIN  ] t=1.0 ->", lin.sample(1.0))

    rot = Sampler([0.0, 1.0], [q0, q90], "LINEAR", "rotation")
    show("[SLERP] t=0.5 ->", rot.sample(0.5))
    print("[SLERP] 期望绕 Y +45°:", [0.0, round(math.sin(math.pi / 8), 6), 0.0,
                                     round(math.cos(math.pi / 8), 6)])
    show("[SLERP] 取反 q1 后 t=0.5 ->", Sampler([0.0, 1.0], [q0, [-c for c in q90]],
                                                "LINEAR", "rotation").sample(0.5))
    show("[长弧 ] 不做最短路 t=0.5 ->", slerp_naive(q0, [-c for c in q90], 0.5))

    cs = Sampler([0.0, 2.0], [(0.0, 0.0, 0.0), (0.0, 10.0, 0.0)], "CUBICSPLINE", "weights")
    show("[CUBIC] 零切线 t=0.5 ->", cs.sample(0.5))
    print("[CUBIC] LINEAR 同点 =", 2.5, "，smoothstep 同点 =", round(10 * smoothstep(0.25), 6))
    cs_lin = Sampler([0.0, 2.0], [(0.0, 0.0, 5.0), (5.0, 10.0, 0.0)], "CUBICSPLINE", "weights")
    show("[CUBIC] 切线=割线速度 t=0.5 ->", cs_lin.sample(0.5))
