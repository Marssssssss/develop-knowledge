"""Yoga 布局模型自检：把源码常量与分支当作「应然」，逐条实跑对拍。

运行： python selfcheck_yoga.py
"""

from main import (
    UNDEFINED, is_defined, inexact_equals, round_value_to_pixel_grid,
    LayoutNode, round_layout_results_to_pixel_grid,
    SizingMode, MeasureMode, measure_mode, can_use_cached_measurement,
    Errata, FlexItem, FlexLine, calculate_flex_line, distribute_free_space,
)

PASS = 0


def ok(cond, label, actual=None):
    global PASS
    assert cond, "FAIL: {} -> {!r}".format(label, actual)
    PASS += 1


def close(a, b, tol=1e-6):
    return a is not None and abs(a - b) < tol


def three_equal_thirds(text=False):
    root = LayoutNode(width=100.0, height=20.0, is_text=text,
                      children=[LayoutNode(width=100.0 / 3, height=20.0,
                                           left=i * 100.0 / 3, is_text=text)
                                for i in range(3)])
    round_layout_results_to_pixel_grid(root, 0.0, 0.0, 1.0)
    return root


def line_of(items, available, gap=0.0, wrap=True):
    ln, nxt = calculate_flex_line(list(items), available, gap=gap, wrap=wrap)
    return ln, nxt


def flex_case(specs, available, consumed, errata=Errata.DEFAULT):
    """specs: [(basis, grow, shrink, min, max), ...]"""
    ln = FlexLine()
    ln.items = [FlexItem(b, grow=g, shrink=s, min_main=mn, max_main=mx)
                for (b, g, s, mn, mx) in specs]
    for it in ln.items:
        ln.size_consumed += it.basis
        ln.total_grow += it.grow
        ln.total_shrink_scaled += -it.shrink * it.basis
    if 0 < ln.total_grow < 1:
        ln.total_grow = 1.0
    if 0 < ln.total_shrink_scaled < 1:
        ln.total_shrink_scaled = 1.0
    trace = []
    sizes, leftover = distribute_free_space(ln, available, errata, trace)
    return sizes, leftover, trace[0]


# ============================================================ A. 浮点比较
ok(inexact_equals(1.0, 1.00001) is True, "A1 epsilon 0.0001：1.00001 视为相等")
ok(inexact_equals(1.0, 1.001) is False, "A2 epsilon 0.0001：1.001 不相等")
ok(inexact_equals(UNDEFINED, UNDEFINED) is True, "A3 NaN 与 NaN 相等")
ok(inexact_equals(1.0, UNDEFINED) is False, "A4 NaN 与非 NaN 不相等")
ok(is_defined(UNDEFINED) is False, "A5 isDefined(NaN)=false（NaN != 自身）")

# ==================================================== B. roundValueToPixelGrid
R = round_value_to_pixel_grid
ok(close(R(1.4, 1.0), 1.0), "B1 1.4 -> 1.0", R(1.4, 1.0))
ok(close(R(1.5, 1.0), 2.0), "B2 1.5 -> 2.0（fract==0.5 进位，非四舍六入五取偶）", R(1.5, 1.0))
ok(close(R(2.5, 1.0), 3.0), "B3 2.5 -> 3.0（同上，不是 2.0）", R(2.5, 1.0))
ok(close(R(1.6, 1.0), 2.0), "B4 1.6 -> 2.0", R(1.6, 1.0))
ok(close(R(-2.2, 1.0), -2.0), "B5 -2.2 -> -2.0（负数 fmod 补 1 后判 0.8>0.5）", R(-2.2, 1.0))
ok(close(R(-2.6, 1.0), -3.0), "B6 -2.6 -> -3.0（补 1 后 0.4<0.5）", R(-2.6, 1.0))
ok(close(R(1.4, 2.0), 1.5), "B7 psf=2：1.4 -> 1.5（先按 psf 缩放再取整）", R(1.4, 2.0))
ok(close(R(1.1, 1.0, force_ceil=True), 2.0), "B8 forceCeil：1.1 -> 2.0", R(1.1, 1.0, force_ceil=True))
ok(close(R(1.9, 1.0, force_floor=True), 1.0), "B9 forceFloor：1.9 -> 1.0", R(1.9, 1.0, force_floor=True))
ok(is_defined(R(UNDEFINED, 1.0)) is False, "B10 输入 NaN -> 返回 YGUndefined")
ok(is_defined(R(0.0, 0.0)) is False, "B11 psf=0 且值 0 -> IEEE 0/0 = NaN（Python 需显式模拟）")

