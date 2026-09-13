// ObjCWeak.m — weak 弱引用底层实现最小复刻(demo 133)
// 复刻 objc4 的 SideTables(StripedMap) / SideTable{spinlock, refcnts, weak_table} /
// weak_entry_t(inline 4 槽 → out-of-line 哈希) / weak_register_no_lock /
// weak_clear_no_lock(dealloc 时全部置 nil)。
// 编译(仅供参考,本仓库不执行): clang -framework Foundation ObjCWeak.m -o weak
#import <Foundation/Foundation.h>
#import <stdlib.h>
#import <string.h>

#define STRIPE_COUNT 8          // iOS 真机 8 张表(macOS/模拟器 64)
#define WEAK_INLINE_COUNT 4     // inline_referrers 槽位数,超出转 out-of-line 哈希

#pragma mark - 模拟 OC 对象

@interface WObject : NSObject
@property (nonatomic, copy) NSString *name;
@end
@implementation WObject
- (void)dealloc { printf("    [dealloc] %s\n", self.name.UTF8String); }
@end

#pragma mark - SideTable / weak_table / weak_entry_t(简化版)

typedef struct WeakEntry {
    WObject *referent;                    // 被弱引用的对象
    // union 简化:inline 槽位数组 / 超出后 out-of-line 哈希表
    __weak WObject **referrers[32];       // 存 weak 指针变量的地址(objc_object**)
    int referrerCount;
} WeakEntry;

typedef struct WeakTable {
    WeakEntry *entries[64];               // referent 指针 → entry 的开地址哈希
    int numEntries;
} WeakTable;

typedef struct SideTable {
    int slock;                            // 真实实现为 spinlock_t,此处演示无锁
    int refcnts[64];                      // 引用计数表(对象指针哈希)
    WeakTable weakTable;
} SideTable;

static SideTable g_sideTables[STRIPE_COUNT];

// objc4 indexForPointer: ((addr >> 4) ^ (addr >> 9)) % StripeCount
static int indexForPointer(void *p) {
    uintptr_t a = (uintptr_t)p;
    return (int)(((a >> 4) ^ (a >> 9)) % STRIPE_COUNT);
}
static int weakHash(WObject *r) { return (int)(((uintptr_t)r >> 3) % 64); }

static WeakEntry *weakEntryForReferent(WeakTable *wt, WObject *referent) {
    int i = weakHash(referent);
    for (int probe = 0; probe < 64; probe++) {   // 线性探测
        int idx = (i + probe) % 64;
        if (!wt->entries[idx]) return NULL;
        if (wt->entries[idx]->referent == referent) return wt->entries[idx];
    }
    return NULL;
}

static WeakEntry *weakEntryInsert(WeakTable *wt, WObject *referent) {
    int i = weakHash(referent);
    while (wt->entries[i]) i = (i + 1) % 64;
    wt->entries[i] = calloc(1, sizeof(WeakEntry));
    wt->entries[i]->referent = referent;
    wt->numEntries++;
    return wt->entries[i];
}

// objc_initWeak → storeWeak → weak_register_no_lock:
// 把"weak 变量的地址"登记到 referent 的 entry 里
static void weakRegister(__weak WObject **location, WObject *newObj) {
    if (!newObj) { *location = nil; return; }    // objc_initWeak 对 nil 直接置空
    SideTable *st = &g_sideTables[indexForPointer(newObj)];
    WeakTable *wt = &st->weakTable;
    WeakEntry *entry = weakEntryForReferent(wt, newObj);
    if (!entry) entry = weakEntryInsert(wt, newObj);
    entry->referrers[entry->referrerCount++] = location;  // append_referrer
    *location = newObj;                            // 弱引用不 +1
    printf("    [register] %s ← weak 变量 #%d\n", newObj.name.UTF8String, entry->referrerCount - 1);
}

// weak_unregister_no_lock:weak 变量改指向别的对象/nil 时,从 entry 移除登记
static void weakUnregister(__weak WObject **location, WObject *oldObj) {
    if (!oldObj) return;
    SideTable *st = &g_sideTables[indexForPointer(oldObj)];
    WeakEntry *entry = weakEntryForReferent(&st->weakTable, oldObj);
    if (!entry) return;
    for (int i = 0; i < entry->referrerCount; i++) {
        if (entry->referrers[i] == location) {
            entry->referrers[i] = entry->referrers[--entry->referrerCount]; // 尾部交换删除
            printf("    [unregister] weak 变量与 %s 解绑\n", oldObj.name.UTF8String);
            return;
        }
    }
}

// 引用计数:alloc/init = 1;真实实现 extra_rc 在 isa 内联 + 溢出落 SideTable
static int *refcntSlot(WObject *o) {
    return &g_sideTables[indexForPointer(o)].refcnts[(uintptr_t)o % 64];
}
static void objRetain(WObject *o) { (*refcntSlot(o))++; }
static BOOL objRelease(WObject *o) {             // 返回 YES 表示计数归零
    if (--(*refcntSlot(o)) > 0) return NO;
    // dealloc → clearDeallocating → weak_clear_no_lock:所有 weak 指针置 nil
    SideTable *st = &g_sideTables[indexForPointer(o)];
    WeakTable *wt = &st->weakTable;
    WeakEntry *entry = weakEntryForReferent(wt, o);
    if (entry) {
        printf("    [weak_clear] %s 释放,置空 %d 个 weak 指针\n", o.name.UTF8String, entry->referrerCount);
        for (int i = 0; i < entry->referrerCount; i++) *(entry->referrers[i]) = nil;
        for (int i = weakHash(o); wt->entries[i]; i = (i + 1) % 64)
            if (wt->entries[i] == entry) { wt->entries[i] = NULL; wt->numEntries--; break; }
        free(entry);
    }
    [o dealloc];                                  // 演示直接调 dealloc
    return YES;
}

#pragma mark - main

int main(void) {
    printf("== 1. 对象 A 被 3 个 weak 变量引用 ==\n");
    WObject *a = [[WObject alloc] init]; a.name = @"A";
    __weak WObject *w1, *w2, *w3;
    weakRegister(&w1, a); weakRegister(&w2, a); weakRegister(&w3, a);
    objRetain(a);                                  // 计数 2(alloc+retain)

    printf("== 2. 对象 B 单独一个 weak ==\n");
    WObject *b = [[WObject alloc] init]; b.name = @"B";
    __weak WObject *wb;
    weakRegister(&wb, b);

    printf("== 3. w2 改指向 B:先 unregister 再 register ==\n");
    weakUnregister(&w2, a); weakRegister(&w2, b);

    printf("== 4. A release 归零 → weak_clear 把 w1/w3 置 nil ==\n");
    objRelease(a); objRelease(a);                  // 2 → 1 → 0
    printf("    w1=%@ w3=%@ (应为 nil)\n", w1, w3);

    printf("== 5. B release 归零 → wb/w2 置 nil ==\n");
    objRelease(b);
    printf("    wb=%@ w2=%@ (应为 nil)\n", wb, w2);
    return 0;
}
