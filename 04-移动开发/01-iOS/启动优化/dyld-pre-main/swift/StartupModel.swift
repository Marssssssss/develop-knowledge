// StartupModel.swift —— pre-main 四阶段的成本模型 + Swift 与 ObjC 的关键差异
//
// 权威依据(完整引用见同目录 README「参考资料」):WWDC 2016 Session 406 把 main() 之前的
// 加载分成四段 —— Load dylibs / Rebase+Bind / ObjC setup / Initializers。
//
// 与 ObjC 的最大差异(下节 probe 会实测):
//   ObjC 的 `+load` 会在 pre-main 阶段被**无条件执行**,哪怕这个类根本用不上;
//   Swift 的全局常量与静态成员一律**懒初始化**,而且由 dispatch_once 保证只求值一次。
//   Swift Blog《Files and Initialization》的原话:
//   "startup time in Swift scales cleanly with no global initializers to slow it down"。

import Foundation

// MARK: - 成本模型(系数为合成值,只保留量级关系)

struct App {
    var dylibs = 180
    var symbols = 9000
    var globalPointers = 26000
    var classes = 1200
    var selectorRefs = 34000
    var categories = 260
    var loadMethods = 210
    var ctors = 90
    var staticGlobals = 130
    var launchPathPages = 4200
    var lazyInitialized = 0

    /// selector 去重后剩下的唯一 selector 数:引用 34000 次 ≠ 34000 个不同 selector
    var uniqueSelectors: Int { max(1, Int(Double(selectorRefs).squareRoot() * 2 + Double(classes) * 0.8)) }
}

enum Cost {
    static let dylibFixed = 12.0
    static let dylibSymbol = 0.05
    static let rebasePtr = 0.02
    static let perClass = 0.6
    static let selectorRef = 0.004
    static let selectorUnique = 0.3
    static let category = 0.5
    static let loadBody = 3.0
    static let ctor = 1.2
    static let staticGlobal = 1.6
    static let pageFault = 0.05
}

struct Phases {
    var loadDylibs = 0.0
    var rebaseBind = 0.0
    var objcSetup = 0.0
    var initializers = 0.0
    var total: Double { loadDylibs + rebaseBind + objcSetup + initializers }
}

func measure(_ a: App) -> Phases {
    Phases(
        loadDylibs: Cost.dylibFixed * Double(a.dylibs),
        rebaseBind: Cost.rebasePtr * Double(a.globalPointers) + Cost.dylibSymbol * Double(a.symbols),
        objcSetup: Cost.perClass * Double(a.classes) + Cost.category * Double(a.categories)
            + Cost.selectorRef * Double(a.selectorRefs) + Cost.selectorUnique * Double(a.uniqueSelectors),
        initializers: Cost.loadBody * Double(a.loadMethods) + Cost.ctor * Double(a.ctors)
            + Cost.staticGlobal * Double(a.staticGlobals)
    )
}

/// 缺页成本:**不在**上面四段里,DYLD_PRINT_STATISTICS 也看不到它。
func pageFaultCost(_ a: App) -> Double { Cost.pageFault * Double(a.launchPathPages) }

/// DYLD_PRINT_STATISTICS 那种"分行打印四段耗时"的东西。
func report(_ title: String, _ p: Phases) -> String {
    let body = String(format: "Load dylibs=%.0f  Rebase/Bind=%.0f  ObjC setup=%.0f  Initializers=%.0f",
                      p.loadDylibs, p.rebaseBind, p.objcSetup, p.initializers)
    return "\(title.padding(toLength: 22, withPad: " ", startingAt: 0))\(body)   合计=\(Int(p.total))"
}

// MARK: - 断言

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

// MARK: - Swift 侧的 lazy 探针

final class InitProbe: @unchecked Sendable {
    private(set) var globalHits = 0
    private(set) var typeHits = 0
    func bumpGlobal() { globalHits += 1 }
    func bumpType() { typeHits += 1 }
}

/// 全局常量 —— 同样是懒初始化的:这个闭包在首次访问 `probe` 之外的地方被读到之前不会运行。
let probe = InitProbe()

/// 全局常量:Swift 保证「懒 + 只求值一次 + 线程安全」(底层是 dispatch_once)。
let expensiveGlobal: String = {
    probe.bumpGlobal()
    return "global 已初始化"
}()

enum Config {
    /// 存储型类型属性:同样懒,同样只初始化一次,不需要 lazy 修饰符。
    static let expensive: String = {
        probe.bumpType()
        return "static let 已初始化"
    }()
}
