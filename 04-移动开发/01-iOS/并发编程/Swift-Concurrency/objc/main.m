// main.m —— GCD 世界的 6 个场景
//
// 构建运行:
//   clang -fobjc-arc -framework Foundation GCDPrimitives.m main.m -o objc-gcd && ./objc-gcd
//
// 每个场景回答同一个问题的 ObjC 版:"没有 actor / async-await 的时候,这件事怎么做,
// 代价是什么"。Swift 侧给的是语言特性,这一侧给的是语言特性之前的工程手法。

#import "GCDPrimitives.h"
#import <dispatch/dispatch.h>
#import <unistd.h>

// MARK: - [1] 串行队列:互斥 + 严格 FIFO

static void scenario1(LACheck *c) {
    [c section:@"[1] 串行队列:互斥 + 严格 FIFO"];
    LASerialQueue *q = [[LASerialQueue alloc] initWithLabel:@"com.demo.serial"];
    dispatch_semaphore_t finished = dispatch_semaphore_create(0);
    NSMutableArray<NSString *> *order = [NSMutableArray array];

    // 故意让先提交的跑得久、后提交的跑得快:串行队列仍然严格按提交顺序执行
    for (int i = 0; i < 3; i++) {
        int idx = i;
        dispatch_async(q.queue, ^{
            usleep((useconds_t)(15000 - idx * 5000));
            [order addObject:[NSString stringWithFormat:@"block%d", idx]];
            dispatch_semaphore_signal(finished);
        });
    }
    for (int i = 0; i < 3; i++) { dispatch_semaphore_wait(finished, DISPATCH_TIME_FOREVER); }
    [c that:@"串行队列严格 FIFO:完成顺序 = 提交顺序(与各自耗时无关)"
      condition:[order isEqualToArray:(@[@"block0", @"block1", @"block2"])]
        detail:order.description];

    // 互斥:4 个 block × 250 次自增,在串行队列上结果精确
    __block NSInteger safe = 0;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    for (int i = 0; i < 4; i++) {
        dispatch_async(q.queue, ^{
            for (int k = 0; k < 250; k++) { safe++; }
            dispatch_semaphore_signal(done);
        });
    }
    for (int i = 0; i < 4; i++) { dispatch_semaphore_wait(done, DISPATCH_TIME_FOREVER); }
    [c that:@"串行队列保证互斥:4×250 次自增精确等于 1000"
      condition:(safe == 1000)
        detail:[NSString stringWithFormat:@"safe=%ld", (long)safe]];

    // 对照:把同一个"读-改-写"放到并发队列上,中间留一个可重入窗口
    __block NSInteger racy = 0;
    dispatch_queue_t global = dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0);
    dispatch_semaphore_t done2 = dispatch_semaphore_create(0);
    for (int i = 0; i < 4; i++) {
        dispatch_async(global, ^{
            NSInteger snapshot = racy;          // 读
            usleep(2000);                       // 窗口:四个 block 都会读到同一个旧值
            racy = snapshot + 1;                // 写回「旧值 + 1」
            dispatch_semaphore_signal(done2);
        });
    }
    for (int i = 0; i < 4; i++) { dispatch_semaphore_wait(done2, DISPATCH_TIME_FOREVER); }
    [c that:@"并发队列上同一个读-改-写:4 次自增只剩 1(丢失更新)"
      condition:(racy < 4)
        detail:[NSString stringWithFormat:@"racy=%ld", (long)racy]];
}

// MARK: - [2] 队列层级:为什么检测死锁不能用 dispatch_get_current_queue

static void scenario2(LACheck *c) {
    [c section:@"[2] 队列层级:专属键 vs 已废弃的 dispatch_get_current_queue"];
    LASerialQueue *upstream = [[LASerialQueue alloc] initWithLabel:@"com.demo.upstream"];
    dispatch_queue_t downstream = dispatch_queue_create("com.demo.downstream", DISPATCH_QUEUE_SERIAL);
    dispatch_set_target_queue(downstream, upstream.queue);   // downstream 由 upstream 提供执行上下文

    __block BOOL sawUpstreamKey = NO;
    dispatch_sync(downstream, ^{
        // 当前代码跑在 downstream 上,但它的「上游」是 upstream
        sawUpstreamKey = upstream.onQueue;
    });
    [c that:@"在 target 队列上执行时,沿层级能查到上游队列的专属键"
      condition:sawUpstreamKey
        detail:@"dispatch_get_current_queue 只会返回 downstream,答不出「在不在 upstream 链上」"];

    __block NSInteger depth = 0;
    [upstream syncGuarded:^{
        depth++;
        [upstream syncGuarded:^{ depth++; }];   // 已在上游链上 → 直接执行,不死锁
    }];
    [c that:@"守卫版 sync:检测到自己已在队列上时直接执行,而不是 dispatch_sync"
      condition:(depth == 2)
        detail:[NSString stringWithFormat:@"depth=%ld", (long)depth]];

    // QoS 是队列的声明属性,不是调度保证 —— 这里验证它能被读回来
    dispatch_queue_attr_t attr =
        dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL, QOS_CLASS_UTILITY, 0);
    dispatch_queue_t utilityQueue = dispatch_queue_create("com.demo.utility", attr);
    int relative = 0;
    qos_class_t cls = dispatch_queue_get_qos_class(utilityQueue, &relative);
    [c that:@"QoS 是队列的声明属性,可以原样读回(dispatch_queue_get_qos_class)"
      condition:(cls == QOS_CLASS_UTILITY)
        detail:@"但「高优先级先跑」只是执行器的倾向;SE-0304 明说执行器不保证按提交顺序运行"];

    __block BOOL insideIsOnQueue = NO;
    [upstream syncGuarded:^{ insideIsOnQueue = upstream.onQueue; }];
    [c that:@"从队列外部调用时,block 真的被派发到队列上执行(内部 onQueue = YES)"
      condition:insideIsOnQueue detail:@"守卫逻辑只在「已经在链上」时走直接执行分支"];
}

