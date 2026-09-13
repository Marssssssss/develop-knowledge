// SwiftCategory.swift — Category 合并顺序 + 关联对象两层哈希最小复刻(demo 136,Swift 版)
// Swift 的 extension ≈ 匿名 Category(不能加存储属性、不能 override);
// 本文件复刻:attachCategories 前插合并(后编译者胜)+ +load 顺序 +
// AssociationsManager 两层哈希(对象 DISGUISE → key → {policy, value})与 dealloc 释放。

import Foundation

// —— ① Category 合并 ——
struct MiniMethod { let sel: String; let from: String }
struct MiniCategory { let clsName: String; let catName: String; let methods: [(String, String)] }

final class MethodList {
    private(set) var methods: [MiniMethod] = []
    func attachOriginal(_ cls: String, _ sels: [String]) {
        methods = sels.map { MiniMethod(sel: $0, from: cls) }
    }
    // attachCategories:倒序遍历分类,方法整体前插(memmove+memcopy 语义)
    func attach(_ cats: [MiniCategory]) {
        for c in cats.reversed() {
            for (sel, _) in c.methods.reversed() {
                methods.insert(MiniMethod(sel: sel, from: c.catName), at: 0)
            }
        }
    }
    // 消息查找:第一个命中生效 → 前插的分类方法"覆盖"同名原方法
    func lookup(_ sel: String) -> String? {
        methods.first { $0.sel == sel }?.from
    }
    func describe(_ stage: String) {
        print("  [\(stage)] 方法列表: " + methods.map { "\($0.sel)(\($0.from))" }.joined(separator: " "))
    }
}

// —— ② 关联对象 ——
enum AssociationPolicy { case assign, retainNonatomic, copyNonatomic }

struct ObjcAssociation { let policy: AssociationPolicy; let value: AnyObject? }

// ObjectAssociationMap:key → {policy, value}
final class ObjectAssociationMap {
    var assoc: [UnsafeRawPointer: ObjcAssociation] = [:]   // key 用静态地址
}
// AssociationsHashMap:对象(DISGUISE 取负伪装)→ 二级表
final class AssociationsManager {
    static let shared = AssociationsManager()
    private var map: [Int: ObjectAssociationMap] = [:]     // 真实实现:全局一张 + 自旋锁
    private func disguiseHash(_ obj: AnyObject) -> Int {   // DisguisedPtr = -(uintptr_t)ptr
        let a = UInt(bitPattern: ObjectIdentifier(obj).hashValue)
        return Int(((~a &+ 1) >> 3) % 32)
    }
    func set(_ obj: AnyObject, key: UnsafeRawPointer, value: AnyObject?, policy: AssociationPolicy) {
        guard let value else {                             // 传 nil = 移除该 key
            map[disguiseHash(obj)]?.assoc.removeValue(forKey: key)
            return
        }
        if map[disguiseHash(obj)]?.assoc[key] != nil {
            print("    [set] 覆盖旧值")
        }
        let m = map[disguiseHash(obj)] ?? { let m = ObjectAssociationMap(); map[disguiseHash(obj)] = m; return m }()
        m.assoc[key] = ObjcAssociation(policy: policy, value: value)
    }
    func get(_ obj: AnyObject, key: UnsafeRawPointer) -> AnyObject? {
        map[disguiseHash(obj)]?.assoc[key]?.value
    }
    // objc_destructInstance:对象释放时全清(RETAIN/COPY 值按 policy release)
    func removeAll(_ obj: AnyObject) {
        let h = disguiseHash(obj)
        if let m = map[h] {
            print("    [dealloc] 按 policy 释放 \(m.assoc.count) 个关联值并移除二级表")
            map.removeValue(forKey: h)
        }
    }
}

// —— 演示 ——
let kNameKey = UnsafeRawPointer(bitPattern: 0x1)!           // 静态 key 地址(惯例 static var 地址)
final class Model {}

func demo() {
    print("== 1. Category:宿主只有 originalMethod ==")
    let list = MethodList()
    list.attachOriginal("Model", ["originalMethod"])
    list.describe("attach 前")

    print("== 2. CatA(先编译)/ CatB(后编译)各带 description ==")
    let catA = MiniCategory(clsName: "Model", catName: "CatA", methods: [("description", "v@:")])
    let catB = MiniCategory(clsName: "Model", catName: "CatB", methods: [("description", "v@:")])
    list.attach([catA, catB])
    list.describe("attach 后")
    print("  查找 description → 由 \(list.lookup("description")!) 提供(后编译的 CatB 前插获胜)")

    print("== 3. +load 顺序:父类 → 本类 → 分类(编译序);+initialize 首消息时懒调用一次 ==")
    print("  [load] Model → Model(CatA) → Model(CatB)")

    print("== 4. 关联对象:extension 不能加存储属性,用关联对象模拟 ==")
    let m = Model()
    let mgr = AssociationsManager.shared
    mgr.set(m, key: kNameKey, value: "hello" as NSString, policy: .retainNonatomic)
    print("    get → \(mgr.get(m, key: kNameKey)!)")
    mgr.set(m, key: kNameKey, value: "world" as NSString, policy: .copyNonatomic)
    print("    get → \(mgr.get(m, key: kNameKey)!)")
    mgr.set(m, key: kNameKey, value: nil, policy: .retainNonatomic)
    print("    set nil → get → \((mgr.get(m, key: kNameKey) as? String) ?? "nil")(传 nil 即移除)")
    mgr.set(m, key: kNameKey, value: "again" as NSString, policy: .retainNonatomic)

    print("== 5. 对象释放:destructInstance 清关联 ==")
    mgr.removeAll(m)
}
demo()
