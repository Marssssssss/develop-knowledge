// SwiftUI / Observation 的"前世今生" —— Objective-C 视角:手写细粒度依赖追踪。
//
// 权威依据(实际读过):
//   * Apple/SwiftUI/State:`@State` 是视图的单一数据源,存储由 SwiftUI 管理;
//     "the view will only update when the reference to the object changes"(把
//     ObservableObject 放进 State 的经典陷阱);默认值每次实例化都被求值。
//   * Apple/Observation / swift-evolution SE-0395:Observation 用宏 + 注册器
//     (ObservationRegistrar 的 access / withMutation / willSet)替代
//     Combine 时代的 ObservableObject + @Published —— 后者在任何属性变化前广播
//     objectWillChange,粒度是整个对象;前者只记录 apply 闭包里**读过**的属性。
//
// Observation 出现之前,想在 ObjC 里做同样的事必须手写:一张"属性 → 依赖"表 +
// 在 setter 里查表通知。本文件就是那张表,和 Swift / Python 版对照着看。
//
// 运行: clang -fobjc-arc -framework Foundation ObjCStateTracking.m -o state && ./state

#import <Foundation/Foundation.h>

// ---------------------------------------------------------------------------
// 1. 依赖图:属性 key → 依赖回调列表(ObservationRegistrar 的 access/mutation)
// ---------------------------------------------------------------------------
@interface LADependencyGraph : NSObject
@property (nonatomic, readonly) NSInteger notifyCount;
- (void)beginTracking;
- (instancetype)endTrackingWithOnChange:(void (^)(void))onChange;
- (void)accessKey:(NSString *)key;
- (void)mutatingKey:(NSString *)key;
@end

@implementation LADependencyGraph {
    NSMutableSet<NSString *> *_reads;
    NSMutableDictionary<NSString *, NSMutableArray<void (^)(void)> *> *_deps;
    NSInteger _notifyCount;
}

- (instancetype)init {
    if ((self = [super init])) {
        _reads = nil;
        _deps = [NSMutableDictionary dictionary];
    }
    return self;
}

- (NSInteger)notifyCount { return _notifyCount; }

- (void)beginTracking { _reads = [NSMutableSet set]; }

- (instancetype)endTrackingWithOnChange:(void (^)(void))onChange {
    for (NSString *key in _reads) {
        NSMutableArray *list = _deps[key];
        if (!list) { list = [NSMutableArray array]; _deps[key] = list; }
        [list addObject:[onChange copy]];
    }
    _reads = nil;
    return self;
}

- (void)accessKey:(NSString *)key {
    [_reads addObject:key];                  // 只在追踪期内有效
}

- (void)mutatingKey:(NSString *)key {
    NSMutableArray *list = _deps[key];       // 一次性:通知后清空
    [_deps removeObjectForKey:key];
    for (void (^cb)(void) in list) {
        _notifyCount++;
        cb();
    }
}
@end

// ---------------------------------------------------------------------------
// 2. 可观察对象:任何写操作都先过依赖图
// ---------------------------------------------------------------------------
@interface LAObservableObject : NSObject
@property (nonatomic, readonly) LADependencyGraph *graph;
- (void)setTitle:(NSString *)title;
- (void)setAvailable:(BOOL)available;
- (BOOL)available;
@end

@implementation LAObservableObject {
    NSString *_title;
    BOOL _available;
}

- (instancetype)init {
    if ((self = [super init])) { _graph = [LADependencyGraph new]; _title = @"A sample book"; _available = YES; }
    return self;
}

- (void)setTitle:(NSString *)title {
    [_graph mutatingKey:@"title"];            // 先通知、后写入
    _title = [title copy];
}

- (void)setAvailable:(BOOL)available {
    [_graph mutatingKey:@"isAvailable"];
    _available = available;
}

- (BOOL)available {
    [_graph accessKey:@"isAvailable"];
    return _available;
}
@end

// ---------------------------------------------------------------------------
// 3. 视图:body 求值期读到的属性成为依赖
// ---------------------------------------------------------------------------
@interface LARenderingView : NSObject
@property (nonatomic, readonly) NSInteger renders;
@property (nonatomic, copy) NSArray<NSString *> *reads;
@property (nonatomic, strong) LADependencyGraph *graph;
- (instancetype)initWithGraph:(LADependencyGraph *)graph;
- (void)render;                               // 模拟一次 body 求值
@end

