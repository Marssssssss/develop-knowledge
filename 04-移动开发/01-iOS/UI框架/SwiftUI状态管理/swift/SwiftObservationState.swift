// SwiftUI 状态管理与 Observation 依赖追踪 —— Swift 版最小模型。
//
// 权威依据(实际读过):
//   * Apple/SwiftUI/State:`@State` 是"单一数据源",存储由 SwiftUI 管理;
//     "When the value changes, SwiftUI updates the parts of the view hierarchy that
//     depend on the value";默认值**每次实例化都会求值**(所以别放副作用,用 .task
//     推迟);只读共享用值、要能改必须传 Binding;把 ObservableObject 塞进 State
//     时"the view will only update when the reference to the object changes"。
//   * Apple/Observation:`@Observable` 宏编译期补 Observable 一致性;
//     `withObservationTracking(_:onChange:)`"only tracks properties read in its
//     apply closure" —— 读 title 的视图不会因为 isAvailable 变化而更新。
//
// 本文件把上述语义做成可运行的对照实验(与 python/observation_check.py 同题):
// 注册器记录"谁读了谁" → 只通知读过该属性的视图;再与粗粒度整对象失效对照。
//
// 运行: swift SwiftObservationState.swift

import Foundation

// MARK: - 注册器:ObservationRegistrar 的语义

final class Registrar {
    private(set) var reading: Set<String>?
    private var deps: [String: [() -> Void]] = [:]
    private(set) var notifyCount = 0

    /// apply 闭包求值期间调用:把这次读到的 key 记进当前追踪集合。
    func access(_ key: String) {
        reading?.insert(key)
    }

    /// 登记一次追踪:执行 apply、收集它读过的 key,把这些 key 的失效回调记下来。
    func track(_ apply: () -> Void, onChange: @escaping () -> Void) {
        var reads = Set<String>()
        reading = reads
        apply()
        reading = nil
        for key in reads { deps[key, default: []].append(onChange) }
    }

    /// 属性写入前调用:只通知读过该 key 的依赖(一次性,通知后清空)。
    func mutation(_ key: String) {
        let callbacks = deps.removeValue(forKey: key) ?? []
        for cb in callbacks {
            notifyCount += 1
            cb()
        }
    }
}

/// `@Observable` 的模型:读写都经过注册器。
final class Observable {
    let reg = Registrar()
    private var store: [String: Any] = [:]

    func get<T>(_ key: String) -> T? {
        reg.access(key)
        return store[key] as? T
    }

    func set(_ key: String, _ value: Any) {
        reg.mutation(key)
        store[key] = value
    }
}

/// 视图:body() 求值期间读到的属性成为它的依赖。
final class View {
    let name: String
    private let reg: Registrar
    private(set) var renders = 0
    private(set) var watched: Set<String> = []

    init(_ name: String, _ reg: Registrar) {
        self.name = name
        self.reg = reg
    }

    func render(reading keys: [String]) {
        reg.track({ for k in keys { self.reg.access(k) } }, onChange: { self.body() })
        watched = Set(keys)
        body()
    }

    func body() { renders += 1 }
}

/// ObservableObject 的模型:objectWillChange 在任何属性变化前广播。
final class CoarsePublisher {
    private var observers: [() -> Void] = []
    private(set) var notifyCount = 0

    func subscribe(_ cb: @escaping () -> Void) { observers.append(cb) }

    func willChange(_ key: String) {
        for cb in observers {
            notifyCount += 1
            cb()
        }
    }
}

/// `@State` 的模型:值挂在"视图身份"上,跨 body 求值保留;默认值每次都求值。
final class StateBox {
    private var store: [String: Any] = [:]
    private(set) var initCount = 0

    func make(identity: String, initial: Any) {
        initCount += 1                       // 默认值每次实例化都会被求值
        if store[identity] == nil { store[identity] = initial }
    }

    func value<T>(_ identity: String) -> T? { store[identity] as? T }
    func set(_ identity: String, _ v: Any) { store[identity] = v }
    func remove(_ identity: String) { store.removeValue(forKey: identity) }
}

/// `@Binding`:对某个存储槽的可读写投影(`$state`)。
struct Binding {
    let box: StateBox
    let identity: String

    func get<T>() -> T? { box.value(identity) }
    func set(_ v: Any) { box.set(identity, v) }
}

// MARK: - 自检

var pass = 0, fail = 0

func check(_ label: String, _ ok: Bool, _ detail: String = "") {
    ok ? (pass += 1) : (fail += 1)
    print("  [\(ok ? "PASS" : "FAIL")] \(label)\(detail.isEmpty ? "" : "   \(detail)")")
}

