// LaunchOrder.m —— pre-main 的 Initializers 阶段到底按什么顺序、调用了谁
//
// 构建运行:
//   clang -fobjc-arc -framework Foundation LaunchOrder.m -o launch-order && ./launch-order
//
// 这个文件不测"快不快",只测**顺序与次数** —— 它们全部是 ObjC runtime 的硬保证,
// 也正是启动优化里最容易踩的那几个点:
//   * +load 在 main() 之前被调用,而且是**直接按 IMP 调用**,不走 objc_msgSend;
//   * 父类的 +load 先于子类;类的 +load 先于它自己的分类;
//   * 因此 +load **不会被继承**:子类没实现,父类的 +load 不会被替它调用一次;
//   * +initialize 相反:懒(第一次发消息才跑)、走 objc_msgSend、**会被继承** ——
//     所以父类的 +initialize 实现可能被执行多次(标准对策是判 self == [Parent class]);
//   * __attribute__((constructor)) 与 +load 都在 main() 之前,但两者的相对顺序不保证。

#import <Foundation/Foundation.h>
#import <string.h>

// 记录事件:用 C 数组而不是 NSMutableArray —— +load 跑得太早,
// 不该依赖"ObjC 容器已经可用"这件事(这正是 Apple 文档说 +load 里"能做的事很少"的原因)。
static const char *gEvents[64];
static int gEventCount = 0;

static void record(const char *what) {
    if (gEventCount < 64) { gEvents[gEventCount++] = what; }
}

static int firstIndex(const char *what) {
    for (int i = 0; i < gEventCount; i++) {
        if (strcmp(gEvents[i], what) == 0) { return i; }
    }
    return -1;
}

static int countOf(const char *what) {
    int n = 0;
    for (int i = 0; i < gEventCount; i++) {
        if (strcmp(gEvents[i], what) == 0) { n++; }
    }
    return n;
}

// MARK: - 被观察的类

@interface SuperClass : NSObject
@end

@implementation SuperClass
+ (void)load { record("+load SuperClass"); }

+ (void)initialize {
    // 关键:同一个 IMP 会被 SuperClass 与 SubClass 各触发一次,所以要分清 self
    if (self == [SuperClass class]) { record("+initialize SuperClass self=SuperClass"); }
    else { record("+initialize SuperClass self=SubClass(继承来的实现)"); }
}
@end

// 分类的 +load 排在"它所属的那个类"的 +load 之后。
// 而且 +load 是少数"类和分类都实现时两者都会被调用"的方法之一(不发生覆盖)。
@interface SuperClass (Extra)
@end

@implementation SuperClass (Extra)
+ (void)load { record("+load SuperClass(Extra) 分类"); }
@end

// 子类**故意不实现 +load**:用来证明 +load 不被继承。
@interface SubClass : SuperClass
@end

@implementation SubClass
@end

// 另一个子类实现了 +load:用来证明"父类 +load 先于子类 +load"。
@interface SubWithLoad : SuperClass
@end

@implementation SubWithLoad
+ (void)load { record("+load SubWithLoad"); }
@end

// 只有 +initialize、没有 +load:用来证明 +initialize 是懒的。
@interface LazyOnly : NSObject
@end

@implementation LazyOnly
+ (void)initialize { record("+initialize LazyOnly"); }
@end

// MARK: - C 构造器:同样在 main() 之前跑

__attribute__((constructor))
static void ctor_one(void) { record("__attribute__((constructor)) #1"); }

__attribute__((constructor))
static void ctor_two(void) { record("__attribute__((constructor)) #2"); }

// MARK: - 断言

static int gPassed = 0;
static int gFailed = 0;

