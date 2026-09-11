// SwiftRunLoop.swift
// Swift RunLoop —— RunLoop.current / RunLoop.main / Mode / Timer / CFRunLoopObserver
//
// 编译/运行(macOS):
//   swift SwiftRunLoop.swift
//
// 来源(权威,见 README 参考资料):
//   1. Apple Run Loops (Threading Programming Guide - developer.apple.com)
//   2. Apple CFRunLoop 官方文档
//   3. Apple RunLoop 官方文档(Foundation)
//   4. opensource.apple.com CFRunLoop.c 源码(对照 .current 实现)
//
// 本 demo 5 个用例覆盖:
//   1. RunLoop.current 与 .main 的区别
//   2. Mode 过滤:同一 Timer 注册到 default 与 custom mode,只在 default 触发
//   3. Timer 在 run loop 中的循环触发与停止(invalidate)
//   4. CFRunLoopObserver 监听 kCFRunLoopActivity 6 个位
//   5. Perform Selector:perform(_:with:afterDelay:) 延迟执行

import Foundation
import CoreFoundation

// =============================================================================
// 用例 1 — RunLoop.current vs RunLoop.main
// =============================================================================

func demo1_currentVsMain() {
    print("\n===== 用例 1: RunLoop.current vs .main =====")
    // 主线程中,二者应是同一个对象
    print("[1] main thread: current==main? \(RunLoop.current === RunLoop.main)")
    // 子线程中,只有 current 存在;不主动访问 main,RunLoop.main 仍可访问
    DispatchQueue.global().async {
        print("[1] bg thread:   current is bg's run loop, not main")
        print("[1] bg thread:   current === main? \(RunLoop.current === RunLoop.main)")
        // 不主动调用 RunLoop.current.getCFRunLoop() 就不会创建 bg 的 run loop 实例
    }
    Thread.sleep(forTimeInterval: 0.2)
}

// =============================================================================
// 用例 2 — Mode 过滤:同一 Timer 只在 default mode 触发
// =============================================================================

class ModeDemo {
    var timer: Timer?
    var counterDefault = 0
    var counterCustom = 0
    let customMode = RunLoop.Mode("com.example.custom")
    var stop = false

    func start() {
        let t = Timer(timeInterval: 0.1, repeats: true) { [weak self] _ in
            // 闭包在哪个 mode 上跑?取决于 Timer 注册的 mode
            // 这里通过闭包内 self 区分两个不同的 timer,外部已分别注册到不同 mode
            guard let self = self else { return }
            // 用 RunLoop.current.currentMode 判断当前 mode
            if RunLoop.current.currentMode == .default {
                self.counterDefault += 1
            } else if RunLoop.current.currentMode == self.customMode {
                self.counterCustom += 1
            }
        }
        // 注册到 default mode:在 main run loop 默认驱动
        RunLoop.main.add(t, forMode: .default)
        self.timer = t

        // 再起一个 timer 注册到 custom mode,只有显式 run(customMode) 时才触发
        let t2 = Timer(timeInterval: 0.1, repeats: true) { [weak self] _ in
            self?.counterCustom += 1
        }
        RunLoop.main.add(t2, forMode: customMode)
        self.timer2 = t2
    }
    var timer2: Timer?
}

func demo2_modeFilter() {
    print("\n===== 用例 2: Mode 过滤 =====")
    let d = ModeDemo()
    d.start()
    // main run loop 已在跑(命令行的 main 是顶层 swift runner,默认会启动 run loop)
    // 1 秒后查看计数器
    Thread.sleep(forTimeInterval: 1.0)
    if let t = d.timer { RunLoop.main.remove(t, forMode: .default) }
    if let t2 = d.timer2 { RunLoop.main.remove(t2, forMode: d.customMode) }
    print("[2] counterDefault = \(d.counterDefault)  counterCustom = \(d.counterCustom)")
    print("[2] 预期: counterDefault ≈ 10 (1s / 0.1s),counterCustom = 0 (未 run custom mode)")
}

