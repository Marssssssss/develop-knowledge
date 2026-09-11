/* 直方分位数估算 histogram_quantile —— demo (C99, no deps)
 * 依据 https://prometheus.io/docs/querying/functions/ 与
 *     https://prometheus.io/docs/practices/histograms
 * 编译: gcc -O2 -Wall -Wextra -o quantile_demo quantile_demo.c */
#include <math.h>
#include <stdio.h>
#include <string.h>

#define MAX_BK 12

/* 官方单调修复: 先忽略相对差 < 1e-12 的微小下降, 再把非单调桶抬升为前桶值 */
static void repair_monotonic(double *counts, int n)
{
    for (int i = 1; i < n; i++) {
        double prev = counts[i - 1], cur = counts[i];
        if (cur < prev)
            counts[i] = prev; /* 浮点噪声(<1e-12)与真实非单调都抬升 */
    }
}

/* Prometheus 语义: 累积桶 + 桶内线性插值 + 官方边界规则 */
static double histogram_quantile(double phi, const double *les,
                                 const double *counts_in, int n)
{
    if (isnan(phi))
        return NAN;
    if (phi < 0)
        return -INFINITY;
    if (phi > 1)
        return INFINITY;
    if (n < 2 || !isinf(les[n - 1]))
        return NAN; /* 桶数 <2 或缺 +Inf */
    double counts[MAX_BK];
    memcpy(counts, counts_in, sizeof(double) * (size_t)n);
    repair_monotonic(counts, n);

    double rank = phi * counts[n - 1];
    for (int i = 0; i < n; i++) {
        if (counts[i] < rank)
            continue;                 /* 还没容纳 rank */
        if (isinf(les[i]))
            return les[i - 1];        /* 最高桶 -> 次高桶上界(不外插) */
        double lower, prev;
        if (i == 0) {
            lower = (les[0] > 0) ? 0.0 : les[0]; /* 最低桶下界假设为 0 */
            prev = 0.0;
        } else {
            lower = les[i - 1];
            prev = counts[i - 1];
        }
        if (counts[i] == prev)
            return lower;             /* 空桶防除零 */
        return lower + (les[i] - lower) * (rank - prev) / (counts[i] - prev);
    }
    return NAN;
}

static int approx(double a, double b)
{
    double d = fabs(a - b);
    return d < 1e-9 * (1.0 + fabs(a) + fabs(b));
}

int main(void)
{
    printf("== demo 1: official error-analysis example (220ms spike) ==\n");
    double les[] = {0.1, 0.2, 0.3, 0.45, INFINITY};
    double counts[] = {0, 0, 100, 100, 100};
    double p95 = histogram_quantile(0.95, les, counts, 5);
    printf("    buckets le={0.1,0.2,0.3,0.45,+Inf}, 100 obs in (0.2,0.3]\n");
    printf("    p95 = %.3fs (est) vs true 0.220s -> err %.0fms\n",
           p95, fabs(p95 - 0.22) * 1000);
    if (!approx(p95, 0.295)) return 1; /* 必须 = 0.2 + 0.1*0.95 */

    printf("\n== demo 2: spike shifts +100ms -> boundary discontinuity ==\n");
    double counts2[] = {0, 0, 0, 100, 100};
    double p95b = histogram_quantile(0.95, les, counts2, 5);
    printf("    p95 = %.3fs (true 0.320s, err %.0fms)\n", p95b, fabs(p95b - 0.32) * 1000);
    if (!approx(p95b, 0.3 + 0.15 * 0.95)) return 1;
    printf("    estimate jumped %.3f -> %.3f though true value only +100ms\n", p95, p95b);

    printf("\n== demo 3: full histogram p50/p90/p95/p99 ==\n");
    double les3[] = {0.05, 0.1, 0.2, 0.5, 1.0, INFINITY};
    double counts3[] = {24054, 33444, 100392, 129389, 133988, 144320};
    int phis[] = {50, 90, 95, 99};
    for (size_t i = 0; i < sizeof phis / sizeof phis[0]; i++)
        printf("    p%02d = %.3fs\n", phis[i],
               histogram_quantile(phis[i] / 100.0, les3, counts3, 6));
    if (!approx(histogram_quantile(0.5, les3, counts3, 6),
                0.1 + 0.1 * (0.5 * 144320 - 33444) / (100392 - 33444)))
        return 1;
    printf("    min-est(phi=0) = %.3fs, max-est(phi=1) = %.3fs\n",
           histogram_quantile(0.0, les3, counts3, 6),
           histogram_quantile(1.0, les3, counts3, 6));

    printf("\n== demo 4: boundary rules ==\n");
    double one[] = {0.1};
    if (!isnan(histogram_quantile(0.5, one, (double[]){10}, 1))) return 1;
    double noinf[] = {0.1, 0.3};
    if (!isnan(histogram_quantile(0.5, noinf, (double[]){5, 10}, 2))) return 1;
    if (!isinf(histogram_quantile(-0.1, les3, counts3, 6))) return 1;
    if (!isinf(histogram_quantile(1.1, les3, counts3, 6))) return 1;
    if (!isnan(histogram_quantile(NAN, les3, counts3, 6))) return 1;
    printf("    <2 buckets / missing +Inf -> NaN; phi out of range -> +/-Inf; NaN phi -> NaN\n");
    double hi = histogram_quantile(0.999999, les3, counts3, 6);
    if (!approx(hi, 1.0)) return 1;
    printf("    quantile in top bucket -> second-highest bound = %.3f\n", hi);

    printf("\n== demo 5: monotonicity repair ==\n");
    double bad[] = {10, 20, 15, 30, 30};
    repair_monotonic(bad, 5);
    double want[] = {10, 20, 20, 30, 30};
    if (memcmp(bad, want, sizeof want) != 0) return 1;
    printf("    {10,20,15,30,30} -> {10,20,20,30,30} (non-monotonic bucket raised)\n");
    double tiny[] = {1.0, 1.0 - 1e-16};
    repair_monotonic(tiny, 2);
    if (tiny[1] != 1.0) return 1;
    printf("    sub-1e-12 relative dip treated as float noise\n");

    printf("\n== demo 6: summary quantiles are NOT aggregatable ==\n");
    double inst_a = 0.100, inst_b = 0.300;
    printf("    avg(p95_a=%.3f, p95_b=%.3f) = %.3fs (statistically meaningless, 'BAD!')\n",
           inst_a, inst_b, (inst_a + inst_b) / 2);
    double les_m[] = {0.1, 0.3, INFINITY};
    double counts_m[] = {1, 2, 2};
    double merged = histogram_quantile(0.95, les_m, counts_m, 3);
    if (!approx(merged, 0.3)) return 1;
    printf("    merged-bucket p95 = %.3fs (correct, 'GOOD' per docs)\n", merged);

    printf("\nALL CHECKS PASSED\n");
    return 0;
}