// [1] 细粒度失效:文档里 Book / BookView 的例子
print("[1] 细粒度失效:只追踪 apply 闭包里读过的属性")
let book = Observable()
book.set("title", "A sample book")
book.set("isAvailable", true)
let titleView = View("BookView(读 title)", book.reg)
let bothView = View("BookView(读 title+isAvailable)", book.reg)
titleView.render(reading: ["title"])
bothView.render(reading: ["title", "isAvailable"])
let before = (titleView.renders, bothView.renders)

book.set("isAvailable", false)
check("改 isAvailable 不触发只读 title 的视图", titleView.renders == before.0,
      "renders=\(titleView.renders)")
check("改 isAvailable 触发读过它的视图", bothView.renders == before.1 + 1,
      "renders=\(bothView.renders)")
book.set("title", "New title")
check("改 title 同时触发两个视图",
      titleView.renders == before.0 + 1 && bothView.renders == before.1 + 2,
      "\(titleView.renders)/\(bothView.renders)")

// [2] 对照:粗粒度整对象失效
print("[2] 对照:ObservableObject 的 objectWillChange(粗粒度)")
let pub = CoarsePublisher()
var coarseRenders = 0
pub.subscribe { coarseRenders += 1 }
pub.willChange("title")
pub.willChange("isAvailable")
check("粗粒度:改两个属性收到 2 次通知(即使视图没读 isAvailable)",
      pub.notifyCount == 2, "notify=\(pub.notifyCount)")
check("细粒度:同一场景 0 次通知", book.reg.notifyCount == 0,
      "notify=\(book.reg.notifyCount)")

// [3] 追踪是一次性的
print("[3] 追踪的一次性")
let car = Observable()
car.set("name", "Herbie")
var fires = 0
car.reg.track({ _ = car.get("name") as String? }, onChange: { fires += 1 })
car.set("name", "Lightning")
check("第一次修改触发 onChange", fires == 1, "fires=\(fires)")
car.set("name", "Sally")
check("未重新登记时第二次不再触发(一次性)", fires == 1, "fires=\(fires)")
car.reg.track({ _ = car.get("name") as String? }, onChange: { fires += 1 })
car.set("name", "Doc")
check("重新登记后再次触发", fires == 2, "fires=\(fires)")

// [4] @State 存储生命周期
print("[4] @State 存储生命周期")
let box = StateBox()
box.make(identity: "PlayerView#1", initial: 0.0)
check("首次实例化采用默认值 0", (box.value("PlayerView#1") as Double?) == 0)
box.set("PlayerView#1", 0.75)
box.make(identity: "PlayerView#1", initial: 0.0)          // body 重算 → 结构体再次实例化
check("重新实例化后旧值保留(默认值被忽略)",
      (box.value("PlayerView#1") as Double?) == 0.75,
      "value=\(box.value("PlayerView#1") as Double? ?? -1)")
check("默认值仍然被求值了(所以别在默认值里做重活)", box.initCount == 2,
      "initCount=\(box.initCount)")
box.remove("PlayerView#1")
box.make(identity: "PlayerView#1", initial: 0.0)
check("视图移除后状态销毁,重新回到默认值", (box.value("PlayerView#1") as Double?) == 0)

// [5] @Binding 读写投影
print("[5] @Binding 读写投影")
let box2 = StateBox()
box2.make(identity: "PlayerView#1", initial: false)
let playButton = Binding(box: box2, identity: "PlayerView#1")
playButton.set(!(playButton.get() as Bool? ?? false))     // 子视图 toggle
check("子视图通过 Binding 写回父视图状态",
      (box2.value("PlayerView#1") as Bool?) == true)
check("父视图侧写入同样可见(同一个存储槽)",
      (playButton.get() as Bool?) == true)

// [6] 陷阱:ObservableObject 放进 @State
print("[6] 陷阱:ObservableObject 放进 @State")
let pub2 = CoarsePublisher()
let box3 = StateBox()
var objectRef: [String: Any] = ["title": "A", "isAvailable": true]
box3.make(identity: "ContentView#1", initial: objectRef)
var renders = 0
func renderOnStateChange() { renders += 1 }
renderOnStateChange()
objectRef["title"] = "B"                                  // 只改对象内部属性
check("只改内部属性:引用未变、也没订阅 objectWillChange → 视图不更新",
      renders == 1, "renders=\(renders)")
box3.set("ContentView#1", ["title": "B", "isAvailable": true])
renderOnStateChange()
check("换掉整个引用后视图更新(正确做法 @StateObject / @Observable)",
      renders == 2, "renders=\(renders)")
var stateObjectRenders = 0
pub2.subscribe { stateObjectRenders += 1 }
stateObjectRenders += 1
pub2.willChange("title")
check("@StateObject 订阅后属性变化也能触发更新",
      stateObjectRenders == 2 && pub2.notifyCount == 1,
      "renders=\(stateObjectRenders) notify=\(pub2.notifyCount)")

print("\n断言 \(pass) 通过 / \(fail) 失败")
exit(fail == 0 ? 0 : 1)
