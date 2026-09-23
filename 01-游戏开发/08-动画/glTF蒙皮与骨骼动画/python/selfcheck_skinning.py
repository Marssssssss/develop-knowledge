"""glTF 蒙皮模型自检：全部断言实跑，期望值均逐条手算过。

运行：python selfcheck_skinning.py
"""

import math

from skinning import (
    Node, Skin, mat_identity, mat_mul, mat_mul_many, mat_inverse, mat_scale,
    mat_translation, mat_from_quat, xform_point, mat_is_close, skin_vertex,
    quantize_weights, dequantize_unorm, validate_weight_sum,
)

COUNT = 0
FAIL = []


def ok(cond, msg):
    global COUNT
    COUNT += 1
    if not cond:
        FAIL.append(msg)
        print("FAIL:", msg)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def vec_close(a, b, tol=1e-9):
    return all(close(x, y, tol) for x, y in zip(a, b))


# ---------------------------------------------------------------- 规范示例骨架
# 取自 Specification.adoc「Joint Hierarchy」示例：joints=[1,2]，
# 规范原文：node_0 的 translation 与 node_1 的 scale 生效，
#           node_3 的 translation 与 node_4 的 rotation 被忽略。


def build_spec_skeleton():
    n0 = Node("node_0", translation=(0.0, 1.0, 0.0))
    n1 = Node("node_1", scale=(0.5, 0.5, 0.5), parent=n0)
    n2 = Node("node_2", parent=n1)
    n3 = Node("node_3", translation=(1.0, 0.0, 0.0))
    n4 = Node("node_4", rotation=(0.0, 1.0, 0.0, 0.0), parent=n3)
    return n0, n1, n2, n3, n4


n0, n1, n2, n3, n4 = build_spec_skeleton()
skin = Skin([n1, n2])  # 未给 inverseBindMatrices → 以当前姿态为绑定姿态

# 1) 绑定姿态下关节矩阵必为单位阵
jm = skin.joint_matrices()
ok(mat_is_close(jm[0], mat_identity()), "绑定姿态 jointMatrix(0) 应为单位阵")
ok(mat_is_close(jm[1], mat_identity()), "绑定姿态 jointMatrix(1) 应为单位阵")

# 2) 绑定姿态蒙皮 = 原顶点（权重和为 1 时不产生形变）
v = (1.0, 2.0, 3.0)
r = skin_vertex(skin, v, [0, 1], [0.5, 0.5])
ok(vec_close(r, v), f"绑定姿态蒙皮应得到原顶点, 实得 {r}")
r = skin_vertex(skin, v, [0, 1, 0, 1], [0.5, 0.5, 0.0, 0.0])
ok(vec_close(r, v), f"四槽位（重复关节权重为 0）绑定姿态仍为原顶点, 实得 {r}")

# 3) 挂着 mesh 的节点及其父节点变换 MUST be ignored
n3.translation = (7.0, -3.0, 2.0)
n4.rotation = (0.0, 0.0, 0.0, 1.0)
n4.translation = (9.0, 9.0, 9.0)
r2 = skin_vertex(skin, v, [0, 1], [0.5, 0.5])
ok(vec_close(r2, v), f"改 node_3/node_4 的变换不得影响蒙皮结果, 实得 {r2}")

# 4) 关节自身旋转生效：node_1 绕 Y 转 180°（均匀缩放与平移在共轭中抵消 → 结果恰为 R）
n1.rotation = (0.0, 1.0, 0.0, 0.0)
jm = skin.joint_matrices()
R = mat_from_quat((0.0, 1.0, 0.0, 0.0))
ok(mat_is_close(jm[0], R), "均匀缩放+平移共轭后 jointMatrix 应等于纯旋转 R")
ok(vec_close(skin_vertex(skin, (1.0, 0.0, 0.0), [0], [1.0]), (-1.0, 0.0, 0.0)),
    "v=(1,0,0) 绕 Y 转 180° 应得 (-1,0,0)")
ok(vec_close(skin_vertex(skin, (0.0, 0.0, 1.0), [0], [1.0]), (0.0, 0.0, -1.0)),
    "v=(0,0,1) 绕 Y 转 180° 应得 (0,0,-1)")