static void that(const char *label, int ok, const char *detail) {
    if (ok) { gPassed++; }
    else { gFailed++; }
    printf("  [%s] %s%s%s\n", ok ? "PASS" : "FAIL", label,
           detail[0] ? "   " : "", detail);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        int atMainEntry = gEventCount;      // main 刚开始时已经发生了几个事件
        printf("[0] pre-main 期间记录到 %d 个事件:\n", atMainEntry);
        for (int i = 0; i < atMainEntry; i++) { printf("      %2d. %s\n", i, gEvents[i]); }

        printf("\n[1] +load 与 constructor 全部早于 main()\n");
        {
            char buf[256];
            snprintf(buf, sizeof(buf), "main() 入口时 +load 已发生 %d 次",
                     countOf("+load SuperClass") + countOf("+load SuperClass(Extra) 分类")
                     + countOf("+load SubWithLoad"));
            that("三个 +load 都已在 main() 之前执行完",
                 firstIndex("+load SuperClass") >= 0
                 && firstIndex("+load SuperClass(Extra) 分类") >= 0
                 && firstIndex("+load SubWithLoad") >= 0, buf);
            that("两个 __attribute__((constructor)) 也已在 main() 之前执行完",
                 firstIndex("__attribute__((constructor)) #1") >= 0
                 && firstIndex("__attribute__((constructor)) #2") >= 0,
                 "两者与 +load 的相对顺序不保证,只保证都在 main() 之前");
        }

        printf("\n[2] 父类先于子类、类先于分类\n");
        {
            int iSuper = firstIndex("+load SuperClass");
            int iSub = firstIndex("+load SubWithLoad");
            int iCat = firstIndex("+load SuperClass(Extra) 分类");
            char buf[256];
            snprintf(buf, sizeof(buf), "SuperClass=%d  SubWithLoad=%d  分类=%d", iSuper, iSub, iCat);
            that("父类的 +load 先于子类的 +load", iSuper >= 0 && iSub > iSuper, buf);
            that("类的 +load 先于它自己的分类的 +load", iCat > iSuper, buf);
            that("类和分类的 +load 都被调用(不会被分类覆盖)",
                 countOf("+load SuperClass") == 1 && countOf("+load SuperClass(Extra) 分类") == 1,
                 "同一个类与其分类各算一次");
        }

        printf("\n[3] +load 不被继承\n");
        {
            char buf[256];
            snprintf(buf, sizeof(buf), "SuperClass 的 +load 执行了 %d 次(期望 1,不是 2)",
                     countOf("+load SuperClass"));
            that("子类没实现 +load 时,父类的 +load 不会被替它再跑一次",
                 countOf("+load SuperClass") == 1, buf);
            that("这才是 +load 的代价:它是按 IMP 直接调用,不走 objc_msgSend",
                 countOf("+load SuperClass") == 1, "所以子类不会"继承"到一次调用");
        }

        printf("\n[4] +initialize 是懒的\n");
        {
            that("main() 开始时,LazyOnly 的 +initialize 还没跑",
                 firstIndex("+initialize LazyOnly") == -1,
                 "没被发过消息的类,它的 +initialize 一次都不跑");
            [LazyOnly class];                       // 只取类对象,不发消息
            that("仅取类对象也不触发 +initialize", firstIndex("+initialize LazyOnly") == -1, "[LazyOnly class] 不是消息");
            (void)[LazyOnly new];                   // 第一次发消息
            that("第一次发消息后 +initialize 才跑",
                 firstIndex("+initialize LazyOnly") >= 0, "+initialize 走 objc_msgSend");
            (void)[LazyOnly new];
            that("再发消息不会重复初始化(每个类只 initialize 一次)",
                 countOf("+initialize LazyOnly") == 1, "第二次访问只是普通消息");
        }

        printf("\n[5] +initialize 会被继承 → 父类的实现可能跑多次\n");
        {
            (void)[SuperClass new];
            (void)[SubClass new];
            char buf[256];
            snprintf(buf, sizeof(buf), "SuperClass 实现 self=SuperClass:%d  self=SubClass:%d",
                     countOf("+initialize SuperClass self=SuperClass"),
                     countOf("+initialize SuperClass self=SubClass(继承来的实现)"));
            that("同一份 +initialize 实现为 SuperClass 和 SubClass 各跑了一次",
                 countOf("+initialize SuperClass self=SuperClass") == 1
                 && countOf("+initialize SuperClass self=SubClass(继承来的实现)") == 1, buf);
            that("这就是必须判 self == [SuperClass class] 的原因",
                 countOf("+initialize SuperClass self=SubClass(继承来的实现)") == 1,
                 "不判 self 就会把一个类的初始化跑两遍,甚至两遍都跑错对象");
        }

        printf("\n断言 %d 通过 / %d 失败\n", gPassed, gFailed);
        return gFailed ? 1 : 0;
    }
}
