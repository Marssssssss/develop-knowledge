// ObjCAutoreleasePool.m — AutoreleasePoolPage 双向链表最小复刻(demo 132)
// 参考 objc4 NSObject.mm 中 AutoreleasePoolPage 的公开结构推导:
//   magic(校验) / next(栈顶指针) / thread / parent / child / depth / hiwat
//   SIZE = 4096 字节一页;POOL_BOUNDARY(旧名 POOL_SENTINEL)= nil 哨兵
// 编译(仅供参考,本仓库不执行): clang -framework Foundation ObjCAutoreleasePool.m -o pool
#import <Foundation/Foundation.h>
#import <stdlib.h>
#import <string.h>

#define PAGE_CAPACITY 8   // 演示用:每页只放 8 个槽位(真实为 4096B/8B≈505 个)
#define POOL_BOUNDARY ((id)nil)  // 哨兵对象,objc4 中就是 nil 的别名

#pragma mark - 模拟的 autoreleased 对象

@interface DemoObject : NSObject
@property (nonatomic, copy) NSString *name;
@end
@implementation DemoObject
- (void)dealloc { printf("  [release] %s 引用计数归零,dealloc\n", self.name.UTF8String); }
@end

#pragma mark - AutoreleasePoolPage

typedef struct AutoreleasePoolPage {
    const char *magic;              // 校验字段(真实实现为 magic_t,防内存踩踏)
    id *next;                       // 指向下一个可存放对象地址的槽位
    AutoreleasePoolPage *parent;    // 双向链表:父页(首页为 NULL)
    AutoreleasePoolPage *child;     // 双向链表:子页(尾页为 NULL)
    uint32_t depth;                 // 页深度,首页 0 递增
    id slots[PAGE_CAPACITY];        // 对象槽位(真实实现与头部同处一个 4096B 页)
} AutoreleasePoolPage;

static AutoreleasePoolPage *g_hotPage = NULL;  // 线程局部概念,此处全局演示

static AutoreleasePoolPage *pageNew(AutoreleasePoolPage *parent) {
    AutoreleasePoolPage *p = calloc(1, sizeof(AutoreleasePoolPage));
    p->magic = "POOLPAGE";
    p->parent = parent;
    p->depth = parent ? parent->depth + 1 : 0;
    if (parent) { parent->child = p; }         // 构造函数:挂到父页 child
    return p;
}

static id *pageAdd(AutoreleasePoolPage *p, id obj) {   // page->add(obj):压栈
    id *ret = p->next;
    *p->next = obj;
    p->next++;
    return ret;
}

static BOOL pageFull(AutoreleasePoolPage *p) { return p->next >= p->slots + PAGE_CAPACITY; }
static BOOL pageEmpty(AutoreleasePoolPage *p) { return p->next == p->slots; }
static void setHotPage(AutoreleasePoolPage *p) { g_hotPage = p; }

// autoreleaseFast 三分支:热页未满直接加 / 满则找或建子页 / 无热页则建首页
static id *autoreleaseFast(id obj) {
    AutoreleasePoolPage *page = g_hotPage;
    if (page && !pageFull(page)) {
        return pageAdd(page, obj);
    } else if (page) {                        // autoreleaseFullPage
        do {
            if (page->child) page = page->child;
            else page = pageNew(page);
        } while (pageFull(page));
        setHotPage(page);
        return pageAdd(page, obj);
    } else {                                  // autoreleaseNoPage
        AutoreleasePoolPage *p = pageNew(NULL);
        setHotPage(p);
        if (obj != POOL_BOUNDARY) p->slots[0] = POOL_BOUNDARY, p->next = p->slots; // 防裸 pop
        return pageAdd(p, obj);
    }
}

// objc_autoreleasePoolPush:压入哨兵,返回其地址作为 pool token
static void *poolPush(void) { return autoreleaseFast(POOL_BOUNDARY); }

