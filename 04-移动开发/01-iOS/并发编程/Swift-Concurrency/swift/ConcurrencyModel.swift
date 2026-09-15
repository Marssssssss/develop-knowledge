// ConcurrencyModel.swift
// Swift 并发参考模型 —— 被 main.swift 的 7 个场景复用。
//
// 术语对齐权威文档(见同目录 README「参考资料」):
//   * SE-0304 把"任务的一次运行片段"称为 **job**:
//     "The execution of a task can be seen as a succession of periods where the task
//      was running, each of which ends at a suspension point" —— 本文件里叫**同步段**。
//   * SE-0304 把"提交的任务永不并发执行"的执行器称为 **exclusive executor**:
//     "given any two jobs that were submitted and run, the end of one must
//      happen-before the beginning of the other" —— 这就是 actor 的串行保证。
//   * SE-0306:actor 的 async 方法是**可重入**的,挂起点会释放 actor。

import Foundation

// MARK: - 断言与计时

/// 断言计数器。只在顶层串行使用,@unchecked Sendable 是为了让它能安全穿过 async 边界
/// (它不持有任何被并发访问的可变状态 —— 所有跨任务共享的状态都由 actor 持有)。
final class Check: @unchecked Sendable {
    private(set) var passed = 0
    private(set) var failures: [String] = []

    func section(_ title: String) { print(title) }

    func that(_ label: String, _ condition: Bool, _ detail: String = "") {
        let tail = detail.isEmpty ? "" : "   \(detail)"
        if condition { passed += 1; print("  [PASS] \(label)\(tail)") }
        else { failures.append(label); print("  [FAIL] \(label)\(tail)") }
    }

    func summarize() -> Int32 {
        print("\n断言 \(passed) 通过 / \(failures.count) 失败")
        if !failures.isEmpty { print("失败项:", failures) }
        return failures.isEmpty ? 0 : 1
    }
}

/// Duration → 毫秒
func ms(_ d: Duration) -> Double {
    Double(d.components.seconds) * 1000 + Double(d.components.attoseconds) / 1e15
}

/// 用 ContinuousClock 实测一段 async 代码的墙钟耗时(毫秒)。
func measure(_ body: () async -> Void) async -> Double {
    let clock = ContinuousClock()
    let start = clock.now
    await body()
    return ms(start.duration(to: clock.now))
}

// MARK: - 共享日志(多个并发任务往同一个有序表里写)

actor Log {
    private var lines: [String] = []
    func add(_ line: String) { lines.append(line) }
    var text: String { lines.joined(separator: " → ") }
}

actor Tally {
    private(set) var processed = 0
    func add() { processed += 1 }
}

// MARK: - [2] actor:同步段原子 ⇒ 无丢失更新

actor BankAccount {
    private(set) var balance = 0
    private(set) var seen: [Int] = []
    private(set) var jobs = 0

    /// 读-改-写全在**一个 job** 内完成,中间没有 await ⇒ 不可能被打断。
    func deposit(_ amount: Int, pause: Duration = .milliseconds(10)) async -> Int {
        jobs += 1
        balance += amount
        seen.append(balance)
        try? await Task.sleep(for: pause)     // 挂起点:actor 在此刻对其它 job 开放
        return balance
    }
}

/// 无隔离的对照:同一个"读-改-写"被拆到两次 await 之间。
/// 即使每一次读写本身都是原子的,结果依然是错的 —— actor 的存在就是为了防这个。
actor SplitCounter {
    private(set) var value = 0
    func read() -> Int { value }
    func write(_ v: Int) { value = v }
}

// MARK: - [3] 可重入:check, await, act

actor Vault {
    private(set) var balance: Int
    init(_ balance: Int) { self.balance = balance }

    /// 天真写法:check 与 act 之间隔着一次 await → 中间是重入窗口。
    func naiveWithdraw(_ amount: Int) async -> String {
        if balance >= amount {                                   // check
            try? await Task.sleep(for: .milliseconds(50))         // await:actor 被释放
            balance -= amount                                    // act:前提可能已失效
            return "ok"
        }
        return "rejected"
    }

    /// 正确写法:复检紧贴在 act 之前,两者在同一个 job 内 ⇒ 原子。
    func carefulWithdraw(_ amount: Int) async -> String {
        guard balance >= amount else { return "rejected" }
        try? await Task.sleep(for: .milliseconds(50))
        guard balance >= amount else { return "rejected" }        // await 之后必须复检
        balance -= amount
        return "ok"
    }
}

// MARK: - [4] 可重入的收益:两个 actor 互相 await(SE-0306 的 OddOddy/EvenEvan)

actor Parity {
    let name: String
    private var peer: Parity?
    /// 同一 actor 上**同时处于挂起中**的调用数峰值。
    /// 只要它 > 1,就说明可重入真的发生了(否则第二次调用会被挡在门外)。
    private(set) var maxConcurrentEntries = 0
    private var active = 0

    init(_ name: String) { self.name = name }
    func link(to other: Parity) { peer = other }

    func isEven(_ n: Int) async -> Bool {
        active += 1
        maxConcurrentEntries = max(maxConcurrentEntries, active)
        defer { active -= 1 }
        guard n != 0 else { return true }
        guard let peer else { return false }
        return await peer.isOdd(n - 1)          // 挂起点
    }

    func isOdd(_ n: Int) async -> Bool {
        active += 1
        maxConcurrentEntries = max(maxConcurrentEntries, active)
        defer { active -= 1 }
        guard n != 0 else { return false }
        guard let peer else { return true }
        return await peer.isEven(n - 1)
    }
}

// MARK: - [7] actor 不保证 FIFO

actor Ledger {
    private(set) var order: [String] = []
    /// 先睡再记账:先投递、但睡得久的那个会后完成。
    @discardableResult
    func record(_ label: String, after delay: Duration) async -> String {
        try? await Task.sleep(for: delay)
        order.append(label)
        return label
    }
}
