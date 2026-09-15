// main.swift —— 渲染循环模型的 Swift 侧自检
//
// 构建运行(需要 Swift 5.7+ / macOS 13+,纯 Foundation,不需要 UIKit):
//   swiftc RenderLoop.swift main.swift -o render-loop && ./render-loop
//
// 这里只保留四个最容易被忽略的场景;完整的 29 条断言在 python/render_loop_check.py,
// 真实 CALayer 上的 drawInContext: 计数实验在 objc/RenderTraps.m。

import Foundation

// [1] 一帧的预算
func scenario1(_ c: Check) {
    c.section("[1] 一帧的预算 = 到下一个 VSYNC 的时间")
    c.that("60Hz 的帧预算是 16.67ms", abs(msPerFrame60 - 16.6667) < 0.001,
           String(format: "%.3fms", msPerFrame60))
    c.that("120Hz 的预算减半为 8.33ms(ProMotion 上同一段代码更容易掉帧)",
           abs(msPerFrame120 * 2 - msPerFrame60) < 0.01,
           String(format: "%.3fms", msPerFrame120))

    var ok = Frame(); ok.layout = 4; ok.display = 3; ok.prepare = 2; ok.render = 6
    var late = Frame(); late.layout = 8; late.display = 6; late.prepare = 4; late.render = 9
    c.that("15ms 的一帧在 60Hz 上不迟到", ok.missedFrames == 0,
           String(format: "commit=%.0fms total=%.0fms", ok.commit, ok.total))
    c.that("27ms 的一帧迟到 2 帧 → 用户多等 33.3ms(hitch time 按整帧计)",
           late.missedFrames == 2 && abs(late.hitchMs - 33.33) < 0.01,
           String(format: "%.0fms → 迟到 %d 帧 = %.2fms", late.total, late.missedFrames, late.hitchMs))
    c.that("commit 段与 render 段都可能成为瓶颈,对应两类 hitch",
           late.commit > 0 && late.render > 0,
           "commit hitch 在应用进程内,render hitch 在 render server 内")
}

// [2] 脏标记:只重排/重绘脏的那个子树
func scenario2(_ c: Check) {
    c.section("[2] 脏标记 + 请求合并:几千个 layer 也能跑满 60fps 的原因")
    var big = (0..<10000).map { Layer(name: "cell\($0)", coords: 6, opaque: true, pixels: 40000) }
    for i in 0..<50 { big[i].dirty = true }
    let (_, touched) = layoutPass(big)
    c.that("改 50 个 layer 的 bounds → 只访问这 50 个,其余 9950 个一次都不碰",
           touched == 50, "访问 \(touched) / 共 \(big.count) 个")
    c.that("访问量少 200 倍", Double(big.count) / Double(touched) == 200.0,
           String(format: "%.0fx", Double(big.count) / Double(touched)))

    var twenty = (0..<20).map { Layer(name: "row\($0)", coords: 5) }
    for i in 0..<20 { twenty[i].dirty = true }
    let once = layoutPass(twenty)
    for _ in 0..<1000 { for i in 0..<20 { twenty[i].dirty = true } }   // 再标脏 1000 轮
    let many = layoutPass(twenty)
    c.that("把 20 个 layer 各标脏 1000 次,layout 仍然只访问 20 个",
           many.touched == 20, "dirty 是布尔量,标 1 次和标 1000 次等价")
    c.that("成本只与「被标脏的 layer 数」成正比,与「修改次数」无关",
           abs(many.cost - once.cost) < 1e-12 && many.touched == once.touched,
           String(format: "标 1 次与标 1000 次成本相同(%.3f)", once.cost))
}

