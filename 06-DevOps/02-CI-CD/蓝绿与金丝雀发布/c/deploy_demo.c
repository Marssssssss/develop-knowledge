/* deploy_demo.c — 蓝绿与金丝雀发布(四种部署策略模拟 + 分析门控自动回滚)
 *
 * 统一 LCG 随机数(state = 1664525*state + 1013904223 mod 2^32),
 * 三种语言输出可逐行比对:
 *   demo 1 Recreate vs RollingUpdate 停机对比
 *   demo 2 Blue-Green 切流 + 瞬时回滚 / 晋升
 *   demo 3 Canary 步进分权重 + 分析门控(坏版本中止 / 好版本晋升)
 */
#include <stdio.h>
#include <stdint.h>

typedef struct {
    uint32_t state;
} LCG;

static double rnd(LCG *g)                    /* [0,1) 确定性随机 */
{
    g->state = 1664525u * g->state + 1013904223u;
    return (double)g->state / 4294967296.0;
}

/* Recreate:先删旧再启新;切换窗口内的请求全部失败(必然停机) */
static int simulate_recreate(int n, double err_new, uint32_t seed,
                             double gap, int *gap_n)
{
    LCG g = { seed };
    int errors = 0;
    *gap_n = (int)(n * gap);
    for (int i = 0; i < n; i++) {
        if (i < *gap_n)
            errors++;                       /* 停机窗口:没有版本在服务 */
        else if (rnd(&g) < err_new)
            errors++;                       /* 新版本自身错误 */
    }
    return errors;
}

/* RollingUpdate:逐实例替换;每轮 r/replicas 的请求到新版本,无停机 */
static int simulate_rolling(int n, double err_new, uint32_t seed,
                           int replicas)
{
    LCG g = { seed };
    int errors = 0, batch = n / replicas;
    for (int r = 1; r <= replicas; r++) {
        for (int i = 0; i < batch; i++)
            if (rnd(&g) < (double)r / replicas * err_new)
                errors++;
        printf("    round %d: %d/%d new pods\n", r, r, replicas);
    }
    return errors;
}

/* Blue-Green:切流瞬时;analysis_after 个请求后统计错误率决定回切/晋升。
 * 回滚瞬时且零额外错误的根因:旧 ReplicaSet 未缩容,切回只是改 selector。 */
static int simulate_bluegreen(int n, double err_new, uint32_t seed,
                              double threshold, int analysis_after,
                              double *rate, const char **verdict)
{
    LCG g = { seed };
    int errors = 0;
    for (int i = 0; i < analysis_after; i++)   /* 切流后新版本全量服务 */
        if (rnd(&g) < err_new)
            errors++;
    *rate = (double)errors / analysis_after;
    if (*rate > threshold) {
        *verdict = "rolled back (switch activeService to old RS)";
        return errors;
    }
    for (int i = 0; i < n - analysis_after; i++) /* 晋升:剩余全走新版本 */
        if (rnd(&g) < err_new)
            errors++;
    *verdict = "promoted (old RS scaled down)";
    return errors;
}

/* Canary:按步分权重;每步统计新版本错误率,超阈值即中止回滚 */
static int simulate_canary(int n, double err_new, uint32_t seed,
                           const int *steps, int n_steps,
                           double threshold, char *verdict)
{
    LCG g = { seed };
    int errors = 0, batch = n / n_steps;
    for (int s = 0; s < n_steps; s++) {
        int w = steps[s], new_total = 0, new_err = 0;
        for (int i = 0; i < batch; i++) {
            if (rnd(&g) < (double)w / 100.0) { /* 路由:weight% 到新版本 */
                new_total++;
                if (rnd(&g) < err_new) {        /* 新版本自身错误率 */
                    new_err++;
                    errors++;
                }
            }   /* 旧版本(稳定)错误率视为 0 */
        }
        if (new_total > 0
            && (double)new_err / new_total > threshold) {
            snprintf(verdict, 160, "aborted at %d%% (err=%d/%d > %.2f)",
                     w, new_err, new_total, threshold);
            return errors;
        }
    }
    snprintf(verdict, 160, "promoted to 100%%");
    return errors;
}

int main(void)
{
    const int n = 1000;
    int errors, gap_n, steps[3] = { 10, 33, 100 };
    double rate;
    const char *verdict;
    char buf[160];

    printf("== demo 1: Recreate vs RollingUpdate (n=1000, 10%% 切换窗口) ==\n");
    errors = simulate_recreate(n, 0.0, 42, 0.10, &gap_n);
    printf("  Recreate: %d errors (%d 请求落在停机窗口)"
           " — 两版本从不共存,但必然停机\n", errors, gap_n);
    printf("  Rolling:  0 errors — 无停机,但版本共存:\n");
    simulate_rolling(n, 0.0, 42, 5);

    printf("\n== demo 2: Blue-Green (n=1000, analysis after 200 req) ==\n");
    errors = simulate_bluegreen(n, 0.20, 7, 0.05, 200, &rate, &verdict);
    printf("  坏版本(err=20%%): %d errors, 检测错误率 %.2f -> %s\n",
           errors, rate, verdict);
    printf("    回滚零额外成本:旧 RS 未缩容,切回 selector 即恢复\n");
    errors = simulate_bluegreen(n, 0.0, 7, 0.05, 200, &rate, &verdict);
    printf("  好版本(err=0%%):  %d errors, 检测错误率 %.2f -> %s\n",
           errors, rate, verdict);
    printf("    切换期间 2x 副本成本是蓝绿的代价\n");

    printf("\n== demo 3: Canary steps=[10,33,100] (n=1000, threshold=5%%) ==\n");
    errors = simulate_canary(n, 0.20, 9, steps, 3, 0.05, buf);
    printf("  坏版本(err=20%%): %d errors -> %s\n", errors, buf);
    printf("    爆炸半径被限制在第 1 步的 10%% 流量内\n");
    errors = simulate_canary(n, 0.0, 9, steps, 3, 0.05, buf);
    printf("  好版本(err=0%%):  %d errors -> %s\n", errors, buf);
    printf("    三步分析全过,新版本晋升为 stable\n");
    return 0;
}
