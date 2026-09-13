// SwiftKVO.swift — KVO isa-swizzling 语义最小复刻(demo 135,Swift 版)
// Swift 无法在运行时动态派生类,本文件用"派生通知子类 + 方法表切换"复刻 KVO 语义:
//   注册 → 对象的派生身份生效(等价 isa-swizzling)
//   setter 重写:willChangeValueForKey → 原 setter → didChangeValueForKey → 回调观察者
//   -class 伪装:对外仍报原类名;_isKVOA 标志
//   移除 → 身份复原(派生类缓存保留,复用)

import Foundation

// —— 方法表:类名 → [sel: 实现],isa 调包 = 换一张方法表 ——
typealias Setter = (Int) -> Void
final class MethodTable {
    let className: String
    let parent: MethodTable?
    var setters: [String: Setter] = [:]
    var isKVODerived = false
    var maskedClassName: String?                    // 重写 -class 的伪装名
    init(_ name: String, _ parent: MethodTable? = nil) {
        self.className = name; self.parent = parent
    }
    func lookup(_ sel: String) -> Setter? {
        if let s = setters[sel] { return s }
        return parent?.lookup(sel)                  // 沿继承链上行
    }
    var displayName: String { maskedClassName ?? className }
}

// —— 观察者 ——
struct KVOChange { let oldValue: Int; let newValue: Int }
protocol KVObserver: AnyObject { func observeValue(for key: String, of obj: MiniKVOObj, change: KVOChange) }

// —— 模拟对象:isa 是一张可热替换的方法表 ——
final class MiniKVOObj {
    var methodTable: MethodTable                    // 等价 isa 指针
    private(set) var storedAge: Int = 0             // ivar
    init(_ table: MethodTable) { self.methodTable = table }

    var age: Int { storedAge }
    func setAge(_ v: Int) {
        // 消息机制语义:setAge: 从当前方法表(可能已调包)查找
        if let setter = methodTable.lookup("setAge:") {
            setter(v)
        } else {
            storedAge = v                           // 根类原始 setter
        }
    }
    // 重写 -class 的伪装:对外 displayName
    var describedClass: String { methodTable.displayName }
    // _isKVOA 私有标志
    var isKVOA: Bool { methodTable.isKVODerived }
}

// —— mini-KVO 引擎 ——
enum MiniKVO {
    static var observers: [String: [WeakBox]] = [:]          // keyPath → 观察者(弱持有)
    static var derivedCache: [String: MethodTable] = [:]     // 派生类缓存(移除后仍复用)
    final class WeakBox { weak var obs: KVObserver?; init(_ o: KVObserver) { obs = o } }

    static func kvoTable(for obj: MiniKVOObj) -> MethodTable {
        let original = obj.methodTable
        let derivedName = "NSKVONotifying_" + original.className
        if let cached = derivedCache[derivedName] { return cached }
        // ① 动态派生:子类方法表,继承原表
        let derived = MethodTable(derivedName, original)
        derived.isKVODerived = true
        derived.maskedClassName = original.className         // ③ -class 伪装
        // ② 重写 setAge::will → 父类 setter → did → 通知观察者
        derived.setters["setAge:"] = { [weak obj] newAge in
            guard let obj else { return }
            print("    [willChangeValueForKey:age]")
            let old = obj.storedAge
            obj.storedAge = newAge                           // 等价 [super setAge:]
            print("    [didChangeValueForKey:age]")
            let change = KVOChange(oldValue: old, newValue: newAge)
            for box in observers["age"] ?? [] {
                box.obs?.observeValue(for: "age", of: obj, change: change)
            }
        }
        derivedCache[derivedName] = derived
        return derived
    }

    static func addObserver(_ obj: MiniKVOObj, _ obs: KVObserver, forKey key: String) {
        let derived = kvoTable(for: obj)
        if obj.methodTable !== derived {
            print("    [addObserver] isa 调包:\(obj.methodTable.className) → \(derived.className)")
            obj.methodTable = derived                        // ④ object_setClass 语义
        }
        observers[key, default: []].append(WeakBox(obs))
    }

    static func removeObserver(_ obj: MiniKVOObj, forKey key: String) {
        observers[key] = nil
        if obj.methodTable.isKVODerived, let parent = obj.methodTable.parent {
            obj.methodTable = parent                        // isa 复原(派生表缓存保留)
            print("    [removeObserver] isa 复原 → \(parent.className)")
        }
    }
}

// —— 业务演示 ——
final class Screen: KVObserver {
    let name: String
    init(_ n: String) { name = n }
    func observeValue(for key: String, of obj: MiniKVOObj, change: KVOChange) {
        print("    [通知] \(name) 收到 \(key):\(change.oldValue) → \(change.newValue)")
    }
}

func demo() {
    let personTable = MethodTable("Person")                  // 原类方法表
    let p = MiniKVOObj(personTable)

    print("== 1. 注册前:真实类 \(p.methodTable.className),伪装类 \(p.describedClass),_isKVOA=\(p.isKVOA) ==")
    let obs = Screen("obs1")
    MiniKVO.addObserver(p, obs, forKey: "age")

    print("== 2. 注册后:真实类 \(p.methodTable.className),-class 仍报 \(p.describedClass),_isKVOA=\(p.isKVOA) ==")
    print("== 3. 赋值走派生 setter(通知链)==")
    p.setAge(20)

    MiniKVO.removeObserver(p, forKey: "age")
    print("== 4. 移除后赋值不再通知 ==")
    p.setAge(30)
    print("    age=\(p.age),_isKVOA=\(p.isKVOA)")
}
demo()
