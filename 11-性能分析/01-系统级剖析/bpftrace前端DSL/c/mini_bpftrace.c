/* mini_bpftrace.c — bpftrace 后端语义的 C 复刻
 *
 * 复刻三件事(与 python/、go/ 同口径,输出格式逐字符对齐官方样例):
 *   1. hist() 的 log2 分桶 + 标签(>=1024 用 k/M/G 刻度)
 *   2. hist()/lhist() 的 ASCII 渲染:标签左对齐 15 列 + 计数右对齐 9 列 + 柱区 52
 *   3. 事件驱动的 sys_enter/sys_exit 配对(@start[tid] -> hist(delta))
 *
 * 只依赖 libc。完整 DSL 词法/语法分析器见 python 版。
 * 编译: gcc -O2 -Wall -Wextra -pedantic mini_bpftrace.c -o mini_bpftrace
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define MAX_BUCKETS 64
#define BAR_WIDTH   52
#define MAX_TIDS    64

/* ---------- 1. hist() 分桶: 0/1 -> 0 ; 2..3 -> 1 ; 4..7 -> 2 ; 8..15 -> 3 … */
static int hist_index(long long v)
{
    int i = 0;
    if (v < 2) {
        return 0;                 /* 0 与 1 共用首桶(官方输出写作 [0, 1]) */
    }
    while ((1LL << (i + 1)) <= v) {
        i++;
    }
    return i;
}

/* 桶边界刻度: >=1024 起用 k/M/G(官方 tutorial 出现 [2k, 4k) / [512k, 1M)) */
static void fmt_bucket(long long n, char *out, size_t n_out)
{
    if (n >= (1LL << 30)) {
        snprintf(out, n_out, "%lldG", n >> 30);
    } else if (n >= (1LL << 20)) {
        snprintf(out, n_out, "%lldM", n >> 20);
    } else if (n >= (1LL << 10)) {
        snprintf(out, n_out, "%lldk", n >> 10);
    } else {
        snprintf(out, n_out, "%lld", n);
    }
}

static void hist_label(int i, char *out, size_t n_out)
{
    char lo[24], hi[24];
    if (i == 0) {
        snprintf(out, n_out, "[0, 1]");   /* 右括号是方括号:同时收纳 0 与 1 */
        return;
    }
    fmt_bucket(1LL << i, lo, sizeof lo);
    fmt_bucket(1LL << (i + 1), hi, sizeof hi);
    snprintf(out, n_out, "[%s, %s)", lo, hi);
}

/* ---------- 2. lhist(): M = (max-min)/step 个区间桶,再加两端越界桶 ---------- */
static int lhist_m(long long lo, long long hi, long long step)
{
    return (int)((hi - lo) / step);
}

static int lhist_index(long long v, long long lo, long long hi, long long step)
{
    if (v <= lo) {
        return -1;                                  /* (..., min] */
    }
    if (v >= hi) {
        return lhist_m(lo, hi, step);               /* [max, ...) */
    }
    return (int)((v - lo) / step);
}

static void lhist_label(int k, long long lo, long long hi, long long step,
                        char *out, size_t n_out)
{
    if (k == -1) {
        snprintf(out, n_out, "(...,%lld]", lo);
    } else if (k == lhist_m(lo, hi, step)) {
        snprintf(out, n_out, "[%lld,...)", hi);
    } else {
        snprintf(out, n_out, "[%lld, %lld)", lo + (long long)k * step,
                 lo + (long long)(k + 1) * step);
    }
}

/* ---------- 3. 渲染:标签<15 + 计数>9 + " |" + 柱区52 + "|" ---------- */
static int bar_len(long long c, long long maxc)
{
    int n;
    if (c <= 0) {
        return 0;
    }
    n = (int)(c * BAR_WIDTH / maxc);
    return n > 0 ? n : 1;      /* 非零计数至少给 1 个 @,否则窄峰会被抹掉 */
}

static void print_row(const char *label, long long c, long long maxc)
{
    int n = bar_len(c, maxc), i;
    printf("%-15s%9lld |", label, c);
    for (i = 0; i < n; i++) {
        putchar('@');
    }
    for (; i < BAR_WIDTH; i++) {
        putchar(' ');
    }
    puts("|");
}

/* ---------- 4. 执行引擎: sys_enter 记账 / sys_exit 配对并删除 ---------- */
struct agg {
    char  name[32];            /* 例如 "@ns[app]" */
    int   used;
    long long buckets[MAX_BUCKETS];
};

struct engine {
    struct agg aggs[8];
    int n_aggs;
    long long start[MAX_TIDS];     /* 以 tid 为键的 @start[tid],-1 表示未设置 */
};

static struct agg *agg_get(struct engine *e, const char *name)
{
    int i;
    for (i = 0; i < e->n_aggs; i++) {
        if (strcmp(e->aggs[i].name, name) == 0) {
            return &e->aggs[i];
        }
    }
    if (e->n_aggs >= (int)(sizeof e->aggs / sizeof e->aggs[0])) {
        return NULL;
    }
    memset(&e->aggs[e->n_aggs], 0, sizeof e->aggs[0]);
    snprintf(e->aggs[e->n_aggs].name, sizeof e->aggs[0].name, "%s", name);
    e->aggs[e->n_aggs].used = 1;
    return &e->aggs[e->n_aggs++];
}

