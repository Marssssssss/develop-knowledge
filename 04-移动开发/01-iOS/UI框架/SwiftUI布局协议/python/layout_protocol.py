"""SwiftUI Layout 协议:尺寸协商(proposal → response)的可执行模型。

模型口径(全部对应 Apple 官方文档,见 README 参考资料):
* 父视图向子视图提出 ProposedViewSize,子视图**自己**决定尺寸并返回;
* 三种特殊提案:.zero → 最小尺寸、.infinity → 最大尺寸、.unspecified → 理想尺寸;
* 自定义 Layout 只需实现 sizeThatFits 与 placeSubviews,其余有默认实现;
* ViewThatFits 按**提供顺序**挑选第一个「在受约束的轴上理想尺寸放得下」的子视图;
* AnyLayout 换布局类型不销毁子视图状态;
* makeCache 的结果会同时喂给 sizeThatFits 与 placeSubviews。
"""

INF = float("inf")


class ProposedViewSize:
    """width/height 为 None 表示该轴 unspecified。"""

    __slots__ = ("width", "height")

    def __init__(self, width=None, height=None):
        self.width = width
        self.height = height

    @staticmethod
    def zero():
        return ProposedViewSize(0.0, 0.0)

    @staticmethod
    def infinity():
        return ProposedViewSize(INF, INF)

    @staticmethod
    def unspecified():
        return ProposedViewSize(None, None)

    def replacing_unspecified_dimensions(self, by):
        return ProposedViewSize(
            by.width if self.width is None else self.width,
            by.height if self.height is None else self.height,
        )

    def __repr__(self):
        f = lambda v: "nil" if v is None else ("inf" if v == INF else f"{v:g}")
        return f"Proposed({f(self.width)}, {f(self.height)})"


class Size:
    __slots__ = ("width", "height")

    def __init__(self, width, height):
        self.width = width
        self.height = height

    def __repr__(self):
        return f"({self.width:g} x {self.height:g})"


class Subview:
    """LayoutSubview 代理:持有 min / ideal / max,按提案返回自己选的尺寸。"""

    def __init__(self, name, min_w, ideal_w, max_w, min_h, ideal_h, max_h):
        self.name = name
        self.min = (min_w, min_h)
        self.ideal = (ideal_w, ideal_h)
        self.max = (max_w, max_h)
        self.placed_at = None
        self.measure_count = 0

    def size_that_fits(self, proposal):
        self.measure_count += 1
        out = []
        for axis in (0, 1):
            p = (proposal.width, proposal.height)[axis]
            lo, mid, hi = self.min[axis], self.ideal[axis], self.max[axis]
            if p is None:
                out.append(mid)                 # unspecified → ideal
            elif p == 0:
                out.append(lo)                  # zero → minimum
            elif p == INF:
                out.append(hi)                  # infinity → maximum
            else:
                out.append(max(lo, min(hi, p)))
        return Size(out[0], out[1])

    def place(self, at, size):
        self.placed_at = (at[0], at[1])
        self.placed_size = size


class Subviews(list):
    pass


class Layout:
    """Layout 协议:两个必需方法 + 三个有默认实现的钩子。"""

    def make_cache(self, subviews):
        return None

    def size_that_fits(self, proposal, subviews, cache):
        raise NotImplementedError

    def place_subviews(self, in_bounds, proposal, subviews, cache):
        raise NotImplementedError

    def explicit_alignment(self, of, in_proposal):
        return None                              # 默认实现

    def spacing(self, subviews, cache):
        return 0.0                               # 默认实现


