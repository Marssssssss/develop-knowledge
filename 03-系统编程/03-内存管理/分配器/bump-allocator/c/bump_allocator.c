/*
 * bump_allocator.c — 最小 bump (arena) 分配器
 *
 * 工作机制:
 *   - 预分配一大块连续内存(mmap PROT_READ|PROT_WRITE MAP_PRIVATE|MAP_ANONYMOUS)
 *   - 维护一个 offset(从 base 起),每次分配把 offset 对齐到 alignment,
 *     然后对齐的地址返回给调用方、offset += size
 *   - 释放只能整块重置(offset = 0)— 不能 free 单个对象
 *
 * 对齐数学:
 *   current 是当前 offset 对应的绝对地址(uintptr_t)
 *   aligned = (current + alignment - 1) & ~(alignment - 1)
 *   即把 current 向上取整到 alignment 的倍数
 *
 * 注意事项:
 *   - struct 中不能放匿名 struct(必须 typedef struct Arena { ... } Arena;)
 *     以便内部 struct Arena *next 自引用
 *   - alignment 必须为 2 的幂(分配函数会校验),否则 & ~(alignment-1) 失效
 *   - 大对象请求 alignment + size 后可能超过 capacity;本 demo 演示单块,
 *     超过则返回 NULL
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define ARENA_CAPACITY (64 * 1024)  /* 64 KiB 单块 */

typedef struct Arena {
    char    *base;       /* mmap 出的整块首地址                 */
    size_t   capacity;   /* 总容量(字节)                      */
    size_t   offset;     /* 下一个分配的起点(offset 相对 base) */
} Arena;

