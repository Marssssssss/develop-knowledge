// ObjCARC.m
// Objective-C ARC 内存管理 —— retainCount 显式观察 + strong/weak/__unsafe_unretained
//
// 编译(在 macOS 上):
//   clang -fobjc-arc -framework Foundation ObjCARC.m -o objc_arc
//   ./objc_arc
//
// 来源(权威,见 README 参考资料):
//   1. Apple Transitioning to ARC Release Notes(developer.apple.com)
//   2. clang llvm.org ARC 文档
//   3. WWDC 2011 #322 "Objective-C Advancements in Depth" (ARC 起源)
//
// 本 demo 4 个用例覆盖(对应 Swift 版本的 4 个):
//   1. 基本引用计数 + retainCount NSLog 输出
//   2. Person/Apartment 强引用循环 + weak 解决
//   3. Customer/CreditCard 强引用循环 + __unsafe_unretained 对应 unowned
//   4. Block 闭包 + __weak self 捕获列表
//
// 注意: macOS 命令行使用 NSLog / NSObjectFoundation.framework
//       ARC 下 retain/release/retainCount 方法仍可调用但编译器会告警;
//       用 CFGetRetainCount(ObjectType *) 替代更标准。

#import <Foundation/Foundation.h>

// =============================================================================
// 用例 1 — 基本引用计数
// =============================================================================

@interface Person1 : NSObject
@property (nonatomic, copy) NSString *name;
- (instancetype)initWithName:(NSString *)name;
@end
@implementation Person1
- (instancetype)initWithName:(NSString *)name {
    if (self = [super init]) { _name = [name copy]; NSLog(@"[1] Person1 %@ init", _name); }
    return self;
}
- (void)dealloc { NSLog(@"[1] Person1 %@ dealloc", _name); }
@end

// =============================================================================
// 用例 2 — Person/Apartment 强引用循环 + weak
// =============================================================================

@class Person2;
@interface Apartment2 : NSObject
@property (nonatomic, copy) NSString *unit;
@property (nonatomic, weak) Person2 *tenant;       // weak -> tenant 可为 nil
- (instancetype)initWithUnit:(NSString *)unit;
@end
@interface Person2 : NSObject
@property (nonatomic, copy) NSString *name;
@property (nonatomic, strong) Apartment2 *apartment;
- (instancetype)initWithName:(NSString *)name;
@end
@implementation Apartment2
- (instancetype)initWithUnit:(NSString *)unit {
    if (self = [super init]) { _unit = [unit copy]; NSLog(@"[2] Apartment2 %@ init", _unit); }
    return self;
}
- (void)dealloc { NSLog(@"[2] Apartment2 %@ dealloc", _unit); }
@end
@implementation Person2
- (instancetype)initWithName:(NSString *)name {
    if (self = [super init]) { _name = [name copy]; NSLog(@"[2] Person2 %@ init", _name); }
    return self;
}
- (void)dealloc { NSLog(@"[2] Person2 %@ dealloc", _name); }
@end

// =============================================================================
// 用例 3 — Customer/CreditCard 强引用循环 + __unsafe_unretained 对应 unowned
// =============================================================================

@class Customer3;
@interface CreditCard3 : NSObject
@property (nonatomic, readonly) uint64_t number;
@property (nonatomic, unsafe_unretained) Customer3 *customer;  // unsafe_unretained ≈ Swift unowned
- (instancetype)initWithNumber:(uint64_t)number customer:(Customer3 *)customer;
@end
@interface Customer3 : NSObject
@property (nonatomic, copy) NSString *name;
@property (nonatomic, strong) CreditCard3 *card;
- (instancetype)initWithName:(NSString *)name;
@end
@implementation CreditCard3
- (instancetype)initWithNumber:(uint64_t)number customer:(Customer3 *)customer {
    if (self = [super init]) { _number = number; _customer = customer;
        NSLog(@"[3] CreditCard3 #%llu init", number); }
    return self;
}
- (void)dealloc { NSLog(@"[3] CreditCard3 #%llu dealloc", _number); }
@end
@implementation Customer3
- (instancetype)initWithName:(NSString *)name {
    if (self = [super init]) { _name = [name copy]; NSLog(@"[3] Customer3 %@ init", _name); }
    return self;
}
- (void)dealloc { NSLog(@"[3] Customer3 %@ dealloc", _name); }
@end

