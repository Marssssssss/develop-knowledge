// SwiftWeak.swift — weak 弱引用底层实现最小复刻(demo 133,Swift 版)
// Swift 的 weak 与 Objective-C 共用同一套 objc runtime 副作用表(SideTable/weak_table);
// 本文件用纯 Swift 复刻:StripedMap 分桶 → weak_entry(inline 4 槽 → out-of-line)
// → register/unregister → 对象释放时 weak_clear 全部置 nil。

import Foundation

let stripeCount = 8          // iOS 真机 8 张表(macOS/模拟器 64)
let weakInlineCount = 4      // inline 槽数,超出转 out-of-line(此处统一用数组演示)

final class WObject {
    let name: String
    init(_ name: String) { self.name = name }
    deinit { print("    [dealloc] \(name)") }
}

// —— SideTable:自旋锁 + 引用计数表 + 弱引用表(简化) ——
final class SideTable {
    var refcnts: [ObjectIdentifier: Int] = [:]        // 引用计数
    var weakTable = WeakTable()
}
final class WeakEntry {
    let referent: ObjectIdentifier                     // 被弱引用对象
    var referrers: [UnsafeMutablePointer<WObject?>] = [] // weak 变量的"地址"
    init(referent: ObjectIdentifier) { self.referent = referent }
}
final class WeakTable {
    var entries: [ObjectIdentifier: WeakEntry] = [:]   // referent → entry 哈希
}

final class SideTables {
    static let shared = SideTables()
    var tables: [SideTable] = (0..<stripeCount).map { _ in SideTable() }
    // objc4 indexForPointer: ((addr >> 4) ^ (addr >> 9)) % StripeCount
    func table(for o: WObject) -> SideTable {
        let a = UInt(bitPattern: ObjectIdentifier(o).hashValue)
        return tables[Int(((a >> 4) ^ (a >> 9)) % UInt(stripeCount))]
    }
}

enum WeakRuntime {
    // objc_initWeak → storeWeak → weak_register_no_lock
    static func registerWeak(_ location: UnsafeMutablePointer<WObject?>, to new: WObject?) {
        guard let new else { location.pointee = nil; return }   // nil 直接置空
        let st = SideTables.shared.table(for: new)
        let id = ObjectIdentifier(new)
        let entry = st.weakTable.entries[id] ?? {
            let e = WeakEntry(referent: id); st.weakTable.entries[id] = e; return e
        }()
        entry.referrers.append(location)             // append_referrer(inline→out-of-line)
        location.pointee = new                        // 弱引用不加计数
        print("    [register] \(new.name) ← weak 变量 #\(entry.referrers.count - 1)")
    }

    // weak_unregister_no_lock:weak 变量改指向时解绑旧登记
    static func unregisterWeak(_ location: UnsafeMutablePointer<WObject?>, from old: WObject?) {
        guard let old else { return }
        let st = SideTables.shared.table(for: old)
        guard let entry = st.weakTable.entries[ObjectIdentifier(old)] else { return }
        entry.referrers.removeAll { $0 == location }
        print("    [unregister] weak 变量与 \(old.name) 解绑")
    }

    // dealloc → clearDeallocating → weak_clear_no_lock:所有登记的 weak 指针置 nil
    static func clearWeak(of obj: WObject) {
        let st = SideTables.shared.table(for: obj)
        let id = ObjectIdentifier(obj)
        guard let entry = st.weakTable.entries[id] else { return }
        print("    [weak_clear] \(obj.name) 释放,置空 \(entry.referrers.count) 个 weak 指针")
        for loc in entry.referrers { loc.pointee = nil }   // objc_object** 逐位置 nil
        st.weakTable.entries.removeValue(forKey: id)
    }
}

// —— 极简引用计数(alloc=1,retain+1,release 归零触发 weak_clear + deinit) ——
final class RefCount {
    static let shared = RefCount()
    private var counts: [ObjectIdentifier: (obj: WObject, n: Int)] = [:]
    func initCount(_ o: WObject) { counts[ObjectIdentifier(o)] = (o, 1) }
    func retain(_ o: WObject) { counts[ObjectIdentifier(o)]!.n += 1 }
    func release(_ o: WObject) {
        guard var c = counts[ObjectIdentifier(o)] else { return }
        c.n -= 1
        if c.n == 0 {
            counts.removeValue(forKey: ObjectIdentifier(o))
            WeakRuntime.clearWeak(of: o)               // 先清 weak,再释放内存
        } else {
            counts[ObjectIdentifier(o)] = c
        }
    }
}

// 模拟 __weak 变量:Swift 无法声明原始 weak 指针变量,用"变量槽 + 指针"表达
var slot1 = UnsafeMutablePointer<WObject?>.allocate(capacity: 1)
var slot2 = UnsafeMutablePointer<WObject?>.allocate(capacity: 1)
var slot3 = UnsafeMutablePointer<WObject?>.allocate(capacity: 1)
var slotB = UnsafeMutablePointer<WObject?>.allocate(capacity: 1)
defer { slot1.deallocate(); slot2.deallocate(); slot3.deallocate(); slotB.deallocate() }

func demo() {
    print("== 1. 对象 A 被 3 个 weak 变量引用 ==")
    let a = WObject("A"); RefCount.shared.initCount(a)
    WeakRuntime.registerWeak(slot1, to: a)
    WeakRuntime.registerWeak(slot2, to: a)
    WeakRuntime.registerWeak(slot3, to: a)
    RefCount.shared.retain(a)                          // 计数 2

    print("== 2. 对象 B 单独一个 weak ==")
    let b = WObject("B"); RefCount.shared.initCount(b)
    WeakRuntime.registerWeak(slotB, to: b)

    print("== 3. slot2 改指向 B:先 unregister 再 register ==")
    WeakRuntime.unregisterWeak(slot2, from: a)
    WeakRuntime.registerWeak(slot2, to: b)

    print("== 4. A 计数归零 → weak_clear 置空 slot1/slot3 ==")
    RefCount.shared.release(a); RefCount.shared.release(a)
    print("    slot1=\(slot1.pointee.map { $0.name } ?? "nil") slot3=\(slot3.pointee.map { $0.name } ?? "nil")(应为 nil)")

    print("== 5. B 计数归零 → slotB/slot2 置 nil ==")
    RefCount.shared.release(b)
    print("    slotB=\(slotB.pointee.map { $0.name } ?? "nil") slot2=\(slot2.pointee.map { $0.name } ?? "nil")(应为 nil)")
}

demo()
