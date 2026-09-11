// SwiftARC.swift
// Swift ARC 内存管理 —— 引用计数 + strong/weak/unowned + 闭包捕获列表
//
// 编译/运行:
//   swift SwiftARC.swift
//
// 来源(权威,见 README 参考资料):
//   1. Swift Book - Automatic Reference Counting (docs.swift.org)
//   2. WWDC 2021 #10216 "ARC in Swift: Basics and beyond"
//   3. Apple Transitioning to ARC Release Notes (Objective-C 侧 ARC 历史)
//
// 本 demo 4 个用例覆盖:
//   1. 基本引用计数(retain/release 等价行为,用 CFGetRetainCount 验证)
//   2. 类间强引用循环(Person/Apartment) + weak 打破循环
//   3. 类间强引用循环(Customer/CreditCard) + unowned 打破循环
//   4. 闭包与 self 的循环引用 + 捕获列表 [weak self] 解决
//
// 所有 class 实例的 deinit 用 print 标记,可观察 ARC 实际回收时机。

import Foundation

// =============================================================================
// 用例 1 — 基本引用计数(Swift 侧 retainCount 可观察)
// =============================================================================

class Person1 {
    let name: String
    init(name: String) { self.name = name; print("[1] Person1 \(name) init") }
    deinit { print("[1] Person1 \(name) deinit") }
}

// =============================================================================
// 用例 2 — 类间强引用循环: Person <-> Apartment(双方都可为 nil)
// =============================================================================

class Person2 {
    let name: String
    var apartment: Apartment2?
    init(name: String) { self.name = name; print("[2] Person2 \(name) init") }
    deinit { print("[2] Person2 \(name) deinit") }
}

class Apartment2 {
    let unit: String
    weak var tenant: Person2?     // weak: 租客先走,租客可为 nil
    init(unit: String) { self.unit = unit; print("[2] Apartment2 \(unit) init") }
    deinit { print("[2] Apartment2 \(unit) deinit") }
}

// =============================================================================
// 用例 3 — 类间强引用循环: Customer -> CreditCard(单向,CreditCard.customer 非可选)
// =============================================================================

class Customer3 {
    let name: String
    var card: CreditCard3?
    init(name: String) { self.name = name; print("[3] Customer3 \(name) init") }
    deinit { print("[3] Customer3 \(name) deinit") }
}

class CreditCard3 {
    let number: UInt64
    unowned let customer: Customer3   // unowned: 卡必属客户,客户寿命 ≥ 卡
    init(number: UInt64, customer: Customer3) {
        self.number = number
        self.customer = customer
        print("[3] CreditCard3 #\(number) init")
    }
    deinit { print("[3] CreditCard3 #\(number) deinit") }
}

// =============================================================================
// 用例 4 — 闭包与 self 的循环引用(HTMLElement 风格,asHTML 闭包捕获 self)
// =============================================================================

class HTMLElement4 {
    let name: String
    let text: String?

    // 闭包与 self 互相强引用 -> 循环
    lazy var asHTMLStrongRef: () -> String = {
        return "<\(self.name)>\(self.text ?? "")</\(self.name)>"
    }

    // 用 [weak self] 捕获列表打破循环
    lazy var asHTMLWeakSelf: () -> String = {
        // guard let self = self else { return "<deinit>" }  // Swift 5.7+ 写法
        // 这里用经典写法以兼容 Swift 5.0~5.6
        return "<\(self.name)>\(self.text ?? "")</\(self.name)>"
    } // <-- 注:Swift 5.7 前,此处需显式 [weak self];见下方 setWeakSelfClosure()

    init(name: String, text: String? = nil) {
        self.name = name
        self.text = text
        print("[4] HTMLElement4 <\(name)> init")
    }
    deinit { print("[4] HTMLElement4 <\(name)> deinit") }

    // 显式重新设定为 [weak self] 版本,以便 demo 看到释放
    func setWeakSelfClosure() {
        asHTMLWeakSelf = { [weak self] in
            guard let self = self else { return "<deinit>" }
            return "<\(self.name)>\(self.text ?? "")</\(self.name)>"
        }
    }
}

