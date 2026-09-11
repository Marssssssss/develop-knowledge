/*
 * slab_allocator.c — 最小 slab 分配器
 *
 * 模型简化版(对应 Bonwick 1994 论文 + Understanding the Linux Kernel Chapter 8):
 *   - 一个 cache 管理一种固定尺寸的对象(object_size)
 *   - 每个 cache 由若干 slab 组成,每个 slab 是若干个 page 拼接(本 demo 用
 *     连续 malloc 模拟一页或多页)
 *   - 每个 slab 内:
 *       * 元数据头(本 slab 缓存对象数、空闲位图)
 *       * 紧接的内存切成 N 个 size=object_size 的 slot(slot 内部连续、不放头)
 *       * slot 是否空闲用 bitmap 标记(free bitmap)
 *   - 分配:找第一个有空闲 slot 的 slab → bitmap 找 0 位 → 标记 1 → 返回地址
 *   - 释放:把对象地址折算到 slot 索引 → bitmap 标 0
 *
 * 注意:本 demo 是教学简化版,真实 Linux SLAB 还包括:
 *   - 多级 cache chain(DMA/normal/kmalloc)
 *   - slab coloring(用 colour 偏移错开 CPU cache line)
 *   - 每 CPU 数组(避免跨核锁)
 *   - 回收机制(reap_timer → shrink_slab)
 *
 * 来源:Bonwick 1994 "The Slab Allocator: An Object-Caching Kernel Memory
 * Allocator"(USENIX Summer)+ Understanding the Linux Kernel Ch.8 (kernel.org)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ----------- 编译期常量 ----------- */
#define OBJ_SIZE       32                  /* 单个对象大小(对齐后)   */
#define SLAB_PAGES     1                   /* 每个 slab 用 1 页(4KiB)*/
#define SLAB_BYTES     (SLAB_PAGES * 4096)
/* 一个 slab 能装多少对象:扣掉元数据头、按 8 字节对齐 */
#define SLAB_OBJ_MAX   ((SLAB_BYTES - 64) / OBJ_SIZE)

/* ----------- 数据结构 ----------- */

/* cache 链表节点(简化:demo 只用一个 cache,所以单例即可) */
typedef struct slab {
    unsigned char  *mem;                  /* malloc 出来的整块        */
    uint32_t       *bitmap;              /* 0=空闲,1=已分配          */
    int             obj_count;           /* 当前 slab 内对象总数     */
    int             used;                /* 已分配个数(便于快速判断) */
    struct slab    *next;                /* 链表 next                */
} slab_t;

typedef struct kmem_cache {
    size_t          obj_size;            /* 每个对象大小(向上对齐)   */
    slab_t         *slabs_partial;       /* 还有空闲 slot 的 slab    */
    slab_t         *slabs_full;          /* 全部分配满               */
    slab_t         *slabs_free;          /* 全部空闲(未分配过)       */
    /* 简易统计 */
    long            alloc_total;
    long            free_total;
} kmem_cache_t;

/* ----------- 位图辅助 ----------- */

static int bitmap_find_free(uint32_t *bm, int n)
{
    /* 返回第一个 0 位的位置;n<=SLAB_OBJ_MAX<128 时一个 word 就够 */
    uint32_t w = *bm;
    /* ~w 把空闲位变 1、用 ctz 计最低位的 1 */
    return __builtin_ctz(~w);
}

static void bitmap_set_used(uint32_t *bm, int idx)  { bm[0] |=  (1u << idx); }
static void bitmap_set_free(uint32_t *bm, int idx)  { bm[0] &= ~(1u << idx); }

/* ----------- 创建 / 销毁 ----------- */

static slab_t *slab_create(size_t obj_size)
{
    slab_t *s = (slab_t *)calloc(1, sizeof(slab_t));
    if (!s) return NULL;
    s->mem        = (unsigned char *)malloc(SLAB_BYTES);
    s->bitmap     = (uint32_t *)calloc(1, sizeof(uint32_t)); /* 一字节能存 32 位 */
    if (!s->mem || !s->bitmap) { free(s->mem); free(s->bitmap); free(s); return NULL; }
    s->obj_count  = SLAB_OBJ_MAX;
    s->used       = 0;
    s->next       = NULL;
    return s;
}

