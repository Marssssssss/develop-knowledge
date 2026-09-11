// ObjCGCD.m
// Objective-C GCD —— dispatch_semaphore / dispatch_group / dispatch_barrier
//
// 编译/运行(macOS):
//   clang -fobjc-arc -framework Foundation ObjCGCD.m -o objc_gcd
//   ./objc_gcd
//
// 来源(权威,见 README 参考资料):
//   1. Apple Dispatch framework 官方文档
//   2. Apple Grand Central Dispatch (GCD) Reference
//   3. Apple Concurrency Programming Guide - Dispatch Queues
//
// 本 demo 4 个用例对应 Swift 版本:
//   1. dispatch_semaphore 限制并发
//   2. dispatch_group + dispatch_group_notify
//   3. concurrent queue + dispatch_barrier_async 实现读写锁
//   4. dispatch_sync 主队列死锁警告

#import <Foundation/Foundation.h>

// =============================================================================
// 用例 1 — dispatch_semaphore 限制并发
// =============================================================================

void demo1_semaphore(void) {
    NSLog(@"\n===== 用例 1: dispatch_semaphore 限制并发 =====");
    const NSInteger maxConcurrent = 3;
    const NSInteger totalTasks = 10;
    dispatch_semaphore_t sem = dispatch_semaphore_create(maxConcurrent);
    dispatch_group_t group = dispatch_group_create();
    dispatch_queue_t q = dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0);
    NSDate *start = [NSDate date];

    for (NSInteger i = 0; i < totalTasks; i++) {
        dispatch_group_enter(group);
        dispatch_async(q, ^{
            dispatch_semaphore_wait(sem, DISPATCH_TIME_FOREVER);   // 计数 -1
            [NSThread sleepForTimeInterval:0.2];
            NSTimeInterval e = [[NSDate date] timeIntervalSinceDate:start];
            NSLog(@"[task %ld] running, elapsed=%.2fs", (long)i, e);
            dispatch_semaphore_signal(sem);                         // 计数 +1
            dispatch_group_leave(group);
        });
    }
    dispatch_group_wait(group, DISPATCH_TIME_FOREVER);
    NSTimeInterval total = [[NSDate date] timeIntervalSinceDate:start];
    NSLog(@"[semaphore] all %ld tasks done in %.2fs",
          (long)totalTasks, total);
}

// =============================================================================
// 用例 2 — dispatch_group fan-out / fan-in
// =============================================================================

@interface Group2State : NSObject
@property (nonatomic, strong) NSMutableArray<NSNumber *> *results;
@property (nonatomic, strong) NSLock *lock;
@end
@implementation Group2State
- (instancetype)init { if (self = [super init]) {
    _results = [NSMutableArray array]; _lock = [[NSLock alloc] init]; } return self;
}
@end

void demo2_group(void) {
    NSLog(@"\n===== 用例 2: dispatch_group fan-out/fan-in =====");
    dispatch_group_t group = dispatch_group_create();
    Group2State *state = [[Group2State alloc] init];

    for (NSInteger i = 0; i < 5; i++) {
        dispatch_group_enter(group);
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
            // 模拟不同延迟
            [NSThread sleepForTimeInterval:(0.05 + (arc4random_uniform(150)) / 1000.0)];
            NSInteger r = i * 10;
            [state.lock lock];
            [state.results addObject:@(r)];
            [state.lock unlock];
            NSLog(@"[group] task %ld -> %ld", (long)i, (long)r);
            dispatch_group_leave(group);
        });
    }
    dispatch_group_notify(group, dispatch_get_main_queue(), ^{
        // 排序(闭包在并发 queue 上写,这里读)
        [state.lock lock];
        NSArray *sorted = [state.results sortedArrayUsingSelector:@selector(compare:)];
        [state.lock unlock];
        NSLog(@"[group] all 5 done, results = %@", sorted);
    });
    dispatch_group_wait(group, DISPATCH_TIME_FOREVER);
}

// =============================================================================
// 用例 3 — concurrent queue + dispatch_barrier_async 读写锁
// =============================================================================

@interface Cache3 : NSObject {
    NSMutableDictionary<NSString *, NSNumber *> *_storage;
    dispatch_queue_t _queue;
}
- (NSNumber *)read:(NSString *)key;
- (void)write:(NSString *)key value:(NSInteger)value;
- (NSDictionary *)snapshot;
@end
@implementation Cache3
- (instancetype)init {
    if (self = [super init]) {
        _storage = [NSMutableDictionary dictionary];
        _queue = dispatch_queue_create("cache.q", DISPATCH_QUEUE_CONCURRENT);
    }
    return self;
}
- (NSNumber *)read:(NSString *)key {
    __block NSNumber *v;
    dispatch_sync(_queue, ^{ v = _storage[key]; });
    return v;
}
- (void)write:(NSString *)key value:(NSInteger)value {
    dispatch_barrier_async(_queue, ^{ _storage[key] = @(value); });
}
- (NSDictionary *)snapshot {
    __block NSDictionary *snap;
    dispatch_barrier_sync(_queue, ^{ snap = [_storage copy]; });
    return snap;
}
@end

void demo3_barrier(void) {
    NSLog(@"\n===== 用例 3: concurrent + dispatch_barrier_async =====");
    Cache3 *cache = [[Cache3 alloc] init];
    dispatch_group_t group = dispatch_group_create();

    for (NSInteger i = 0; i < 8; i++) {
        dispatch_group_enter(group);
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
            NSString *k = [NSString stringWithFormat:@"k%ld", (long)(i % 3)];
            NSNumber *v = [cache read:k];
            NSLog(@"[barrier] reader %ld -> %@=%@", (long)i, k, v);
            dispatch_group_leave(group);
        });
    }
    for (NSInteger i = 0; i < 3; i++) {
        dispatch_group_enter(group);
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
            [cache write:[NSString stringWithFormat:@"k%ld", (long)i] value:i * 100];
            NSLog(@"[barrier] writer %ld -> k%ld=%ld", (long)i, (long)i, (long)(i*100));
            dispatch_group_leave(group);
        });
    }
    dispatch_group_notify(group, dispatch_get_main_queue(), ^{
        NSLog(@"[barrier] snapshot = %@", [cache snapshot]);
    });
    dispatch_group_wait(group, DISPATCH_TIME_FOREVER);
}

// =============================================================================
// 用例 4 — 主队列 dispatch_sync 死锁警告
// =============================================================================

void demo4_mainQueueDeadlock(void) {
    NSLog(@"\n===== 用例 4(警告): main.sync 死锁演示 =====");
    NSLog(@"[deadlock] 注: 在 main 线程调用,故意演示死锁原理");

    // bg 线程 sync 到 main —— main 线程被外部 dispatch_main / RunLoop 占用时永远等
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
        NSLog(@"[deadlock] bg 线程提交 sync 到 main,预期会死锁...");
        dispatch_sync(dispatch_get_main_queue(), ^{
            NSLog(@"[deadlock] 不可能到达 —— main.queue 已被外部 sync 占用");
        });
    });
    [NSThread sleepForTimeInterval:0.3];
    NSLog(@"[deadlock] main 线程未卡死(bg sync main 永远等 main 空闲)");
    // 真正死锁:在 main 线程自己 sync 自己 —— 取消注释即触发:
    // dispatch_sync(dispatch_get_main_queue(), ^{ NSLog(@"不会执行"); });
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        demo1_semaphore();
        demo2_group();
        demo3_barrier();
        demo4_mainQueueDeadlock();
    }
    NSLog(@"\n=== all demos done ===");
    return 0;
}