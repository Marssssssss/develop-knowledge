"""glTF 2.0 线性混合蒙皮（Skinned Mesh）最小模型。

依据（均为实读原文）：
- KhronosGroup/glTF@main specification/2.0/Specification.adoc「Skins / Joint Hierarchy /
  Skinned Mesh Attributes」三节与「Animations」一节。

要点（规范原句的工程落地）：
1. 「Only the joint transforms are applied to the skinned mesh; the transform of the
   skinned mesh node MUST be ignored.」——蒙皮不使用挂着 mesh 的那个节点的变换。
2. 「per-joint inverse bind matrices (when present) MUST be applied before the base node
   transforms」——先乘 inverseBindMatrix，再走骨骼自身的节点变换。
   故关节矩阵取 jointMatrix(j) = globalJointTransform(j) · inverseBindMatrix(j)，
   顶点结果 v' = Σ_k w_k · jointMatrix(k) · v（权重和为 1 时不产生额外缩放）。
3. 每个集合最多 4 个关节：JOINTS_n 为 uint8/uint16，WEIGHTS_n 为 float32/unorm8/unorm16；
   权重非负；同一顶点内同一关节不得出现两个非零权重；未使用的关节槽 SHOULD 置 0。
4. unorm8/unorm16 存储时，权重在归一化之前的整数和 MUST 分别为 255 / 65535；
   inverseBindMatrices 必须是 MAT4 浮点、第四行 [0,0,0,1]、元素数 ≥ joints 数、不得含 NaN/Inf。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Mat = List[List[float]]  # 行主序 4x4，列向量约定：v' = M · v
Vec = Tuple[float, float, float, float]


# ---------------------------------------------------------------- 矩阵/向量


def mat_identity() -> Mat:
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def mat_mul(a: Mat, b: Mat) -> Mat:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def mat_mul_many(mats: Sequence[Mat]) -> Mat:
    out = mat_identity()
    for m in mats:
        out = mat_mul(out, m)
    return out


def mat_translation(t: Sequence[float]) -> Mat:
    return [[1.0, 0.0, 0.0, float(t[0])],
            [0.0, 1.0, 0.0, float(t[1])],
            [0.0, 0.0, 1.0, float(t[2])],
            [0.0, 0.0, 0.0, 1.0]]


def mat_scale(s: Sequence[float]) -> Mat:
    return [[float(s[0]), 0.0, 0.0, 0.0],
            [0.0, float(s[1]), 0.0, 0.0],
            [0.0, 0.0, float(s[2]), 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def mat_from_quat(q: Sequence[float]) -> Mat:
    """xyzw 四元数（glTF 的 rotation 即 xyzw）转旋转矩阵。"""
    x, y, z, w = (float(c) for c in q)
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n == 0.0:
        raise ValueError("zero quaternion")
    x, y, z, w = x / n, y / n, z / n, w / n
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0.0],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0.0],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def mat_inverse(m: Mat) -> Mat:
    """Gauss-Jordan 求 4x4 逆（列向量约定）。"""
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(4)] for i, row in enumerate(m)]
    n = 4
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-18:
            raise ValueError("singular matrix")
        a[col], a[piv] = a[piv], a[col]
        pv = a[col][col]
        a[col] = [x / pv for x in a[col]]
        for r in range(n):
            if r != col and a[r][col] != 0.0:
                f = a[r][col]
                a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    return [row[n:] for row in a]


def xform_point(m: Mat, v: Sequence[float]) -> Tuple[float, float, float]:
    p = [float(v[0]), float(v[1]), float(v[2]), 1.0]
    out = [sum(m[i][k] * p[k] for k in range(4)) for i in range(4)]
    if abs(out[3]) > 1e-12:
        out = [c / out[3] for c in out]
    return (out[0], out[1], out[2])


def mat_is_close(a: Mat, b: Mat, tol: float = 1e-9) -> bool:
    return all(abs(a[i][j] - b[i][j]) <= tol for i in range(4) for j in range(4))


# ---------------------------------------------------------------- 节点层级


class Node:
    """glTF node：TRS 局部变换 + 父子层级。"""

    def __init__(self, name: str, translation=(0.0, 0.0, 0.0),
                 rotation=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0),
                 parent: Optional["Node"] = None):
        self.name = name
        self.translation = tuple(float(c) for c in translation)
        self.rotation = tuple(float(c) for c in rotation)
        self.scale = tuple(float(c) for c in scale)
        self.parent = parent
        self.children: List["Node"] = []
        if parent is not None:
            parent.children.append(self)

    def local_matrix(self) -> Mat:
        return mat_mul_many([mat_translation(self.translation),
                             mat_from_quat(self.rotation),
                             mat_scale(self.scale)])

    def global_matrix(self) -> Mat:
        chain: List[Mat] = []
        cur: Optional[Node] = self
        while cur is not None:
            chain.append(cur.local_matrix())
            cur = cur.parent
        return mat_mul_many(list(reversed(chain)))


# ---------------------------------------------------------------- 皮肤


class Skin:
    """skin.joints + inverseBindMatrices。"""

    def __init__(self, joints: Sequence[Node], inverse_bind: Optional[Sequence[Mat]] = None):
        self.joints: List[Node] = list(joints)
        if inverse_bind is None:
            # 未给出时按规范允许的缺省：以当前姿态为绑定姿态（结果退化为单位矩阵）
            self.inverse_bind: List[Mat] = [mat_inverse(j.global_matrix()) for j in self.joints]
        else:
            self.inverse_bind = [m for m in inverse_bind]
        self._validate()

    def _validate(self) -> None:
        # inverseBindMatrices 元素数 MUST >= joints 元素数
        if len(self.inverse_bind) < len(self.joints):
            raise ValueError("inverseBindMatrices 元素数必须不少于 joints 数")
        for m in self.inverse_bind:
            # 第四行 MUST 为 [0,0,0,1]
            if not all(abs(c) <= 1e-12 for c in m[3][:3]) or abs(m[3][3] - 1.0) > 1e-12:
                raise ValueError("inverseBindMatrices 第四行必须是 [0,0,0,1]")
            # MUST NOT 含 NaN / Inf
            for row in m:
                for c in row:
                    if math.isnan(c) or math.isinf(c):
                        raise ValueError("inverseBindMatrices 不得含 NaN/Inf")

    def joint_matrix(self, j: int) -> Mat:
        """jointMatrix(j) = globalJointTransform(j) · inverseBindMatrix(j)。"""
        return mat_mul(self.joints[j].global_matrix(), self.inverse_bind[j])

    def joint_matrices(self) -> List[Mat]:
        return [self.joint_matrix(j) for j in range(len(self.joints))]


def skin_vertex(skin: Skin, v: Sequence[float],
                joints: Sequence[int], weights: Sequence[float]) -> Tuple[float, float, float]:
    """线性混合蒙皮：v' = Σ_k w_k · jointMatrix(k) · v。

    注意：不乘「挂着 mesh 的节点」的全局变换（规范明确 MUST be ignored）。
    """
    if len(joints) > 4 or len(weights) > 4:
        raise ValueError("单个 JOINTS_n / WEIGHTS_n 集合最多 4 个关节")
    if len(joints) != len(weights):
        raise ValueError("JOINTS_n 与 WEIGHTS_n 集合数必须相等")
    seen: Dict[int, int] = {}
    for k, j in enumerate(joints):
        if not (0 <= j < len(skin.joints)):
            raise ValueError("关节索引越界（All joint values MUST be within the range）")
        if weights[k] < 0.0:
            raise ValueError("权重不得为负（joint weights MUST NOT be negative）")
        if weights[k] != 0.0:
            if j in seen:
                raise ValueError("同一顶点内同一关节不得有多于一个非零权重")
            seen[j] = k
    mats = skin.joint_matrices()
    acc = [0.0, 0.0, 0.0, 0.0]
    for k, j in enumerate(joints):
        w = weights[k]
        if w == 0.0:
            continue
        m = mats[j]
        p = [float(v[0]), float(v[1]), float(v[2]), 1.0]
        for i in range(4):
            acc[i] += w * sum(m[i][c] * p[c] for c in range(4))
    # 注意：真实蒙皮**不做齐次除法**。当权重和 ≠ 1 时 acc[3] 就等于权重和，
    # 若强行做透视除法会把「向原点收缩」的效果除掉 —— 这正是规范要求权重归一的原因。
    return (acc[0], acc[1], acc[2])


# ---------------------------------------------------------------- 权重编码


def quantize_weights(weights: Sequence[float], bits: int = 8) -> List[int]:
    """把浮点权重量化成 unorm 整数，并保证归一化前整数和恰为 2^bits - 1。

    规范：unorm8 → 和 MUST 为 255；unorm16 → 和 MUST 为 65535。
    实现用「最大余数法」分配舍入误差：朴素四舍五入会给出 256（违反规范）。
    """
    if bits not in (8, 16):
        raise ValueError("仅支持 unorm8 / unorm16")
    max_int = (1 << bits) - 1
    raw = [w * max_int for w in weights]
    base = [int(math.floor(x)) for x in raw]
    remain = max_int - sum(base)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)
    out = base[:]
    for i in range(remain):
        out[order[i % len(order)]] += 1
    return out


def dequantize_unorm(vals: Sequence[int], bits: int = 8) -> List[float]:
    max_int = (1 << bits) - 1
    return [v / max_int for v in vals]


def validate_weight_sum(weights: Sequence[float], nonzero: int, tol_scale: float = 2e-7) -> bool:
    """官方校验工具阈值：2e-7 × 该顶点非零权重个数。"""
    tol = tol_scale * max(1, nonzero)
    return abs(sum(weights) - 1.0) <= tol
