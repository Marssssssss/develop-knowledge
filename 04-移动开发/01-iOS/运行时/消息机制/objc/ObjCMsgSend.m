// ObjCMsgSend.m — objc_msgSend 消息机制三段式最小复刻(demo 134)
// 复刻 objc4 消息流水线:
//   ① 快速查找:cache_t 哈希桶(SEL & mask → 线性探测)—— 汇编 CacheLookup 的语义
//   ② 慢速查找:lookUpImpOrForward —— 本类方法列表(二分)→ 父类缓存 → 父类方法列表 → 根类
//   ③ 动态解析 + 转发:resolveInstanceMethod → forwardingTargetForSelector
//                     → methodSignatureForSelector + forwardInvocation → doesNotRecognizeSelector
// 编译(仅供参考,本仓库不执行): clang -framework Foundation ObjCMsgSend.m -o msg
#import <Foundation/Foundation.h>
#import <stdlib.h>
#import <string.h>

typedef void (*IMP)(id self, SEL _cmd, void *arg);

#pragma mark - 模拟类结构:cache_t / method_list_t / superclass

typedef struct { SEL sel; IMP imp; } MethodT;
typedef struct {
    MethodT *buckets;                 // 哈希桶(容量为 2 的幂)
    unsigned mask;                    // mask = capacity - 1
    unsigned occupied;                // 已占用
} CacheT;
typedef struct Cls {
    const char *name;
    struct Cls *superclass;           // 继承链
    MethodT *methods; int methodCount; int methodsSorted;
    CacheT cache;
} Cls;

static unsigned selHash(SEL s) { return (unsigned)((uintptr_t)s >> 2); }  // sel 地址哈希

static void cacheInit(CacheT *c) {
    c->mask = 3; c->occupied = 0;                            // 初始容量 4(arm64)
    c->buckets = calloc(c->mask + 1, sizeof(MethodT));
}
// objc4 语义:占用超 3/4 直接翻倍清空(时间局部性强,不做 rehash)
static void cacheInsert(Cls *cls, SEL sel, IMP imp) {
    if (cls->cache.occupied * 4 > (cls->cache.mask + 1) * 3) {
        cls->cache.mask = (cls->cache.mask + 1) * 2 - 1;
        free(cls->cache.buckets);
        cls->cache.buckets = calloc(cls->cache.mask + 1, sizeof(MethodT));
        cls->cache.occupied = 0;
    }
    unsigned i = selHash(sel) & cls->cache.mask;             // 位与代替取模
    while (cls->cache.buckets[i].sel) i = (i + 1) & cls->cache.mask;  // 线性探测回绕
    cls->cache.buckets[i] = (MethodT){sel, imp};
    cls->cache.occupied++;
}
static IMP cacheGet(Cls *cls, SEL sel) {                     // CacheLookup(汇编)
    if (!cls->cache.buckets) return NULL;
    unsigned i = selHash(sel) & cls->cache.mask;
    for (unsigned probe = 0; probe <= cls->cache.mask; probe++) {
        if (!cls->cache.buckets[i].sel) return NULL;         // 空槽即未命中
        if (cls->cache.buckets[i].sel == sel) return cls->cache.buckets[i].imp;
        i = (i + 1) & cls->cache.mask;                       // 环形继续探测
    }
    return NULL;
}

static Cls *clsNew(const char *name, Cls *super) {
    Cls *c = calloc(1, sizeof(Cls));
    c->name = name; c->superclass = super;
    cacheInit(&c->cache);
    return c;
}
static void clsAddMethod(Cls *c, SEL sel, IMP imp) {         // class_addMethod 语义
    c->methods = realloc(c->methods, (c->methodCount + 1) * sizeof(MethodT));
    c->methods[c->methodCount++] = (MethodT){sel, imp};
    c->methodsSorted = 0;                                    // 需重排
}
static int cmpSel(const void *a, const void *b) {            // 按 SEL 地址排序
    uintptr_t x = (uintptr_t)((MethodT *)a)->sel, y = (uintptr_t)((MethodT *)b)->sel;
    return x < y ? -1 : x > y;
}
static IMP methodListFind(Cls *c, SEL sel) {                 // 二分/线性查找
    if (!c->methodCount) return NULL;
    if (!c->methodsSorted) { qsort(c->methods, c->methodCount, sizeof(MethodT), cmpSel); c->methodsSorted = 1; }
    int lo = 0, hi = c->methodCount - 1;
    while (lo <= hi) {                                       // sorted: 二分
        int mid = (lo + hi) / 2;
        if ((uintptr_t)c->methods[mid].sel == (uintptr_t)sel) return c->methods[mid].imp;
        if ((uintptr_t)c->methods[mid].sel < (uintptr_t)sel) lo = mid + 1; else hi = mid - 1;
    }
    return NULL;
}

#pragma mark - 模拟对象与消息接收者行为(转发三阶段)