# ==================================================== C. 布局结果取整
root = three_equal_thirds()
ok([c.left for c in root.children] == [0.0, 33.0, 67.0],
   "C1 三等分位置 = [0, 33, 67]", [c.left for c in root.children])
ok([c.width for c in root.children] == [33.0, 34.0, 33.0],
   "C2 三等分宽度 = [33, 34, 33]（中间那格吃掉误差）", [c.width for c in root.children])
ok(abs(sum(c.width for c in root.children) - 100.0) < 1e-9,
   "C3 取整后总宽仍是 100")

text_root = three_equal_thirds(text=True)
ok([c.width for c in text_root.children] == [34.0, 34.0, 34.0],
   "C4 文本节点宽度一律向上（34 ≥ 未取整的 33.33），不会截字",
   [c.width for c in text_root.children])

t = LayoutNode(width=10.0, height=10.0, left=33.7, is_text=True)
round_layout_results_to_pixel_grid(t, 0.0, 0.0, 1.0)
ok(close(t.left, 33.0), "C5 文本节点位置只向下取整：33.7 -> 33", t.left)
n = LayoutNode(width=10.0, height=10.0, left=33.7, is_text=False)
round_layout_results_to_pixel_grid(n, 0.0, 0.0, 1.0)
ok(close(n.left, 34.0), "C6 普通节点位置按四舍五入：33.7 -> 34", n.left)

# ==================================================== D. 尺寸模式映射
ok(measure_mode(SizingMode.StretchFit) == MeasureMode.Exactly, "D1 StretchFit -> Exactly")
ok(measure_mode(SizingMode.MaxContent) == MeasureMode.Undefined, "D2 MaxContent -> Undefined")
ok(measure_mode(SizingMode.FitContent) == MeasureMode.AtMost, "D3 FitContent -> AtMost")

# ==================================================== E. 测量缓存
C = can_use_cached_measurement
ok(C(SizingMode.MaxContent, 100, SizingMode.MaxContent, 100,
     SizingMode.MaxContent, 100, SizingMode.MaxContent, 100,
     -1.0, 50) is False, "E1 上次算出的宽度为负 -> 直接否决（不看其它条件）")

ok(C(SizingMode.MaxContent, 100, SizingMode.MaxContent, 100,
     SizingMode.MaxContent, 100, SizingMode.MaxContent, 100,
     80.0, 50) is True, "E2 模式与可用空间都相同 -> 命中缓存")

ok(C(SizingMode.StretchFit, 100, SizingMode.MaxContent, 100,
     SizingMode.MaxContent, 10, SizingMode.MaxContent, 100,
     100.0, 50, margin_row=0.0) is True, "E3 StretchFit 且可用空间等于上次测得值 -> 命中")

ok(C(SizingMode.FitContent, 100, SizingMode.MaxContent, 100,
     SizingMode.MaxContent, 200, SizingMode.MaxContent, 100,
     80.0, 50) is True, "E4 现在 FitContent、上次 MaxContent 且新空间装得下旧尺寸 -> 命中")

ok(C(SizingMode.FitContent, 60, SizingMode.MaxContent, 100,
     SizingMode.MaxContent, 200, SizingMode.MaxContent, 100,
     80.0, 50) is False, "E5 同上但新空间 60 < 旧尺寸 80 -> 不命中")

ok(C(SizingMode.FitContent, 60, SizingMode.MaxContent, 100,
     SizingMode.FitContent, 100, SizingMode.MaxContent, 100,
     50.0, 50) is True, "E6 两次都是 FitContent、收紧后旧测得值仍装得下 -> 命中")