// MARK: - [3] dispatch_group:能等,但没有编译期保证

static void scenario3(LACheck *c) {
    [c section:@"[3] dispatch_group:能等全部,但忘了等没人提醒你"];
    dispatch_queue_t work = dispatch_queue_create("com.demo.work", DISPATCH_QUEUE_CONCURRENT);
    LAGroupWaiter *group = [LAGroupWaiter new];
    dispatch_semaphore_t lock = dispatch_semaphore_create(1);
    NSMutableArray<NSNumber *> *done = [NSMutableArray array];

    for (int i = 0; i < 3; i++) {
        int idx = i;
        [group add:^{
            usleep((useconds_t)((idx + 1) * 20000));
            dispatch_semaphore_wait(lock, DISPATCH_TIME_FOREVER);
            [done addObject:@(idx)];
            dispatch_semaphore_signal(lock);
        } onQueue:work];
    }
    BOOL allDone = [group waitWithTimeout:2.0
                                    queue:dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0)
                                 onFinish:^{ }];
    [c that:@"dispatch_group_wait 等到全部 3 个子任务"
      condition:(allDone && done.count == 3) detail:done.description];
    [c that:@"子任务完成顺序不保证,只保证都被等到(耗时 60ms 的那个最后完成)"
      condition:(done.count == 3 && [done.lastObject intValue] == 2)
        detail:@"最后一个完成的是耗时最久的那个"];

    // 对照:忘掉 wait —— 没有任何编译期提示,主线程直接往下读到不完整的结果
    LAGroupWaiter *forgotten = [LAGroupWaiter new];
    NSMutableArray<NSNumber *> *partial = [NSMutableArray array];
    for (int i = 0; i < 3; i++) {
        int idx = i;
        [forgotten add:^{
            usleep(50000);
            dispatch_semaphore_wait(lock, DISPATCH_TIME_FOREVER);
            [partial addObject:@(idx)];
            dispatch_semaphore_signal(lock);
        } onQueue:work];
    }
    [c that:@"忘了 wait 时调用方立刻读到 0 条结果 —— 漏等是静默错误"
      condition:(partial.count == 0)
        detail:[NSString stringWithFormat:@"partial=%lu", (unsigned long)partial.count]];
    (void)[forgotten waitWithTimeout:2.0
                               queue:dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0)
                            onFinish:^{ }];
}

// MARK: - [4] 把异步串起来:嵌套 vs 扁平续体

static void scenario4(LACheck *c) {
    [c section:@"[4] 回调地狱:嵌套深度 = 步骤数"];
    LAStep s1 = ^(void (^next)(NSError *error)) { usleep(2000); next(nil); };
    LAStep s2 = ^(void (^next)(NSError *error)) { usleep(2000); next(nil); };
    LAStep s3 = ^(void (^next)(NSError *error)) { usleep(2000); next(nil); };
    NSArray<LAStep> *steps = @[ s1, s2, s3 ];

    __block NSError *nestedError = nil;
    LARunSequentialNested(steps, ^(NSError *error) { nestedError = error; });
    NSUInteger nestedDepth = LACallbackNestingDepth();

    __block NSError *flatError = nil;
    LARunSequentialFlat(steps, ^(NSError *error) { flatError = error; });
    NSUInteger flatDepth = LACallbackNestingDepth();

    [c that:@"嵌套写法:递归嵌套深度 = 步骤数(3 步 → 3 层)"
      condition:(nestedDepth == steps.count)
        detail:[NSString stringWithFormat:@"depth=%lu", (unsigned long)nestedDepth]];
    [c that:@"扁平续体写法:嵌套深度恒为 1(3 步 → 1 层)"
      condition:(flatDepth == 1)
        detail:[NSString stringWithFormat:@"depth=%lu", (unsigned long)flatDepth]];
    [c that:@"两种写法结果一致(都成功走到最后一步),差别只在代码形状"
      condition:(nestedError == nil && flatError == nil)
        detail:@"而形状决定了错误处理要重复写几遍"];

    // 失败传播:嵌套写法要在每一层回调里重复判断 error
    LAStep f1 = ^(void (^next)(NSError *error)) { next(nil); };
    LAStep f2 = ^(void (^next)(NSError *error)) {
        next([NSError errorWithDomain:@"demo" code:42 userInfo:nil]);
    };
    LAStep f3 = ^(void (^next)(NSError *error)) { next(nil); };   // 不应被调用到
    __block BOOL reachedEnd = NO;
    LARunSequentialNested(@[ f1, f2, f3 ], ^(NSError *error) { reachedEnd = (error == nil); });
    [c that:@"中间步骤失败 → 后续步骤不再执行,错误冒到最外层"
      condition:(!reachedEnd) detail:@"Swift 里这一整套被 await + throws 取代"];
}