// =============================================================================
// 主入口
// =============================================================================

func demo1_basicRefcount() {
    print("\n===== 用例 1: 基本引用计数 =====")
    let p1 = Person1(name: "Alice")
    let p2 = p1                  // 强引用 +1 (编译器插入 retain)
    let p3 = p1                  // 强引用 +1
    print("retainCount(p1) = \(CFGetRetainCount(p1))")   // 期望: 3
    p3 = nil                     // 编译器插入 release
    print("retainCount(p1) = \(CFGetRetainCount(p1))")   // 期望: 2
    p2 = nil
    print("retainCount(p1) = \(CFGetRetainCount(p1))")   // 期望: 1
    p1 = nil                     // 计数 → 0,触发 deinit
}

func demo2_weakBreaksCycle() {
    print("\n===== 用例 2: weak 打破 Person/Apartment 强引用循环 =====")
    let john: Person2? = Person2(name: "John")
    let unit4A: Apartment2? = Apartment2(unit: "4A")
    john!.apartment = unit4A
    unit4A!.tenant = john        // 弱引用,不增加 john 的 retainCount
    print("after wiring, john ref count = \(CFGetRetainCount(john!))")  // 期望: 1 (john 局部变量)
    print("after wiring, apt  ref count = \(CFGetRetainCount(unit4A!))") // 期望: 1 (局部变量)
    // 释放局部变量,john 和 unit4A 引用计数 → 0,二者都 deinit(无循环)
}

func demo3_unownedBreaksCycle() {
    print("\n===== 用例 3: unowned 打破 Customer/CreditCard 强引用循环 =====")
    let alice: Customer3? = Customer3(name: "Alice")
    alice!.card = CreditCard3(number: 1234_5678_9012_3456, customer: alice!)
    print("alice ref count = \(CFGetRetainCount(alice!))")    // 期望: 1 (alice 局部变量)
    print("card  ref count = \(CFGetRetainCount(alice!.card!))") // 期望: 1 (alice.card 局部变量)
    // 释放 alice,creditCard 的 unowned customer 已无效但本 demo 不再访问
}

func demo4_closureCaptureList() {
    print("\n===== 用例 4: 闭包捕获列表 [weak self] 打破循环 =====")
    var paragraph: HTMLElement4? = HTMLElement4(name: "p", text: "hello, world")
    paragraph!.setWeakSelfClosure()
    // 调用 setWeakSelfClosure 后,asHTMLWeakSelf 是 [weak self] 版,不会循环
    // (asHTMLStrongRef 仍是默认的强引用版本,故意保留用以对照)
    print("asHTMLWeakSelf() = \(paragraph!.asHTMLWeakSelf())")
    paragraph = nil
    // HTMLElement4 析构一次 (weak self 版闭包不持有 self)
    // asHTMLStrongRef 因 lazy 尚未求值,因此未真正产生强引用
    // 此 demo 重点在于 lazy 闭包若先被求值就会形成循环
}

func demo5_lazyStrongRefCycle() {
    print("\n===== 用例 5(对照): lazy 强引用闭包触发循环 =====")
    var div: HTMLElement4? = HTMLElement4(name: "div", text: "x")
    _ = div!.asHTMLStrongRef      // 触发 lazy 求值,闭包强引用 self,自此循环
    print("after touching lazy, div ref count = \(CFGetRetainCount(div!))")  // 期望: 2 (局部变量 + 闭包)
    div = nil                     // 局部变量断开,但 lazy 闭包仍在闭包体内部持引用
    // 不会有 deinit 打印 —— self 被闭包强引用,循环形成
    print("(预期: div 未 deinit,因闭包持有 self 强引用形成循环)")
}

// =============================================================================
// 启动
// =============================================================================

demo1_basicRefcount()
demo2_weakBreaksCycle()
demo3_unownedBreaksCycle()
demo4_closureCaptureList()
demo5_lazyStrongRefCycle()
print("\n=== all demos done ===")