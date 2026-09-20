// Swift 6 严格并发:对照 python/concurrency_regions.py 的同题 Swift 侧代码。
// 本文件不参与自动化测试(本机无 Swift 工具链),用于人工对照阅读:
// 注释里标 // ERROR: 的行就是 Swift 6 语言模式下会报的错,与 Python 模型的判定一一对应。

import Foundation

// MARK: - 1. Sendable(SE-0302)

struct MyPerson2 {                     // 非 public + 成员全 Sendable → 隐式 Sendable
    var name: String
    var age: Int
}

class NotConcurrent { }

struct MyPerson3 {                     // 含非 Sendable 成员 → 不是 Sendable
    var nc: NotConcurrent
}

final class FinalImmutable: Sendable { // final + 全部 let 且类型 Sendable → 允许
    let state: String
    init(state: String) { self.state = state }
}

final class FinalMutable: Sendable {   // ERROR: stored property 'state' is mutable
    var state: String
    init(state: String) { self.state = state }
}

final class LegacyBox: @unchecked Sendable {   // @unchecked:由作者自行保证
    private let lock = NSLock()
    private var _value: NSMutableString
    init(_ v: NSMutableString) { _value = v }
    func append(_ s: String) { lock.lock(); defer { lock.unlock() }; _value.append(s) }
}

struct Pair<T> {                       // 未约束的泛型:不隐式 Sendable,也不隐式条件一致
    var a: T
    var b: T
}

struct SendablePair<T: Sendable> {     // T: Sendable 时实例数据保证 Sendable → 隐式一致
    var a: T
    var b: T
}

actor ClientStore {                    // actor 隐式 Sendable
    static let shared = ClientStore()
    private var clients: [Client] = []
    func addClient(_ c: Client) { clients.append(c) }
}

// MARK: - 2. 隔离区合并(SE-0414)

final class Client {
    var name: String
    var friend: Client?                // 一旦赋值,friend 的隔离区并入 self 所在区域
    init(name: String) { self.name = name }
}

func openNewAccount(name: String) async {
    let client = Client(name: name)
    // Regions: [(client)]
    await ClientStore.shared.addClient(client)   // 区域并入 ClientStore 的隔离域
    // Regions: [{(client), ClientStore.shared}]
    client.name = "changed"
    // ERROR: 已传递出去的非 Sendable 值在调用方继续使用 —— 可能数据竞争
}

func twoDisconnectedClients() async {
    let john = Client(name: "John")
    let joanna = Client(name: "Joanna")
    // Regions: [(john), (joanna)] —— 彼此不可达,是两个独立区域
    await ClientStore.shared.addClient(john)
    await ClientStore.shared.addClient(joanna)   // OK:joanna 仍在自己的 disconnected 区
}

func aliasedClients() async {
    let john = Client(name: "John")
    let joanna = Client(name: "Joanna")
    john.friend = joanna               // Regions: [(john, joanna)]
    await ClientStore.shared.addClient(john)
    await ClientStore.shared.addClient(joanna)
    // ERROR: joanna 已可通过 john.friend 触达,属同一区域
}

func mergedIntoInvalidRegion() async {
    let a1 = ClientStore()
    let a2 = ClientStore()
    let x = Client(name: "x")
    if Bool.random() {
        await a1.addClient(x)          // Regions: [{(x), a1}, {(), a2}]
    } else {
        await a2.addClient(x)          // Regions: [{(), a1}, {(x), a2}]
    }
    // 汇合后 x 的隔离域静态不可判定 → invalid region,此后任何使用都报错
    _ = x                              // ERROR: invalid isolation region
}

// MARK: - 3. 弱传递与 nonisolated 异步函数

@MainActor func transferToMainActor<T>(_ t: T) async { }

func nonIsolatedCallee(_ x: Client) async { }

func weakTransferDemo(_ x: Client) async {
    await nonIsolatedCallee(x)         // nonisolated async:同 task 域内,不是传递
    _ = x                              // OK —— 与「同 task 调用不算传递」对应
    await transferToMainActor(x)       // ERROR:x 属 task 隔离区,不能传出 task
}

func reuseAfterNonisolated() async {
    let x = Client(name: "reuse")
    await nonIsolatedCallee(x)         // nonisolated 函数没有持久隔离状态
    // 函数返回后区域重新变回 disconnected,可以再次传递
    await transferToMainActor(x)       // OK
}

final class NonSendableOwned {
    let letSendable: String = "let"
    var varSendable: String = "var"
}

@MainActor func modifyOnMainActor(_ x: NonSendableOwned) async {
    x.varSendable = "written on main"
}

func sendableFieldAfterWeakTransfer() async {
    let x = NonSendableOwned()
    await modifyOnMainActor(x)
    _ = x.letSendable                  // OK:let 字段,不会有并发写
    _ = x.varSendable                  // ERROR:var 字段,可能与 @MainActor 的写竞争
}

// MARK: - 4. 全局变量(SE-0412)

var mutableGlobal = 0
// ERROR(Swift 6):reference to var 'mutableGlobal' is not concurrency-safe

let immutableGlobal = 0                // OK:不可变且 Sendable

@MainActor var mainActorGlobal = 0     // OK:隔离到 global actor

nonisolated(unsafe) var unsafeGlobal = 0   // OK:显式关闭静态检查,自行保证同步

// MARK: - 5. 生命周期:弱传递下所有权仍在调用方

func ownershipDemo() async {
    let x = Client(name: "owned")
    await transferToMainActor(x)
    print("After nonisolated callee")  // 先打印
    // x 的 deinit 在本作用域末尾才发生 —— transfer 是弱传递,不是 +1 转移
}
