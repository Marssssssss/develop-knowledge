// main.swift —— Swift 并发 7 个场景的自检
//
// 构建运行(需要 Swift 5.7+ / macOS 13+;未用 Swift 6 严格并发模式):
//   swiftc ConcurrencyModel.swift main.swift -o swift-concurrency && ./swift-concurrency
//
// 与 python/concurrency_check.py 的分工:
//   Python 版跑在**虚拟时钟**上,断言是精确数字(耗时 = 100ms / = 50ms);
//   Swift 版跑在**真实运行时**上,断言的是结构性事实(原子性、可重入、级联取消、顺序),
//   耗时类断言只比较量级 —— 真实机器上的调度抖动不该让自检变红。

import Foundation

// [1] ── await 串行 vs async let 并行
func scenario1(_ c: Check) async {
    c.section("[1] `await a(); await b()` 串行 vs `async let` 并行")
    let jobs = [("A", 30), ("B", 50), ("C", 20)]
    let log = Log()

    func download(_ name: String, _ millis: Int) async -> String {
        await log.add("\(name) 开始")
        try? await Task.sleep(for: .milliseconds(millis))
        await log.add("\(name) 结束")
        return name
    }

    // 串行:下面几行依赖结果,所以每一步都要等
    let serial = await measure {
        for (name, millis) in jobs { _ = await download(name, millis) }
    }
    let serialLog = await log.text

    // 并行:三个 async let 在声明处就各自起飞,最后才汇合
    let log2 = Log()
    func download2(_ name: String, _ millis: Int) async -> String {
        await log2.add("\(name) 开始")
        try? await Task.sleep(for: .milliseconds(millis))
        await log2.add("\(name) 结束")
        return name
    }
    let parallel = await measure {
        async let a = download2("A", 30)
        async let b = download2("B", 50)
        async let d = download2("C", 20)
        _ = await a
        _ = await b
        _ = await d
    }
    let parallelLog = await log2.text

    c.that("串行 await 的耗时 ≈ 30+50+20 = 100ms", abs(serial - 100) < 40, String(format: "%.0fms", serial))
    c.that("async let 的耗时 ≈ max(30,50,20) = 50ms", abs(parallel - 50) < 30, String(format: "%.0fms", parallel))
    c.that("并行至少快 1.5 倍", serial / parallel > 1.5, String(format: "%.2fx", serial / parallel))
    if let i = serialLog.range(of: "A 结束")?.lowerBound,
       let j = serialLog.range(of: "B 开始")?.lowerBound {
        c.that("串行下 B 在 A 结束之后才启动", j > i, serialLog)
    }
    if let i = parallelLog.range(of: "A 结束")?.lowerBound,
       let j = parallelLog.range(of: "B 开始")?.lowerBound {
        c.that("并行下 B 在 A 结束前就启动了(真的重叠)", j < i, parallelLog)
    }
}

// [2] ── actor:同步段原子 ⇒ 无丢失更新
func scenario2(_ c: Check) async {
    c.section("[2] actor 串行化:读-改-写在一个 job 内完成")
    let bank = BankAccount()
    await withTaskGroup(of: Int.self) { group in
        for _ in 0..<5 { group.addTask { await bank.deposit(10) } }
        for await _ in group {}
    }
    let balance = await bank.balance
    let seen = await bank.seen
    let jobs = await bank.jobs
    c.that("5 次 +10 后余额 = 50(并发下没有丢失更新)", balance == 50, "balance=\(balance)")
    c.that("观察序列严格递增 → 每个 job 都是原子的", seen == [10, 20, 30, 40, 50], "\(seen)")
    c.that("actor 上执行了 5 个 job(挂起后让位,再回来接着跑)", jobs == 5, "jobs=\(jobs)")

    // 对照:同一个「读-改-写」被拆到两次 await 之间 —— 每一步都原子,结果依然错
    let split = SplitCounter()
    let a = await split.read()
    let b = await split.read()
    await split.write(a + 10)
    await split.write(b + 10)
    let value = await split.value
    c.that("把读-改-写拆到两次 await 之间:两次 +10 只剩 10", value == 10, "value=\(value)")
}

// [3] ── 可重入:check, await, act
func scenario3(_ c: Check) async {
    c.section("[3] 可重入导致 await 之后的前提可能失效")
    let naive = Vault(100)
    let results = await withTaskGroup(of: String.self, returning: [String].self) { group in
        for _ in 0..<2 { group.addTask { await naive.naiveWithdraw(80) } }
        var out: [String] = []
        for await r in group { out.append(r) }
        return out
    }
    let naiveBalance = await naive.balance
    c.that("天真写法:两笔 80 都判为可提,余额被扣成 -60",
           naiveBalance == -60 && results.filter { $0 == "ok" }.count == 2,
           "balance=\(naiveBalance) \(results)")

    let careful = Vault(100)
    let results2 = await withTaskGroup(of: String.self, returning: [String].self) { group in
        for _ in 0..<2 { group.addTask { await careful.carefulWithdraw(80) } }
        var out: [String] = []
        for await r in group { out.append(r) }
        return out
    }
    let carefulBalance = await careful.balance
    c.that("把复检放回同步段:第二笔被拒,余额 = 20",
           carefulBalance == 20 && results2.filter { $0 == "rejected" }.count == 1,
           "balance=\(carefulBalance) \(results2)")
}

