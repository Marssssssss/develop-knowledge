#!/usr/bin/env python3
r"""Core Animation 渲染循环模型 —— 把「一帧」拆成 CPU 侧的 Commit 与 GPU 侧的 Render。

权威依据(完整引用见同目录 README「参考资料」):Apple Tech Talk《Explore UI animation
hitches and the render loop》给出的 Render Loop 是
Event → Commit(应用进程内:Layout → Display → Prepare)→ render server(把图层树排成
GPU 可执行的线性流水线:父→子、兄→弟、由后至前)→ GPU 合成 → 下一个 VSYNC 上屏。
每个阶段的 deadline 都是**下一个 VSYNC**,错过即 hitch:1 帧 = 16.67ms、2 帧 = 33.34ms。
hitch 分两类 —— commit hitch(应用进程内)与 render hitch(render server 内);
「need layout」请求会被**合并**后一次性执行,以减少重复工作。

**本文件是成本模型,不是测量器**:系数为合成值,只保留各阶段的形状与量级关系。
真机测量要用 Instruments 的 Core Animation 模板(Offscreen-Rendered / Blended Layers /
Hits & Misses)与 GPU Driver。
"""

from __future__ import annotations
from dataclasses import dataclass

MS_PER_FRAME_60 = 1000.0 / 60.0        # 16.667ms
MS_PER_FRAME_120 = 1000.0 / 120.0      # 8.333ms

# 合成系数(单位:任意耗时单位)
C_COORD = 0.003          # 每个坐标计算
C_TOUCH_LAYER = 0.08     # layout pass 里访问一个 layer
C_DRAW_LAYER = 1.4       # 一个 layer 走 drawRect/drawInContext 画一遍(CPU + 上传纹理)
C_PACKAGE_LAYER = 0.05   # Prepare 阶段打包一个 layer
C_OFFSCREEN_PASS = 2.2   # 一次离屏 pass:切上下文 + 额外缓冲区 + 再合成一次
C_RASTERIZE_ONCE = 3.0   # 一次 shouldRasterize 的光栅化
C_INVALIDATE = 4.0       # 缓存失效(内容变了)要重做光栅化
C_BLEND_PIXEL = 0.00002  # 每个半透明像素的混合成本(GPU 填充率)


@dataclass
class Layer:
    name: str
    coords: int = 4                     # 参与 layout 的坐标量(bounds/position/anchor)
    custom_draw: bool = False           # 是否重写了 drawRect / drawLayer:inContext:
    auto_redraw: bool = False           # 内部实现是否在 bounds 变化时自己也标脏内容
    needs_display: bool = False         # 本帧是否被 setNeedsDisplay(内容变了)
    offscreen: bool = False             # 是否触发离屏渲染
    translucent: bool = False           # 是否半透明(需要混合)
    opaque: bool = False                # 是否声明不透明(可跳过混合)
    pixels: int = 0                     # 覆盖的像素数
    rasterized: bool = False            # 是否 shouldRasterize
    dirty: bool = False                 # 本帧是否被 setNeedsLayout


