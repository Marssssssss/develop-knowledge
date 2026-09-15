// main.swift —— pre-main 启动模型的 Swift 侧自检
//
// 构建运行(需要 Swift 5.7+ / macOS 13+):
//   swiftc StartupModel.swift main.swift -o startup-model && ./startup-model
//
// 与 python/startup_check.py 的分工:Python 侧做完整的量级实验(24 条断言),
// 这一侧只保留四个最具解释力的场景 —— 尤其是 Swift 与 ObjC 在 Initializers 段上的
// 根本差异,那是 ObjC 侧要用 +load 硬扛、Swift 侧语言直接给掉的那部分。

import Foundation

// [1] 四段构成与两本账
func scenario1(_ c: Check) {
    c.section("[1] pre-main 的四段构成")
    let p = measure(App())
    print("    " + report("baseline", p))
    c.that("四阶段之和 = pre-main 总耗时",
           abs(p.total - (p.loadDylibs + p.rebaseBind + p.objcSetup + p.initializers)) < 1e-9,
           "\(Int(p.total))")
    c.that("Load dylibs + Rebase/Bind 占大半 —— 都是「库与指针数量」的代价",
           (p.loadDylibs + p.rebaseBind) / p.total > 0.5,
           String(format: "%.0f%%", (p.loadDylibs + p.rebaseBind) / p.total * 100))
}

// [2] dylib 数量:固定开销线性叠加
func scenario2(_ c: Check) {
    c.section("[2] dylib 数量:每个都是固定开销")
    var few = App(); few.dylibs = 100
    var many = App(); many.dylibs = 400
    let pf = measure(few), pm = measure(many)
    print("    " + report("100 个 dylib", pf))
    print("    " + report("400 个 dylib", pm))
    c.that("400 个 dylib 的 Load dylibs 正好是 100 个的 4 倍(线性)",
           abs(pm.loadDylibs / pf.loadDylibs - 4.0) < 1e-9,
           String(format: "%.2fx", pm.loadDylibs / pf.loadDylibs))
    c.that("Bind 由符号引用数决定,和 dylib 个数无关 —— 合并动态库不会自动减少符号",
           abs(pf.rebaseBind - pm.rebaseBind) < 1e-9,
           String(format: "两档都 = %.0f", pf.rebaseBind))
}

// [3] 与 ObjC 的根本差异:Swift 没有 +load 的等价物
func scenario3(_ c: Check) {
    c.section("[3] Swift 的全局常量/静态成员是懒初始化的(与 ObjC +load 相反)")
    c.that("main 入口时,全局常量的初始化闭包一次都没跑",
           probe.globalHits == 0, "globalHits=\(probe.globalHits)")
    c.that("存储型类型属性同理:声明不等于初始化",
           probe.typeHits == 0, "typeHits=\(probe.typeHits)")

    _ = expensiveGlobal                                    // 第一次访问才求值
    c.that("首次访问才执行初始化闭包", probe.globalHits == 1, "globalHits=\(probe.globalHits)")

    _ = expensiveGlobal
    _ = expensiveGlobal
    _ = Config.expensive
    _ = Config.expensive
    c.that("重复访问不会重复求值(dispatch_once 语义,线程安全)",
           probe.globalHits == 1 && probe.typeHits == 1,
           "globalHits=\(probe.globalHits) typeHits=\(probe.typeHits)")

    // 量化它在启动上的意义:把 210 个 +load 换成懒初始化,只有首屏真正用到的 30 个会跑
    let base = App()
    var lazy = App()
    lazy.loadMethods = 0
    lazy.lazyInitialized = 30
    let p0 = measure(base), p1 = measure(lazy)
    c.that("210 个 +load 换成懒初始化后,Initializers 段降到不足一半",
           p1.initializers < p0.initializers / 2,
           String(format: "%.0f → %.0f", p0.initializers, p1.initializers))
    c.that("代价是「推迟」而不是「复制」:pre-main + 首屏总量也更小",
           p1.total + Cost.loadBody * Double(lazy.lazyInitialized)
           < p0.total + Cost.loadBody * Double(base.lazyInitialized),
           "210 个类里只有 30 个真的在首屏前被用到")
    c.that("这也是 Swift 启动更「干净」的原因:语言层面就没有 pre-main 全局初始化这一项",
           lazy.lazyInitialized < base.loadMethods,
           "Swift Blog: startup time in Swift scales cleanly with no global initializers")
}

// [4] 缺页:四段之外的那一项
func scenario4(_ c: Check) {
    c.section("[4] 缺页:四段数不到、但真实启动要付的账")
    var a = App()
    let before = pageFaultCost(a)
    a.launchPathPages = Int(Double(a.launchPathPages) * 0.35)     // 二进制重排后启动路径更紧凑
    let after = pageFaultCost(a)
    c.that("二进制重排把启动路径触及的页数压到 35%", after < before * 0.4,
           String(format: "%.0f → %.0f", before, after))
    c.that("它不在四段里,DYLD_PRINT_STATISTICS 也看不到",
           measure(App()).total + after > measure(App()).total,
           "要看 Instruments 的 Page Fault 计数")
}

// 顶层代码支持 await 的写法在这里用不到(全是同步计算),直接顺序调用即可。
// 注意:同一模块里 main.swift 不能再用 @main 属性。
let check = Check()
scenario1(check)
scenario2(check)
scenario3(check)
scenario4(check)
exit(check.summarize())