ok(vec_close(skin_vertex(skin, (0.0, 5.0, 0.0), [0], [1.0]), (0.0, 5.0, 0.0)),
    "绕 Y 轴旋转时 y 分量不变")

# ---------------------------------------------------------------- LBS 固有收缩
root = Node("root")
a = Node("A", parent=root)  # 先在绑定姿态（单位）下建 skin，之后再摆姿势
b = Node("B", parent=root)
sk2 = Skin([a, b])
a.rotation = (0.0, 0.0, math.sin(math.pi / 8), math.cos(math.pi / 8))    # 绕 Z +45°
b.rotation = (0.0, 0.0, -math.sin(math.pi / 8), math.cos(math.pi / 8))   # 绕 Z -45°
pa = skin_vertex(sk2, (1.0, 0.0, 0.0), [0], [1.0])
pb = skin_vertex(sk2, (1.0, 0.0, 0.0), [1], [1.0])
ok(vec_close(pa, (math.cos(math.pi / 4), math.sin(math.pi / 4), 0.0), 1e-9),
    f"+45° 旋转应得 (0.7071,0.7071,0), 实得 {pa}")
ok(vec_close(pb, (math.cos(math.pi / 4), -math.sin(math.pi / 4), 0.0), 1e-9),
    f"-45° 旋转应得 (0.7071,-0.7071,0), 实得 {pb}")
pm = skin_vertex(sk2, (1.0, 0.0, 0.0), [0, 1], [0.5, 0.5])
ok(vec_close(pm, (math.cos(math.pi / 4), 0.0, 0.0), 1e-9),
    f"±45° 各半混合应得 (0.7071,0,0)（经典 LBS 收缩）, 实得 {pm}")
ok(close(math.dist(pm, (0.0, 0.0, 0.0)), math.cos(math.pi / 4), 1e-9),
    "混合结果长度应为 cos45°=0.7071，而非正确插值旋转给出的 1.0")
ok(abs(math.dist(pm, (0, 0, 0)) - 1.0) > 0.29, "LBS 收缩约 29.3%，应明显小于 1")

# ---------------------------------------------------------------- 权重和 ≠ 1
ident = Skin([Node("J0"), Node("J1")])
r = skin_vertex(ident, (2.0, 0.0, 0.0), [0, 1], [0.25, 0.25])
ok(vec_close(r, (1.0, 0.0, 0.0)), f"权重和 0.5 时顶点缩到一半（故要求归一）, 实得 {r}")
r = skin_vertex(ident, (2.0, 0.0, 0.0), [0, 1], [0.75, 0.25])
ok(vec_close(r, (2.0, 0.0, 0.0)), f"权重和仍为 1 时无缩放, 实得 {r}")

# ---------------------------------------------------------------- 权重量化
q8 = quantize_weights([0.25, 0.25, 0.25, 0.25], 8)
ok(sum(q8) == 255, f"unorm8 权重和必须为 255, 实得 {sum(q8)} -> {q8}")
naive = [int(round(x * 255)) for x in (0.25, 0.25, 0.25, 0.25)]
ok(sum(naive) == 256, f"朴素四舍五入会给出 256（违反规范）, 实得 {naive}")
ok(max(q8) - min(q8) <= 1, f"最大余数法分配后各分量极差应 ≤1, 实得 {q8}")

q8b = quantize_weights([1.0 / 3, 1.0 / 3, 1.0 / 3], 8)
ok(sum(q8b) == 255 and q8b == [85, 85, 85], f"三等分应为 [85,85,85], 实得 {q8b}")

q16 = quantize_weights([0.25, 0.25, 0.25, 0.25], 16)
ok(sum(q16) == 65535, f"unorm16 权重和必须为 65535, 实得 {sum(q16)}")
ok(q16 == [16384, 16384, 16384, 16383], f"unorm16 四等分实得 {q16}")

dw = dequantize_unorm(q8, 8)
ok(close(sum(dw), 1.0, 1e-12), f"反归一化后权重和应为 1, 实得 {sum(dw)}")
ok(validate_weight_sum(dw, nonzero=4), "反归一化结果应通过 2e-7×4 的官方阈值")
ok(not validate_weight_sum([0.5, 0.5, 1e-6], nonzero=3), "超出 2e-7×3 阈值应判不通过")
ok(validate_weight_sum([0.5, 0.5, 1e-8], nonzero=3), "在 2e-7×3 阈值内应判通过")

