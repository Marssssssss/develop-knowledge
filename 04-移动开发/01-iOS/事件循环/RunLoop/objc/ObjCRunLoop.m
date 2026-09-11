// ObjCRunLoop.m
// Objective-C RunLoop —— NSRunLoop / CFRunLoopRef / Timer / Observer
//
// 编译/运行(macOS):
//   clang -fobjc-arc -framework Foundation ObjCRunLoop.m -o objc_runloop
//   ./objc_runloop
//
// 来源(权威,见 README 参考资料):
//   1. Apple Run Loops(Threading Programming Guide)
//   2. Apple CFRunLoop 官方文档
//   3. Apple NSRunLoop 官方文档
//
// 本 demo 5 个用例对应 Swift 版本:
//   1. currentRunLoop vs mainRunLoop
//   2. Mode 过滤
//   3. Timer 生命周期
//   4. CFRunLoopObserver 监听 6 个 Activity
//   5. performSelector:withObject:afterDelay: 延迟

#import <Foundation/Foundation.h>

// =============================================================================
// 用例 1 — currentRunLoop vs mainRunLoop
// =============================================================================

void demo1_currentVsMain(void) {
    NSLog(@"\n===== 用例 1: currentRunLoop vs mainRunLoop =====");
    NSLog(@"[1] main thread: current==main? %d",
          [NSRunLoop currentRunLoop] == [NSRunLoop mainRunLoop]);
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
        NSLog(@"[1] bg thread:   current is bg's run loop, not main");
        NSLog(@"[1] bg thread:   current === main? %d",
              [NSRunLoop currentRunLoop] == [NSRunLoop mainRunLoop]);
    });
    [NSThread sleepForTimeInterval:0.2];
}

// =============================================================================
// 用例 2 — Mode 过滤
// =============================================================================

static NSInteger gCounterDefault = 0;
static NSInteger gCounterCustom = 0;
static NSString *const kCustomMode = @"com.example.custom";

void demo2_modeFilter(void) {
    NSLog(@"\n===== 用例 2: Mode 过滤 =====");
    NSTimer *t1 = [NSTimer scheduledTimerWithTimeInterval:0.1 repeats:YES
                                                   block:^(NSTimer * _Nonnull t) {
        if ([NSRunLoop currentRunLoop].currentMode == NSDefaultRunLoopMode)
            gCounterDefault++;
    }];
    NSTimer *t2 = [NSTimer scheduledTimerWithTimeInterval:0.1 repeats:YES
                                                   block:^(NSTimer * _Nonnull t) {
        gCounterCustom++;
    }];
    // 把 t2 转移到 custom mode(原 scheduledTimer 自动注册到 default)
    [[NSRunLoop mainRunLoop] addTimer:t2 forMode:kCustomMode];
    [NSThread sleepForTimeInterval:1.0];
    [t1 invalidate];
    [t2 invalidate];
    NSLog(@"[2] counterDefault=%ld counterCustom=%ld (预期 ~10 / 0)",
          (long)gCounterDefault, (long)gCounterCustom);
}

// =============================================================================
// 用例 3 — Timer 生命周期(invalidate 停止)
// =============================================================================

void demo3_timerLifecycle(void) {
    NSLog(@"\n===== 用例 3: Timer 生命周期 =====");
    __block NSInteger count = 0;
    NSTimer *t = [NSTimer scheduledTimerWithTimeInterval:0.1 repeats:YES
                                                  block:^(NSTimer * _Nonnull t) {
        count++;
        NSLog(@"[3] timer fire #%ld", (long)count);
    }];
    [NSThread sleepForTimeInterval:0.7];
    [t invalidate];
    NSLog(@"[3] after invalidate, count=%ld", (long)count);
    [NSThread sleepForTimeInterval:0.5];
    NSLog(@"[3] final count=%ld (预期不再增长)", (long)count);
}

// =============================================================================
// 用例 4 — CFRunLoopObserver 监听 6 个 Activity
// =============================================================================

static void ObserverCallback(CFRunLoopObserverRef observer,
                             CFRunLoopActivity activity, void *info) {
    NSString *str;
    switch (activity) {
        case kCFRunLoopEntry:        str = @"kCFRunLoopEntry"; break;
        case kCFRunLoopBeforeTimers: str = @"kCFRunLoopBeforeTimers"; break;
        case kCFRunLoopBeforeSources:str = @"kCFRunLoopBeforeSources"; break;
        case kCFRunLoopBeforeWaiting:str = @"kCFRunLoopBeforeWaiting"; break;
        case kCFRunLoopAfterWaiting: str = @"kCFRunLoopAfterWaiting"; break;
        case kCFRunLoopExit:         str = @"kCFRunLoopExit"; break;
        default:                     str = [NSString stringWithFormat:@"other(%lu)", (unsigned long)activity];
    }
    NSLog(@"[4] observer fire: %@", str);
}

void demo4_observer(void) {
    NSLog(@"\n===== 用例 4: CFRunLoopObserver =====");
    CFRunLoopObserverRef o = CFRunLoopObserverCreate(
        kCFAllocatorDefault,
        kCFRunLoopAllActivities,
        YES, 0, ObserverCallback, NULL);
    CFRunLoopAddObserver(CFRunLoopGetMain(), o, kCFRunLoopDefaultMode);

    [[NSRunLoop mainRunLoop] performBlock:^{
        NSLog(@"[4] (perform block fired)");
    }];

    [NSThread sleepForTimeInterval:0.3];
    CFRunLoopRemoveObserver(CFRunLoopGetMain(), o, kCFRunLoopDefaultMode);
    CFRelease(o);
}

// =============================================================================
// 用例 5 — performSelector:withObject:afterDelay: 延迟执行
// =============================================================================

void demo5_perform(void) {
    NSLog(@"\n===== 用例 5: performSelector:afterDelay: =====");
    NSDate *scheduled = [NSDate date];
    [[NSRunLoop mainRunLoop] performSelector:@selector(performCallback)
                                   withObject:nil
                                      waitUntilDone:NO];

    // 实现 performCallback 用 performBlock 即可
    [[NSRunLoop mainRunLoop] performBlock:^{
        NSTimeInterval e = [[NSDate date] timeIntervalSinceDate:scheduled];
        NSLog(@"[5] perform block fired, elapsed=%.3fs", e);
    }];

    [NSThread sleepForTimeInterval:0.5];
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        demo1_currentVsMain();
        demo2_modeFilter();
        demo3_timerLifecycle();
        demo4_observer();
        demo5_perform();
    }
    NSLog(@"\n=== all demos done ===");
    return 0;
}

// 让 main 持续(命令行跑完后 run loop 自然结束)
// 注意:命令行 Foundation 程序通常不自动启动主 run loop,
// 因此本 demo 的 "run loop pass" 依赖 main run loop 在 dispatch_async/task
// 完成后系统默认 dispatch 事件源驱动一次循环;真实 iOS app 由 UIApplicationMain
// 启动主 run loop 并维持无限循环。
// 若需手动维持:dispatch_main();  // 不会返回