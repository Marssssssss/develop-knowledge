from layout_protocol import *

# SwiftUI 布局协议 自检:python selfcheck_layout_protocol.py
# 模型语义见 layout_protocol.py

def _ck(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")


def run_selfcheck():
    n = 0

    def ok(label, cond, detail=""):
        nonlocal n
        n += 1
        _ck(label, cond, detail)
        print(f"ok {n:02d} - {label}")

    # --- 1. 三种特殊提案
    text = Subview("Text", min_w=20, ideal_w=120, max_w=INF,
                   min_h=10, ideal_h=20, max_h=INF)
    ok(".zero → 最小尺寸", text.size_that_fits(ProposedViewSize.zero()) .width == 20)
    ok(".unspecified → 理想尺寸", text.size_that_fits(ProposedViewSize.unspecified()).width == 120)
    ok(".infinity → 最大尺寸", text.size_that_fits(ProposedViewSize.infinity()).width == INF)

    btn = Subview("Button", min_w=44, ideal_w=44, max_w=44,
                  min_h=22, ideal_h=22, max_h=22)
    ok("提案小于最小尺寸时仍不小于 min",
       btn.size_that_fits(ProposedViewSize(10, 10)).width == 44)
    ok("提案大于最大尺寸时不超过 max",
       btn.size_that_fits(ProposedViewSize(999, 999)).width == 44)
    ok("中间提案被按 min/max 夹住",
       text.size_that_fits(ProposedViewSize(80, None)).width == 80)

    p = ProposedViewSize(None, 30).replacing_unspecified_dimensions(Size(100, 200))
    ok("replacingUnspecifiedDimensions 只替换 unspecified 轴",
       p.width == 100 and p.height == 30, p)

    # --- 2. 尺寸自下而上:父提议、子决定
    subs = Subviews([Subview("A", 10, 60, 200, 10, 20, 40),
                     Subview("B", 10, 90, 200, 10, 20, 40)])
    hl = HStackLayout(spacing=8)
    cache = hl.make_cache(subs)
    size = hl.size_that_fits(ProposedViewSize.unspecified(), subs, cache)
    ok("HStack 合成宽度 = 理想宽之和 + 间距", size.width == 60 + 90 + 8, size)
    ok("HStack 合成高度 = 子视图最大高度", size.height == 20, size)

    tight = hl.size_that_fits(ProposedViewSize(120, None), Subviews(
        [Subview("C", 10, 60, 200, 10, 20, 40), Subview("D", 10, 90, 200, 10, 20, 40)]), None)
    ok("空间不足时 HStack 按重新提议的结果收缩", tight.width <= 120 + 1e-9, tight)

    # --- 3. placeSubviews 负责定位,sizeThatFits 只报尺寸
    subs2 = Subviews([Subview("A", 10, 60, 200, 10, 20, 40),
                      Subview("B", 10, 90, 200, 10, 20, 40)])
    hl2 = HStackLayout(spacing=8)
    hl2.size_that_fits(ProposedViewSize.unspecified(), subs2, None)
    hl2.place_subviews((0, 0), ProposedViewSize.unspecified(), subs2, None)
    ok("第一个子视图放在 bounds 原点", subs2[0].placed_at == (0, 0), subs2[0].placed_at)
    ok("第二个子视图按前一个的宽度 + spacing 顺延",
       subs2[1].placed_at == (68, 0), subs2[1].placed_at)

    # --- 4. 缓存:makeCache 的量一次,sizeThatFits/placeSubviews 复用
    def measured(layout_maker):
        sv = Subviews([Subview(f"S{i}", 10, 50, 200, 10, 20, 40) for i in range(4)])
        lay = layout_maker()
        c = lay.make_cache(sv)
        lay.size_that_fits(ProposedViewSize.unspecified(), sv, c)
        lay.place_subviews((0, 0), ProposedViewSize.unspecified(), sv, c)
        return sum(s.measure_count for s in sv)

    with_cache = measured(lambda: HStackLayout(cached=True))
    without_cache = measured(lambda: HStackLayout(cached=False))
    ok("有 cache 时子视图只被量一次", with_cache == 4, with_cache)
    ok("无 cache 时 placeSubviews 会再量一遍", without_cache == 8, without_cache)
    ok("cache 把测量次数减半", without_cache == 2 * with_cache)

    # --- 5. ViewThatFits 按提供顺序挑第一个放得下的
    wide = Subview("HStack版", 40, 200, INF, 10, 20, 40)
    mid = Subview("只有进度条", 10, 100, INF, 10, 20, 40)
    narrow = Subview("只有文字", 10, 50, INF, 10, 20, 40)
    vtf = ViewThatFits([wide, mid, narrow])
    ok("宽度 200 时选第一个(最宽的那个)",
       vtf.size_that_fits(ProposedViewSize(200, None)).width == 200 and vtf.selected == 0)
    vtf2 = ViewThatFits([wide, mid, narrow])
    ok("宽度 100 时跳过第一个选中第二个",
       vtf2.size_that_fits(ProposedViewSize(100, None)).width == 100 and vtf2.selected == 1)
    vtf3 = ViewThatFits([wide, mid, narrow])
    ok("宽度 50 时落到最后一个",
       vtf3.size_that_fits(ProposedViewSize(50, None)).width == 50 and vtf3.selected == 2)

    # 只约束水平轴:垂直方向再高也算「放得下」
    tall = Subview("高个子", 10, 100, INF, 10, 500, INF)
    vtf4 = ViewThatFits([tall], axes=("horizontal",))
    ok("只约束水平轴时,超高但够宽的子视图照样被选中",
       vtf4.size_that_fits(ProposedViewSize(120, 100)).height == 500 and vtf4.selected == 0)
    vtf5 = ViewThatFits([tall])
    ok("两轴都约束时,超高就放不下(退回最后一个)",
       vtf5.size_that_fits(ProposedViewSize(120, 100)).height == 500)

    # --- 6. AnyLayout:换布局类型不销毁子视图状态
    sv = [Subview("X", 10, 60, 200, 10, 20, 40), Subview("Y", 10, 60, 200, 10, 20, 40)]
    box = AnyLayoutBox(HStackLayout(), sv)
    box.run(ProposedViewSize.unspecified())
    ok("HStack 下两个子视图 y 相同、x 递增",
       box.subviews[0].placed_at[1] == box.subviews[1].placed_at[1]
       and box.subviews[1].placed_at[0] > box.subviews[0].placed_at[0])
    ids_before = [id(s) for s in sv]
    box.relayout(VStackLayout(), ProposedViewSize.unspecified())
    ok("换布局后 Subviews 数组仍是同一批对象", [id(s) for s in sv] == ids_before)
    ok("换成 VStack 后 x 相同、y 递增",
       box.subviews[0].placed_at[0] == box.subviews[1].placed_at[0]
       and box.subviews[1].placed_at[1] > box.subviews[0].placed_at[1])

    # --- 7. 协议只需两个必需方法,其余有默认实现
    class MinimalLayout(Layout):
        def size_that_fits(self, proposal, subviews, cache):
            return Size(1, 1)

        def place_subviews(self, in_bounds, proposal, subviews, cache):
            for s in subviews:
                s.place((in_bounds[0], in_bounds[1]), Size(1, 1))

    ml = MinimalLayout()
    ok("未实现 explicitAlignment 时走默认实现(返回 None)",
       ml.explicit_alignment("h", ProposedViewSize.unspecified()) is None)
    ok("未实现 spacing 时走默认实现(0)", ml.spacing(Subviews(), None) == 0.0)
    ok("只实现两个方法即可完成一次布局",
       ml.size_that_fits(ProposedViewSize.unspecified(), Subviews(), None).width == 1)

    print(f"\n全部 {n} 条断言通过")
    return n


if __name__ == "__main__":
    run_selfcheck()