// releaseUntil:从热页逐槽 release,直到 next 回到 stop(token 指向的哨兵槽位)
// objc4 原实现以指针比较为循环条件(非哨兵值比较):嵌套池的内层哨兵会被跨过并跳过
static void pageReleaseUntil(AutoreleasePoolPage *page, id *stop) {
    while (1) {
        while (pageEmpty(page)) {             // 空页回退到父页(真实实现还会 kill 空子页)
            page = page->parent;
            setHotPage(page);
        }
        if (page->next == stop) break;        // next 回到本池哨兵槽位,边界判定看地址
        page->next--;                         // --next 先出栈
        id obj = *page->next;
        if (obj == POOL_BOUNDARY) continue;   // 跨过已 pop 的内层哨兵(残留槽位)
        printf("  [pop] 向 %s 发送 release\n", ((DemoObject *)obj).name.UTF8String);
        [obj release];                        // MRC 语义;ARC 演示文件不编译运行
        *page->next = (id)0xA3;               // SCRIBBLE 0xA3 标记已释放槽位
    }
    setHotPage(page);
}

// objc_autoreleasePoolPop(token):释放 token 之后入栈的所有对象
static void poolPop(void *token) {
    if (!g_hotPage) return;
    AutoreleasePoolPage *page = g_hotPage;
    while (pageEmpty(page) && page->parent) page = page->parent;
    pageReleaseUntil(page, (id *)token);
}

static void pagePrint(AutoreleasePoolPage *cold) {
    for (AutoreleasePoolPage *p = cold; p; p = p->child) {
        printf("  [page depth=%u] slots:", p->depth);
        for (id *s = p->slots; s < p->next; s++)
            printf(" %s", *s == POOL_BOUNDARY ? "<BOUNDARY>" : ((DemoObject *)*s).name.UTF8String);
        printf("\n");
    }
}

#pragma mark - clang 改写后的 @autoreleasepool 就是这两行的结构体

typedef struct __AtAutoreleasePool {
    void *token;
    __AtAutoreleasePool() { token = poolPush(); }      // 构造 = push
    ~__AtAutoreleasePool() { poolPop(token); }         // 析构 = pop
} AtAutoreleasePool;

#pragma mark - main:嵌套池 + 分页演示

int main(void) {
    printf("== 1. 外层池入栈 6 个对象(每页容量 8,含哨兵)==\n");
    AtAutoreleasePool outer;                            // { push(); ... pop(); }
    DemoObject *objs[6];
    for (int i = 0; i < 6; i++) {
        objs[i] = [[DemoObject alloc] init];           // retainCount = 1(alloc)
        objs[i].name = [NSString stringWithFormat:@"obj%d", i];
        autoreleaseFast(objs[i]);                       // 等价 [objs[i] autorelease]
        [objs[i] retain];                               // 模拟池持有:+1
    }
    printf("== 2. 内层嵌套池:再入栈 2 个对象,内层 pop 只释放内层 ==\n");
    {
        AtAutoreleasePool inner;
        for (int i = 6; i < 8; i++) {
            objs[i] = [[DemoObject alloc] init];
            objs[i].name = [NSString stringWithFormat:@"obj%d", i];
            autoreleaseFast(objs[i]);
            [objs[i] retain];
        }
        printf("  内层池结束前:\n");
        pagePrint(g_hotPage->parent ? g_hotPage->parent : g_hotPage);
        // 内层析构:pop(inner.token) → 只 release obj6/obj7(逆序)
    }
    printf("  内层 pop 后(栈顶回到内层哨兵,外层对象仍在):\n");
    pagePrint(g_hotPage->parent ? g_hotPage->parent : g_hotPage);

    printf("== 3. 填满当前页触发分页(child page)==\n");
    for (int i = 8; i < 12; i++) {
        DemoObject *o = [[DemoObject alloc] init];
        o.name = [NSString stringWithFormat:@"obj%d", i];
        autoreleaseFast(o);
        [o retain];
    }
    AutoreleasePoolPage *cold = g_hotPage;
    while (cold->parent) cold = cold->parent;
    pagePrint(cold);                                    // 应看到 depth=0 与 depth=1 两页

    printf("== 4. 外层 pop:逆序释放全部(含跨页)==\n");
    // outer 析构 = pop(outer.token),从热页向哨兵逆序 release
    return 0;                                           // 结构体析构顺序:outer 最后
}