@implementation LARenderingView
- (instancetype)initWithGraph:(LADependencyGraph *)graph {
    if ((self = [super init])) { _graph = graph; }
    return self;
}

- (void)render {
    [_graph beginTracking];
    for (NSString *key in _reads ?: @[]) [_graph accessKey:key];
    __weak LARenderingView *weakSelf = self;
    [_graph endTrackingWithOnChange:^{ [weakSelf body]; }];
    [self body];
}

- (void)body { _renders++; }
@end

// ---------------------------------------------------------------------------
// 4. ObservableObject 的粗粒度广播(Combine 时代)
// ---------------------------------------------------------------------------
@interface LACoarsePublisher : NSObject
@property (nonatomic, readonly) NSInteger notifyCount;
- (void)subscribe:(void (^)(void))cb;
- (void)willChange:(NSString *)key;
@end

@implementation LACoarsePublisher {
    NSMutableArray<void (^)(void)> *_observers;
    NSInteger _notifyCount;
}

- (instancetype)init {
    if ((self = [super init])) { _observers = [NSMutableArray array]; }
    return self;
}

- (NSInteger)notifyCount { return _notifyCount; }

- (void)subscribe:(void (^)(void))cb { [_observers addObject:[cb copy]]; }

- (void)willChange:(NSString *)key {
    for (void (^cb)(void) in _observers) { _notifyCount++; cb(); }
}
@end

// ---------------------------------------------------------------------------
// 5. @State 存储:按"视图身份"存,跨 body 求值保留
// ---------------------------------------------------------------------------
@interface LAStateStore : NSObject
@property (nonatomic, readonly) NSInteger initCount;
- (void)makeIdentity:(NSString *)identity initial:(id)initial;
- (id)valueForIdentity:(NSString *)identity;
- (void)setValue:(id)value forIdentity:(NSString *)identity;
- (void)removeIdentity:(NSString *)identity;
@end

@implementation LAStateStore {
    NSMutableDictionary<NSString *, id> *_store;
    NSInteger _initCount;
}

- (instancetype)init {
    if ((self = [super init])) { _store = [NSMutableDictionary dictionary]; }
    return self;
}

- (NSInteger)initCount { return _initCount; }

- (void)makeIdentity:(NSString *)identity initial:(id)initial {
    _initCount++;                             // 默认值每次实例化都会求值
    if (!_store[identity]) _store[identity] = initial;
}

- (id)valueForIdentity:(NSString *)identity { return _store[identity]; }
- (void)setValue:(id)value forIdentity:(NSString *)identity { _store[identity] = value; }
- (void)removeIdentity:(NSString *)identity { [_store removeObjectForKey:identity]; }
@end

// ---------------------------------------------------------------------------
// 6. 自检
// ---------------------------------------------------------------------------
static int gPass = 0, gFail = 0;

static void Check(NSString *label, BOOL ok, NSString *detail) {
    ok ? gPass++ : gFail++;
    printf("  [%s] %s%s\n", ok ? "PASS" : "FAIL", label.UTF8String,
           detail.length ? [@"   " stringByAppendingString:detail].UTF8String : "");
}