// [4] ── 可重入的收益:两个 actor 互相 await
func scenario4(_ c: Check) async {
    c.section("[4] 可重入的收益:两个 actor 互相 await 不会死锁")
    let odd = Parity("Odd")
    let even = Parity("Even")
    await odd.link(to: even)
    await even.link(to: odd)

    // SE-0306 的 OddOddy / EvenEvan:isEven 依赖 isOdd,isOdd 又依赖回 isEven。
    // 若 actor 不可重入,第一次挂起就把自己锁住 → 永久挂起(死锁)。
    let result = await odd.isEven(10)
    let peak = await odd.maxConcurrentEntries
    let peakEven = await even.maxConcurrentEntries
    c.that("互相递归的 isEven/isOdd 正常返回(可重入保证了向前推进)", result == true, "isEven(10)=\(result)")
    c.that("Odd 上同时有 2 个调用处于挂起中 → 可重入确实发生了", peak >= 2, "maxConcurrentEntries=\(peak)")
    c.that("Even 上同样观测到重入", peakEven >= 2, "maxConcurrentEntries=\(peakEven)")
}

// [5] ── 协作式取消
func scenario5(_ c: Check) async {
    c.section("[5] 协作式取消:级联传播 + 不检查就不停")
    let tally = Tally()

    let group = Task {
        await withTaskGroup(of: Void.self) { g in
            for _ in 0..<3 {
                g.addTask {
                    for _ in 0..<3 {
                        if Task.isCancelled { return }               // 挂起点前检查
                        try? await Task.sleep(for: .milliseconds(40))
                        if Task.isCancelled { return }               // 挂起点后复检
                        await tally.add()
                    }
                }
            }
        }
    }
    try? await Task.sleep(for: .milliseconds(50))
    group.cancel()                                                   // 取消父任务
    _ = await group.value
    let processed = await tally.processed
    c.that("取消父任务级联到全部子任务:没有一个跑满 3 项", processed < 9, "processed=\(processed)/9")
    c.that("取消确实中断了工作(不是全部跑完)", processed < 9, "processed=\(processed)")

    // 不检查取消的任务:标志置了也没用 —— 这就是「协作式」的含义
    let stubborn = Tally()
    let t = Task {
        for _ in 0..<3 {
            try? await Task.sleep(for: .milliseconds(30))
            await stubborn.add()                                     // 从不检查取消
        }
    }
    t.cancel()
    _ = await t.value
    let stubbornProcessed = await stubborn.processed
    c.that("不检查取消标志的任务照样跑完 3 轮(取消不是强杀)", stubbornProcessed == 3, "processed=\(stubbornProcessed)")
}

// [6] ── 结构化并发:必须等子任务 + 优先级
func scenario6(_ c: Check) async {
    c.section("[6] 结构化并发:父任务不会忘记等子任务")
    let log = Log()
    let total = await measure {
        await withTaskGroup(of: Void.self) { g in
            g.addTask { try? await Task.sleep(for: .milliseconds(200)); await log.add("慢 结束") }
            g.addTask { try? await Task.sleep(for: .milliseconds(40)); await log.add("快 结束") }
        }   // 作用域退出时保证所有子任务都已完成
        await log.add("group 返回")
    }
    let text = await log.text
    c.that("group 返回时最慢的子任务(200ms)已经做完", total >= 200, String(format: "%.0fms", total))
    c.that("父任务的后续代码排在全部子任务之后", text.hasSuffix("group 返回"), text)

    // 子任务默认继承父任务优先级
    let innerGroup = Task(priority: .utility) {
        await withTaskGroup(of: TaskPriority.self, returning: [TaskPriority].self) { g in
            for _ in 0..<3 { g.addTask { Task.currentPriority } }
            var out: [TaskPriority] = []
            for await p in g { out.append(p) }
            return out
        }
    }
    let inherited = await innerGroup.value
    c.that("子任务自动继承父任务优先级(.utility × 3)",
           inherited.count == 3 && inherited.allSatisfy { $0 == .utility }, "\(inherited)")

    // 高优先级任务去等一个低优先级任务 → 后者被提升
    let inner = Task(priority: .low) {
        try? await Task.sleep(for: .milliseconds(100))
        return Task.currentPriority
    }
    let outer = Task(priority: .high) { await inner.value }
    let reported = await outer.value
    c.that("被高优先级任务等待后,低优先级任务不再是 .low", reported != .low, "Task.currentPriority=\(reported)")
}

// [7] ── actor 不保证 FIFO
func scenario7(_ c: Check) async {
    c.section("[7] actor 不保证 FIFO")
    let ledger = Ledger()
    // 先投递「慢」的,再投递「快」的
    async let slow = ledger.record("先投递(200ms)", after: .milliseconds(200))
    async let fast = ledger.record("后投递(40ms)", after: .milliseconds(40))
    _ = await slow
    _ = await fast
    let order = await ledger.order
    c.that("后投递的先完成 → actor 上的 job 不保证按到达顺序收尾",
           order.first?.contains("后投递") == true, "\(order)")
}

// 顶层代码支持 await(Swift 5.7+ / SE-0343),所以入口不需要 @main。
// 注意:同一个模块里 main.swift 不能再用 @main 属性,两者会冲突。
let check = Check()
await scenario1(check)
await scenario2(check)
await scenario3(check)
await scenario4(check)
await scenario5(check)
await scenario6(check)
await scenario7(check)
exit(check.summarize())