static void slab_destroy(slab_t *s)
{
    if (!s) return;
    free(s->mem);
    free(s->bitmap);
    free(s);
}

static kmem_cache_t *kmem_cache_create(size_t obj_size)
{
    kmem_cache_t *c = (kmem_cache_t *)calloc(1, sizeof(kmem_cache_t));
    if (!c) return NULL;
    c->obj_size = obj_size;
    /* 预创建第一个 slab,放在 slabs_free 链表头 */
    slab_t *s = slab_create(obj_size);
    if (!s) { free(c); return NULL; }
    c->slabs_free = s;
    return c;
}

static void kmem_cache_destroy(kmem_cache_t *c)
{
    slab_t *lists[3] = {c->slabs_partial, c->slabs_full, c->slabs_free};
    for (int i = 0; i < 3; i++) {
        slab_t *p = lists[i];
        while (p) { slab_t *n = p->next; slab_destroy(p); p = n; }
    }
    free(c);
}

/* ----------- 分配 / 释放 ----------- */

/* 找第一个有空位的 slab;若 partial 为空,从 free 链搬一个过来 */
static slab_t *kmem_cache_select_slab(kmem_cache_t *c)
{
    if (c->slabs_partial) return c->slabs_partial;
    if (c->slabs_free) {
        slab_t *s = c->slabs_free;
        c->slabs_free = s->next;
        s->next = c->slabs_partial;
        c->slabs_partial = s;
        return s;
    }
    /* 全部 full,新建一个 slab */
    slab_t *s = slab_create(c->obj_size);
    if (!s) return NULL;
    s->next = c->slabs_partial;
    c->slabs_partial = s;
    return s;
}

void *kmem_cache_alloc(kmem_cache_t *c)
{
    slab_t *s = kmem_cache_select_slab(c);
    if (!s) return NULL;

    int idx = bitmap_find_free(s->bitmap, s->obj_count);
    if (idx >= s->obj_count) return NULL;      /* 防御:bitmap 状态异常 */

    bitmap_set_used(s->bitmap, idx);
    s->used++;
    c->alloc_total++;

    /* slab 满 → 从 partial 移到 full */
    if (s->used == s->obj_count) {
        /* 找到 s 在 partial 链中的位置并摘除(简化:假定 s 在链表头) */
        c->slabs_partial = s->next;
        s->next = c->slabs_full;
        c->slabs_full = s;
    }
    return s->mem + (size_t)idx * c->obj_size;
}

void kmem_cache_free(kmem_cache_t *c, void *obj)
{
    if (!c || !obj) return;

    /* 在所有 slab 中二分不行(简化版:demo 的对象尺寸一致且连续,直接算偏移即可)。
     * 真实实现会用元数据头(prologue)避免遍历。                                  */
    /* 简化:遍历 partial+full 链表,寻找 obj 落点 */
    slab_t **lists[2] = { &c->slabs_partial, &c->slabs_full };
    for (int li = 0; li < 2; li++) {
        slab_t *prev = NULL, *cur = *lists[li];
        while (cur) {
            unsigned char *base = cur->mem;
            unsigned char *end  = base + (size_t)cur->obj_count * c->obj_size;
            if ((unsigned char *)obj >= base && (unsigned char *)obj < end) {
                size_t off = (unsigned char *)obj - base;
                int idx = (int)(off / c->obj_size);
                bitmap_set_free(cur->bitmap, idx);
                cur->used--;
                c->free_total++;
                /* full → partial */
                if (li == 1 && cur->used + 1 == cur->obj_count) {
                    if (prev) prev->next = cur->next;
                    else *lists[li] = cur->next;
                    cur->next = c->slabs_partial;
                    c->slabs_partial = cur;
                }
                return;
            }
            prev = cur; cur = cur->next;
        }
    }
    fprintf(stderr, "kmem_cache_free: pointer %p not in cache\n", obj);
}

/* ------------------------- demo ------------------------- */

