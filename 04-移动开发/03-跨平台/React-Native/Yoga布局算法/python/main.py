"""Yoga 布局引擎核心算法的可执行模型。

对应源码（facebook/yoga @main）：
  yoga/algorithm/PixelGrid.cpp        —— roundValueToPixelGrid / roundLayoutResultsToPixelGrid
  yoga/algorithm/Cache.cpp            —— canUseCachedMeasurement
  yoga/algorithm/FlexLine.cpp         —— calculateFlexLine
  yoga/algorithm/CalculateLayout.cpp  —— distributeFreeSpaceFirstPass / SecondPass / resolveFlexibleLength
  yoga/algorithm/SizingMode.h         —— SizingMode <-> MeasureMode
  yoga/numeric/Comparison.h           —— inexactEquals / isDefined
  yoga/config/Config.h                —— 默认 errata 与 pointScaleFactor

所有常量与分支顺序均按源码 1:1 转写，不做「修正」；源码与数学期望冲突处只记录。
"""

import math
from enum import Enum

UNDEFINED = float("nan")


def is_defined(v: float) -> bool:
    return v == v


def inexact_equals(a: float, b: float) -> bool:
    """源码：硬编码 epsilon 0.0001；两边都是 NaN 也算相等。"""
    if is_defined(a) and is_defined(b):
        return abs(a - b) < 0.0001
    return (not is_defined(a)) and (not is_defined(b))


def _ieee_div(a: float, b: float) -> float:
    """C++ 的 float 除法不会抛异常：0/0 -> NaN，x/0 -> ±inf。Python 需要显式模拟。"""
    if b == 0.0:
        if a == 0.0:
            return UNDEFINED
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


# ---------------------------------------------------------------- 像素网格对齐


def round_value_to_pixel_grid(
    value: float,
    point_scale_factor: float,
    force_ceil: bool = False,
    force_floor: bool = False,
) -> float:
    """PixelGrid.cpp:15 的逐行转写。"""
    scaled_value = value * point_scale_factor
    fractial = math.fmod(scaled_value, 1.0)
    if fractial < 0:  # 只为正数服务：fmod(-2.2)= -0.2 -> 0.8
        fractial += 1.0
    if inexact_equals(fractial, 0):
        scaled_value = scaled_value - fractial
    elif inexact_equals(fractial, 1.0):
        scaled_value = scaled_value - fractial + 1.0
    elif force_ceil:
        scaled_value = scaled_value - fractial + 1.0
    elif force_floor:
        scaled_value = scaled_value - fractial
    else:
        up = (not math.isnan(fractial)) and (
            fractial > 0.5 or inexact_equals(fractial, 0.5)
        )
        scaled_value = scaled_value - fractial + (1.0 if up else 0.0)
    if math.isnan(scaled_value) or math.isnan(point_scale_factor):
        return UNDEFINED
    return _ieee_div(scaled_value, point_scale_factor)


class LayoutNode:
    def __init__(self, width=0.0, height=0.0, left=0.0, top=0.0, is_text=False,
                 children=None):
        self.width, self.height = width, height
        self.left, self.top = left, top
        self.is_text = is_text
        self.children = children or []

    def __repr__(self):
        return "Node(L={} T={} W={} H={})".format(
            round(self.left, 4), round(self.top, 4),
            round(self.width, 4), round(self.height, 4))


