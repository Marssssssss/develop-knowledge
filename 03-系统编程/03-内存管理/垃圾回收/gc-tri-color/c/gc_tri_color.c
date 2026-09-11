/*
 * gc_tri_color.c — 最小三色标记垃圾收集器(stop-the-world 版)
 *
 * 算法来源:Dijkstra, Lamport, Martin, Scholten, Steffens (1978)
 *   "On-the-Fly Garbage Collection: An Exercise in Cooperation"
 *
 * 模型:
 *   - 堆 = 一组对象(Object)+ 它们之间的"引用"边
 *   - 三色标记:每个对象初始白色,根可达 → 灰色,所有子节点已访问 → 黑色
 *   - 不变式(no black → white):任何黑色对象的出度**不能**指向白色对象
 *   - 终止条件:没有灰色对象 → 所有白色对象都是不可达,可回收
 *
 * 本 demo 是教学简化版(stop-the-world),不含写屏障;并发版需要 Dijkstra / Yuasa / SATB
 * 三种写屏障之一,见 README 与参考资料。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define MAX_OBJS  128
#define MAX_KIDS  4   /* 每个对象最多 4 个子引用 */

/* 三色 */
enum color { WHITE = 0, GRAY = 1, BLACK = 2 };

typedef struct obj {
    int   id;
    int   n_kids;
    int   kids[MAX_KIDS];         /* 子对象的 id 列表(简化:整型 ID) */
    int   mark_state;             /* 0=白色 1=灰色 2=黑色;冗余存,与 color 字段对应 */
} Object;

/* 堆 = 全局对象表 + "根"(主程序已知) */
static Object  heap[MAX_OBJS];
static int     heap_size  = 0;
static int     roots[MAX_OBJS];
static int     roots_n    = 0;

/* 工作栈:维护灰色对象 */
static int      gray_stack[MAX_OBJS];
static int      gray_top  = 0;

/* -------- 堆管理 -------- */

static Object *alloc_object(int id)
{
    if (heap_size >= MAX_OBJS) return NULL;
    Object *o = &heap[heap_size++];
    o->id = id;
    o->n_kids = 0;
    o->mark_state = WHITE;
    return o;
}

static void add_kid(Object *o, int kid_id) {
    if (o->n_kids < MAX_KIDS) o->kids[o->n_kids++] = kid_id;
}

static void add_root(int id) {
    roots[roots_n++] = id;
}

/* -------- 三色标记 -------- */

static void push_gray(int id) {
    if (gray_top >= MAX_OBJS) { fprintf(stderr, "gray stack overflow\n"); exit(1); }
    gray_stack[gray_top++] = id;
}

static int pop_gray(void) {
    if (gray_top == 0) return -1;
    return gray_stack[--gray_top];
}

/* 主循环:从 roots 开始,沿引用边推黑白翻转 */
static void mark(void)
{
    /* 1) 初始:把所有根对应的对象设为灰色,入栈 */
    for (int i = 0; i < roots_n; i++) {
        int rid = roots[i];
        for (int j = 0; j < heap_size; j++) {
            if (heap[j].id == rid && heap[j].mark_state == WHITE) {
                heap[j].mark_state = GRAY;
                push_gray(rid);
            }
        }
    }

    /* 2) 取灰色出栈,标记其子节点;自己变黑 */
    while (gray_top > 0) {
        int id = pop_gray();
        /* 找到 id 对应的对象 */
        Object *o = NULL;
        for (int j = 0; j < heap_size; j++) {
            if (heap[j].id == id) { o = &heap[j]; break; }
        }
        if (!o) continue;

        /* 子节点:白 → 灰 */
        for (int k = 0; k < o->n_kids; k++) {
            int kid_id = o->kids[k];
            for (int j = 0; j < heap_size; j++) {
                if (heap[j].id == kid_id && heap[j].mark_state == WHITE) {
                    heap[j].mark_state = GRAY;
                    push_gray(kid_id);
                }
            }
        }
        o->mark_state = BLACK;
    }
}

