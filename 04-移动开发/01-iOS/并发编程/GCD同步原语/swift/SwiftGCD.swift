// SwiftGCD.swift
// Swift GCD —— DispatchQueue / DispatchSemaphore / DispatchGroup / DispatchWorkItemFlags.barrier
//
// 编译/运行:
//   swift SwiftGCD.swift
//
// 来源(权威,见 README 参考资料):
//   1. Apple DispatchQueue 官方文档 (developer.apple.com/documentation/dispatch/dispatchqueue)
//   2. Apple "Concurrency Programming Guide - Dispatch Queues" (developer.apple.com/library/archive/.../OperationQueues.html)
//   3. Apple Grand Central Dispatch (GCD) Reference (libdispatch header 官方收录)
//   4. WWDC 2017 #706 "Modernizing Grand Central Dispatch Usage"
//
// 本 demo 4 个用例覆盖:
//   1. DispatchSemaphore 计数信号量(限制并发上限)
//   2. DispatchGroup fan-out / fan-in(异步任务统一回调)
//   3. concurrent queue + .barrier 实现读写锁
//   4. main queue sync 死锁演示(警告性 demo,展示什么是错的)

import Foundation

// =============================================================================
// 用例 1 — DispatchSemaphore 限制并发上限为 N
// =============================================================================

func demo1_semaphore() {
    print("\n===== 用例 1: DispatchSemaphore 限制并发 =====")
    let maxConcurrent = 3
    let totalTasks = 10
    let sem = DispatchSemaphore(value: maxConcurrent)
    let group = DispatchGroup()
    let q = DispatchQueue.global(qos: .userInitiated)
    let start = Date()

    for i in 0..<totalTasks {
        group.enter()
        q.async {
            sem.wait()                                  // 计数 -1
            defer { sem.signal(); group.leave() }       // 计数 +1
            // 模拟 200ms 工作
            Thread.sleep(forTimeInterval: 0.2)
            let elapsed = Date().timeIntervalSince(start)
            print("[task \(i)] running, elapsed=\(String(format: "%.2f", elapsed))s")
        }
    }

    group.notify(queue: .main) {
        let total = Date().timeIntervalSince(start)
        print("[semaphore] all \(totalTasks) tasks done in \(String(format: "%.2f", total))s "
              + "(expected ~\(Double(totalTasks)/Double(maxConcurrent) * 0.2)s)")
    }
    group.wait()       // 阻塞当前线程到 group 完成(避免 demo 提前 return)
}

// =============================================================================
// 用例 2 — DispatchGroup fan-out / fan-in
// =============================================================================

func demo2_group() {
    print("\n===== 用例 2: DispatchGroup fan-out/fan-in =====")
    let group = DispatchGroup()
    var results = [Int]()
    results.reserveCapacity(5)

    // 锁保护 results(闭包全部在并发 queue,跨线程写)
    let lock = NSLock()

    for i in 0..<5 {
        group.enter()
        DispatchQueue.global().async {
            // 模拟不同延迟的网络请求
            Thread.sleep(forTimeInterval: Double.random(in: 0.05...0.2))
            let r = i * 10
            lock.lock(); results.append(r); lock.unlock()
            print("[group] task \(i) -> \(r)")
            group.leave()
        }
    }

    group.notify(queue: .main) {
        print("[group] all 5 done, results = \(results.sorted())")
    }
    group.wait()
}

// =============================================================================
// 用例 3 — concurrent queue + .barrier 实现读写锁
// barrier write 串行化、读并发
// =============================================================================

final class ThreadSafeCache {
    private var storage: [String: Int] = [:]
    private let queue = DispatchQueue(label: "cache.q",
                                      attributes: .concurrent)

    func read(_ key: String) -> Int? {
        queue.sync { storage[key] }                     // 并发读
    }
    func write(_ key: String, value: Int) {
        queue.async(flags: .barrier) {                  // barrier: 等所有读完成后独占
            storage[key] = value
        }
    }
    func snapshot() -> [String: Int] {
        queue.sync(flags: .barrier) { storage }         // 整快照也用 barrier 保证一致
    }
}

func demo3_barrier() {
    print("\n===== 用例 3: concurrent + .barrier 读写锁 =====")
    let cache = ThreadSafeCache()
    let group = DispatchGroup()

    // 8 个并发读
    for i in 0..<8 {
        group.enter()
        DispatchQueue.global().async {
            let v = cache.read("k\(i % 3)")
            print("[barrier] reader \(i) -> k\(i % 3)=\(v ?? -1)")
            group.leave()
        }
    }
    // 3 个 barrier 写
    for i in 0..<3 {
        group.enter()
        DispatchQueue.global().async {
            cache.write("k\(i)", value: i * 100)
            print("[barrier] writer \(i) -> k\(i)=\(i*100)")
            group.leave()
        }
    }
    group.notify(queue: .main) {
        print("[barrier] snapshot = \(cache.snapshot())")
    }
    group.wait()
}

// =============================================================================
// 用例 4 — main queue sync 死锁演示(警告性,实际工作流严禁这样做)
// =============================================================================

func demo4_mainQueueDeadlock() {
    print("\n===== 用例 4(警告): main.sync 死锁演示 =====")
    print("[deadlock] 注: 本 demo 在 main 线程调用,故意制造死锁")
    print("[deadlock] 死锁条件: 已在 main queue 执行 -> sync 提交到 main queue -> 永远等不到自己")

    // 用 detached thread 演示,避免主 demo 卡死
    DispatchQueue.global().async {
        print("[deadlock] 在 background 线程,提交 sync 到 main,预期会死锁...")
        DispatchQueue.main.sync {
            print("[deadlock] 不可能到达 —— main.queue 正被外部 sync 占用")
        }
    }
    // 给 background 一点时间触发,主线程若进入 sync 会卡死
    Thread.sleep(forTimeInterval: 0.3)
    print("[deadlock] 主线程未卡死(bg 线程 sync main 的任务等 main 释放,本 demo 结束前 main 没空去执行)")

    // 真正演示死锁:让 main 自己 sync main
    // 取消注释即触发主 demo 卡死:
    // DispatchQueue.main.sync { print("不会执行") }
}

// =============================================================================
// 启动
// =============================================================================

demo1_semaphore()
demo2_group()
demo3_barrier()
demo4_mainQueueDeadlock()
print("\n=== all demos done ===")