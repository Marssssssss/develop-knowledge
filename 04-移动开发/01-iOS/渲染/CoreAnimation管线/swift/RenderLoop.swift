// RenderLoop.swift —— 渲染循环模型(与 python/render_loop_check.py 同一套系数)
//
// 权威依据(完整引用见同目录 README「参考资料」):Apple Tech Talk
// 《Explore UI animation hitches and the render loop》给出的 Render Loop 是
// Event → Commit(应用进程内:Layout → Display → Prepare)→ render server →
// GPU 合成 → 下一个 VSYNC 上屏。deadline 永远是**下一个 VSYNC**;
// 错过即 hitch,1 帧 = 16.67ms、2 帧 = 33.34ms,分 commit hitch 与 render hitch 两类。
//
// 真实的 CALayer 实验(数 drawInContext: 被调用的次数)在 objc/RenderTraps.m 里,
// 这一个是可断言的成本模型 —— 系数是合成值,只保留各阶段的形状与量级关系。

import Foundation

let msPerFrame60 = 1000.0 / 60.0     // 16.667ms
let msPerFrame120 = 1000.0 / 120.0   // 8.333ms

enum Cost {
    static let coord = 0.003         // 每个坐标计算
    static let touchLayer = 0.08     // layout pass 里访问一个 layer
    static let drawLayer = 1.4       // 一个 layer 画一遍(CPU + 上传纹理)
    static let packageLayer = 0.05   // Prepare 阶段打包一个 layer
    static let offscreenPass = 2.2   // 一次离屏 pass
    static let rasterizeOnce = 3.0   // 一次 shouldRasterize
    static let invalidate = 4.0      // 缓存失效要重做光栅化
    static let blendPixel = 0.00002  // 每个半透明像素的混合成本
}

struct Layer {
    var name: String
    var coords = 4
    var customDraw = false
    var autoRedraw = false           // 内部实现在 bounds 变化时自己也标脏内容
    var needsDisplay = false
    var offscreen = false
    var translucent = false
    var opaque = false
    var pixels = 0
    var rasterized = false
    var dirty = false
}

struct Frame {
    var layout = 0.0, display = 0.0, prepare = 0.0, render = 0.0
    var budget = msPerFrame60
    /// Commit = Layout + Display + Prepare,全部在应用进程内
    var commit: Double { layout + display + prepare }
    var total: Double { commit + render }
    /// 迟到几帧:每错过一个 VSYNC,用户就多等一帧
    var missedFrames: Int { total <= budget ? 0 : Int((total - 1e-9) / budget) + 1 }
    var hitchMs: Double { Double(missedFrames) * budget }
}

/// Layout:只访问**被标脏**的 layer;dirty 是布尔量,标 1 次与标 1000 次等价。
func layoutPass(_ layers: [Layer]) -> (cost: Double, touched: Int) {
    let dirty = layers.filter { $0.dirty }
    return (dirty.reduce(0.0) { $0 + Cost.coord * Double($1.coords) + Cost.touchLayer }, dirty.count)
}

/// Display:只有「被 setNeedsDisplay 且自定义绘制」的 layer 才拿到 texture-backed CGContext。
func displayPass(_ layers: [Layer]) -> (cost: Double, drawn: Int) {
    let drawn = layers.filter { $0.needsDisplay && $0.customDraw }
    return (Double(drawn.count) * Cost.drawLayer, drawn.count)
}

func offscreenPasses(_ layers: [Layer]) -> Int {
    layers.filter { $0.offscreen || $0.rasterized }.count
}

func blendCost(_ layers: [Layer]) -> Double {
    layers.filter { $0.translucent && !$0.opaque }
        .reduce(0.0) { $0 + Cost.blendPixel * Double($1.pixels) }
}

func renderFrame(_ layers: [Layer], budget: Double = msPerFrame60) -> Frame {
    let (layout, _) = layoutPass(layers)
    let (display, _) = displayPass(layers)
    var f = Frame()
    f.budget = budget
    f.layout = layout
    f.display = display
    f.prepare = Cost.packageLayer * Double(layers.count)
    f.render = Cost.packageLayer * Double(layers.count) * 2
        + Cost.offscreenPass * Double(offscreenPasses(layers)) + blendCost(layers)
    return f
}

/// shouldRasterize 的盈亏平衡帧数:复用帧数低于它时,光栅化是**亏**的。
func rasterizeBreakEven(perFrameSaving: Double,
                        rasterizeCost: Double = Cost.rasterizeOnce,
                        invalidateCost: Double = Cost.invalidate) -> Int {
    perFrameSaving <= 0 ? Int.max : Int((rasterizeCost + invalidateCost) / perFrameSaving) + 1
}

final class Check: @unchecked Sendable {
    private(set) var passed = 0
    private(set) var failures: [String] = []

    func section(_ t: String) { print(t) }

    func that(_ label: String, _ ok: Bool, _ detail: String = "") {
        if ok { passed += 1; print("  [PASS] \(label)\(detail.isEmpty ? "" : "   \(detail)")") }
        else { failures.append(label); print("  [FAIL] \(label)\(detail.isEmpty ? "" : "   \(detail)")") }
    }

    func summarize() -> Int32 {
        print("\n断言 \(passed) 通过 / \(failures.count) 失败")
        if !failures.isEmpty { print("失败项:", failures) }
        return failures.isEmpty ? 0 : 1
    }
}