def round_layout_results_to_pixel_grid(node, abs_left, abs_top, psf=1.0):
    """PixelGrid.cpp:65。注意宽度是「两条绝对边各自取整后再相减」。"""
    node_left, node_top = node.left, node.top
    abs_node_left = abs_left + node_left
    abs_node_top = abs_top + node_top
    abs_node_right = abs_node_left + node.width
    abs_node_bottom = abs_node_top + node.height

    if psf != 0.0:
        text_rounding = node.is_text  # NodeType::Text：绝不向下取整，避免截字
        node.left = round_value_to_pixel_grid(node_left, psf, False, text_rounding)
        node.top = round_value_to_pixel_grid(node_top, psf, False, text_rounding)

        scaled_w = node.width * psf
        has_fractional_width = not inexact_equals(round(scaled_w), scaled_w)
        scaled_h = node.height * psf
        has_fractional_height = not inexact_equals(round(scaled_h), scaled_h)

        node.width = (
            round_value_to_pixel_grid(
                abs_node_right, psf,
                text_rounding and has_fractional_width,
                text_rounding and not has_fractional_width)
            - round_value_to_pixel_grid(abs_node_left, psf, False, text_rounding)
        )
        node.height = (
            round_value_to_pixel_grid(
                abs_node_bottom, psf,
                text_rounding and has_fractional_height,
                text_rounding and not has_fractional_height)
            - round_value_to_pixel_grid(abs_node_top, psf, False, text_rounding)
        )
    for child in node.children:
        round_layout_results_to_pixel_grid(child, abs_node_left, abs_node_top, psf)
    return node


# ------------------------------------------------------------------ 尺寸模式


class SizingMode(Enum):
    StretchFit = 0   # 「撑满可用空间」-> MeasureMode::Exactly
    MaxContent = 1   # 「理想尺寸」      -> MeasureMode::Undefined
    FitContent = 2   # 「夹紧到可用空间」-> MeasureMode::AtMost


class MeasureMode(Enum):
    Undefined = 0
    Exactly = 1
    AtMost = 2


def measure_mode(m: SizingMode) -> MeasureMode:
    return {
        SizingMode.StretchFit: MeasureMode.Exactly,
        SizingMode.MaxContent: MeasureMode.Undefined,
        SizingMode.FitContent: MeasureMode.AtMost,
    }[m]


# ------------------------------------------------------------------ 测量缓存


def can_use_cached_measurement(
    width_mode, available_width, height_mode, available_height,
    last_width_mode, last_available_width,
    last_height_mode, last_available_height,
    last_computed_width, last_computed_height,
    margin_row=0.0, margin_column=0.0, psf=0.0,
) -> bool:
    """Cache.cpp:45 的逐行转写（含四条否决/放行规则）。"""
    if (is_defined(last_computed_height) and last_computed_height < 0) or \
       (is_defined(last_computed_width) and last_computed_width < 0):
        return False

    use_rounded = psf != 0
    eff_w = round_value_to_pixel_grid(available_width, psf) if use_rounded else available_width
    eff_h = round_value_to_pixel_grid(available_height, psf) if use_rounded else available_height
    eff_lw = round_value_to_pixel_grid(last_available_width, psf) if use_rounded else last_available_width
    eff_lh = round_value_to_pixel_grid(last_available_height, psf) if use_rounded else last_available_height

    same_w = last_width_mode == width_mode and inexact_equals(eff_lw, eff_w)
    same_h = last_height_mode == height_mode and inexact_equals(eff_lh, eff_h)

    def size_is_exact_and_matches(size_mode, size, last_computed):
        return size_mode == SizingMode.StretchFit and inexact_equals(size, last_computed)

    def old_is_max_and_still_fits(size_mode, size, last_mode, last_computed):
        return (size_mode == SizingMode.FitContent
                and last_mode == SizingMode.MaxContent
                and (size >= last_computed or inexact_equals(size, last_computed)))

    def new_stricter_and_valid(size_mode, size, last_mode, last_size, last_computed):
        return (last_mode == SizingMode.FitContent and size_mode == SizingMode.FitContent
                and is_defined(last_size) and is_defined(size) and is_defined(last_computed)
                and last_size > size
                and (last_computed <= size or inexact_equals(size, last_computed)))

    w_ok = (same_w
            or size_is_exact_and_matches(width_mode, available_width - margin_row, last_computed_width)
            or old_is_max_and_still_fits(width_mode, available_width - margin_row, last_width_mode, last_computed_width)
            or new_stricter_and_valid(width_mode, available_width - margin_row, last_width_mode,
                                      last_available_width, last_computed_width))
    h_ok = (same_h
            or size_is_exact_and_matches(height_mode, available_height - margin_column, last_computed_height)
            or old_is_max_and_still_fits(height_mode, available_height - margin_column, last_height_mode, last_computed_height)
            or new_stricter_and_valid(height_mode, available_height - margin_column, last_height_mode,
                                      last_available_height, last_computed_height))
    return w_ok and h_ok


