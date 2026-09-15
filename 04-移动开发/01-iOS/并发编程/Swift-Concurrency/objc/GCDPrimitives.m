// GCDPrimitives.m
#import "GCDPrimitives.h"

// MARK: - 断言

@implementation LACheck {
    NSUInteger _passed;
    NSMutableArray<NSString *> *_failures;
}
- (instancetype)init {
    if ((self = [super init])) { _failures = [NSMutableArray array]; }
    return self;
}
- (NSUInteger)passed { return _passed; }
- (NSArray<NSString *> *)failures { return [_failures copy]; }

- (void)section:(NSString *)title { printf("%s\n", title.UTF8String); }

- (void)that:(NSString *)label condition:(BOOL)condition detail:(NSString *)detail {
    NSString *tail = detail.length ? [NSString stringWithFormat:@"   %@", detail] : @"";
    if (condition) {
        _passed++;
        printf("  [PASS] %s%s\n", label.UTF8String, tail.UTF8String);
    } else {
        [_failures addObject:label];
        printf("  [FAIL] %s%s\n", label.UTF8String, tail.UTF8String);
    }
}

- (int)summarize {
    printf("\n断言 %lu 通过 / %lu 失败\n", (unsigned long)_passed, (unsigned long)_failures.count);
    if (_failures.count) { printf("失败项: %s\n", _failures.description.UTF8String); }
    return _failures.count ? 1 : 0;
}
@end

// MARK: - 串行队列

@implementation LASerialQueue {
    dispatch_queue_t _queue;
    const void *_key;          // 用 self 的地址做键 → 每个实例互不干扰
}

- (instancetype)initWithLabel:(NSString *)label {
    if ((self = [super init])) {
        _label = [label copy];
        _queue = dispatch_queue_create(label.UTF8String, DISPATCH_QUEUE_SERIAL);
        _key = (__bridge const void *)self;
        dispatch_queue_set_specific(_queue, _key, (void *)1, NULL);
    }
    return self;
}

- (dispatch_queue_t)queue { return _queue; }

- (BOOL)onQueue {
    // 沿队列层级向上查找:命中就说明当前代码在本队列(或其 target)上。
    return dispatch_get_specific(_key) != NULL;
}

- (void)syncGuarded:(dispatch_block_t)block {
    if (self.onQueue) {
        block();                    // 已在队列上 → 直接执行,绝不 dispatch_sync
    } else {
        dispatch_sync(_queue, block);
    }
}
@end

// MARK: - 任务组

@implementation LAGroupWaiter {
    dispatch_group_t _group;
}
- (instancetype)init {
    if ((self = [super init])) { _group = dispatch_group_create(); }
    return self;
}
- (BOOL)finished {
    // 只能"限时探测":返回 0 表示已全部完成
    return dispatch_group_wait(_group, dispatch_time(DISPATCH_TIME_NOW, 0)) == 0;
}
- (void)add:(dispatch_block_t)block onQueue:(dispatch_queue_t)queue {
    dispatch_group_async(_group, queue, block);
}
- (BOOL)waitWithTimeout:(double)seconds queue:(dispatch_queue_t)completionQueue
              onFinish:(dispatch_block_t)onFinish {
    dispatch_group_notify(_group, completionQueue ?: dispatch_get_main_queue(), onFinish);
    dispatch_time_t deadline = dispatch_time(DISPATCH_TIME_NOW,
                                             (int64_t)(seconds * NSEC_PER_SEC));
    return dispatch_group_wait(_group, deadline) == 0;
}
@end

// MARK: - 回调链

static NSUInteger gNestingDepth = 0;
static NSUInteger gCurrentDepth = 0;

NSUInteger LACallbackNestingDepth(void) { return gNestingDepth; }

void LARunSequentialNested(NSArray<LAStep> *steps, void (^done)(NSError *_Nullable error)) {
    gNestingDepth = 0;
    gCurrentDepth = 0;
    // 每一层的 next 里继续展开下一层 → 嵌套深度随步骤数线性增长
    __block void (^run)(NSUInteger);
    run = ^(NSUInteger i) {
        gCurrentDepth++;
        if (gCurrentDepth > gNestingDepth) { gNestingDepth = gCurrentDepth; }
        if (i >= steps.count) { gCurrentDepth--; done(nil); return; }
        steps[i](^(NSError *error) {
            if (error) { gCurrentDepth--; done(error); return; }
            run(i + 1);                     // 回调里再回调 —— 地狱就在这里
        });
    };
    run(0);
}

void LARunSequentialFlat(NSArray<LAStep> *steps, void (^done)(NSError *_Nullable error)) {
    gNestingDepth = 1;
    gCurrentDepth = 1;
    // 只有一个递归点:下一步由循环推进,而不是嵌在上一层回调里
    for (NSUInteger i = 0; i < steps.count; i++) {
        __block BOOL ok = NO;
        __block NSError *stepError = nil;
        steps[i](^(NSError *error) { stepError = error; ok = YES; });
        if (!ok) { done([NSError errorWithDomain:@"flat" code:1 userInfo:nil]); return; }
        if (stepError) { done(stepError); return; }
    }
    done(nil);
}
