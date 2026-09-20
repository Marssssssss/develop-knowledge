// NotificationCenter 的 Objective-C 侧对照(人工审查用)。
// 这里演示的是 NSNotification 时代的 selector 注册路径,以及「同步投递」这条最容易踩的规则。

#import <Foundation/Foundation.h>

@interface Trap : NSObject
@property (nonatomic) NSMutableArray<NSString *> *log;
@end

@implementation Trap

- (instancetype)init {
    if ((self = [super init])) {
        _log = [NSMutableArray array];
    }
    return self;
}

// MARK: - selector 注册:name / object 为 nil 表示不筛选

- (void)registerAll {
    [[NSNotificationCenter defaultCenter] addObserver:self
                                             selector:@selector(handle:)
                                                 name:nil        // 收所有名字
                                               object:nil];      // 收所有发送者
}

- (void)registerPrecise:(id)sender {
    [[NSNotificationCenter defaultCenter] addObserver:self
                                             selector:@selector(handle:)
                                                 name:@"N"
                                               object:sender];   // 两端都要匹配
}

- (void)handle:(NSNotification *)note {
    // 注意:这个方法跑在 **post 所在的线程** 上,没有自动切主线程
    [self.log addObject:note.name];
}

// MARK: - 同步投递:post 会等到所有 block / selector 跑完才返回

- (void)demonstrateSyncPost {
    __block BOOL finished = NO;
    id token = [[NSNotificationCenter defaultCenter]
        addObserverForName:@"N" object:nil queue:nil
                 usingBlock:^(NSNotification * _Nonnull note) {
        // 这里如果做耗时操作,post 的那一行就会被拖住
        [NSThread sleepForTimeInterval:0.5];
        finished = YES;
    }];

    [[NSNotificationCenter defaultCenter] postNotificationName:@"N" object:nil];
    // 走到这一行时 finished 已经是 YES —— post 是同步的
    NSAssert(finished, @"post 返回时 block 必然已经跑完");

    [[NSNotificationCenter defaultCenter] removeObserver:token];
}

// MARK: - 一次性通知:在 block 里摘掉自己

- (void)demonstrateOnce {
    __block id<NSObject> token = nil;
    token = [[NSNotificationCenter defaultCenter]
        addObserverForName:@"Once" object:nil queue:nil
                 usingBlock:^(NSNotification * _Nonnull note) {
        [self.log addObject:note.name];
        [[NSNotificationCenter defaultCenter] removeObserver:token];
    }];
    // 第二次 post 不会再命中
}

// MARK: - 注销:dealloc 之前必须摘干净

- (void)dealloc {
    [[NSNotificationCenter defaultCenter] removeObserver:self];
}

@end