# ------------------------------------------------------------------- errata


class Errata:
    None_ = 0
    StretchFlexBasis = 1
    AbsolutePositionWithoutInsetsExcludesPadding = 2
    AbsolutePercentAgainstInnerSize = 4
    MinSizeUndefinedInsteadOfAuto = 8
    FlexFirstPassUsesRunningTotals = 16
    All = 2147483647
    Classic = 2147483646

    DEFAULT = MinSizeUndefinedInsteadOfAuto | FlexFirstPassUsesRunningTotals

    @staticmethod
    def has(errata: int, bit: int) -> bool:
        return (errata & bit) == bit


# ----------------------------------------------------------------- 弹性行


class FlexItem:
    def __init__(self, basis, grow=0.0, shrink=1.0, margin=0.0,
                 min_main=None, max_main=None, display="flex", position="relative",
                 name=""):
        self.basis = basis
        self.grow = grow
        self.shrink = shrink
        self.margin = margin
        self.min_main = min_main
        self.max_main = max_main
        self.display = display
        self.position = position
        self.name = name
        self.size = basis


class FlexLine:
    def __init__(self):
        self.items = []
        self.size_consumed = 0.0
        self.total_grow = 0.0
        self.total_shrink_scaled = 0.0
        self.remaining_free_space = 0.0


def calculate_flex_line(items, available_main, gap=0.0, wrap=True, start=0):
    """FlexLine.cpp:16。返回 (本行, 下一个待处理下标)。"""
    line = FlexLine()
    size_consumed_including_min = 0.0
    first = None
    i = start
    while i < len(items):
        child = items[i]
        i += 1
        if child.display == "none" or child.position == "absolute":
            continue
        if first is None:
            first = child
        leading_gap = 0.0 if child is first else gap
        bounded = _bound_axis_within_min_max(child, child.basis)
        if (size_consumed_including_min + bounded + child.margin + leading_gap > available_main
                and wrap and len(line.items) > 0):
            i -= 1  # 这个孩子留到下一行
            break
        size_consumed_including_min += bounded + child.margin + leading_gap
        line.size_consumed += bounded + child.margin + leading_gap
        if child.grow != 0 or child.shrink != 0:
            line.total_grow += child.grow
            line.total_shrink_scaled += -child.shrink * child.basis
        line.items.append(child)
    # 源码：只有「大于 0 且小于 1」才抬到 1；shrink 合计恒为负，永远不会触发
    if 0 < line.total_grow < 1:
        line.total_grow = 1.0
    if 0 < line.total_shrink_scaled < 1:
        line.total_shrink_scaled = 1.0
    return line, i


def _bound_axis_within_min_max(item, size):
    if item.min_main is not None and size < item.min_main:
        return item.min_main
    if item.max_main is not None and size > item.max_main:
        return item.max_main
    return size


def _bound_axis(item, size):
    return _bound_axis_within_min_max(item, size)


# ------------------------------------------------------- 两遍自由空间分配