/* 初始化:把 Arena 控制结构与底层 buffer 放在同一 mmap 区域,简化管理。       */
static Arena *arena_create(size_t capacity)
{
    /* Arena 控制头 + 数据段一并 mmap,只一次系统调用。                       */
    size_t total   = sizeof(Arena) + capacity;
    void  *region  = mmap(NULL, total,
                          PROT_READ | PROT_WRITE,
                          MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (region == MAP_FAILED) {
        perror("mmap");
        return NULL;
    }
    Arena *a      = (Arena *)region;
    a->base       = (char *)region + sizeof(Arena);
    a->capacity   = capacity;
    a->offset     = 0;
    return a;
}

/* 整块重置:offset 回 0,后续分配复用同一段内存。                            */
static void arena_reset(Arena *a)
{
    a->offset = 0;
}

/* 销毁:把 mmap 区域还给内核。                                                 */
static int arena_destroy(Arena *a)
{
    if (!a) return 0;
    size_t total = sizeof(Arena) + a->capacity;
    return munmap(a, total);
}

/* 对齐到 2 的幂;返回 (x 向上取整到 alignment 倍数)。                       */
static inline uintptr_t align_up(uintptr_t x, size_t alignment)
{
    /* alignment 必为 2 的幂;否则 & ~(alignment-1) 会给出错误结果。          */
    return (x + alignment - 1) & ~(alignment - 1);
}

/* 分配 size 字节,按 alignment 对齐。                                          *
 * 返回:成功 → 对齐后的指针;失败 → NULL.                                       */
static void *arena_alloc(Arena *a, size_t size, size_t alignment)
{
    if (!a || alignment == 0 || (alignment & (alignment - 1)) != 0) {
        /* alignment 必须是 2 的幂,这是 (x + a-1) & ~(a-1) 能正确取整的前提 */
        fprintf(stderr, "arena_alloc: invalid alignment (%zu)\n", alignment);
        return NULL;
    }

    /* 1) 当前 offset 转绝对地址 → 向上对齐                                */
    uintptr_t current  = (uintptr_t)(a->base + a->offset);
    uintptr_t aligned  = align_up(current, alignment);
    size_t    pad      = (size_t)(aligned - current);   /* 填充字节数      */

    /* 2) 加上 size 检查是否超容量                                          */
    if (a->offset + pad + size > a->capacity) {
        fprintf(stderr, "arena_alloc: OOM (need %zu, free %zu)\n",
                pad + size, a->capacity - a->offset);
        return NULL;
    }

    /* 3) 提交新的 offset                                                  */
    a->offset += pad + size;
    return (void *)aligned;
}

/* ----------------------- demo ----------------------- */

typedef struct {
    const char *name;
    unsigned    id;
} Data;

static void demo_basic(Arena *a)
{
    printf("[1] basic allocations (alignment = sizeof(void*) = %zu)\n",
           sizeof(void *));

    int  *iptr  = arena_alloc(a, sizeof(int), _Alignof(int));
    *iptr = 42;
    printf("    int*  -> offset-aligned to %zu-byte boundary, value=%d\n",
           _Alignof(int), *iptr);

    double *dptr = arena_alloc(a, sizeof(double), _Alignof(double));
    *dptr = 3.14;
    printf("    double* -> value=%.2f\n", *dptr);

    Data  *d   = arena_alloc(a, sizeof(Data), _Alignof(Data));
    d->name = "hello";
    d->id   = 7;
    printf("    struct Data -> {name=\"%s\", id=%u}\n", d->name, d->id);

    printf("    arena offset after 3 allocs = %zu bytes\n", a->offset);
}

static void demo_alignment(Arena *a)
{
    printf("\n[2] explicit alignment (32-byte)\n");

    /* 先分配一个 1 字节对象,把 offset 推到非对齐位置;然后用 32 字节对齐查看填充  */
    char  *c    = arena_alloc(a, sizeof(char), 1);
    *c = 'X';
    size_t before = a->offset;

    int  *aligned32 = arena_alloc(a, sizeof(int), 32);
    size_t after    = a->offset;

    printf("    char*  @offset %zu   (before)\n", before - 1);
    printf("    int*   @alignment 32, padding = %zu bytes (offset went %zu -> %zu)\n",
           after - before - sizeof(int), before, after);
    printf("    int value = %d\n", *aligned32);
}

static void demo_reset_reuse(Arena *a)
{
    printf("\n[3] reset & reuse — bump allocator has no per-object free\n");

    size_t before = a->offset;
    arena_reset(a);
    printf("    reset(): offset %zu -> 0\n", before);

    /* 第二次分配能从 offset=0 重用同一段 buffer。                              */
    int *p1 = arena_alloc(a, sizeof(int), _Alignof(int));
    *p1 = 100;
    int *p2 = arena_alloc(a, sizeof(int), _Alignof(int));
    *p2 = 200;
    printf("    reuse: p1=%d, p2=%d  (offset=%zu)\n", *p1, *p2, a->offset);
    printf("    note: previous int=42, double=3.14, etc. are now logically freed\n");
}

static void demo_oob_check(Arena *a)
{
    printf("\n[4] OOM check — request too large for remaining capacity\n");
    size_t remaining = a->capacity - a->offset;
    printf("    remaining = %zu bytes, capacity = %zu bytes\n",
           remaining, a->capacity);

    size_t huge = a->capacity;  /* 故意请求 = capacity,显然超 remaining         */
    void *p = arena_alloc(a, huge, 1);
    if (!p) printf("    arena_alloc(%zu) returned NULL as expected ✓\n", huge);
    else    printf("    unexpected: alloc succeeded??\n");
}

int main(void)
{
    printf("=== bump (arena) allocator demo ===\n");
    printf("capacity = %d KiB\n", ARENA_CAPACITY / 1024);

    Arena *a = arena_create(ARENA_CAPACITY);
    if (!a) return 1;

    demo_basic(a);
    demo_alignment(a);
    demo_reset_reuse(a);
    demo_oob_check(a);

    arena_destroy(a);
    printf("\n[ok] arena destroyed.\n");
    return 0;
}