// =============================================================================
// 用例 4 — Block 闭包 + __weak self 捕获列表
// =============================================================================

typedef NSString *(^HTMLElementBlock)(void);

@interface HTMLElement4 : NSObject
@property (nonatomic, copy) NSString *name;
@property (nonatomic, copy) NSString *text;
@property (nonatomic, copy) HTMLElementBlock asHTML;   // 默认 strong self
@property (nonatomic, copy) HTMLElementBlock asHTMLWeakSelf;
- (instancetype)initWithName:(NSString *)name text:(NSString *)text;
- (void)setupWeakSelfBlock;
@end
@implementation HTMLElement4
- (instancetype)initWithName:(NSString *)name text:(NSString *)text {
    if (self = [super init]) { _name = [name copy]; _text = [text copy];
        NSLog(@"[4] HTMLElement4 <%@> init", _name); }
    return self;
}
- (void)setupWeakSelfBlock {
    __weak typeof(self) wself = self;
    _asHTMLWeakSelf = ^NSString *{
        __strong typeof(wself) sself = wself;     // weak -> strong 临时
        if (!sself) return @"<deinit>";
        return [NSString stringWithFormat:@"<%@>%@</%@>", sself.name, sself.text ?: @"", sself.name];
    };
}
- (void)dealloc { NSLog(@"[4] HTMLElement4 <%@> dealloc", _name); }
@end

// =============================================================================
// 主入口
// =============================================================================

void demo1_basicRefcount(void) {
    NSLog(@"\n===== 用例 1: 基本引用计数 =====");
    Person1 *p = [[Person1 alloc] initWithName:@"Alice"];
    NSLog(@"retainCount = %ld", (long)CFGetRetainCount(p));   // 1
    Person1 *p2 = p;
    NSLog(@"retainCount = %ld", (long)CFGetRetainCount(p));   // 2
    p2 = nil;
    NSLog(@"retainCount = %ld", (long)CFGetRetainCount(p));   // 1
    p = nil;     // 计数 → 0,dealloc
}

void demo2_weakBreaksCycle(void) {
    NSLog(@"\n===== 用例 2: weak 打破 Person/Apartment 强引用循环 =====");
    Person2 *john = [[Person2 alloc] initWithName:@"John"];
    Apartment2 *unit4A = [[Apartment2 alloc] initWithUnit:@"4A"];
    john.apartment = unit4A;
    unit4A.tenant = john;          // weak,不会增加 john retainCount
    NSLog(@"john ref count = %ld", (long)CFGetRetainCount(john));   // 1
    NSLog(@"apt  ref count = %ld", (long)CFGetRetainCount(unit4A)); // 2 (局部 + john.apartment)
    john = nil;
    unit4A = nil;
    // 二者都 dealloc
}

void demo3_unownedBreaksCycle(void) {
    NSLog(@"\n===== 用例 3: __unsafe_unretained 打破 Customer/CreditCard 强引用循环 =====");
    Customer3 *alice = [[Customer3 alloc] initWithName:@"Alice"];
    alice.card = [[CreditCard3 alloc] initWithNumber:1234567890123456ULL customer:alice];
    NSLog(@"alice ref count = %ld", (long)CFGetRetainCount(alice));        // 1
    NSLog(@"card  ref count = %ld", (long)CFGetRetainCount(alice.card));    // 1 (局部 + alice.card)
    alice = nil;     // alice 与 card 都 dealloc
    // 注:此后再访问 alice.card.customer 会触发悬挂指针(unsafe_unretained 不自动置 nil)
}

void demo4_closureCaptureList(void) {
    NSLog(@"\n===== 用例 4: Block 捕获列表 __weak 打破循环 =====");
    HTMLElement4 *p = [[HTMLElement4 alloc] initWithName:@"p" text:@"hello, world"];
    [p setupWeakSelfBlock];      // 把 asHTMLWeakSelf 设成 __weak self 版
    NSLog(@"asHTMLWeakSelf() = %@", p.asHTMLWeakSelf());
    p = nil;
    // HTMLElement4 应正常 dealloc,因为 asHTMLWeakSelf 是 __weak
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        demo1_basicRefcount();
        demo2_weakBreaksCycle();
        demo3_unownedBreaksCycle();
        demo4_closureCaptureList();
    }
    NSLog(@"\n=== all demos done ===");
    return 0;
}