/* comm 与 @start[tid] 都就绪才算一次合法配对(对应 bpftrace 的谓词) */
static void on_enter(struct engine *e, int tid, long long nsecs)
{
    if (tid >= 0 && tid < MAX_TIDS) {
        e->start[tid] = nsecs;
    }
}

static void on_exit(struct engine *e, int tid, long long nsecs, const char *comm)
{
    struct agg *a;
    long long delta;
    if (tid < 0 || tid >= MAX_TIDS || e->start[tid] < 0) {
        return;                       /* 缺 @start[tid]: 谓词挡住,不记账 */
    }
    delta = nsecs - e->start[tid];
    {
        char key[40];
        snprintf(key, sizeof key, "@ns[%s]", comm);
        a = agg_get(e, key);
    }
    if (a != NULL) {
        a->buckets[hist_index(delta)]++;
    }
    e->start[tid] = -1;               /* delete(@start, tid) */
}

static void dump_hist(const struct agg *a)
{
    int last = -1, i;
    long long maxc = 0;
    char label[32];
    /* bpftrace 恒从桶 0 开始打印(官方 tutorial 里 [0, 1] 行为 0 也照样打印) */
    for (i = 0; i < MAX_BUCKETS; i++) {
        if (a->buckets[i] > 0 && i > last) {
            last = i;
        }
        if (a->buckets[i] > maxc) {
            maxc = a->buckets[i];
        }
    }
    if (last < 0) {
        printf("%s: (empty)\n", a->name);
        return;
    }
    printf("%s:\n", a->name);
    for (i = 0; i <= last; i++) {
        hist_label(i, label, sizeof label);
        print_row(label, a->buckets[i], maxc);
    }
}

int main(void)
{
    struct engine e;
    /* 与 python/、go/ 完全相同的合成事件: 8 条 4~14us + 4 条 16~31ms + 3 条写 */
    static const struct { const char *name; long long d; } ev[] = {
        {"read", 4000}, {"read", 5200}, {"read", 6800}, {"read", 8000},
        {"read", 9500}, {"read", 11000}, {"read", 13000}, {"read", 14500},
        {"openat", 16000000}, {"openat", 20000000}, {"openat", 24000000},
        {"openat", 31000000}, {"write", 700}, {"write", 1300}, {"write", 2600},
    };
    struct agg lin;
    long long ts = 0, total = 0;
    size_t i;
    int k, v;
    static const long long sv[] = {66, 120, 300, 500, 900, 1900, 2500, 99999};

    memset(&e, 0, sizeof e);
    for (i = 0; i < MAX_TIDS; i++) {
        e.start[i] = -1;
    }

    printf("== syscall 延迟直方图(@ns[app]) ==\n");
    for (i = 0; i < sizeof ev / sizeof ev[0]; i++) {
        int tid = 100 + (int)(i % 2);
        ts += 1000;
        on_enter(&e, tid, ts);
        ts += ev[i].d;
        on_exit(&e, tid, ts, "app");
    }
    for (i = 0; i < (size_t)e.n_aggs; i++) {
        int b;
        dump_hist(&e.aggs[i]);
        for (b = 0; b < MAX_BUCKETS; b++) {
            total += e.aggs[i].buckets[b];
        }
    }
    printf("  配对成 accounted = %lld 条;剩余未删的 @start 键 = ", total);
    {
        int left = 0;
        for (i = 0; i < MAX_TIDS; i++) {
            if (e.start[i] >= 0) left++;
        }
        printf("%d 个\n", left);
    }

    /* 未配对的 exit(comm 不符 / 缺 @start)都不应记账 */
    on_exit(&e, 7, 5, "other");
    printf("  喂 1 个无 @start 的 exit 后 accounted 仍为 %lld 条\n", total);

    printf("== lhist() 线性直方图(vfs_read 返回字节数, 0~2000 步长 200) ==\n");
    memset(&lin, 0, sizeof lin);
    snprintf(lin.name, sizeof lin.name, "@bytes[comm]");
    for (i = 0; i < sizeof sv / sizeof sv[0]; i++) {
        lin.buckets[lhist_index(sv[i], 0, 2000, 200) + 1]++;   /* +1: 桶 -1 挪到下标 0 */
    }
    {
        int last = -1;
        long long maxc = 0;
        char label[32];
        /* lhist 的桶已全部预置(下标 0 即 (...,0]),故从下标 0 开始打印 */
        for (k = 0; k < MAX_BUCKETS; k++) {
            if (lin.buckets[k] > 0) {
                if (k > last) last = k;
            }
            if (lin.buckets[k] > maxc) maxc = lin.buckets[k];
        }
        printf("%s:\n", lin.name);
        for (k = 0; k <= last; k++) {
            lhist_label(k - 1, 0, 2000, 200, label, sizeof label);
            print_row(label, lin.buckets[k], maxc);
        }
    }
    /* lhist 桶数 = M + 2: 越界两端各占一桶 */
    v = lhist_m(0, 2000, 200) + 2;
    printf("  lhist(_,0,2000,200) 桶数 = M+2 = %d;  2000 与 99999 同落 [2000,...) = %d\n",
           v, lhist_index(2000, 0, 2000, 200) == lhist_index(99999, 0, 2000, 200));
    return 0;
}
