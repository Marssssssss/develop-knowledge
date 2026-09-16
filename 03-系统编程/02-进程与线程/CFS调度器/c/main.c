/*
 * CFS 完全公平调度器:最小模拟(C 实现)
 *
 * 依据 docs.kernel.org/scheduler/sched-design-CFS.html:
 *   vruntime += delta * NICE_0 / weight;永远挑 vruntime 最小(最左);
 *   min_vruntime 单调递增,用于放置新激活实体。
 *
 * 本文件断言:等权重平分、权重比即 CPU 份额比、min_vruntime 单调。
 */
#include <stdio.h>
#include <stdlib.h>

#define NICE_0 1024
#define GRAN 8
#define TOTAL_MS 1000

typedef struct {
    const char *name;
    long long weight;
    long long vruntime;
    long long runtime;
} task;

static void check(int cond, const char *label)
{
    if (cond) {
        printf("PASS: %s\n", label);
    } else {
        printf("FAIL: %s\n", label);
        exit(EXIT_FAILURE);
    }
}

static task *leftmost(task **ts, int n)
{
    task *best = ts[0];
    for (int i = 1; i < n; i++) {
        if (ts[i]->vruntime < best->vruntime)
            best = ts[i];               /* 平手时保留表序最前(先入队优先) */
    }
    return best;
}

static long long run_and_measure(task **ts, int n, long long total_ms,
                                 long long *min_vruntime_hist, int *hist_len)
{
    long long min_vruntime = 0;
    long long ran = 0;
    while (ran < total_ms) {
        task *cur = leftmost(ts, n);
        cur->vruntime += GRAN * NICE_0 / cur->weight;   /* 记账 */
        cur->runtime += GRAN;
        ran += GRAN;

        long long m = ts[0]->vruntime;                   /* 推进 min_vruntime */
        for (int i = 1; i < n; i++)
            if (ts[i]->vruntime < m)
                m = ts[i]->vruntime;
        if (m > min_vruntime)
            min_vruntime = m;
        if (min_vruntime_hist != NULL && *hist_len < 4096)
            min_vruntime_hist[(*hist_len)++] = min_vruntime;
    }
    return ran;
}

static int monotone(const long long *h, int n)
{
    for (int i = 1; i < n; i++)
        if (h[i] < h[i - 1])
            return 0;
    return 1;
}

int main(void)
{
    /* 场景 1:等权重 -> 平分 */
    task a = {"A", 1024, 0, 0}, b = {"B", 1024, 0, 0};
    task *ts1[2] = {&a, &b};
    long long hist[4096];
    int hist_len = 0;
    run_and_measure(ts1, 2, TOTAL_MS, hist, &hist_len);
    check(a.runtime + b.runtime == TOTAL_MS, "equal weights: total preserved");
    check(a.runtime - b.runtime <= GRAN && b.runtime - a.runtime <= GRAN,
          "equal weights: shares within one granularity");
    check(monotone(hist, hist_len), "min_vruntime is monotone");

    /* 场景 2:权重比即份额比(1024 : 512 ~= 2 : 1) */
    task heavy = {"heavy", 1024, 0, 0}, light = {"light", 512, 0, 0};
    task *ts2[2] = {&heavy, &light};
    int hl = 0;
    run_and_measure(ts2, 2, TOTAL_MS, hist, &hl);
    long long diff = heavy.runtime - 2 * light.runtime;
    if (diff < 0)
        diff = -diff;
    check(heavy.runtime + light.runtime == TOTAL_MS, "ratio: total preserved");
    check(diff <= 2 * GRAN, "weight ratio 2:1 is the CPU share");
    diff = heavy.vruntime - light.vruntime;
    if (diff < 0)
        diff = -diff;
    check(diff <= 2 * (2 * NICE_0 * GRAN / 512), "vruntime stays balanced");

    /* 场景 3:迟到者以 min_vruntime 放置后立即公平 */
    task early = {"E", 1024, 0, 0}, late = {"L", 1024, 0, 0};
    task *ts3[2] = {&early, &late};
    task *solo[1] = {&early};
    int e0 = 0;
    run_and_measure(solo, 1, 500, hist, &e0);           /* E 独跑 500ms */
    long long mvr = early.vruntime;
    if (late.vruntime < mvr)
        late.vruntime = mvr;                             /* clamp 放置 */
    check(late.vruntime == early.vruntime, "late joiner placed at min_vruntime");
    int e1 = 0;
    run_and_measure(ts3, 2, 500, hist, &e1);
    diff = early.runtime - late.runtime - 500;
    if (diff < 0)
        diff = -diff;
    check(diff <= 2 * GRAN, "late joiner then fair share (E = L + 500ms)");

    puts("CFS simulation (C) passed");
    return 0;
}
