# -*- coding: utf-8 -*-
"""
浏览器渲染管线模拟器（样式 → 布局 → 绘制 → 合成）。

权威依据：MDN《How browsers work》
  https://developer.mozilla.org/en-US/docs/Web/Performance/How_browsers_work
  - "Rendering steps include style, layout, paint, and in some cases compositing."
  - "The first time the size and position of each node is determined is called layout.
     Subsequent recalculations of layout are called reflows."
  - "A reflow sparks a repaint and a re-composite."（图片未给尺寸 → 图片到达后 reflow）
  - 层提升："<video> and <canvas>, and any element which has the CSS properties of
    opacity, a 3D transform, will-change"；被提升的节点连同后代画进同一层，
    "unless a descendant necessitates its own layer"。
  - 主线程预算："must take the browser less than 16.67ms"。

模型：把一次样式改动分类成「要重排 / 只重绘 / 只合成」，并统计各阶段真实发生次数。
"""

GEOMETRY_PROPS = {"width", "height", "top", "left", "margin", "margin-top",
                  "padding", "font-size", "border-width", "display", "position"}
PAINT_ONLY_PROPS = {"color", "background", "background-color", "box-shadow",
                    "border-color", "visibility", "outline-color"}
COMPOSITABLE_PROPS = {"opacity", "transform", "filter"}
LAYER_HINT_PROPS = {"will-change", "transform3d"}
LAYER_TAGS = {"video", "canvas", "iframe"}

FRAME_BUDGET_MS = 16.67


class Element:
    def __init__(self, tag, style=None, children=None, text=None):
        self.tag = tag
        self.style = dict(style or {})
        self.children = list(children or [])
        self.text = text
        self.box = None                 # 布局结果 (x, y, w, h)
        self.parent = None
        for c in self.children:
            c.parent = self

    def add(self, child):
        child.parent = self
        self.children.append(child)
        return child


def is_layered(el):
    """自身是否需要独立合成层。"""
    if el.tag in LAYER_TAGS:
        return True
    return any(p in el.style for p in LAYER_HINT_PROPS)


def own_layer(el):
    """元素最终落在哪个独立层：自己需要层就是自己，否则继承最近的需要层的祖先。"""
    node = el
    while node is not None:
        if is_layered(node):
            return node
        node = node.parent
    return None


def in_render_tree(el):
    """display:none 的节点不进 render tree（MDN：render tree 只含可见节点）。"""
    return el.style.get("display") != "none"


class Pipeline:
    def __init__(self, root):
        self.root = root
        self.layouts = 0
        self.paints = 0
        self.composites = 0
        self.dirty_layout = True
        self.dirty_paint = True
        self.last_frame_ms = 0.0

    # ---- 样式写入：按属性分类决定脏到哪一层 ----
    def set_style(self, el, prop, value):
        if el.style.get(prop) == value:
            return "noop"
        el.style[prop] = value
        if prop in GEOMETRY_PROPS or prop == "width" or prop == "height":
            self.dirty_layout = True
            self.dirty_paint = True
            return "layout"
        if prop in COMPOSITABLE_PROPS and own_layer(el) is not None:
            # 已提升的层改 opacity/transform 只需重新合成，层内容不必重绘
            return "composite"
        self.dirty_paint = True
        return "paint"

    # ---- 强制同步布局：读几何属性时若布局是脏的，必须立刻算 ----
    def read_layout(self, el):
        if self.dirty_layout:
            self.layout()
        return el.box

    def layout(self):
        self.layouts += 1
        self.dirty_layout = False
        y = 0
        for el in self._walk():
            w = int(str(el.style.get("width", "100")).replace("px", "") or 0)
            h = int(str(el.style.get("height", "20")).replace("px", "") or 0)
            el.box = (0, y, w, h)
            y += h
        return self.layouts

    def paint(self):
        self.paints += 1
        self.dirty_paint = False

    def composite(self):
        self.composites += 1

    def frame(self, style_ms=2.0, layout_ms=3.0, paint_ms=4.0, composite_ms=1.0):
        """一帧：style 与 layout 只在脏时执行，paint/composite 紧随其后。"""
        ms = style_ms
        if self.dirty_layout or any(e.box is None for e in self._walk()):
            self.layout()
            ms += layout_ms
        if self.dirty_paint:
            self.paint()
            ms += paint_ms
        self.composite()
        ms += composite_ms
        self.last_frame_ms = ms
        return ms

    def layers(self):
        seen = []
        for el in self._walk():
            owner = own_layer(el)
            if owner is not None and owner not in seen:
                seen.append(owner)
        return seen

    def _walk(self):
        out = []
        stack = [self.root]
        while stack:
            el = stack.pop()
            if not in_render_tree(el) or el.parent is not None and not in_render_tree(el.parent):
                continue
            out.append(el)
            stack.extend(reversed(el.children))
        return out