ok(C(SizingMode.FitContent, 60, SizingMode.MaxContent, 100,
     SizingMode.FitContent, 40, SizingMode.MaxContent, 100,
     50.0, 50) is False, "E7 上次空间并不更宽（40 < 60）-> 走不到第三条规则")

ok(C(SizingMode.MaxContent, 1.4, SizingMode.MaxContent, 100,
     SizingMode.MaxContent, 1.2, SizingMode.MaxContent, 100,
     80.0, 50, psf=1.0) is True,
   "E8 psf=1：可用 1.4 与 1.2 取整后都是 1 -> 视为同一约束")

ok(C(SizingMode.MaxContent, 1.4, SizingMode.MaxContent, 100,
     SizingMode.MaxContent, 1.2, SizingMode.MaxContent, 100,
     80.0, 50, psf=0.0) is False,
   "E9 psf=0（不做取整比较）：1.4 与 1.2 视为不同 -> 不命中")

# ==================================================== F. errata
ok(Errata.DEFAULT == 24, "F1 默认 errata = MinSizeUndefinedInsteadOfAuto(8) | "
   "FlexFirstPassUsesRunningTotals(16) = 24", Errata.DEFAULT)
ok(Errata.has(Errata.DEFAULT, Errata.MinSizeUndefinedInsteadOfAuto) is True,
   "F2 默认开启「auto min 仍按 undefined 处理」（即 §4.5 的自动最小尺寸默认不生效）")
ok(Errata.has(Errata.DEFAULT, Errata.FlexFirstPassUsesRunningTotals) is True,
   "F3 默认开启「第一遍用滚动总量」（即默认行为是修复前的行为）")
ok(Errata.Classic == Errata.All ^ Errata.StretchFlexBasis,
   "F4 Classic = All 去掉 StretchFlexBasis", Errata.Classic)
ok(Errata.has(Errata.Classic, Errata.StretchFlexBasis) is False, "F5 Classic 不含 StretchFlexBasis")
ok(Errata.has(Errata.Classic, Errata.FlexFirstPassUsesRunningTotals) is True,
   "F6 Classic 含其它全部位")
ok(Errata.has(Errata.None_, Errata.All) is False, "F7 None 什么都不含")

# ==================================================== G. FlexLine 分行
ln, nxt = line_of([FlexItem(40.0) for _ in range(3)], 100.0, gap=10.0)
ok(len(ln.items) == 2 and nxt == 2, "G1 gap=10：40+10+40=90，第三个会到 140 -> 断行",
   (len(ln.items), nxt))
ok(close(ln.size_consumed, 90.0), "G2 已消耗含 gap = 90", ln.size_consumed)

ln0, nxt0 = line_of([FlexItem(40.0) for _ in range(3)], 100.0, gap=0.0)
ok(len(ln0.items) == 2 and close(ln0.size_consumed, 80.0),
   "G3 无 gap：两个 40 进第一行（80 ≤ 100），第三个溢出", (len(ln0.items), ln0.size_consumed))

ln1, _ = line_of([FlexItem(150.0)], 100.0, wrap=True)
ok(len(ln1.items) == 1, "G4 首项即使放不下也留在当前行（断行要求 itemsInFlow 非空）")

ln2, _ = line_of([FlexItem(40.0) for _ in range(3)], 100.0, wrap=False)
ok(len(ln2.items) == 3, "G5 NoWrap：三个都在同一行")

ln3, _ = line_of([FlexItem(20.0, grow=0.2), FlexItem(20.0, grow=0.3)], 100.0)
ok(close(ln3.total_grow, 1.0), "G6 grow 合计 0.5 < 1 -> 抬到 1（源码注释：need floored to 1）",
   ln3.total_grow)

ln4, _ = line_of([FlexItem(20.0, grow=1.0), FlexItem(20.0, grow=2.0)], 100.0)
ok(close(ln4.total_grow, 3.0), "G7 grow 合计 ≥ 1 时不抬")

ln5, _ = line_of([FlexItem(40.0, shrink=1.0), FlexItem(40.0, shrink=1.0)], 100.0)
ok(close(ln5.total_shrink_scaled, -80.0),
   "G8 shrink 合计按 basis 缩放：-(1×40 + 1×40) = -80", ln5.total_shrink_scaled)

