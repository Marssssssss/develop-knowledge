// SwiftMsgSend.swift — objc_msgSend 消息机制三段式最小复刻(demo 134,Swift 版)
// Swift 方法默认静态/虚表派发,但 @objc dynamic 修饰符仍走 ObjC 消息机制;
// 本文件用纯 Swift 复刻:cache_t 哈希缓存(3/4 扩容清空)→ 方法列表二分 →
// 继承链上行 → 动态解析 → 快速转发 → 完整转发 → doesNotRecognizeSelector。

import Foundation

typealias MethodIMP = (String) -> Void      // (消息参数) -> 实现

struct MethodT { let sel: String; let imp: MethodIMP }

// —— cache_t:开放寻址哈希,容量 2 的幂,mask 位与代替取模 ——
final class CacheT {
    private(set) var buckets: [MethodT?]
    private(set) var mask: Int              // capacity - 1
    private(set) var occupied = 0
    init(capacity: Int = 4) {               // arm64 初始容量 4
        self.mask = capacity - 1
        self.buckets = Array(repeating: nil, count: capacity)
    }
    var capacity: Int { mask + 1 }

    func get(_ sel: String) -> MethodIMP? { // CacheLookup:哈希定位→线性探测→回绕
        var i = hash(sel) & mask
        for _ in 0...mask {
            guard let b = buckets[i] else { return nil }   // 空槽 = 未命中
            if b.sel == sel { return b.imp }
            i = (i + 1) & mask
        }
        return nil
    }
    func insert(_ sel: String, _ imp: @escaping MethodIMP) {
        // objc4:占用 > 3/4 → 容量翻倍并清空(时间局部性强,不做 rehash)
        if occupied * 4 > capacity * 3 {
            mask = capacity * 2 - 1
            buckets = Array(repeating: nil, count: mask + 1)
            occupied = 0
        }
        var i = hash(sel) & mask
        while buckets[i] != nil { i = (i + 1) & mask }
        buckets[i] = MethodT(sel: sel, imp: imp)
        occupied += 1
    }
    private func hash(_ s: String) -> Int { // SEL 地址哈希的 Swift 等价物
        var h = 5381
        for b in s.utf8 { h = (h &* 33 &+ Int(b)) }
        return h & 0x7fffffff
    }
}

// —— 类:方法列表(按 sel 排序后二分)+ 父类指针 + 缓存 ——
final class MiniCls {
    let name: String
    let superclass: MiniCls?
    private var methods: [MethodT] = []
    private var sorted = true
    let cache = CacheT()
    init(_ name: String, _ superclass: MiniCls? = nil) {
        self.name = name; self.superclass = superclass
    }
    func addMethod(_ sel: String, _ imp: @escaping MethodIMP) {  // class_addMethod
        methods.append(MethodT(sel: sel, imp: imp)); sorted = false
    }
    func methodListFind(_ sel: String) -> MethodIMP? {           // 排序后二分查找
        if !sorted { methods.sort { $0.sel < $1.sel }; sorted = true }
        var lo = 0, hi = methods.count - 1
        while lo <= hi {
            let mid = (lo + hi) / 2
            if methods[mid].sel == sel { return methods[mid].imp }
            if methods[mid].sel < sel { lo = mid + 1 } else { hi = mid - 1 }
        }
        return nil
    }
}

// —— 消息三阶段兜底钩子(对应 NSObject 三个可重写方法)——
protocol MessagingHooks: AnyObject {
    func resolveInstanceMethod(_ sel: String) -> Bool   // ① 动态解析
    func forwardingTarget(for sel: String) -> MiniObj?  // ② 快速转发
    func forwardInvocation(_ sel: String, _ arg: String) // ③ 完整转发
}
extension MessagingHooks {
    func resolveInstanceMethod(_ sel: String) -> Bool { false }
    func forwardingTarget(for sel: String) -> MiniObj? { nil }
    func forwardInvocation(_ sel: String, _ arg: String) {}
}

final class MiniObj: MessagingHooks {
    let isa: MiniCls
    // ② 快速转发的备用接收者(如 Forwarder)
    var forwardingDelegate: MiniObj?
    init(_ isa: MiniCls) { self.isa = isa }
}