int main(void) {
    @autoreleasepool {
        printf("[1] 细粒度失效:只通知读过该属性的视图\n");
        LAObservableObject *book = [LAObservableObject new];
        LARenderingView *titleOnly = [[LARenderingView alloc] initWithGraph:book.graph];
        titleOnly.reads = @[ @"title" ];
        LARenderingView *both = [[LARenderingView alloc] initWithGraph:book.graph];
        both.reads = @[ @"title", @"isAvailable" ];
        [titleOnly render];
        [both render];
        NSInteger r1 = titleOnly.renders, r2 = both.renders;
        NSInteger n0 = book.graph.notifyCount;

        [book setAvailable:NO];               // 与 title 无关
        NSInteger fineDelta = book.graph.notifyCount - n0;
        Check(@"改 isAvailable 不触发只读 title 的视图", titleOnly.renders == r1,
              [NSString stringWithFormat:@"renders=%ld", (long)titleOnly.renders]);
        Check(@"改 isAvailable 触发读过它的视图", both.renders == r2 + 1,
              [NSString stringWithFormat:@"renders=%ld", (long)both.renders]);
        Check(@"细粒度:这一次只通知 1 个依赖(读过 isAvailable 的那个)",
              fineDelta == 1, [NSString stringWithFormat:@"notify=%ld", (long)fineDelta]);
        [book setTitle:@"New title"];
        Check(@"改 title 同时触发两个视图",
              titleOnly.renders == r1 + 1 && both.renders == r2 + 2, @"");
        printf("[2] 对照:ObservableObject 的粗粒度广播\n");
        LACoarsePublisher *pub = [LACoarsePublisher new];
        __block NSInteger coarseRenders = 0;
        [pub subscribe:^{ coarseRenders++; }];
        [pub willChange:@"title"];
        [pub willChange:@"isAvailable"];
        Check(@"粗粒度:同一场景广播 2 次(哪怕订阅者没读过 isAvailable)",
              pub.notifyCount == 2,
              [NSString stringWithFormat:@"notify=%ld vs 细粒度 1", (long)pub.notifyCount]);
        printf("[3] 追踪的一次性\n");
        LAObservableObject *car = [LAObservableObject new];
        __block NSInteger fires = 0;
        [car.graph beginTracking];
        [car.graph accessKey:@"title"];
        [car.graph endTrackingWithOnChange:^{ fires++; }];
        [car setTitle:@"Lightning"];
        Check(@"第一次修改触发 onChange", fires == 1,
              [NSString stringWithFormat:@"fires=%ld", (long)fires]);
        [car setTitle:@"Sally"];
        Check(@"未重新登记时第二次不再触发(一次性)", fires == 1,
              [NSString stringWithFormat:@"fires=%ld", (long)fires]);
        [car.graph beginTracking];
        [car.graph accessKey:@"title"];
        [car.graph endTrackingWithOnChange:^{ fires++; }];
        [car setTitle:@"Doc"];
        Check(@"重新登记后再次触发", fires == 2,
              [NSString stringWithFormat:@"fires=%ld", (long)fires]);
        printf("[4] @State 存储生命周期\n");
        LAStateStore *store = [LAStateStore new];
        [store makeIdentity:@"PlayerView#1" initial:@0];
        Check(@"首次实例化采用默认值 0",
              [store valueForIdentity:@"PlayerView#1"] != nil &&
                  [[store valueForIdentity:@"PlayerView#1"] isEqual:@0], @"");
        [store setValue:@0.75 forIdentity:@"PlayerView#1"];
        [store makeIdentity:@"PlayerView#1" initial:@0];   // body 重算 → 结构体再次实例化
        Check(@"重新实例化后旧值保留(默认值被忽略)",
              [[store valueForIdentity:@"PlayerView#1"] isEqual:@0.75],
              [store valueForIdentity:@"PlayerView#1"].description);
        Check(@"默认值仍被求值(别在默认值里做重活)", store.initCount == 2,
              [NSString stringWithFormat:@"initCount=%ld", (long)store.initCount]);
        [store removeIdentity:@"PlayerView#1"];
        [store makeIdentity:@"PlayerView#1" initial:@0];
        Check(@"视图移除后状态销毁,回到默认值",
              [[store valueForIdentity:@"PlayerView#1"] isEqual:@0], @"");
        printf("[5] 陷阱:ObservableObject 放进 @State\n");
        LACoarsePublisher *pub2 = [LACoarsePublisher new];
        LAStateStore *store2 = [LAStateStore new];
        NSMutableDictionary *objRef = [@{@"title" : @"A"} mutableCopy];
        [store2 makeIdentity:@"ContentView#1" initial:objRef];
        __block NSInteger renders = 1;
        objRef[@"title"] = @"B";              // 只改对象内部属性,不碰 @State
        Check(@"只改内部属性:引用未变、也没订阅 objectWillChange → 视图不更新",
              renders == 1, [NSString stringWithFormat:@"renders=%ld", (long)renders]);
        [store2 setValue:@{@"title" : @"B"} forIdentity:@"ContentView#1"];   // 换引用
        renders++;
        Check(@"换掉整个引用后视图更新(正确做法:@StateObject / @Observable)",
              renders == 2, [NSString stringWithFormat:@"renders=%ld", (long)renders]);
        __block NSInteger objRenders = 1;
        [pub2 subscribe:^{ objRenders++; }];
        [pub2 willChange:@"title"];
        Check(@"@StateObject 订阅后属性变化也能触发更新",
              objRenders == 2 && pub2.notifyCount == 1,
              [NSString stringWithFormat:@"renders=%ld", (long)objRenders]);
        printf("\n断言 %d 通过 / %d 失败\n", gPass, gFail);
    }
    return gFail == 0 ? 0 : 1;
}