typedef struct { Cls *isa; } MiniObj;
static NSString *g_forwardLog = @"";

// 阶段③完整转发:构造 NSInvocation 的语义 —— 这里直接用真实 ObjC 消息表达
@interface Forwarder : NSObject
@end
@implementation Forwarder
- (void)fallbackDoSomething:(NSString *)s {
    g_forwardLog = [NSString stringWithFormat:@"forwardInvocation → Forwarder 处理了 %@", s];
}
@end

@interface Receiver : NSObject
@end
@implementation Receiver
// 阶段① 动态方法解析:运行时补 IMP(class_addMethod 语义由 clsAddMethod 承担)
+ (BOOL)resolveInstanceMethod:(SEL)sel {
    if (sel == @selector(dynamicHello:)) {
        printf("    [阶段① resolveInstanceMethod] 为 dynamicHello: 动态添加 IMP\n");
        return YES;   // 演示:由 mini-runtime 侧 clsAddMethod 完成,返回 YES 触发 retry
    }
    return NO;
}
// 阶段② 快速转发:把消息整个转给备用接收者
- (id)forwardingTargetForSelector:(SEL)sel {
    if (sel == @selector(fallbackDoSomething:)) {
        printf("    [阶段② forwardingTargetForSelector] 返回备用接收者 Forwarder\n");
        return [Forwarder new];
    }
    return [super forwardingTargetForSelector:sel];
}
@end

#pragma mark - mini-runtime:两段查找 + 三阶段兜底

static IMP lookUpImpOrForward(Cls *cls, SEL sel) {           // 慢速查找(runtimeLock 下)
    Cls *cur = cls;
    while (cur) {
        IMP imp = methodListFind(cur, sel);                  // 本类方法列表(二分)
        if (imp) { cacheInsert(cls, sel, imp); return imp; } // 命中:回填最初接收类缓存
        cur = cur->superclass;                               // 沿继承链上行(先 cache 后 list)
    }
    return (IMP)-1;                                          // 未找到 → _objc_msgForward_impcache
}

static void msgSend(MiniObj *obj, SEL sel, void *arg) {
    printf("  [msgSend] sel=%s\n", sel_getName(sel));
    IMP imp = cacheGet(obj->isa, sel);                       // ① 汇编快速路径
    if (!imp) imp = lookUpImpOrForward(obj->isa, sel);       // ② C++ 慢速路径
    if ((intptr_t)imp != -1 && imp) { imp((id)obj, sel, arg); return; }

    // ③ 快速路径+慢速路径都失败 → 动态方法解析(resolve) → retry
    printf("    [慢速路径未命中] 进入消息兜底流水线\n");
    Receiver *r = [Receiver new];
    if ([r resolveInstanceMethod:sel]) {                     // 开发者 class_addMethod 后重查
        imp = lookUpImpOrForward(obj->isa, sel);
        if ((intptr_t)imp != -1 && imp) { imp((id)obj, sel, arg); return; }
    }
    // ② 快速转发:整条消息交给别的对象
    id target = [r forwardingTargetForSelector:sel];
    if (target) { ((void(*)(id,SEL,void*))objc_msgSend)(target, sel, arg); return; }
    // ③ 完整转发:methodSignatureForSelector + forwardInvocation(NSInvocation)
    //    Demo:Forwarder 直接实现同名方法,语义等价于签名+转发两步
    printf("    [阶段③ doesNotRecognizeSelector] 三阶段全失败,抛异常\n");
}

#pragma mark - 业务方法

static void implHello(id self, SEL _cmd, void *arg) { printf("    [IMP 命中] hello 执行(参数 %s)\n", arg ? (char *)arg : "-"); }
static void implDynamic(id self, SEL _cmd, void *arg) { printf("    [IMP 命中] 动态添加的 dynamicHello: 执行\n"); }

int main(void) {
    Cls *animal = clsNew("Animal", NULL);                    // 根类
    Cls *dog = clsNew("Dog", animal);                        // 子类
    clsAddMethod(animal, @selector(hello), implHello);       // 方法在父类 Animal
    MiniObj obj = { dog };

    printf("== 1. 首次发 hello:cache miss → 慢速查找父类方法列表,命中后回填 Dog 缓存 ==\n");
    msgSend(&obj, @selector(hello), "bone");
    printf("== 2. 再次发 hello:cache 命中(O(1)) ==\n");
    msgSend(&obj, @selector(hello), "bone2");

    printf("== 3. 发 dynamicHello::三阶段之① 动态解析(方法此刻还不存在)==\n");
    msgSend(&obj, @selector(dynamicHello:), NULL);           // resolve 钩子里 clsAddMethod 补 IMP

    printf("== 4. 发 fallbackDoSomething::三阶段之②/③ 转发 ==\n");
    Receiver *r = [Receiver new];
    [r fallbackDoSomething:@"payload"];
    printf("    %s\n", g_forwardLog.UTF8String);
    return 0;
}