ln6, _ = line_of([FlexItem(40.0, shrink=0.01)], 100.0)
ok(close(ln6.total_shrink_scaled, -0.4),
   "G9 shrink 合计为负，源码的 `>0 && <1` 抬起条件永不成立 -> 保持 -0.4（注释与实现不一致，只记录）",
   ln6.total_shrink_scaled)

# ==================================================== H. 两遍自由空间分配
sizes, leftover, tr = flex_case([(20.0, 1.0, 1.0, None, None),
                                 (20.0, 1.0, 1.0, None, None),
                                 (20.0, 1.0, 1.0, None, None)], 100.0, 60.0)
ok(all(close(s, 100.0 / 3) for s in sizes) and close(leftover, 0.0),
   "H1 无 min/max：三等分剩余 40 -> 各 33.33，无残留", (sizes, leftover))

sizes_o, _, _ = flex_case([(20.0, 1.0, 1.0, None, 25.0),
                           (20.0, 1.0, 1.0, None, 35.0),
                           (20.0, 1.0, 1.0, None, None)], 100.0, 60.0,
                          errata=Errata.None_)
ok(close(sizes_o[0], 25.0) and close(sizes_o[1], 35.0) and close(sizes_o[2], 37.5),
   "H2 用原始总量：只有首项在第一遍被冻结，末项第二遍又被 max 夹 -> [25, 35, 37.5]", sizes_o)

sizes_r, leftover_r, tr_r = flex_case([(20.0, 1.0, 1.0, None, 25.0),
                                       (20.0, 1.0, 1.0, None, 35.0),
                                       (20.0, 1.0, 1.0, None, None)], 100.0, 60.0,
                                      errata=Errata.DEFAULT)
ok(close(sizes_r[2], 40.0) and close(leftover_r, 0.0),
   "H3 用滚动总量（默认）：两项都在第一遍被冻结，末项吃到全部剩余 -> [25, 35, 40]",
   (sizes_r, leftover_r))
ok(tr_r["frozen"] == 2, "H4 滚动总量下第一遍冻结 2 项（原始总量只冻结 1 项）", tr_r["frozen"])

sizes_s, _, _ = flex_case([(60.0, 0.0, 1.0, None, None),
                           (60.0, 0.0, 1.0, None, None)], 100.0, 120.0)
ok(close(sizes_s[0], 50.0) and close(sizes_s[1], 50.0),
   "H5 收缩：剩余 -20 按 shrink×basis 加权 -> [50, 50]", sizes_s)

sizes_m, _, _ = flex_case([(60.0, 0.0, 1.0, 55.0, None),
                           (60.0, 0.0, 1.0, None, None)], 100.0, 120.0)
ok(close(sizes_m[0], 55.0) and close(sizes_m[1], 45.0),
   "H6 收缩带 min：首项被冻结在 55，剩余 -15 全给末项 -> [55, 45]", sizes_m)

sizes_b, _, tr_b = flex_case([(60.0, 0.0, 1.0, 55.0, None),
                              (60.0, 0.0, 1.0, 55.0, None)], 100.0, 120.0)
ok(close(sizes_b[0], 55.0) and close(sizes_b[1], 55.0),
   "H7 两项都被冻结后 shrink 合计归零，触发 1e-6 相对 epsilon 保护 -> [55, 55]", sizes_b)
ok(sum(sizes_b) > 100.0, "H8 上例总宽 110 > 100：官方注释明说「不保证处理所有情况」", sum(sizes_b))

sizes_g, leftover_g, _ = flex_case([(20.0, 0.2, 1.0, None, None),
                                    (20.0, 0.3, 1.0, None, None)], 100.0, 40.0)
ok(close(sizes_g[0], 32.0) and close(sizes_g[1], 38.0),
   "H9 grow 合计被抬到 1 后，剩余 60 只分掉 12+18 -> [32, 38]", sizes_g)
ok(close(leftover_g, 30.0), "H10 上例残留 30 的自由空间不再分配（grow 之和 < 1 的已知行为）",
   leftover_g)

print("Yoga 布局引擎自检：{} 项断言全部通过".format(PASS))
