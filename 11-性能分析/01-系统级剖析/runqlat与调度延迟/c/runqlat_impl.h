/* runqlat_impl.h — runqlat_check.c 的实现部分(文本级包含,不是独立翻译单元)
 *
 * 包含:头文件、常量、2 的幂分桶与直方图渲染、模式识别、wakeup/switch 配对、
 *       /proc/schedstat 文本解析、排队论近似。自检与 main 在 runqlat_check.c。
 *
 * 为什么用 #include 而不是拆成第二个 .c:本机没有 C 工具链,拆成两个翻译单元就
 * 必须同步改 static/原型,改错也编不出来、发现不了。文本包含让所有 static 定义
 * 仍留在同一个 TU 里,零链接风险,只是让单文件行数落到 300 行以内。
 *
 * 编译入口始终是 runqlat_check.c:  cc -O2 -o runqlat_check runqlat_check.c -lm
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define BAR_W     40   /* runqlat 柱区宽度(与官方样例一致) */
#define CPU_COUNT 8
#define NBUCKET   16   /* 重放覆盖到的桶数(最长 32768~65535 us) */
#define TIDTAB    8192

/* ------------------------------------------------------------ 1. 2 的幂分桶 */
static int bucket_index(double usec) {
    int i = 0;
    if (usec < 2) return 0;                       /* 第 0 桶是 [0,1] 而不是 [1,2] */
    while ((1LL << (i + 1)) <= (long long)usec) i++;
    return i;
}
static long long bucket_low(int i)  { return i == 0 ? 0 : (1LL << i); }
static long long bucket_high(int i) { return (1LL << (i + 1)) - 1; }

/* 一行 = "%10s -> %-11s: %-9d|%-40s|"(逐字符对齐官方 runqlat 样例) */
static void row_str(char *out, size_t n, int i, int c, int maxc) {
    int bar = (maxc > 0 && c > 0) ? c * BAR_W / maxc : 0;
    char stars[BAR_W + 1];
    int k;
    for (k = 0; k < bar; k++) stars[k] = '*';
    stars[bar] = '\0';
    snprintf(out, n, "%10lld -> %-11lld: %-9d|%-*s|",
             bucket_low(i), bucket_high(i), c, BAR_W, stars);
}
static int hist_last(const int *b, int nb) {
    int i, last = -1;
    for (i = 0; i < nb; i++) if (b[i]) last = i;
    return last;
}
static int hist_max(const int *b, int nb) {
    int i, m = 0;
    for (i = 0; i < nb; i++) if (b[i] > m) m = b[i];
    return m;
}
static int count_char(const char *s, char ch) {
    int n = 0;
    for (; *s; s++) if (*s == ch) n++;
    return n;
}

/* ------------------------------------------------------------ 2. 模式识别 */
/* 找出「模式」:连续非空且占比达标的桶段。双峰即两段。返回段数,段落写进 out。 */
static int modes(const int *b, int nb, double min_share, int out[][2], int outmax) {
    int i, total = 0, n = 0, lo = -1, hi = -1;
    for (i = 0; i < nb; i++) total += b[i];
    if (total == 0) return 0;
    for (i = 0; i < nb; i++) {
        if ((double)b[i] / (double)total < min_share) continue;
        if (lo < 0)                    { lo = hi = i; }
        else if (i == hi + 1)          { hi = i; }
        else { if (n < outmax) { out[n][0] = lo; out[n][1] = hi; } n++; lo = hi = i; }
    }
    if (lo >= 0) { if (n < outmax) { out[n][0] = lo; out[n][1] = hi; } n++; }
    return n;
}

/* ------------------------------------------------------------ 3. 事件配对 */
typedef struct { int kind; long long ts; int tid; } ev_t;   /* kind 0=wakeup 1=switch(next) */

/* switch 里的 next 线程才是「开始运行」的那一个;prev 是「离开 CPU」。
   没有对应 wakeup 的首次上 CPU 不能算成 now-0(会造出巨值),应计数并跳过。 */
static void pair_latencies(const ev_t *ev, int n, int *buckets, int nb,
                           int *paired, int *orphan) {
    static long long pend[TIDTAB];
    static unsigned char has[TIDTAB];
    int i;
    *paired = *orphan = 0;
    memset(has, 0, sizeof has);
    for (i = 0; i < n; i++) {
        int t = ev[i].tid;
        if (t < 0 || t >= TIDTAB) continue;
        if (ev[i].kind == 0) { pend[t] = ev[i].ts; has[t] = 1; continue; }
        if (has[t]) {
            int bi = bucket_index((double)(ev[i].ts - pend[t]) / 1000.0);
            if (bi >= 0 && bi < nb) buckets[bi]++;
            has[t] = 0;
            (*paired)++;
        } else {
            (*orphan)++;
        }
    }
}

