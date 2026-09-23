"""glTF 蒙皮演示入口：绑定姿态不变性、权重编码、LBS 收缩、规范示例的「被忽略」变换。"""

import math

from skinning import (
    Node, Skin, mat_identity, mat_is_close, mat_from_quat, skin_vertex,
    quantize_weights, dequantize_unorm,
)


def demo_bind_pose():
    n0 = Node("node_0", translation=(0.0, 1.0, 0.0))
    n1 = Node("node_1", scale=(0.5, 0.5, 0.5), parent=n0)
    n2 = Node("node_2", parent=n1)
    skin = Skin([n1, n2])
    print("[绑定姿态] jointMatrix 是否单位阵:",
          [mat_is_close(m, mat_identity()) for m in skin.joint_matrices()])
    print("[绑定姿态] 蒙皮 (1,2,3) ->", skin_vertex(skin, (1.0, 2.0, 3.0), [0, 1], [0.5, 0.5]))
    n1.rotation = (0.0, 1.0, 0.0, 0.0)  # 绕 Y 180°
    print("[摆姿势后] (1,0,0) ->", skin_vertex(skin, (1.0, 0.0, 0.0), [0], [1.0]))


def demo_weights():
    w = [0.25, 0.25, 0.25, 0.25]
    q = quantize_weights(w, 8)
    print("[unorm8] 0.25×4 ->", q, "和 =", sum(q),
          "；朴素四舍五入 =", [int(round(x * 255)) for x in w])
    print("[unorm8] 反归一化 ->", [round(x, 6) for x in dequantize_unorm(q, 8)])
    q16 = quantize_weights(w, 16)
    print("[unorm16] 和 =", sum(q16), q16)


def demo_lbs_shrink():
    root = Node("root")
    a = Node("A", parent=root)
    b = Node("B", parent=root)
    skin = Skin([a, b])
    a.rotation = (0.0, 0.0, math.sin(math.pi / 8), math.cos(math.pi / 8))
    b.rotation = (0.0, 0.0, -math.sin(math.pi / 8), math.cos(math.pi / 8))
    p = skin_vertex(skin, (1.0, 0.0, 0.0), [0, 1], [0.5, 0.5])
    print("[LBS 收缩] ±45° 各半 ->", tuple(round(c, 6) for c in p),
          "长度 =", round(math.dist(p, (0, 0, 0)), 6), "（正确插值旋转应为 1.0）")


def demo_nonnormalized():
    skin = Skin([Node("J0"), Node("J1")])
    print("[权重和 0.5] (2,0,0) ->", skin_vertex(skin, (2.0, 0.0, 0.0), [0, 1], [0.25, 0.25]))
    print("[权重和 1.0] (2,0,0) ->", skin_vertex(skin, (2.0, 0.0, 0.0), [0, 1], [0.75, 0.25]))


if __name__ == "__main__":
    demo_bind_pose()
    demo_weights()
    demo_lbs_shrink()
    demo_nonnormalized()
