// GCDPrimitives.h
// GCD 侧的三个原语 —— Swift actor/async-await 之前的那个世界。
//
// 为什么 ObjC 侧用 GCD 而不是"ObjC 版 actor":Swift 的 actor 与 async/await 都**没有**
// ObjC 等价物。ObjC 世界里"保护共享状态 + 等一组任务 + 把异步串起来"分别由
// dispatch_queue / dispatch_group / completion handler 三件事承担,
// 本文件把这三件事各写成一个最小原语,好在 main.m 里逐条对照 Swift 缺了什么。

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// MARK: - 断言

@interface LACheck : NSObject
@property (nonatomic, readonly) NSUInteger passed;
@property (nonatomic, readonly) NSArray<NSString *> *failures;
- (void)section:(NSString *)title;
- (void)that:(NSString *)label condition:(BOOL)condition detail:(NSString *)detail;
- (int)summarize;
@end

// MARK: - 1. 串行队列

/// 串行队列 + **队列专属键**。
///
/// `dispatch_get_current_queue()` 从 iOS 6 起被废弃:它只能返回"最内层"的队列,
/// 而队列是可以嵌套(target queue)的,所以它答不出"我是不是在 queueA 这条链上"。
/// 标准做法是给队列挂一个专属键,`dispatch_get_specific()` 会**沿队列层级向上查找**,
/// 命中就说明当前代码已经在这条链上 —— 此时再 `dispatch_sync` 会死锁,应当直接执行。
@interface LASerialQueue : NSObject
@property (nonatomic, readonly) dispatch_queue_t queue;
@property (nonatomic, readonly, copy) NSString *label;
/// 当前代码是否已经在本队列(或其后代队列)上。
@property (nonatomic, readonly) BOOL onQueue;
- (instancetype)initWithLabel:(NSString *)label;
/// 安全版的 dispatch_sync:已在队列上就直接执行,否则同步派发。
- (void)syncGuarded:(dispatch_block_t)block;
@end

// MARK: - 2. 任务组

/// `dispatch_group` —— 结构化并发的前身:能"等全部子任务",但没有编译期保证。
///
/// 与 `withTaskGroup` 的差距(也正是 Swift 把它做进语言的原因):
///   * 编译器不保证你调用 `wait`/`notify` —— 忘了就是漏等,毫无提示;
///   * 一个子任务失败**不会**自动取消兄弟任务(要自己写 `dispatch_group_enter/leave` 管理);
///   * 子任务是 detached 的,取消要靠 `dispatch_block_cancel`(只对未开始的 block 有效)。
@interface LAGroupWaiter : NSObject
@property (nonatomic, readonly) BOOL finished;
- (void)add:(dispatch_block_t)block onQueue:(dispatch_queue_t)queue;
/// 阻塞等待,返回是否在超时前等到全部完成。
- (BOOL)waitWithTimeout:(double)seconds queue:(nullable dispatch_queue_t)completionQueue
              onFinish:(dispatch_block_t)onFinish;
@end

// MARK: - 3. 把异步串起来

/// 一个可能失败的步骤:完成后必须调用回调继续。
typedef void (^LAStep)(void (^next)(NSError *_Nullable error));

/// 嵌套写法(回调地狱):每一步的后续都写在上一层回调里 → 嵌套深度 = 步骤数。
void LARunSequentialNested(NSArray<LAStep> *steps, void (^done)(NSError *_Nullable error));

/// 扁平写法:把"下一步"抽成显式续体,驱动循环只有一个递归点 → 嵌套深度恒为 1。
/// 编译器把 Swift 的 `await` 降级成什么,本质上就是这件事。
void LARunSequentialFlat(NSArray<LAStep> *steps, void (^done)(NSError *_Nullable error));

/// 上面两种写法在递归最深处的嵌套深度(用于定量对比"地狱"有多深)。
NSUInteger LACallbackNestingDepth(void);

NS_ASSUME_NONNULL_END