class HStackLayout(Layout):
    def __init__(self, spacing=0.0, cached=True):
        self.spacing = spacing
        self.cached = cached

    def make_cache(self, subviews):
        if not self.cached:
            return None
        # 缓存里存「按 unspecified 量到的理想尺寸」,sizeThatFits 与 placeSubviews 都用它
        return {"ideal": [s.size_that_fits(ProposedViewSize.unspecified()) for s in subviews]}

    def size_that_fits(self, proposal, subviews, cache):
        if cache is not None and "ideal" in cache:
            sizes = list(cache["ideal"])
        else:
            sizes = [s.size_that_fits(ProposedViewSize.unspecified()) for s in subviews]
        total_w = sum(s.width for s in sizes) + self.spacing * (len(sizes) - 1)
        total_h = max((s.height for s in sizes), default=0.0)
        if proposal.width is not None and proposal.width < total_w:
            # 空间不够:把剩余空间重新分配给子视图,取它们被压缩后的响应
            sizes = self._squeeze(proposal, subviews)
            total_w = sum(s.width for s in sizes) + self.spacing * (len(sizes) - 1)
            total_h = max((s.height for s in sizes), default=0.0)
        self._last_sizes = sizes
        return Size(total_w, total_h)

    def _squeeze(self, proposal, subviews):
        avail = proposal.width - self.spacing * (len(subviews) - 1)
        out = []
        for s in subviews:
            share = max(0.0, avail / max(1, len(subviews)))
            out.append(s.size_that_fits(ProposedViewSize(share, proposal.height)))
        return out

    def place_subviews(self, in_bounds, proposal, subviews, cache):
        # 有缓存就复用 sizeThatFits 量到的结果;没有缓存只能再问一遍子视图
        if cache is not None and "ideal" in cache:
            sizes = list(cache["ideal"])
        elif getattr(self, "_last_sizes", None) is not None and cache is None and self.cached:
            sizes = self._last_sizes
        else:
            sizes = [s.size_that_fits(ProposedViewSize.unspecified()) for s in subviews]
        x = in_bounds[0]
        for s, sz in zip(subviews, sizes):
            s.place((x, in_bounds[1]), sz)
            x += sz.width + self.spacing
        return sizes


class VStackLayout(Layout):
    def __init__(self, spacing=0.0):
        self.spacing = spacing

    def size_that_fits(self, proposal, subviews, cache):
        sizes = [s.size_that_fits(ProposedViewSize.unspecified()) for s in subviews]
        self._last_sizes = sizes
        return Size(max((s.width for s in sizes), default=0.0),
                    sum(s.height for s in sizes) + self.spacing * (len(sizes) - 1))

    def place_subviews(self, in_bounds, proposal, subviews, cache):
        y = in_bounds[1]
        for s, sz in zip(subviews, self._last_sizes):
            s.place((in_bounds[0], y), sz)
            y += sz.height + self.spacing


class ViewThatFits:
    """按提供顺序取第一个「受约束轴上理想尺寸放得下」的子视图。"""

    def __init__(self, children, axes=("horizontal", "vertical")):
        self.children = children
        self.axes = axes
        self.selected = None

    def size_that_fits(self, proposal):
        for i, child in enumerate(self.children):
            ideal = child.size_that_fits(ProposedViewSize.unspecified())
            fits_w = ("horizontal" not in self.axes
                      or proposal.width is None
                      or ideal.width <= proposal.width)
            fits_h = ("vertical" not in self.axes
                      or proposal.height is None
                      or ideal.height <= proposal.height)
            if fits_w and fits_h:
                self.selected = i
                return ideal
        self.selected = len(self.children) - 1          # 都不合适就用最后一个
        return self.children[-1].size_that_fits(ProposedViewSize.unspecified())


class AnyLayoutBox:
    """AnyLayout:换布局类型而不销毁子视图状态。"""

    def __init__(self, layout, subviews):
        self.layout = layout
        self.subviews = subviews

    def relayout(self, new_layout, proposal):
        self.layout = new_layout                        # 只换算法,subviews 数组不变
        return self.run(proposal)

    def run(self, proposal):
        cache = self.layout.make_cache(self.subviews)
        size = self.layout.size_that_fits(proposal, self.subviews, cache)
        self.layout.place_subviews((0, 0), proposal, self.subviews, cache)
        return size
