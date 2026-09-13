// ObjCCategory.m — Category 加载合并 + 关联对象最小复刻(demo 136)
// 复刻两套机制:
// ① Category:category_t 结构 → _read_images 收集 → attachCategories 把分类方法
//    "前插"进宿主类方法列表(后编译者优先)→ +load 调用顺序(父类→本类→分类)
// ② 关联对象:AssociationsManager(全局单张表,自旋锁)
//    → AssociationsHashMap: 对象地址(DISGUISE) → ObjectAssociationMap
//    → ObjectAssociationMap: key → ObjcAssociation{policy, value}
//    → objc_set/get/removeAssociatedObjects;dealloc 时按 policy 释放
// 编译(仅供参考,本仓库不执行): clang -framework Foundation ObjCCategory.m -o cat
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <stdlib.h>

#pragma mark - ① Category:category_t 与 attachCategories 复刻

typedef struct {
    const char *clsName;
    const char *catName;             // 分类名
    SEL methodSels[4];               // 实例方法(简化:最多 4 个)
    const char *methodNames[4];
    int methodCount;
} MiniCategory;

// 模拟宿主类方法列表(可增长;attach 后分类方法插在最前)
typedef struct { SEL sel; const char *name; const char *from; } MiniMethod;
static MiniMethod g_methods[16];
static int g_methodCount = 0;

static void printMethods(const char *stage) {
    printf("  [%s] 宿主方法列表:", stage);
    for (int i = 0; i < g_methodCount; i++) printf(" %s(%s)", g_methods[i].name, g_methods[i].from);
    printf("\n");
}

// attachCategories 核心:倒序遍历分类(编译顺序靠后的先生效),方法"前插"
static void attachCategories(MiniCategory **cats, int n) {
    for (int i = n - 1; i >= 0; i--) {              // attachList 倒序
        MiniCategory *c = cats[i];
        for (int m = c->methodCount - 1; m >= 0; m--) {
            // 整体后移一格,分类方法插到最前(与类方法同名 → 覆盖效果)
            memmove(g_methods + 1, g_methods, g_methodCount * sizeof(MiniMethod));
            g_methods[0] = (MiniMethod){c->methodSels[m], c->methodNames[m], c->catName};
            g_methodCount++;
        }
    }
}
// 消息查找:方法列表线性扫,第一个命中生效 → 分类方法"覆盖"原方法/先编译的分类
static const char *methodLookup(SEL sel) {
    for (int i = 0; i < g_methodCount; i++)
        if (g_methods[i].sel == sel) return g_methods[i].from;
    return NULL;
}

#pragma mark - ② 关联对象:两层哈希复刻

typedef struct {
    const void *key;                 // 通常用 static char / @selector()
    id value;
    objc_AssociationPolicy policy;   // RETAIN / COPY / ASSIGN × atomic
} ObjcAssociation;                   // 二级表条目

typedef struct {
    ObjcAssociation assoc[8];        // ObjectAssociationMap:key → {value, policy}
    int count;
} ObjectAssociationMap;

static ObjectAssociationMap *g_assocMap[32];        // AssociationsHashMap:对象 → 二级表
// DISGUISE:指针取负伪装成整数,防泄漏检测工具误报(真实实现 DisguisedPtr)
static int disguiseHash(id obj) { return (int)((-(uintptr_t)obj) >> 3) % 32; }

static ObjectAssociationMap *mapFor(id obj, BOOL create) {
    int h = disguiseHash(obj);
    if (!g_assocMap[h] && create) g_assocMap[h] = calloc(1, sizeof(ObjectAssociationMap));
    return g_assocMap[h];
}

// _object_set_associative_reference 语义
static void miniSetAssociatedObject(id obj, const void *key, id value, objc_AssociationPolicy policy) {
    ObjectAssociationMap *m = mapFor(obj, value != nil);
    if (!m) return;
    // 旧值先出锁外释放(真实实现:old_association 出锁后 release)
    for (int i = 0; i < m->count; i++) {
        if (m->assoc[i].key == key) {
            printf("    [set] 覆盖旧值(旧 policy=%u)\n", m->assoc[i].policy);
            m->assoc[i].value = value; m->assoc[i].policy = policy;
            return;
        }
    }
    m->assoc[m->count++] = (ObjcAssociation){key, value, policy};
}
// objc_getAssociatedObject 语义
static id miniGetAssociatedObject(id obj, const void *key) {
    ObjectAssociationMap *m = mapFor(obj, NO);
    if (!m) return nil;
    for (int i = 0; i < m->count; i++)
        if (m->assoc[i].key == key) return m->assoc[i].value;
    return nil;
}
// objc_destructInstance 里 _object_set_associative_reference(obj, key, nil, …) 全清语义
static void miniRemoveAssociatedObjects(id obj) {
    int h = disguiseHash(obj);
    if (g_assocMap[h]) {
        printf("    [dealloc] 对象释放,按 policy 释放 %d 个关联值并移除二级表\n", g_assocMap[h]->count);
        free(g_assocMap[h]); g_assocMap[h] = NULL;   // RETAIN/COPY 值在此 release
    }
}

#pragma mark - 演示对象

@interface Model : NSObject
@end
@implementation Model
- (void)originalMethod { printf("    originalMethod 由宿主类提供\n"); }
@end

static char kNameKey;                                 // 关联对象 key 惯例:静态地址

int main(void) {
    printf("== 1. Category:宿主只有 originalMethod ==\n");
    SEL selOrig = @selector(originalMethod);
    g_methods[g_methodCount++] = (MiniMethod){selOrig, "originalMethod", "Model"};
    printMethods("attach 前");

    printf("== 2. 模拟编译产物:CatA(先编译)与 CatB(后编译)各带 description ==\n");
    MiniCategory catA = {"Model", "CatA", {@selector(description)}, {"description"}, 1};
    MiniCategory catB = {"Model", "CatB", {@selector(description)}, {"description"}, 1};
    MiniCategory *cats[2] = {&catA, &catB};
    attachCategories(cats, 2);
    printMethods("attach 后");
    const char *winner = methodLookup(@selector(description));
    printf("  查找 description → 由 %s 提供(后编译的 CatB 前插获胜)\n", winner);

    printf("== 3. +load 顺序:父类 → 本类 → 分类(编译顺序);本类只调一次 ==\n");
    printf("  [load] Model(宿主)\n  [load] Model(CatA)  [load] Model(CatB)\n");

    printf("== 4. 关联对象:Category 不能加 ivar,用关联对象模拟属性 ==\n");
    Model *m = [Model new];
    miniSetAssociatedObject(m, &kNameKey, @"hello", OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    printf("    get → %@\n", miniGetAssociatedObject(m, &kNameKey));
    miniSetAssociatedObject(m, &kNameKey, @"world", OBJC_ASSOCIATION_COPY_NONATOMIC);
    printf("    get → %@\n", miniGetAssociatedObject(m, &kNameKey));
    miniSetAssociatedObject(m, &kNameKey, nil, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    printf("    set nil → get → %@(传 nil 即移除该 key)\n", miniGetAssociatedObject(m, &kNameKey));
    miniSetAssociatedObject(m, &kNameKey, @"again", OBJC_ASSOCIATION_RETAIN_NONATOMIC);

    printf("== 5. 对象释放:objc_destructInstance 释放关联值 ==\n");
    miniRemoveAssociatedObjects(m);
    return 0;
}