/* 3) sweep:把白色对象加入 free list(本 demo 标记 to_free=1,不打 free) */
static int sweep_and_count(void)
{
    int reclaimed = 0;
    for (int j = 0; j < heap_size; j++) {
        if (heap[j].mark_state == WHITE) {
            printf("    reclaim obj id=%d (unreachable)\n", heap[j].id);
            reclaimed++;
        } else {
            /* 重置:为下次 GC 做准备(真实 GC 通常在 next cycle 翻转白色) */
            heap[j].mark_state = WHITE;
        }
    }
    /* 简化:让 roots 保持可见(实际不需要操作) */
    return reclaimed;
}

/* -------- demo 用的小工具:从 id 找对象并读一个字段 -------- */
static void print_state(const char *tag) {
    int w = 0, g = 0, b = 0;
    for (int j = 0; j < heap_size; j++) {
        if (heap[j].mark_state == WHITE) w++;
        else if (heap[j].mark_state == GRAY) g++;
        else b++;
    }
    printf("    [%-15s] white=%d gray=%d black=%d\n", tag, w, g, b);
}

/* --------------------- demo --------------------- */

static void clear_heap(void) { heap_size = 0; roots_n = 0; gray_top = 0; }

static void demo_simple_cycle(void)
{
    printf("[1] simple graph: roots -> A -> B, X -> Y (X,Y unreachable)\n");
    clear_heap();
    Object *A = alloc_object(1); add_kid(A, 2);
    Object *B = alloc_object(2);
    Object *X = alloc_object(10); add_kid(X, 11);
    Object *Y = alloc_object(11);
    add_root(1);   /* Root 直接指向 A */

    print_state("initial");
    mark();
    print_state("after mark");
    int reclaimed = sweep_and_count();
    printf("    reclaimed=%d (expected 2: ids 10,11)\n", reclaimed);
}

static void demo_cyclic(void)
{
    printf("\n[2] cycle that refcount CANNOT collect: A <-> B\n");
    printf("    A is a root, A->B and B->A form a cycle\n");
    clear_heap();
    Object *A = alloc_object(1); add_kid(A, 2);
    Object *B = alloc_object(2); add_kid(B, 1);    /* back edge */
    add_root(1);

    mark();
    print_state("after mark");
    int reclaimed = sweep_and_count();
    printf("    reclaimed=%d (expected 0: both reachable via A->B->A)\n", reclaimed);
}

static void demo_disconnected(void)
{
    printf("\n[3] disconnected sub-graph\n");
    printf("    roots -> A; D -> E (D,E unreachable, no path from root)\n");
    clear_heap();
    Object *A = alloc_object(1);
    Object *D = alloc_object(4); add_kid(D, 5);
    Object *E = alloc_object(5);
    add_root(1);

    mark();
    print_state("after mark");
    int reclaimed = sweep_and_count();
    printf("    reclaimed=%d (expected 2: ids 4,5)\n", reclaimed);
}

static void demo_diamond(void)
{
    printf("\n[4] diamond — shared kid (no double-graying)\n");
    printf("    A → B, A → C, B → D, C → D  (D shared, must only be marked once)\n");
    clear_heap();
    Object *A = alloc_object(1); add_kid(A, 2); add_kid(A, 3);
    Object *B = alloc_object(2); add_kid(B, 4);
    Object *C = alloc_object(3); add_kid(C, 4);
    Object *D = alloc_object(4);
    add_root(1);

    mark();
    print_state("after mark");
    /* D 必须只进 gray stack 一次;否则多扫,但结果仍正确 */
    int reclaimed = sweep_and_count();
    printf("    reclaimed=%d (expected 0)\n", reclaimed);
}

static void demo_orphan_subtree(void)
{
    printf("\n[5] orphan subtree via B → X (only reachable through B)\n");
    clear_heap();
    Object *A = alloc_object(1); add_kid(A, 2);
    Object *B = alloc_object(2); add_kid(B, 20);
    Object *X = alloc_object(20);
    add_root(1);

    mark();
    print_state("after mark");
    int reclaimed = sweep_and_count();
    printf("    reclaimed=%d (expected 0)\n", reclaimed);
}

int main(void)
{
    printf("=== tri-color mark-and-sweep GC demo (stop-the-world) ===\n\n");

    demo_simple_cycle();
    demo_cyclic();
    demo_disconnected();
    demo_diamond();
    demo_orphan_subtree();

    printf("\n[ok] all cycles shown. Tri-color reclaims cycles that refcount cannot.\n");
    return 0;
}