# ---------------------------------------------------------------- 校验：非法输入
def raises(fn, msg):
    try:
        fn()
    except ValueError:
        return True
    except Exception as e:  # noqa: BLE001
        print("  (异常类型非 ValueError:", type(e).__name__, e, ")")
        return False
    print("  (未抛异常:", msg, ")")
    return False


sk3 = Skin([Node("X"), Node("Y")])
ok(raises(lambda: skin_vertex(sk3, (1, 0, 0), [0, 0], [0.5, 0.5]), "重复非零关节"),
   "同一顶点内同一关节出现两个非零权重应报错")
ok(skin_vertex(sk3, (1.0, 0.0, 0.0), [0, 0], [1.0, 0.0]) == (1.0, 0.0, 0.0),
   "重复关节但其中一个权重为 0 是允许的（未用槽 SHOULD 置 0）")
ok(raises(lambda: skin_vertex(sk3, (1, 0, 0), [0, 1], [-0.5, 1.5]), "负权重"),
   "负权重应报错")
ok(raises(lambda: skin_vertex(sk3, (1, 0, 0), [0, 5], [0.5, 0.5]), "关节越界"),
   "关节索引越界应报错")
ok(raises(lambda: skin_vertex(sk3, (1, 0, 0), [0, 1, 0, 1, 0], [0.2, 0.2, 0.2, 0.2, 0.2]), "5 个关节"),
   "单集合超过 4 个关节应报错")
ok(raises(lambda: skin_vertex(sk3, (1, 0, 0), [0], [1.0, 1.0]), "集合数不等"),
   "JOINTS_n 与 WEIGHTS_n 集合数不等应报错")

bad4 = mat_identity()
bad4[3][0] = 1.0
ok(raises(lambda: Skin([Node("X")], [bad4]), "IBM 第四行非法"),
   "inverseBindMatrices 第四行必须 [0,0,0,1]")
nan_m = mat_identity()
nan_m[0][0] = float("nan")
ok(raises(lambda: Skin([Node("X")], [nan_m]), "IBM 含 NaN"),
   "inverseBindMatrices 不得含 NaN/Inf")
ok(raises(lambda: Skin([Node("X"), Node("Y")], [mat_identity()]), "IBM 数量不足"),
   "inverseBindMatrices 元素数必须 ≥ joints 数")

# ---------------------------------------------------------------- 层级累积
root2 = Node("root", translation=(0.0, 10.0, 0.0))
c1 = Node("c1", translation=(0.0, 2.0, 0.0), parent=root2)
c2 = Node("c2", parent=c1)
sk4 = Skin([c1, c2])
ok(mat_is_close(sk4.joint_matrices()[0], mat_identity()), "绑定姿态层级关节矩阵仍为单位阵")
c1.rotation = (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4))  # 绕 Z 转 90°
jm = sk4.joint_matrices()
# jointMatrix = G'·G_bind^{-1} = T(t)·R·T(-t)，即「绕该关节绑定位置 t」的旋转。
# c1 与 c2 的绑定世界位置都是 (0,12,0)（root2 抬 10 + c1 抬 2）。
ok(vec_close(xform_point(jm[0], (0.0, 12.0, 0.0)), (0.0, 12.0, 0.0), 1e-9),
   "绕关节自身绑定位置旋转：关节位置不动")
ok(vec_close(xform_point(jm[0], (1.0, 12.0, 0.0)), (0.0, 13.0, 0.0), 1e-9),
   "绕 Z 转 90°：关节处 +X 方向 1 单位的点 -> +Y 方向")
ok(vec_close(xform_point(jm[1], (0.0, 12.0, 0.0)), (0.0, 12.0, 0.0), 1e-9),
   "子关节 c2 的绑定位置同样不动")
ok(vec_close(xform_point(jm[1], (1.0, 12.0, 0.0)), (0.0, 13.0, 0.0), 1e-9),
   "子关节继承父关节的旋转")

print(f"\n断言总数: {COUNT}, 失败: {len(FAIL)}")
if FAIL:
    for m in FAIL:
        print("  -", m)
    raise SystemExit(1)
print("全部通过")