/* 重放官方博文那台「重负载机」的直方图形状(计数取自该样例):
   快峰 1~12 us(正常情况下线程被唤醒后几乎立刻上 CPU),
   慢峰 18~40 ms(CPU 饱和后要排队等),两峰之间是真空。
   这是形状重放,不是真实采集数据。 */
static int build_replay(ev_t *ev, int cap) {
    static const struct { long long lat_us; int n; } plan[] = {
        {1, 233}, {3, 742}, {7, 203}, {12, 173},   /* -> 桶 0,1,2,3 */
        {18000, 809}, {40000, 64}                  /* -> 桶 14,15   */
    };
    long long ts = 0;
    int tid = 1000, n = 0;
    size_t p; int k;
    for (p = 0; p < sizeof(plan) / sizeof(plan[0]); p++) {
        for (k = 0; k < plan[p].n; k++) {
            if (n + 2 > cap) return n;
            tid++;
            ev[n].kind = 0; ev[n].ts = ts; ev[n].tid = tid; n++;   /* wakeup */
            ts += plan[p].lat_us * 1000;
            ev[n].kind = 1; ev[n].ts = ts; ev[n].tid = tid; n++;   /* switch(next) */
        }
        ts += 1000;   /* 事件之间留缝,避免不同组共享同一时间点 */
    }
    return n;
}

/* --------------------------------------------------- 4. /proc/schedstat 解析 */
/* cpu<N> 行 9 个字段(对应官方 sched-stats 文档编号 1..9):
   0 yld_count 1 array_exp 2 sched_count 3 sched_goidle 4 ttwu_count
   5 ttwu_local 6 rq_cpu_time 7 run_delay 8 pcount                      */
static int parse_schedstat(const char *text, long long out[9]) {
    const char *p = text;
    while (*p) {
        const char *e = strchr(p, '\n');
        size_t len = e ? (size_t)(e - p) : strlen(p);
        if (len > 3 && strncmp(p, "cpu", 3) == 0 && p[3] >= '0' && p[3] <= '9') {
            const char *q = p, *end = p + len;
            int got = 0;
            while (q < end && *q != ' ' && *q != '\t') q++;     /* 跳过 "cpu<N>" */
            while (got < 9 && q < end) {
                long long v = 0; int any = 0;
                while (q < end && (*q == ' ' || *q == '\t')) q++;
                while (q < end && *q >= '0' && *q <= '9') { v = v * 10 + (*q - '0'); q++; any = 1; }
                if (!any) break;
                out[got++] = v;
            }
            if (got == 9) return 1;
        }
        if (!e) break;
        p = e + 1;
    }
    return 0;
}

/* 官方 perf sched stats 的派生指标: 等待时间 / 运行时间(%)。
   两次采样 run_delay 都恒为 0 时无法区分「真没人等」与「统计没开」,
   此时返回 0 表示「未统计」,而不是给出一个误导性的 0%。 */
static int run_delay_ratio(const long long a[9], const long long b[9], double *out) {
    long long ran, waited;
    if (a[7] == 0 && b[7] == 0) return 0;
    ran = b[6] - a[6];
    waited = b[7] - a[7];
    if (ran == 0) return 0;
    *out = 100.0 * (double)waited / (double)ran;
    return 1;
}

/* 三个字段: CPU 上时间(ns) / 运行队列等待时间(ns) / 被调度的次数 */
static int parse_pid_schedstat(const char *text, long long out[3]) {
    return sscanf(text, "%lld %lld %lld", &out[0], &out[1], &out[2]) == 3;
}

/* -------------------------------------------------------------- 5. 排队论 */
/* M/M/1 的平均排队等待时间(以服务时间为单位): Wq = rho/(1-rho)。
   rho→1 时发散,这就是「CPU 利用率 90% 与 98% 体感完全不同」的数学来源。 */
static double mmc_wait(double rho) {
    if (rho <= 0) return 0.0;
    if (rho >= 1) return HUGE_VAL;
    return rho / (1 - rho);
}