def distribute_free_space(line, available_main, errata=Errata.DEFAULT, trace=None):
    """FirstPass(CalculateLayout.cpp:1269) + SecondPass(:1063)。
    返回 (最终尺寸列表, 剩余自由空间)。"""
    remaining = available_main - line.size_consumed
    original_remaining = remaining
    use_running = Errata.has(errata, Errata.FlexFirstPassUsesRunningTotals)
    orig_grow = line.total_grow
    orig_shrink = line.total_shrink_scaled
    run_grow, run_shrink = line.total_grow, line.total_shrink_scaled

    frozen = {}
    # ---------- 第一遍：找出被 min/max 触发的项目并冻结 ----------
    delta = 0.0
    for child in line.items:
        basis = _bound_axis_within_min_max(child, child.basis)
        if remaining < 0:
            scaled = -child.shrink * basis
            if is_defined(scaled) and scaled != 0:
                total = run_shrink if use_running else orig_shrink
                base = basis + _ieee_div(remaining, total) * scaled
                bounded = _bound_axis(child, base)
                if is_defined(base) and is_defined(bounded) and base != bounded:
                    delta += bounded - basis
                    run_shrink -= (-child.shrink * basis)
                    frozen[id(child)] = bounded
        elif is_defined(remaining) and remaining > 0:
            factor = child.grow
            if is_defined(factor) and factor != 0:
                total = run_grow if use_running else orig_grow
                base = basis + _ieee_div(remaining, total) * factor
                bounded = _bound_axis(child, base)
                if is_defined(base) and is_defined(bounded) and base != bounded:
                    delta += bounded - basis
                    run_grow -= factor
                    frozen[id(child)] = bounded
    remaining -= delta

    # ---------- 第二遍：定尺寸 ----------
    sizes = []
    distributed = 0.0
    for child in line.items:
        basis = _bound_axis_within_min_max(child, child.basis)
        if id(child) in frozen:
            size = frozen[id(child)]
        elif remaining < 0:
            scaled = -child.shrink * basis
            if scaled != 0:
                magnitude = abs(run_shrink)
                if is_defined(run_shrink) and magnitude < 1e-6:
                    # 源码里显式加了相对 epsilon 保护，否则除以近零值会淹没 min/max
                    child_size = basis + scaled
                else:
                    child_size = basis + _ieee_div(remaining, run_shrink) * scaled
                size = _bound_axis(child, child_size)
            else:
                size = basis
        elif is_defined(remaining) and remaining > 0 and child.grow != 0:
            size = _bound_axis(child, basis + _ieee_div(remaining, run_grow) * child.grow)
        else:
            size = basis
        sizes.append(size)
        distributed += size - basis
    for child, s in zip(line.items, sizes):
        child.size = s
    if trace is not None:
        trace.append({
            "frozen": len(frozen), "delta": delta,
            "remaining_after_first": remaining,
            "leftover": original_remaining - distributed,
        })
    return sizes, original_remaining - distributed


def main():
    print("== roundValueToPixelGrid（psf=1）==")
    for v in (1.4, 1.5, 1.6, 2.5, -2.2, -2.6):
        print("  round({:>5}) = {}".format(v, round_value_to_pixel_grid(v, 1.0)))
    print("  round(1.4, psf=2) = {}".format(round_value_to_pixel_grid(1.4, 2.0)))

    print("== 三等分 100px 的取整（宽度 = 右绝对边取整 - 左绝对边取整）==")
    root = LayoutNode(width=100.0, height=20.0,
                      children=[LayoutNode(width=100.0 / 3, height=20.0, left=i * 100.0 / 3)
                                for i in range(3)])
    round_layout_results_to_pixel_grid(root, 0.0, 0.0, 1.0)
    print("   positions =", [c.left for c in root.children])
    print("   widths    =", [c.width for c in root.children])

    print("== FlexLine 分行（basis 40 ×3 / 可用 100 / gap 10）==")
    items = [FlexItem(40.0, name="c%d" % i) for i in range(3)]
    line, nxt = calculate_flex_line(items, 100.0, gap=10.0, wrap=True)
    print("   行内项目数 =", len(line.items), " 下一个下标 =", nxt,
          " 已消耗 =", line.size_consumed)

    print("== 两遍分配（grow 1:1:1，剩余 40，首项 max 25、次项 max 35）==")
    for label, err in (("原始总量(无 errata)", Errata.None_),
                       ("running totals(默认)", Errata.DEFAULT)):
        line2 = FlexLine()
        line2.items = [FlexItem(20.0, grow=1.0, max_main=25.0),
                       FlexItem(20.0, grow=1.0, max_main=35.0),
                       FlexItem(20.0, grow=1.0)]
        line2.size_consumed = 60.0
        line2.total_grow = 3.0
        tr = []
        sizes, leftover = distribute_free_space(line2, 100.0, err, tr)
        print("  {:<22} sizes={} leftover={:.2f} frozen={}".format(
            label, [round(s, 3) for s in sizes], leftover, tr[0]["frozen"]))


if __name__ == "__main__":
    main()
