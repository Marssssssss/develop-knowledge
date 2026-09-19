# -*- coding: utf-8 -*-
"""render_pipeline.py 自检：每条断言都对应 MDN《How browsers work》的一句原文。"""
from render_pipeline import (Element, Pipeline, own_layer, in_render_tree,
                             FRAME_BUDGET_MS)

ok = 0
fails = []


def check(label, cond, detail=""):
    global ok
    if cond:
        ok += 1
    else:
        fails.append("%s %s" % (label, detail))


def tree():
    root = Element("div", {"width": "400px", "height": "400px"})
    a = root.add(Element("p", {"width": "100px", "height": "20px"}))
    b = root.add(Element("div", {"width": "100px", "height": "30px"}))
    cv = b.add(Element("canvas", {"width": "100px", "height": "30px"}))
    return root, a, b, cv


# --- 1. 首帧：style → layout → paint → composite 各一次 ---
root, a, b, cv = tree()
p = Pipeline(root)
p.frame()
check("A1 首帧各阶段各 1 次", (p.layouts, p.paints, p.composites) == (1, 1, 1),
      "%d/%d/%d" % (p.layouts, p.paints, p.composites))

# --- 2. 改几何属性：reflow 引发 repaint 与 re-composite ---
l0, pt0, cp0 = p.layouts, p.paints, p.composites
p.set_style(a, "width", "150px")
p.frame()
check("B1 几何改动触发 reflow", p.layouts == l0 + 1)
check("B2 reflow 引发 repaint", p.paints == pt0 + 1)
check("B3 reflow 引发 re-composite", p.composites == cp0 + 1)

# --- 3. 改外观属性：只 repaint，不 reflow ---
l0, pt0, cp0 = p.layouts, p.paints, p.composites
p.set_style(a, "color", "red")
p.frame()
check("C1 改颜色不 reflow", p.layouts == l0, "%d vs %d" % (p.layouts, l0))
check("C2 改颜色要 repaint", p.paints == pt0 + 1)
check("C3 改颜色也要 re-composite", p.composites == cp0 + 1)

# --- 4. 已提升层上改 opacity：只需要重新合成，不重绘 ---
l0, pt0, cp0 = p.layouts, p.paints, p.composites
check("D0 canvas 自带独立层", own_layer(cv) is cv)
kind = p.set_style(cv, "opacity", "0.5")
p.frame()
check("D1 分类为 composite-only", kind == "composite", kind)
check("D2 不重绘", p.paints == pt0, "%d vs %d" % (p.paints, pt0))
check("D3 不重排", p.layouts == l0)
check("D4 只重新合成", p.composites == cp0 + 1)

# --- 5. 未提升的元素改 opacity：没有独立层可复用，必须重绘 ---
pt0 = p.paints
kind = p.set_style(a, "opacity", "0.5")
p.frame()
check("E1 未提升层不算 composite-only", kind == "paint", kind)
check("E2 无层则必须 repaint", p.paints == pt0 + 1)

# --- 6. 强制同步布局（layout thrashing）：读-写交错 vs 先写后读 ---
root2, a2, b2, cv2 = tree()
p2 = Pipeline(root2)
p2.frame()
base = p2.layouts
for i in range(5):
    p2.set_style(a2, "width", "%dpx" % (101 + i))   # 必须每次都是新值，否则 noop 不标脏
    p2.read_layout(a2)                     # 每次写后立刻读 → 强制同步布局
check("F1 读写交错 5 次 = 5 次布局", p2.layouts == base + 5, "%d" % (p2.layouts - base))

root3, a3, b3, cv3 = tree()
p3 = Pipeline(root3)
p3.frame()
base3 = p3.layouts
for i in range(5):
    p3.set_style(a3, "width", "%dpx" % (200 + i))
p3.read_layout(a3)                         # 全部写完只读一次
check("F2 批量写后读一次 = 1 次布局", p3.layouts == base3 + 1, "%d" % (p3.layouts - base3))
check("F3 交错写法比批量多 4 次布局", (p2.layouts - base) - (p3.layouts - base3) == 4)

# --- 7. 图片没写尺寸 → 尺寸已知后 reflow（MDN 原例） ---
root4 = Element("div", {"width": "400px", "height": "400px"})
img = root4.add(Element("img", {"height": "20px"}))     # 只有高度，宽度未知
p4 = Pipeline(root4)
p4.frame()
l0 = p4.layouts
p4.set_style(img, "width", "300px")        # 图片加载完成，尺寸确定了
p4.frame()
check("G1 图片尺寸迟到引发 reflow", p4.layouts == l0 + 1, "%d" % (p4.layouts - l0))

root5 = Element("div", {"width": "400px", "height": "400px"})
img2 = root5.add(Element("img", {"width": "300px", "height": "200px"}))
p5 = Pipeline(root5)
p5.frame()
l0 = p5.layouts
p5.frame()
check("G2 预先声明尺寸则不 reflow", p5.layouts == l0)

# --- 8. display:none 不进 render tree ---
root6 = Element("div", {"width": "400px", "height": "400px"})
shown = root6.add(Element("p", {"width": "10px", "height": "10px"}))
hidden = root6.add(Element("p", {"width": "10px", "height": "10px", "display": "none"}))
p6 = Pipeline(root6)
p6.frame()
check("H1 可见节点有布局盒", shown.box is not None)
check("H2 display:none 节点无布局盒", hidden.box is None)
check("H3 不进 render tree", not in_render_tree(hidden))

# --- 9. 层归属：自己不需要层的后代挂在最近的层祖先上 ---
root7 = Element("div", {"width": "400px", "height": "400px"})
wrap = root7.add(Element("div", {"width": "100px", "height": "100px", "will-change": "transform"}))
kid = wrap.add(Element("span", {"width": "10px", "height": "10px"}))
kid2 = kid.add(Element("video", {"width": "10px", "height": "10px"}))
check("I1 后代继承祖先层", own_layer(kid) is wrap)
check("I2 后代自己需要层时独立", own_layer(kid2) is kid2)
p7 = Pipeline(root7)
check("I3 独立层 = will-change 的 div + 自带层的 video",
      [e.tag for e in p7.layers()] == ["div", "video"], [e.tag for e in p7.layers()])
check("I4 span 不单独成层", kid not in p7.layers())

# --- 10. 帧预算（只打印，不做断言：耗时取决于机器） ---
ms = p.frame()
print("frame budget: %.2f ms / %.2f ms" % (ms, FRAME_BUDGET_MS))

print("render_pipeline: %d/%d assertions passed" % (ok, ok + len(fails)))
for f in fails:
    print("  FAIL", f)
raise SystemExit(1 if fails else 0)
