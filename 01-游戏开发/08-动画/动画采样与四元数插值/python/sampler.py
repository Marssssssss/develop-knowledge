"""glTF 2.0 动画采样器与三种插值模式（Appendix C）的最小实现。

依据（实读原文）：KhronosGroup/glTF@main specification/2.0/Specification.adoc
- §Animations：channel/sampler/target 结构、path 取值、输出访问器类型表、
  「Before and after the provided input range, output MUST be clamped to the
  nearest end of the input range」、input 访问器 MUST 定义 min/max、
  同一 (node, path) 在一个动画中 MUST NOT 被使用多次、
  节点定义了 matrix 时 MUST NOT 再动画 TRS。
- Appendix C: Animation Sampler Interpolation Modes：STEP / LINEAR / SLERP /
  CUBICSPLINE 四组公式。

符号沿用规范：`n` 关键帧数、`t_k`、`v_k`、`t_c`、`t_d = t_{k+1} - t_k`、
`t = (t_c - t_k) / t_d`。
"""

from __future__ import annotations

import math
from typing import Callable, List, Sequence, Tuple

Vec = List[float]

# path -> (访问器类型, 允许的组件类型)
PATH_ACCESSOR = {
    "translation": ("VEC3", {"float32"}),
    "rotation": ("VEC4", {"float32", "snorm8", "unorm8", "snorm16", "unorm16"}),
    "scale": ("VEC3", {"float32"}),
    "weights": ("SCALAR", {"float32", "snorm8", "unorm8", "snorm16", "unorm16"}),
}


# ---------------------------------------------------------------- 基础插值


def lerp(a: Vec, b: Vec, t: float) -> Vec:
    """规范 LINEAR（非 rotation）：v_t = (1 - t)·v_k + t·v_{k+1}。"""
    return [(1.0 - t) * x + t * y for x, y in zip(a, b)]


def quat_dot(a: Vec, b: Vec) -> float:
    return sum(x * y for x, y in zip(a, b))


def quat_normalize(q: Vec) -> Vec:
    n = math.sqrt(sum(c * c for c in q))
    if n == 0.0:
        raise ValueError("零四元数无法归一化（规范建议避免 v_k == -v_{k+1}）")
    return [c / n for c in q]


def slerp(q0: Vec, q1: Vec, t: float) -> Vec:
    """规范 SLERP（LINEAR + rotation）：a = arccos(|dot|)，s = sign(dot)。

    v_t = sin(a(1-t))/sin(a) · v_k + s · sin(at)/sin(a) · v_{k+1}
    """
    d = quat_dot(q0, q1)
    a = math.acos(min(1.0, max(-1.0, abs(d))))
    s = 1.0 if d >= 0.0 else -1.0
    if math.sin(a) == 0.0:  # a → 0 时退化为普通线性插值
        return quat_normalize(lerp(q0, q1, t))
    c0 = math.sin(a * (1.0 - t)) / math.sin(a)
    c1 = s * math.sin(a * t) / math.sin(a)
    return [c0 * x + c1 * y for x, y in zip(q0, q1)]


def slerp_naive(q0: Vec, q1: Vec, t: float) -> Vec:
    """不做最短路修正的对照实现：a = arccos(dot)（不取绝对值、不乘符号）。"""
    d = quat_dot(q0, q1)
    a = math.acos(min(1.0, max(-1.0, d)))
    if math.sin(a) == 0.0:
        return quat_normalize(lerp(q0, q1, t))
    c0 = math.sin(a * (1.0 - t)) / math.sin(a)
    c1 = math.sin(a * t) / math.sin(a)
    return [c0 * x + c1 * y for x, y in zip(q0, q1)]


def cubic(v_k: Vec, b_k: Vec, v_k1: Vec, a_k1: Vec, t_d: float, t: float) -> Vec:
    """规范 CUBICSPLINE：

    v_t = (2t³-3t²+1)·v_k + t_d(t³-2t²+t)·b_k
        + (-2t³+3t²)·v_{k+1} + t_d(t³-t²)·a_{k+1}

    注意切线 b_k / a_{k+1} 的单位是「每秒」，故要乘 t_d。
    """
    h00 = 2 * t ** 3 - 3 * t ** 2 + 1
    h10 = t ** 3 - 2 * t ** 2 + t
    h01 = -2 * t ** 3 + 3 * t ** 2
    h11 = t ** 3 - t ** 2
    return [h00 * p0 + t_d * h10 * m0 + h01 * p1 + t_d * h11 * m1
            for p0, m0, p1, m1 in zip(v_k, b_k, v_k1, a_k1)]


def smoothstep(t: float) -> float:
    """切线全零时的 CUBICSPLINE 权重：-2t³+3t²（注意 ≠ 线性 t）。"""
    return -2 * t ** 3 + 3 * t ** 2


# ---------------------------------------------------------------- 采样器


