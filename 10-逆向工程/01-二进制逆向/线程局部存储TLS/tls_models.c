/* TLS 四种访问模型的 C 侧最小实现。
 *
 * 数据来源（本轮实测下载并提取正文）：
 *   Ulrich Drepper, "ELF Handling For Thread-Local Storage" v0.20 (2005-12-21)
 *     §3.1 模块编号从 1 开始，可执行文件固定为 1
 *     §3.2 __tls_get_addr 的原型与延迟分配
 *     §4.1.6 / 4.2.6 / 4.3.6 / 4.4.5 x86-64 四种模型的指令序列与重定位
 *     https://www.uclibc.org/docs/tls.pdf
 *
 * 只建模「算地址」这一层；不建模真实 glibc 的 dtv 扩容、generation counter
 * 与信号安全。
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_MODULES 8
#define EXEC_MODULE_ID 1

/* 一个模块的 TLS template */
struct tls_module {
    int id;
    const char *name;
    size_t block_size;
    size_t offset_of_sym;   /* 本 demo 每个模块只跟踪一个符号，够表达口径差异 */
};

/* 一个线程：dtv[m] 是模块 m 的 TLS 块基址。dtv[0] 是 generation。 */
struct thread {
    int tid;
    uintptr_t tp;                 /* 线程指针（x86-64 上取 %fs:0） */
    uintptr_t dtv[MAX_MODULES];
    uintptr_t next_base;
    int alloc_count;
};

/* 静态 TLS 区：属于它的模块在创建线程时就已经铺好，不参与延迟分配 */
struct static_area {
    int used[MAX_MODULES];        /* 槽是否属于静态区 */
    long delta[MAX_MODULES];      /* 块起始相对 TP 的偏移（variant II 下为负） */
};

static uintptr_t allocate_tls(struct thread *t, const struct tls_module *m)
{
    uintptr_t addr = t->next_base;
    t->next_base = addr + m->block_size + 0x1000;
    t->dtv[m->id] = addr;
    t->alloc_count++;
    return addr;
}

/* §3.2 给出的原型，含「未分配则先分配」的延迟分配分支 */
static uintptr_t tls_get_addr(struct thread *t, const struct tls_module *m,
                              size_t offset)
{
    uintptr_t block = t->dtv[m->id];
    if (block == 0)
        block = allocate_tls(t, m);
    return block + offset;
}

/* GD：每次访问一次调用 ================ */
static uintptr_t gd_access(struct thread *t, const struct tls_module *m)
{
    return tls_get_addr(t, m, m->offset_of_sym);
}

/* LD：先取基址，之后每变量只加 dtpoff */
static uintptr_t ld_access(struct thread *t, const struct tls_module *m,
                           uintptr_t base)
{
    if (base == 0)
        base = tls_get_addr(t, m, 0);
    return base + m->offset_of_sym;
}

/* IE：TP + GOT 槽里启动时填好的 TPOFF */
static uintptr_t ie_access(const struct thread *t, long tpoff)
{
    return t->tp + (uintptr_t)(intptr_t)tpoff;
}

/* LE：TP + 链接期就定死的 immediate */
static uintptr_t le_access(const struct thread *t, long tpoff)
{
    return t->tp + (uintptr_t)(intptr_t)tpoff;
}

static struct thread *new_thread(int tid, uintptr_t tp,
                                 const struct static_area *sa)
{
    struct thread *t = (struct thread *)calloc(1, sizeof(*t));
    int i;
    t->tid = tid;
    t->tp = tp;
    t->next_base = 0x7F0000000000ULL + (uintptr_t)tid * 0x1000000ULL;
    t->dtv[0] = 1;                       /* generation */
    for (i = 0; i < MAX_MODULES; i++) {
        if (sa->used[i])
            t->dtv[i] = (uintptr_t)((intptr_t)t->tp + sa->delta[i]);
    }
    return t;
}

int main(void)
{
    struct static_area sa;
    struct thread *t0, *t1;
    struct tls_module exe_mod = { 1, "a.out", 0x40, 0x20 };
    struct tls_module so_mod = { 2, "libhelper.so", 0x20, 0x08 };
    long tpoff_static;
    uintptr_t gd, ld, ie, le, base;

    memset(&sa, 0, sizeof(sa));
    sa.used[EXEC_MODULE_ID] = 1;
    sa.delta[EXEC_MODULE_ID] = -(long)exe_mod.block_size;   /* variant II */

    t0 = new_thread(0, 0x7FFFFFFF0000ULL, &sa);
    t1 = new_thread(1, 0x7FFFFFFF0000ULL + 0x100000ULL, &sa);

    tpoff_static = sa.delta[EXEC_MODULE_ID] + (long)exe_mod.offset_of_sym;

    base = tls_get_addr(t0, &exe_mod, 0);
    gd = gd_access(t0, &exe_mod);
    ld = ld_access(t0, &exe_mod, base);
    ie = ie_access(t0, tpoff_static);
    le = le_access(t0, tpoff_static);

    printf("模块号：可执行文件=%d，共享库=%d\n", exe_mod.id, so_mod.id);
    printf("静态块基址 = TP + %ld (块大小 0x%zx)\n",
           sa.delta[EXEC_MODULE_ID], exe_mod.block_size);
    printf("x@tpoff = %ld（相对线程指针的负向偏移）\n", tpoff_static);
    printf("GD=0x%llx LD=0x%llx IE=0x%llx LE=0x%llx  -> 四条路径%s\n",
           (unsigned long long)gd, (unsigned long long)ld,
           (unsigned long long)ie, (unsigned long long)le,
           (gd == ld && ld == ie && ie == le) ? "一致" : "不一致");

    printf("动态模块分配次数：线程0=%d（第一次访问才分配）\n", t0->alloc_count);
    gd = gd_access(t0, &so_mod);
    printf("访问 libhelper 后：alloc=%d, addr=0x%llx\n",
           t0->alloc_count, (unsigned long long)gd);
    gd = gd_access(t1, &so_mod);
    printf("线程1 同一变量：addr=0x%llx（%s）\n", (unsigned long long)gd,
           gd != t0->dtv[2] + so_mod.offset_of_sym ? "与线程0 不同" : "错误：相同");

    free(t0);
    free(t1);
    return 0;
}