// MARK: - [5] 取消:只有「尚未开始」的能取消

static void scenario5(LACheck *c) {
    [c section:@"[5] GCD 没有取消:未开始的 block 能取消,已开始的自求多福"];
    dispatch_queue_t q = dispatch_queue_create("com.demo.cancelable", DISPATCH_QUEUE_SERIAL);
    dispatch_semaphore_t gate = dispatch_semaphore_create(0);
    dispatch_semaphore_t confirm = dispatch_semaphore_create(0);
    NSMutableArray<NSString *> *ran = [NSMutableArray array];

    // 先用一个卡住的 block 占住队列,制造「后面那个还没开始」的状态
    dispatch_async(q, ^{ dispatch_semaphore_wait(gate, DISPATCH_TIME_FOREVER); });

    dispatch_block_t pending = dispatch_block_create(0, ^{ [ran addObject:@"未开始的那个"]; });
    dispatch_async(q, pending);
    dispatch_block_cancel(pending);          // 尚未开始 → 取消生效
    dispatch_async(q, ^{ dispatch_semaphore_signal(confirm); });   // 排在它之后,用来确认它被跳过
    dispatch_semaphore_signal(gate);         // 放开队列

    long rc = dispatch_semaphore_wait(confirm,
                                      dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.0 * NSEC_PER_SEC)));
    [c that:@"dispatch_block_cancel 对尚未开始的 block 有效(它被直接跳过)"
      condition:(rc == 0 && ran.count == 0)
        detail:[NSString stringWithFormat:@"ran=%lu", (unsigned long)ran.count]];

    // 已经在跑的 block:没有任何 API 能把它停下来,只能靠自己检查标志位
    __block BOOL stop = NO;
    __block NSInteger ticks = 0;
    dispatch_semaphore_t started = dispatch_semaphore_create(0);
    dispatch_semaphore_t halted = dispatch_semaphore_create(0);
    dispatch_async(q, ^{
        dispatch_semaphore_signal(started);
        for (int i = 0; i < 1000 && !stop; i++) { ticks++; usleep(1000); }
        dispatch_semaphore_signal(halted);
    });
    dispatch_semaphore_wait(started, DISPATCH_TIME_FOREVER);
    usleep(5000);
    stop = YES;                              // 协作式取消:置标志,由 block 自己退出
    rc = dispatch_semaphore_wait(halted, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.0 * NSEC_PER_SEC)));
    [c that:@"已在执行的 block 只能靠自己的标志位退出(协作式取消)"
      condition:(rc == 0 && ticks < 1000)
        detail:[NSString stringWithFormat:@"ticks=%ld/1000", (long)ticks]];
}

// MARK: - [6] self-sync 真死锁(放最后:会故意泄漏一个永久阻塞的线程)

static void scenario6(LACheck *c) {
    [c section:@"[6] 真死锁复现:在队列上再 dispatch_sync 回同一队列"];
    LASerialQueue *q = [[LASerialQueue alloc] initWithLabel:@"com.demo.deadlock"];

    // 先看守卫版:同样嵌套两层,因为检测到已在上游链上,直接执行 → 正常返回
    __block NSInteger guardedDepth = 0;
    [q syncGuarded:^{
        guardedDepth++;
        [q syncGuarded:^{ guardedDepth++; }];
    }];
    [c that:@"守卫版同样嵌套两层,但因为避开了 dispatch_sync → 正常返回"
      condition:(guardedDepth == 2)
        detail:[NSString stringWithFormat:@"depth=%ld", (long)guardedDepth]];

    // 再看不守卫的版本:此刻我们就在 q 上,再 sync 回 q → 队列等自己
    dispatch_semaphore_t survived = dispatch_semaphore_create(0);
    dispatch_async(q.queue, ^{
        dispatch_sync(q.queue, ^{ dispatch_semaphore_signal(survived); });
        dispatch_semaphore_signal(survived);          // 到不了这里
    });
    long rc = dispatch_semaphore_wait(survived,
                                      dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.0 * NSEC_PER_SEC)));
    [c that:@"队列上的 block 内 sync 回同一队列 → 1 秒内没有任何 block 返回(死锁)"
      condition:(rc != 0)
        detail:@"本场景故意留下一个永久阻塞的线程与一条卡死的队列,断言完就 exit"];
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        LACheck *c = [LACheck new];
        scenario1(c);
        scenario2(c);
        scenario3(c);
        scenario4(c);
        scenario5(c);
        scenario6(c);
        exit([c summarize]);
    }
    return 0;
}
