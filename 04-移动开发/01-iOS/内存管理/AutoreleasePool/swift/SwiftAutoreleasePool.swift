// SwiftAutoreleasePool.swift — AutoreleasePoolPage 双向链表最小复刻(demo 132,Swift 版)
// Swift 中 autoreleasepool { } 函数桥接 Objective-C 的同名机制;
// 本文件用纯 Swift 复刻 objc4 的 AutoreleasePoolPage 结构,演示:
//   哨兵(POOL_BOUNDARY)分隔嵌套池 / 页满自动扩 child page / pop 逆序 release

import Foundation

private let poolBoundary: AnyObject? = nil   // objc4: #define POOL_BOUNDARY nil
private let pageCapacity = 8                 // 演示容量(真实一页 4096B)

final class DemoObject {
    let name: String
    init(_ name: String) { self.name = name }
    deinit { print("  [release] \(name) 引用计数归零,deinit") }
}

final class AutoreleasePoolPage {
    // objc4 结构: magic / next / thread / parent / child / depth / hiwat
    let depth: Int
    let parent: AutoreleasePoolPage?
    var child: AutoreleasePoolPage?
    var slots: [AnyObject?] = []             // 对象槽位栈(next 即 slots.endIndex)

    init(parent: AutoreleasePoolPage?) {
        self.parent = parent
        self.depth = parent.map { $0.depth + 1 } ?? 0
        parent?.child = self                 // 构造时挂双向链表
    }
    var isFull: Bool { slots.count >= pageCapacity }
    var isEmpty: Bool { slots.isEmpty }

    @discardableResult
    func add(_ obj: AnyObject?) -> Int {     // page->add(obj) 压栈,返回槽位下标(token)
        slots.append(obj)
        return slots.count - 1
    }
}

enum AutoreleaseStack {
    private static var hotPage: AutoreleasePoolPage?

    // autoreleaseFast 三分支:热页未满 / 满则找或建子页 / 无热页建首页
    private static func autoreleaseFast(_ obj: AnyObject?) -> (AutoreleasePoolPage, Int) {
        if let page = hotPage, !page.isFull {
            return (page, page.add(obj))
        }
        if let page = hotPage {              // autoreleaseFullPage:沿 child 找或新建
            var p = page
            while p.isFull {
                if let c = p.child { p = c } else { p = AutoreleasePoolPage(parent: p) }
            }
            hotPage = p
            return (p, p.add(obj))
        }
        let p = AutoreleasePoolPage(parent: nil)  // autoreleaseNoPage
        hotPage = p
        if obj !== poolBoundary { p.add(poolBoundary) }  // 首页补哨兵防裸 pop
        return (p, p.add(obj))
    }

    // objc_autoreleasePoolPush:压哨兵,返回 token(page + slot 下标)
    static func push() -> PoolToken {
        let (page, slot) = autoreleaseFast(poolBoundary)
        return PoolToken(page: page, slot: slot)
    }

    // objc_autoreleasePoolPop(token):热页逆序出栈 release,直到 next 回到 token 槽位
    // objc4 以指针/槽位比较为停止条件(非哨兵值比较):嵌套池的内层哨兵会被跨过并跳过
    static func pop(_ token: PoolToken) {
        var page: AutoreleasePoolPage? = hotPage
        while let p = page {
            if p.isEmpty {                       // 空页回退父页
                page = p.parent
                continue
            }
            if p === token.page && p.slots.count == token.slot {  // 到本池哨兵槽位止
                hotPage = p
                return
            }
            guard let obj = p.slots.popLast() else { continue }
            if obj === poolBoundary { continue } // 跨过已 pop 的内层哨兵(残留槽位)
            print("  [pop] 向 \(obj!) 发送 release")
            _objRelease(obj!)                    // 池持有 -1,可能触发 deinit
            page = p
        }
        hotPage = page ?? hotPage
    }
}

struct PoolToken { let page: AutoreleasePoolPage; let slot: Int }

// 模拟池持有:存强引用的全局袋(p autorelease == 入袋 +1;pop == 出袋 -1)
final class RetainBag {
    static let shared = RetainBag()
    private var bag: [ObjectIdentifier: [DemoObject]] = [:]
    func retain(_ o: DemoObject) { bag[ObjectIdentifier(o), default: []].append(o) }
    func release(_ o: DemoObject) {
        if var list = bag[ObjectIdentifier(o)] {
            if !list.isEmpty { list.removeLast() }   // -1
            bag[ObjectIdentifier(o)] = list
        }
        if (bag[ObjectIdentifier(o)] ?? []).isEmpty { bag.removeValue(forKey: ObjectIdentifier(o)) }
    }
}
private func _objRelease(_ o: AnyObject) { if let d = o as? DemoObject { RetainBag.shared.release(d) } }
private func _objAutorelease(_ o: DemoObject) { RetainBag.shared.retain(o); _ = AutoreleaseStack.autoreleaseFast(o) }

// clang 改写后的 __AtAutoreleasePool 结构体 → Swift 用 class + deinit 表达
final class AtAutoreleasePool {
    let token: PoolToken
    init() { token = AutoreleaseStack.push() }   // 构造 = push
    deinit { AutoreleaseStack.pop(token) }        // 析构 = pop
}

func demo() {
    print("== 1. 外层池:6 个对象入栈 ==")
    let outer = AtAutoreleasePool()
    var objs = (0..<6).map { DemoObject("obj\($0)") }
    objs.forEach { _objAutorelease($0) }

    print("== 2. 内层嵌套池:2 个对象,内层 pop 只释放内层 ==")
    do {
        let inner = AtAutoreleasePool()
        (6..<8).forEach { _objAutorelease(DemoObject("obj\($0)")) }
        print("  内层作用域结束,deinit inner → 只逆序释放 obj7/obj6")
    }

    print("== 3. 再入 4 个对象,容量 8 触发分页(child page depth=1)==")
    (8..<12).forEach { _objAutorelease(DemoObject("obj\($0)")) }
    var cold = outer.token.page
    while let p = cold.parent { cold = p }
    var page: AutoreleasePoolPage? = cold
    while let p = page {
        print("  [page depth=\(p.depth)] slots: \(p.slots.map { $0 == nil ? "<BOUNDARY>" : "\($0!)" })")
        page = p.child
    }

    print("== 4. 外层结束:逆序释放全部(含跨页)==")
    // outer deinit → pop(outer.token)
}

demo()