class Sampler:
    """一个 animation.sampler。

    values:
      - STEP / LINEAR：长度 n 的列表，元素为标量或向量；
      - CUBICSPLINE：长度 n 的列表，元素为 (a_k, v_k, b_k) 三元组。
    """

    def __init__(self, times: Sequence[float], values: Sequence,
                 interpolation: str = "LINEAR", path: str = "translation",
                 component_type: str = "float32", accessor_type: str = "",
                 has_minmax: bool = True, normalize_result: bool = False):
        self.times: List[float] = [float(x) for x in times]
        self.values = list(values)
        self.interpolation = interpolation
        self.path = path
        self.component_type = component_type
        self.accessor_type = accessor_type or PATH_ACCESSOR.get(path, ("", set()))[0]
        self.has_minmax = has_minmax
        self.normalize_result = normalize_result
        self._validate()

    def _validate(self) -> None:
        if self.interpolation not in ("LINEAR", "STEP", "CUBICSPLINE"):
            raise ValueError("interpolation 只能是 LINEAR / STEP / CUBICSPLINE")
        if self.path not in PATH_ACCESSOR:
            raise ValueError("path 只能是 translation / rotation / scale / weights")
        atype, comps = PATH_ACCESSOR[self.path]
        if self.accessor_type != atype:
            raise ValueError(f"{self.path} 的输出访问器必须是 {atype}")
        if self.component_type not in comps:
            raise ValueError(f"{self.path} 不允许 {self.component_type} 组件类型")
        if not self.has_minmax:
            raise ValueError("Animation sampler 的 input 访问器 MUST 定义 min/max")
        if len(self.times) != len(self.values):
            raise ValueError("input 与 output 元素数不一致")
        for i in range(len(self.times) - 1):
            if self.times[i + 1] < self.times[i]:
                raise ValueError("input 时间必须单调不减")
        if self.interpolation == "CUBICSPLINE" and len(self.times) < 2:
            raise ValueError("CUBICSPLINE 采样器 MUST 至少有 2 个关键帧")
        self._validate_value_shape()

    _DIM = {"VEC3": 3, "VEC4": 4, "SCALAR": 0}

    def _validate_value_shape(self) -> None:
        want = self._DIM[self.accessor_type]
        for v in self.values:
            payload = v[1] if self.interpolation == "CUBICSPLINE" else v
            if want == 0:
                if isinstance(payload, (list, tuple)):
                    raise ValueError("SCALAR 访问器的元素必须是标量")
            elif not isinstance(payload, (list, tuple)) or len(payload) != want:
                raise ValueError(f"{self.accessor_type} 访问器的元素长度必须为 {want}")

    def _segment(self, t_c: float) -> Tuple[int, int, float, bool, bool]:
        """返回 (k, k+1, t, exact, clamped)。

        exact  = t_c 精确落在第 k 个关键帧上；
        clamped = t_c 落在输入区间之外（规范：输出 MUST clamp 到最近的端点）。
        """
        for i, tk in enumerate(self.times):
            if t_c == tk:
                return i, i, 0.0, True, False
        if t_c < self.times[0]:
            return 0, 0, 0.0, False, True
        if t_c > self.times[-1]:
            n = len(self.times) - 1
            return n, n, 0.0, False, True
        for i in range(len(self.times) - 1):
            if self.times[i] < t_c < self.times[i + 1]:
                t_d = self.times[i + 1] - self.times[i]
                return i, i + 1, (t_c - self.times[i]) / t_d, False, False
        raise AssertionError("unreachable")

    def sample(self, t_c: float):
        k, k1, t, exact, clamped = self._segment(t_c)
        if exact or clamped:
            # 命中关键帧：原值直出不做插值；区间外：clamp 到最近的端点值
            return self._value_at(k)

        if self.interpolation == "STEP":
            return self._value_at(k)
        if self.interpolation == "LINEAR":
            a, b = self._value_at(k), self._value_at(k1)
            if self.path == "rotation":
                out = slerp(a, b, t)
            else:
                out = lerp(a, b, t)
            return quat_normalize(out) if self.normalize_result else out
        # CUBICSPLINE
        a_k, v_k, b_k = self.values[k]
        a_k1, v_k1, _b_k1 = self.values[k1]
        t_d = self.times[k1] - self.times[k]
        # SCALAR（weights）路径下值是标量，统一升成一元向量再算，最后还原
        scalar = not isinstance(v_k, (list, tuple))
        out = cubic([v_k] if scalar else v_k, [b_k] if scalar else b_k,
                    [v_k1] if scalar else v_k1, [a_k1] if scalar else a_k1, t_d, t)
        if scalar:
            out = out[0]
        if self.path == "rotation":
            # 规范：rotation 用 CUBICSPLINE 时结果 MUST 归一化
            out = quat_normalize(out)
        elif self.normalize_result:
            out = quat_normalize(out)
        return out

    def _value_at(self, i: int):
        v = self.values[i]
        if self.interpolation == "CUBICSPLINE":
            _a, val, _b = v
            return list(val) if isinstance(val, (list, tuple)) else val
        return list(v) if isinstance(v, (list, tuple)) else v


# ---------------------------------------------------------------- 动画/通道校验


class Animation:
    def __init__(self, channels: Sequence[dict], samplers: Sequence[Sampler],
                 nodes_with_matrix: Sequence[int] = ()):
        self.channels = list(channels)
        self.samplers = list(samplers)
        self.nodes_with_matrix = set(int(x) for x in nodes_with_matrix)

    def validate(self) -> None:
        seen = set()
        for ch in self.channels:
            node = ch.get("node")
            path = ch.get("path")
            if node is None:
                continue  # 规范：未定义 node 时该通道被忽略（除非扩展另有说明）
            if path not in PATH_ACCESSOR:
                raise ValueError("path 只能是 translation / rotation / scale / weights")
            key = (node, path)
            if key in seen:
                raise ValueError("同一 (node, path) 在一个动画中 MUST NOT 被使用多次")
            seen.add(key)
            if node in self.nodes_with_matrix and path in ("translation", "rotation", "scale"):
                raise ValueError("节点定义了 matrix 时 MUST NOT 再动画 TRS 属性")
            if path == "weights" and not ch.get("has_morph", True):
                raise ValueError("没有 morph target 的节点 MUST NOT 被 weights 通道指向")
            idx = ch.get("sampler")
            if idx is None or not (0 <= idx < len(self.samplers)):
                raise ValueError("channel.sampler 必须指向本动画内的采样器")
            if self.samplers[idx].path != path:
                raise ValueError("采样器输出类型与通道 path 不匹配")