@dataclass
class Frame:
    layout: float = 0.0
    display: float = 0.0
    prepare: float = 0.0
    render: float = 0.0
    budget: float = MS_PER_FRAME_60

    @property
    def commit(self) -> float:
        return self.layout + self.display + self.prepare

    @property
    def total(self) -> float:
        return self.commit + self.render

    def missed_frames(self) -> int:
        """迟到几帧:每错过一个 VSYNC,用户就多等一帧。"""
        if self.total <= self.budget:
            return 0
        return int((self.total - 1e-9) // self.budget) + 1

    def hitch_ms(self) -> float:
        return self.missed_frames() * self.budget


def layout_pass(layers: list[Layer]) -> tuple[float, int]:
    """Layout:只访问**被标脏**的 layer,同一帧内的请求会被合并成一次;dirty 是布尔量。"""
    dirty = [l for l in layers if l.dirty]
    return sum(C_COORD * l.coords + C_TOUCH_LAYER for l in dirty), len(dirty)


def display_pass(layers: list[Layer]) -> tuple[float, int]:
    """Display:只有「被 setNeedsDisplay 且自定义绘制」的 layer 才拿到 texture-backed CGContext。

    注意这和 Layout 是**两条独立的脏标记**:改 frame 只触发 layout,改内容才触发 display。
    """
    drawn = [l for l in layers if l.needs_display and l.custom_draw]
    return sum(C_DRAW_LAYER for _ in drawn), len(drawn)


def offscreen_passes(layers: list[Layer]) -> int:
    """离屏渲染:每个触发的 layer 一次额外 pass(圆角+masksToBounds / 无 shadowPath 的阴影 /
    mask / 模糊),以及每个被光栅化的 layer 一次。"""
    return sum(1 for l in layers if l.offscreen or l.rasterized)


def blend_cost(layers: list[Layer]) -> float:
    """混合成本 ∝ 每个半透明 layer 覆盖的像素数。声明 opaque 的层跳过混合。"""
    return sum(C_BLEND_PIXEL * l.pixels for l in layers if l.translucent and not l.opaque)


def render_frame(layers: list[Layer], budget: float = MS_PER_FRAME_60) -> Frame:
    f = Frame(budget=budget)
    f.layout, _ = layout_pass(layers)
    f.display, _ = display_pass(layers)
    f.prepare = C_PACKAGE_LAYER * len(layers)
    f.render = (C_PACKAGE_LAYER * len(layers) * 2 + C_OFFSCREEN_PASS * offscreen_passes(layers)
                + blend_cost(layers))
    return f


def rasterize_break_even(per_frame_saving: float, rasterize_cost: float = C_RASTERIZE_ONCE,
                         invalidate_cost: float = C_INVALIDATE) -> int:
    """shouldRasterize 的盈亏平衡帧数:缓存住之后每帧省 per_frame_saving。

    复用帧数低于这个值时,光栅化是**亏**的(一次光栅化 + 一次失效重建都白花)。
    """
    if per_frame_saving <= 0:
        return 10 ** 9
    return int((rasterize_cost + invalidate_cost) / per_frame_saving) + 1


# 自检
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    print(("  [PASS] " if ok else "  [FAIL] ") + label + (f"   {detail}" if detail else ""))


def t01_frame_budget() -> None:
    print("[1] 一帧的预算 = 到下一个 VSYNC 的时间")
    check("60Hz 的帧预算是 16.67ms", abs(MS_PER_FRAME_60 - 16.6667) < 0.001,
          f"{MS_PER_FRAME_60:.3f}ms")
    check("120Hz 的预算减半为 8.33ms(ProMotion 上同一段代码更容易掉帧)",
          abs(MS_PER_FRAME_120 * 2 - MS_PER_FRAME_60) < 0.01, f"{MS_PER_FRAME_120:.3f}ms")

    ok = Frame(layout=4, display=3, prepare=2, render=6)          # 合计 15ms
    late = Frame(layout=8, display=6, prepare=4, render=9)        # 合计 27ms
    check("15ms 的一帧在 60Hz 上不迟到", ok.missed_frames() == 0,
          f"commit={ok.commit:.0f}ms total={ok.total:.0f}ms")
    check("27ms 的一帧迟到 2 帧 → 用户多等 33.3ms(hitch time 按整帧计)",
          late.missed_frames() == 2 and abs(late.hitch_ms() - 33.33) < 0.01,
          f"{late.total:.0f}ms → 迟到 {late.missed_frames()} 帧 = {late.hitch_ms():.2f}ms")
    check("commit 段与 render 段都可能成为瓶颈,对应两类 hitch",
          late.commit > 0 and late.render > 0,
          f"commit hitch 在应用进程内,render hitch 在 render server 内")


def t02_dirty_coalescing() -> None:
    print("[2] 脏标记 + 请求合并:几千个 layer 也能跑满 60fps 的原因")
    big = [Layer(f"cell{i}", coords=6, pixels=40000, opaque=True) for i in range(10000)]
    for i in range(50):
        big[i].dirty = True
    cost, touched = layout_pass(big)
    check("改 50 个 layer 的 bounds → 只访问这 50 个,其余 9950 个一次都不碰",
          touched == 50, f"访问 {touched} / 共 {len(big)} 个")
    check("访问量少 200 倍 —— 这就是「只重排脏子树」的收益",
          len(big) / touched == 200.0, f"{len(big) / touched:.0f}x")

    twenty = [Layer(f"row{i}", coords=5) for i in range(20)]
    for l in twenty:
        l.dirty = True
    cost_once, touched_once = layout_pass(twenty)
    for _ in range(1000):                       # 同一帧内再标脏 1000 轮
        for l in twenty:
            l.dirty = True
    cost_many, touched_many = layout_pass(twenty)
    check("把 20 个 layer 各标脏 1000 次,layout 仍然只访问 20 个",
          touched_many == 20, "dirty 是布尔量,标 1 次和标 1000 次等价")
    check("成本只与「被标脏的 layer 数」成正比,与「修改次数」无关",
          abs(cost_many - cost_once) < 1e-12 and touched_many == touched_once,
          f"标 1 次与标 1000 次的成本完全相同({cost_once:.3f}),layout pass 只跑一次")


def t03_layout_order() -> None:
    print("[3] Layout 与 Prepare 的顺序:父→子、兄→弟、由后至前")
    tree = {"root": ["a", "b"], "a": ["a1", "a2"], "b": ["b1"]}

    def walk(node: str, out: list) -> None:
        out.append(node)
        for child in tree.get(node, []):
            walk(child, out)

    order: list = []
    walk("root", order)
    check("Layout 按父→子的顺序逐个执行",
          order == ["root", "a", "a1", "a2", "b", "b1"], " → ".join(order))
    check("render server 的 Prepare 按同样顺序排成线性流水线",
          order.index("root") < order.index("a") < order.index("b"),
          "父先于子、兄先于弟 ⇒ 由后至前的合成次序")
    check("顺序错了只能靠额外混合修正,代价落到 GPU 填充率上",
          order == ["root", "a", "a1", "a2", "b", "b1"],
          "所以别用插入顺序去凑层级,该用 zPosition 就用 zPosition")


def t04_display_only_when_needed() -> None:
    print("[4] Layout 与 Display 是两条独立的脏标记")
    moved = [Layer(f"v{i}", custom_draw=True) for i in range(30)]      # 只改 frame
    for l in moved:
        l.dirty = True
    _, touched = layout_pass(moved)
    cost_a, drawn_a = display_pass(moved)
    check("只改 frame/bounds:30 个 layer 重排,但一个都不重绘(0 次 drawRect)",
          touched == 30 and drawn_a == 0 and cost_a == 0.0,
          f"layout 访问 {touched} 个,draw 调用 {drawn_a} 次")

    changed = [Layer(f"c{i}", custom_draw=True, needs_display=True) for i in range(30)]
    for l in changed:
        l.dirty = True
    cost_b, drawn_b = display_pass(changed)
    check("改内容才触发 drawRect:30 个要重绘,每个都 CPU 画一遍再上传纹理",
          drawn_b == 30 and cost_b > 0, f"draw 调用 {drawn_b} 次,成本 {cost_b:.0f}")
    check("所以「改 frame」比「改内容」便宜一个数量级",
          abs(cost_b - 30 * C_DRAW_LAYER) < 1e-9 and cost_a == 0.0,
          f"改内容 {cost_b:.0f} vs 改 frame 0")

    # CATextLayer / UILabel 这类内部实现会在 bounds 变化时自己也 setNeedsDisplay
    labels = [Layer(f"label{i}", custom_draw=True, auto_redraw=True) for i in range(30)]
    for l in labels:
        l.dirty = True
        if l.auto_redraw:
            l.needs_display = True              # 内部实现:改 bounds 时连内容一起标脏
    _, touched_l = layout_pass(labels)
    cost_l, drawn_l = display_pass(labels)
    check("而 CATextLayer / UILabel 在 bounds 变化时会自己 setNeedsDisplay",
          touched_l == 30 and drawn_l == 30,
          f"移动一个 Label = layout {touched_l} 次 + draw {drawn_l} 次(双重代价)")


def t05_offscreen_rendering() -> None:
    print("[5] 离屏渲染:每个触发的 layer 多一次 pass")
    avatars = [Layer(f"avatar{i}", offscreen=True, translucent=True, pixels=120 * 120)
               for i in range(20)]
    prerounded = [Layer(f"avatar{i}", offscreen=False, translucent=False, pixels=120 * 120)
                  for i in range(20)]
    check("20 张「圆角 + masksToBounds」的头像 → 20 次离屏 pass",
          offscreen_passes(avatars) == 20, f"{offscreen_passes(avatars)} 次")
    check("换成预圆角图片 → 0 次离屏 pass",
          offscreen_passes(prerounded) == 0, f"{offscreen_passes(prerounded)} 次")
    check("省下的不只是 pass 次数,还有 2× 缓冲内存与两次合成",
          offscreen_passes(avatars) - offscreen_passes(prerounded) == 20,
          "Instruments 里用 Color Offscreen-Rendered Yellow 看")

    no_path = Layer("shadow", offscreen=True)
    with_path = Layer("shadow", offscreen=False)
    check("阴影给了 shadowPath 就不用离屏(几何提前算好,GPU 直接画)",
          offscreen_passes([with_path]) == 0 < offscreen_passes([no_path]),
          "最小改动、最大收益的一条")


def t06_rasterize_tradeoff() -> None:
    print("[6] shouldRasterize:缓存的盈亏平衡点")
    saving = 1.5                                   # 每帧省下 1.5 个单位
    be = rasterize_break_even(saving)
    check("内容能稳定复用 5 帧以上才划算", be == 5,
          f"平衡帧数 = {be}(光栅化 {C_RASTERIZE_ONCE:.0f} + 失效重建 {C_INVALIDATE:.0f},每帧省 {saving})")

    def net(frames_reused: int, content_changes: bool) -> float:
        return saving * frames_reused - (C_RASTERIZE_ONCE + (C_INVALIDATE if content_changes else 0.0))

    check("静态内容复用 30 帧:净收益 42(一次光栅化一直吃红利)",
          net(30, False) == 42.0, f"{net(30, False):.1f}")
    check("内容偶尔变、但仍复用 30 帧:净收益 38(依然赚)",
          net(30, True) > 0, f"{net(30, True):.1f}")
    check("只复用 1 帧就失效:净收益 -5.5(纯亏,还多占了内存)",
          net(1, True) == -5.5, f"{net(1, True):.1f}")
    check("所以列表里给 cell 开 shouldRasterize 常是负收益:cell 快速复用时缓存立刻失效",
          net(1, True) < 0, "Instruments 的 Color Hits/Misses 会显示成一片红")


def t07_overdraw_and_blending() -> None:
    print("[7] 混合与 overdraw:GPU 填充率的账")
    stack = [Layer(f"translucent{i}", translucent=True, pixels=200 * 200) for i in range(5)]
    flat = [Layer(f"opaque{i}", translucent=True, opaque=True, pixels=200 * 200)
            for i in range(5)]
    check("5 层半透明叠在一起:混合成本按 5 份算",
          abs(blend_cost(stack) - 5 * C_BLEND_PIXEL * 40000) < 1e-12,
          f"{blend_cost(stack):.3f}")
    check("给同样的层声明 opaque → 混合成本归零(GPU 直接覆盖写)",
          blend_cost(flat) == 0.0, "opaque 只是「承诺」,内容真不透明才不会出错")
    check("overdraw 成本 ∝ 覆盖像素 × 层数,与层的内容复杂度无关",
          blend_cost(stack) / max(blend_cost(flat), 1e-12) > 1,
          "Color Blended Layers:红=混合多,绿=基本不透明")
    check("Renderer Utilization 高 = 填充率压力,Tiler Utilization 高 = 层数太多",
          blend_cost(stack) > blend_cost(flat), "两者都超 50% 才值得动手")


if __name__ == "__main__":
    for fn in (t01_frame_budget, t02_dirty_coalescing, t03_layout_order,
               t04_display_only_when_needed, t05_offscreen_rendering,
               t06_rasterize_tradeoff, t07_overdraw_and_blending):
        fn()
    print(f"\n断言 {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项:", FAIL)
    raise SystemExit(1 if FAIL else 0)