// —— mini-runtime:objc_msgSend 全流程 ——
enum MsgSend {
    // lookUpImpOrForward:慢速路径,沿继承链 上行,命中回填最初接收类缓存
    static func lookUp(_ cls: MiniCls, _ sel: String) -> MethodIMP? {
        var cur: MiniCls? = cls
        while let c = cur {
            if let imp = c.methodListFind(sel) {
                cls.cache.insert(sel, imp)               // 回填接收类(非找到方法的父类)
                return imp
            }
            cur = c.superclass
        }
        return nil                                       // → _objc_msgForward_impcache
    }

    static func send(_ obj: MiniObj, _ sel: String, _ arg: String = "-") {
        // ① 汇编快速路径:receiver nil 直接返回(向 nil 发消息为 no-op)
        var imp = obj.isa.cache.get(sel)
        if imp == nil { imp = lookUp(obj.isa, sel) }     // ② 慢速路径
        if let imp { imp(arg); return }

        // ③ 动态解析:resolve 返回 true 表示已 class_addMethod,重查一轮
        if obj.resolveInstanceMethod(sel), let retry = lookUp(obj.isa, sel) {
            retry(arg); return
        }
        // ④ 快速转发:整条消息交给备用接收者(不构造签名,零开销)
        if let target = obj.forwardingTarget(for: sel) {
            print("    [阶段② forwardingTarget] \(sel) → 转给备用接收者")
            send(target, sel, arg); return
        }
        // ⑤ 完整转发:methodSignatureForSelector + forwardInvocation(NSInvocation 语义)
        print("    [阶段③ methodSignature+forwardInvocation]")
        obj.forwardInvocation(sel, arg); return
        // 三阶段全失败 → doesNotRecognizeSelector: 抛 NSInvalidArgumentException
    }
}

// —— 业务演示 ——
let animal = MiniCls("Animal")                            // 根类
let dog = MiniCls("Dog", animal)                          // 子类
animal.addMethod("hello") { print("    [IMP 命中] hello 执行(参数 \($0))") }

let obj = MiniObj(dog)
// ② 快速转发:Dog 不实现 fetch,转发给实现了它的 helper
let helper = MiniObj(MiniCls("Forwarder"))
helper.isa.addMethod("fetch") { print("    [备用接收者] fetch 由 Forwarder 代为执行(参数 \($0))") }
obj.forwardingDelegate = helper
extension MiniObj {
    // ① 动态解析钩子:此刻方法表里还没有,resolve 里补 IMP(class_addMethod 语义)
    func resolveInstanceMethod(_ sel: String) -> Bool {
        if sel == "dynamicHello:" {
            dog.addMethod(sel) { print("    [IMP 命中] 动态添加的 dynamicHello: 执行") }
            return true
        }
        return false
    }
    // ② 快速转发钩子
    func forwardingTarget(for sel: String) -> MiniObj? { sel == "fetch" ? forwardingDelegate : nil }
    // ③ 完整转发钩子:methodSignature + forwardInvocation 语义
    func forwardInvocation(_ sel: String, _ arg: String) {
        print("    [ForwardInvocation] 无法处理 \(sel),等价 doesNotRecognizeSelector 抛异常前最后一站")
    }
}

func demo() {
    print("== 1. 首次发 hello:cache miss → 慢速查找父类 Animal 方法表,回填 Dog 缓存 ==")
    MsgSend.send(obj, "hello", "bone")
    print("== 2. 再次发 hello:cache 命中 O(1) ==")
    MsgSend.send(obj, "hello", "bone2")

    print("== 3. 发 dynamicHello::慢速路径未命中 → ① 动态解析补 IMP → retry 命中 ==")
    MsgSend.send(obj, "dynamicHello:", "x")

    print("== 4. 发 fetch:本类/父类都没有 → ② 快速转发给 Forwarder ==")
    MsgSend.send(obj, "fetch", "stick")

    print("== 5. 发 unknown::三阶段全失败 → doesNotRecognizeSelector ==")
    MsgSend.send(obj, "unknown:", "?")
}
demo()