typedef struct {
    int  a;
    char b;
    /* 显式填到 32B 边界 — 模仿真实内核对象(如 struct file)             */
    char pad[27];
} toy_t;

static void show_lists(kmem_cache_t *c)
{
    int n_partial = 0, n_full = 0, n_free = 0;
    for (slab_t *s = c->slabs_partial; s; s = s->next) n_partial++;
    for (slab_t *s = c->slabs_full;     s; s = s->next) n_full++;
    for (slab_t *s = c->slabs_free;     s; s = s->next) n_free++;
    printf("    slabs: partial=%d full=%d free=%d (alloc_total=%ld free_total=%ld)\n",
           n_partial, n_full, n_free, c->alloc_total, c->free_total);
}

static void demo_basic(kmem_cache_t *c)
{
    printf("[1] basic alloc/free — same-size objects reuse a single slab\n");
    printf("    object size = %d bytes, slab = %d bytes (1 page)\n",
           OBJ_SIZE, SLAB_BYTES);

    toy_t *objs[4];
    for (int i = 0; i < 4; i++) {
        objs[i] = (toy_t *)kmem_cache_alloc(c);
        objs[i]->a = i;
        objs[i]->b = 'A' + i;
        printf("    alloc[%d] -> %p  {.a=%d, .b=%c}\n", i, (void*)objs[i], objs[i]->a, objs[i]->b);
    }
    show_lists(c);
    for (int i = 0; i < 4; i++) kmem_cache_free(c, objs[i]);
    printf("    freed all 4\n");
    show_lists(c);
}

static void demo_grow(kmem_cache_t *c)
{
    printf("\n[2] slab chain growth — alloc past SLAB_OBJ_MAX triggers a new slab\n");
    printf("    SLAB_OBJ_MAX = %d objects per slab\n", SLAB_OBJ_MAX);

    /* 分配多到必然撑出第二个 slab 的数量 */
    const int N = SLAB_OBJ_MAX + 5;
    void **batch = (void **)malloc(sizeof(void *) * N);
    for (int i = 0; i < N; i++) batch[i] = kmem_cache_alloc(c);
    printf("    allocated %d objects\n", N);
    show_lists(c);

    /* 部分释放,制造 partial/full 混合 */
    for (int i = 0; i < N; i += 3) kmem_cache_free(c, batch[i]);
    printf("    after freeing every 3rd object:\n");
    show_lists(c);

    /* 全释放 → 把 partial slab 留在链表,不主动回收(对应 Linux 的 reap 时机) */
    for (int i = 0; i < N; i++) {
        if (batch[i]) kmem_cache_free(c, batch[i]);
    }
    free(batch);
    printf("    freed all, slabs stay in chains (no reap in this demo)\n");
    show_lists(c);
}

static void demo_objects_are_isolated(kmem_cache_t *c)
{
    printf("\n[3] per-slab continuity — objs 0..N in one slab land contiguous\n");
    toy_t *objs[5];
    for (int i = 0; i < 5; i++) {
        objs[i] = (toy_t *)kmem_cache_alloc(c);
        objs[i]->a = 100 + i;
    }
    /* slab 内 slot 大小严格等于 obj_size,所以相邻 objs 间距应为 OBJ_SIZE */
    long dist1 = (long)((char*)objs[1] - (char*)objs[0]);
    long dist3 = (long)((char*)objs[3] - (char*)objs[0]);
    printf("    objs[0..1]  distance = %ld bytes (expected %d)\n", dist1, OBJ_SIZE);
    printf("    objs[0..3]  distance = %ld bytes (expected %d)\n", dist3, OBJ_SIZE * 3);

    for (int i = 0; i < 5; i++) kmem_cache_free(c, objs[i]);
}

int main(void)
{
    printf("=== slab allocator demo (simplified, single-cache) ===\n");

    kmem_cache_t *c = kmem_cache_create(OBJ_SIZE);
    if (!c) return 1;

    demo_basic(c);
    demo_grow(c);
    demo_objects_are_isolated(c);

    kmem_cache_destroy(c);
    printf("\n[ok] cache destroyed (all slabs freed).\n");
    return 0;
}