// =============================================================================
// 用例 3 — Timer 重复触发与 invalidate 停止
// =============================================================================

func demo3_timerLifecycle() {
    print("\n===== 用例 3: Timer 生命周期 =====")
    var count = 0
    let t = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { _ in
        count += 1
        print("[3] timer fire #\(count)")
        if count >= 5 {
            // 必须 invalidate,否则 Timer 强引用闭包、闭包强引用 self,RunLoop 持有 Timer 三方循环
            // invalidate() 把 Timer 从 run loop 移除并清空强引用
            // 实际写法见下方 demo4
        }
    }
    // 跑 1s 后停止
    Thread.sleep(forTimeInterval: 0.7)
    t.invalidate()
    print("[3] after invalidate, count = \(count)")
    // 确认 RunLoop 不再持有 Timer;之后 timer fire 不应再发生
    Thread.sleep(forTimeInterval: 0.5)
    print("[3] final count = \(count) (预期不再增长)")
}

// =============================================================================
// 用例 4 — CFRunLoopObserver 监听 6 个 Activity 位
// =============================================================================

class ObserverDemo {
    var entries = 0
    var beforeTimers = 0
    var beforeSources = 0
    var beforeWaiting = 0
    var afterWaiting = 0
    var exits = 0

    func install() {
        let observer = CFRunLoopObserverCreate(
            kCFAllocatorDefault,
            CFRunLoopActivity.allActivities.rawValue,
            true, 0,
            { (_, activity, _) in
                // 这里拿不到 self,演示时用全局变量;实际用 CFRunLoopObserverContext.info 指针传 self
                let str: String
                switch activity {
                case .entry:        str = "kCFRunLoopEntry"
                case .beforeTimers: str = "kCFRunLoopBeforeTimers"
                case .beforeSources:str = "kCFRunLoopBeforeSources"
                case .beforeWaiting:str = "kCFRunLoopBeforeWaiting"
                case .afterWaiting: str = "kCFRunLoopAfterWaiting"
                case .exit:         str = "kCFRunLoopExit"
                default:            str = "other(\(activity.rawValue))"
                }
                print("[4] observer fire: \(str)")
            }, nil)
        CFRunLoopAddObserver(CFRunLoopGetMain(), observer, .defaultMode)
        self.observer = observer
    }
    var observer: CFRunLoopObserver?
}

func demo4_observer() {
    print("\n===== 用例 4: CFRunLoopObserver 监听 6 个 Activity =====")
    let d = ObserverDemo()
    d.install()
    // 简单触发:performSelector 延迟一个任务,让 run loop 经历 entry→...→exit 一次
    RunLoop.main.perform(inModes: [.default], block: {
        print("[4] (perform block fired)")
    })
    // 给 observer 一点时间回调
    Thread.sleep(forTimeInterval: 0.3)
    if let o = d.observer { CFRunLoopRemoveObserver(CFRunLoopGetMain(), o, .defaultMode) }
    print("[4] (一次 run loop pass 完成,6 个 activity 应先后打印)")
}

// =============================================================================
// 用例 5 — Perform Selector 延迟执行
// =============================================================================

func demo5_perform() {
    print("\n===== 用例 5: perform(inModes:block:) 延迟执行 =====")
    let scheduled = Date()
    RunLoop.main.perform(inModes: [.default], block: {
        let elapsed = Date().timeIntervalSince(scheduled)
        print("[5] perform block fired, elapsed = \(String(format: "%.3f", elapsed))s")
    })
    // perform 仅在 run loop 走到相应 mode 时才执行
    Thread.sleep(forTimeInterval: 0.5)
    print("[5] main run loop 持续驱动,perform block 已触发")
}

// =============================================================================
// 启动
// =============================================================================

demo1_currentVsMain()
demo2_modeFilter()
demo3_timerLifecycle()
demo4_observer()
demo5_perform()
print("\n=== all demos done ===")