// [3] Layout 与 Display 是两条独立的脏标记
func scenario3(_ c: Check) {
    c.section("[3] Layout 与 Display 是两条独立的脏标记")
    var moved = (0..<30).map { Layer(name: "v\($0)", customDraw: true) }
    for i in 0..<30 { moved[i].dirty = true }                     // 只改 frame
    let (_, touched) = layoutPass(moved)
    let (costA, drawnA) = displayPass(moved)
    c.that("只改 frame/bounds:30 个 layer 重排,但一个都不重绘(0 次绘制)",
           touched == 30 && drawnA == 0 && costA == 0.0,
           "layout 访问 \(touched) 个,绘制 \(drawnA) 次")

    var changed = (0..<30).map { Layer(name: "c\($0)", customDraw: true, needsDisplay: true) }
    for i in 0..<30 { changed[i].dirty = true }                   // 改内容
    let (costB, drawnB) = displayPass(changed)
    c.that("改内容才绘制:30 个都要 CPU 画一遍再上传纹理",
           drawnB == 30 && abs(costB - 30 * Cost.drawLayer) < 1e-9,
           String(format: "绘制 %d 次,成本 %.0f", drawnB, costB))
    c.that("所以「改 frame」比「改内容」便宜一个数量级", costA == 0 && costB > 0,
           String(format: "%.0f vs 0", costB))

    var labels = (0..<30).map { Layer(name: "label\($0)", customDraw: true, autoRedraw: true) }
    for i in 0..<30 {
        labels[i].dirty = true
        if labels[i].autoRedraw { labels[i].needsDisplay = true }   // 内部实现自己标脏内容
    }
    let (_, touchedL) = layoutPass(labels)
    let (_, drawnL) = displayPass(labels)
    c.that("而 CATextLayer / UILabel 在 bounds 变化时会自己 setNeedsDisplay",
           touchedL == 30 && drawnL == 30,
           "移动一个 Label = layout \(touchedL) 次 + 绘制 \(drawnL) 次(双重代价)")
}

// [4] 离屏 / 光栅化 / 混合:三个 GPU 侧的账
func scenario4(_ c: Check) {
    c.section("[4] 离屏渲染、shouldRasterize、混合")
    let avatars = (0..<20).map {
        Layer(name: "avatar\($0)", offscreen: true, translucent: true, pixels: 120 * 120)
    }
    let prerounded = (0..<20).map {
        Layer(name: "avatar\($0)", pixels: 120 * 120)
    }
    c.that("20 张「圆角 + masksToBounds」的头像 → 20 次离屏 pass",
           offscreenPasses(avatars) == 20, "\(offscreenPasses(avatars)) 次")
    c.that("换成预圆角图片 → 0 次离屏 pass", offscreenPasses(prerounded) == 0,
           "省下的还有 2× 缓冲内存与两次合成")

    let be = rasterizeBreakEven(perFrameSaving: 1.5)
    c.that("shouldRasterize 的盈亏平衡点是 5 帧(低于它纯亏)", be == 5, "平衡帧数 = \(be)")
    let net30 = 1.5 * 30 - (Cost.rasterizeOnce + Cost.invalidate)
    let net1 = 1.5 - (Cost.rasterizeOnce + Cost.invalidate)
    c.that("稳定复用 30 帧:净收益 38(赚)", net30 > 0, String(format: "%.1f", net30))
    c.that("只复用 1 帧就失效:净收益 -5.5(亏,还多占内存)", net1 < 0,
           "列表 cell 快速复用时缓存立刻失效 → 常是负收益")

    let stack = (0..<5).map { Layer(name: "t\($0)", translucent: true, pixels: 200 * 200) }
    let flat = (0..<5).map { Layer(name: "o\($0)", translucent: true, opaque: true, pixels: 200 * 200) }
    c.that("5 层半透明叠在一起的混合成本是 5 份", abs(blendCost(stack) - 5 * Cost.blendPixel * 40000) < 1e-12,
           String(format: "%.3f", blendCost(stack)))
    c.that("声明 opaque → 混合成本归零(GPU 直接覆盖写)", blendCost(flat) == 0.0,
           "opaque 只是「承诺」,内容真不透明才不会出错")

    let frame = renderFrame(avatars + prerounded + stack)
    c.that("真实一帧的成本由「脏层数 + 离屏 pass + 混合像素」共同决定",
           frame.total > 0 && frame.commit > 0,
           String(format: "commit=%.0f render=%.0f total=%.0f", frame.commit, frame.render, frame.total))
}

// 注意:同一模块里 main.swift 不能再写 @main,两者会冲突。
let check = Check()
scenario1(check)
scenario2(check)
scenario3(check)
scenario4(check)
exit(check.summarize())
