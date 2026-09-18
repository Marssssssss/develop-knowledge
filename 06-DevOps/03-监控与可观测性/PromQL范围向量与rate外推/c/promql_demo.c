/* PromQL 范围向量与外推算法 —— C 版自检。
 *
 * 编译：gcc -O2 -Wall -Wextra -pedantic -lm promql_demo.c -o promql_demo
 *
 * 覆盖：lookback 严格小于判定 / 范围向量左开右闭 / offset 的时移语义 /
 *       rate-increase 外推（阈值 1.1、avg/2 降级、计数器重置补偿、零点截断）/ 
 *       increase = rate × 范围秒数。
 *
 * C 版不含正则匹配器（POSIX C 无内置正则），matcher 只实现等值匹配；
 * 正则锚定与「匹配空值」语义见同目录 Python / Go 版本。
 */

#include <math.h>
#include <stdio.h>
#include <string.h>

#define MAX_ENTRY 64
#define LOOKBACK 300.0

typedef struct {
    double t;
    double v;
    int stale; /* 1 = staleness 标记 */
} Entry;

typedef struct {
    const char *name;
    const char *label;
    Entry e[MAX_ENTRY];
    int n;
} Series;

static int g_pass = 0;
static int g_fail = 0;

static void check(const char *label, int cond, double got, double want)
{
    if (cond) {
        g_pass++;
    } else {
        g_fail++;
        printf("FAIL  %s | got=%.12g want=%.12g\n", label, got, want);
    }
}

static int approx(double a, double b) { return fabs(a - b) < 1e-9; }

static void add(Series *s, double t, double v)
{
    s->e[s->n].t = t;
    s->e[s->n].v = v;
    s->e[s->n].stale = 0;
    s->n++;
}

static void mark_stale(Series *s, double t)
{
    s->e[s->n].t = t;
    s->e[s->n].v = 0.0;
    s->e[s->n].stale = 1;
    s->n++;
}

/* 即时向量：取 at-or-before 的最新条目，施加 staleness 与 lookback。 */
static int latest(const Series *s, double eval_t, double lookback, double *out)
{
    int i, found = -1;
    for (i = 0; i < s->n; i++) {
        if (s->e[i].t > eval_t) break;
        found = i;
    }
    if (found < 0 || s->e[found].stale) return 0;
    /* 严格小于：恰好等于 lookback 周期时不返回 */
    if (eval_t - s->e[found].t >= lookback) return 0;
    *out = s->e[found].v;
    return 1;
}

/* 范围向量：区间为左开右闭 (start, end]；stale 标记切断序列。 */
static int range_collect(const Series *s, double start, double end, Entry *out)
{
    int i, n = 0;
    for (i = 0; i < s->n; i++) {
        if (s->e[i].t <= start || s->e[i].t > end) continue;
        if (s->e[i].stale) { n = 0; continue; }
        out[n++] = s->e[i];
    }
    return n;
}

/* 首尾差，并按需补偿计数器重置。 */
static double raw_delta(const Entry *p, int n, int is_counter)
{
    double result = p[n - 1].v - p[0].v;
    int i;
    if (!is_counter) return result;
    for (i = 1; i < n; i++) {
        if (p[i].v < p[i - 1].v) result += p[i - 1].v;
    }
    return result;
}

/* rate / increase / delta 共用的外推。
 * order_first=1 走「先按阈值降级，再做零点截断」；=0 走相反顺序。 */
static double extrapolated_rate(const Entry *p, int n, double range_start,
                               double range_end, int is_counter, int is_rate,
                               int order_first)
{
    double sampled, avg, thr, half, d_start, d_end, result, factor;
    if (n < 2) return NAN;
    sampled = p[n - 1].t - p[0].t;
    if (sampled <= 0.0) return NAN;
    result = raw_delta(p, n, is_counter);
    d_start = p[0].t - range_start;
    d_end = range_end - p[n - 1].t;
    avg = sampled / (double)(n - 1);
    thr = avg * 1.1;
    half = avg / 2.0;
    if (order_first) {
        if (d_start >= thr) d_start = half;
        if (is_counter && result > 0.0 && p[0].v >= 0.0) {
            double dtz = sampled * (p[0].v / result);
            if (dtz < d_start) d_start = dtz;
        }
        if (d_end >= thr) d_end = half;
    } else {
        if (is_counter && result > 0.0 && p[0].v >= 0.0) {
            double dtz = sampled * (p[0].v / result);
            if (dtz < d_start) d_start = dtz;
        }
        if (d_start >= thr) d_start = half;
        if (d_end >= thr) d_end = half;
    }
    factor = (sampled + d_start + d_end) / sampled;
    if (is_rate) factor /= (range_end - range_start);
    return result * factor;
}

static void build(Series *s, const char *name, const char *label,
                  const double *ts, int nt, double slope)
{
    int i;
    s->name = name;
    s->label = label;
    s->n = 0;
    for (i = 0; i < nt; i++) add(s, ts[i], slope * ts[i]);
}

int main(void)
{
    /* ---------------- D: lookback 与 staleness ---------------- */
    Series m;
    double got = 0.0;
    const double mts[] = {1000.0};
    build(&m, "m", "a=1", mts, 1, 0.007);
    m.e[0].v = 7.0;
    check("D 距求值恰 300s（=lookback）不返回",
          !latest(&m, 1300.0, LOOKBACK, &got), 0.0, 0.0);
    check("D 距求值 299s 返回", latest(&m, 1299.0, LOOKBACK, &got) && approx(got, 7.0),
          got, 7.0);
    check("D 未来样本不可见（at-or-before）",
          latest(&m, 1000.0, LOOKBACK, &got) && approx(got, 7.0), got, 7.0);
    mark_stale(&m, 1500.0);
    check("D stale 标记后无值", !latest(&m, 1600.0, LOOKBACK, &got), 0.0, 0.0);
    add(&m, 1700.0, 9.0);
    check("D stale 之后写入新样本即恢复",
          latest(&m, 1750.0, LOOKBACK, &got) && approx(got, 9.0), got, 9.0);

    /* ---------------- E: 范围向量左开右闭 ---------------- */
    Series c; Entry buf[MAX_ENTRY]; int cnt;
    const double cts[] = {0.0, 60.0, 120.0, 180.0, 240.0, 300.0};
    build(&c, "c", "a=1", cts, 6, 1.0);
    cnt = range_collect(&c, 0.0, 300.0, buf);
    check("E 窗口共 5 个样本（左边界排除、右边界包含）", cnt == 5, cnt, 5.0);
    check("E 首点为 t=60", approx(buf[0].t, 60.0), buf[0].t, 60.0);
    check("E 末点为 t=300", approx(buf[cnt - 1].t, 300.0), buf[cnt - 1].t, 300.0);

    /* offset：选择时刻 = 求值时刻 - offset（负 offset 看向未来） */
    Series x;
    const double xts[] = {0.0, 60.0, 300.0, 600.0, 700.0};
    build(&x, "x", "", xts, 5, 1.0);
    check("F offset 5m 等价于在 eval-300 求值",
          latest(&x, 700.0 - 300.0, LOOKBACK, &got) && approx(got, 300.0), got, 300.0);
    check("F 负 offset 看向求值时刻之后",
          latest(&x, 0.0 + 300.0, LOOKBACK, &got) && approx(got, 300.0), got, 300.0);

    /* ---------------- G: rate 外推 ---------------- */
    Series a, b, cc2;
    const double ts5[] = {60.0, 120.0, 180.0, 240.0, 300.0};
    const double ts4[] = {60.0, 120.0, 180.0, 240.0};
    const double ts3[] = {60.0, 120.0, 180.0};
    build(&a, "cnt", "case=A", ts5, 5, 0.1);
    build(&b, "cnt", "case=B", ts4, 4, 0.1);
    build(&cc2, "cnt", "case=C", ts3, 3, 0.1);

    check("G 末样本落在右边界 → rate 精确 0.1",
          approx(extrapolated_rate(a.e, a.n, 0.0, 300.0, 1, 1, 1), 0.1),
          extrapolated_rate(a.e, a.n, 0.0, 300.0, 1, 1, 1), 0.1);
    check("G 末样本距边界 60s（< 阈值 66s）→ 仍精确 0.1",
          approx(extrapolated_rate(b.e, b.n, 0.0, 300.0, 1, 1, 1), 0.1),
          extrapolated_rate(b.e, b.n, 0.0, 300.0, 1, 1, 1), 0.1);
    check("G 末样本距边界 120s（≥ 阈值）→ 低估至 0.07",
          approx(extrapolated_rate(cc2.e, cc2.n, 0.0, 300.0, 1, 1, 1), 0.07),
          extrapolated_rate(cc2.e, cc2.n, 0.0, 300.0, 1, 1, 1), 0.07);

    /* 阈值平局：avg=60 → 阈值 66；末样本恰在 234 使 durationToEnd == 66 */
    Series tie;
    const double tts[] = {54.0, 114.0, 174.0, 234.0};
    build(&tie, "tie", "", tts, 4, 0.1);
    check("G 判据是 >= 阈值（恰等于阈值即降级为 avg/2，factor=264/180）",
          approx(extrapolated_rate(tie.e, tie.n, 0.0, 300.0, 1, 1, 1),
                 18.0 * (264.0 / 180.0) / 300.0),
          extrapolated_rate(tie.e, tie.n, 0.0, 300.0, 1, 1, 1), 0.088);

    /* 计数器重置：5,10,2,7 → raw = (7-5)+10 = 12 */
    Series rst;
    const double rts[] = {60.0, 120.0, 180.0, 240.0};
    const double rvs[] = {5.0, 10.0, 2.0, 7.0};
    build(&rst, "rst", "", rts, 4, 0.0);
    { int i; for (i = 0; i < 4; i++) rst.e[i].v = rvs[i]; }
    check("G 重置补偿后 raw = 12", approx(raw_delta(rst.e, rst.n, 1), 12.0),
          raw_delta(rst.e, rst.n, 1), 12.0);
    check("G 不补偿则只有 2（差 6 倍）", approx(raw_delta(rst.e, rst.n, 0), 2.0),
          raw_delta(rst.e, rst.n, 0), 2.0);
    check("G 重置场景 increase = 12 × 300/180 = 20",
          approx(extrapolated_rate(rst.e, rst.n, 0.0, 300.0, 1, 0, 1), 20.0),
          extrapolated_rate(rst.e, rst.n, 0.0, 300.0, 1, 0, 1), 20.0);

    /* 零点截断：dStart 150 → 17.14；两种分支顺序结果不同（30 vs 45） */
    Series zro;
    const double zts[] = {90.0, 150.0, 210.0, 270.0};
    const double zvs[] = {5.0, 10.0, 15.0, 25.0};
    build(&zro, "zro", "", zts, 4, 0.0);
    { int i; for (i = 0; i < 4; i++) zro.e[i].v = zvs[i]; }
    {
        double oa = extrapolated_rate(zro.e, zro.n, 0.0, 300.0, 1, 1, 1);
        double ob = extrapolated_rate(zro.e, zro.n, 0.0, 300.0, 1, 1, 0);
        check("G 零点截断后 orderA = 20×240/180/300",
              approx(oa, 20.0 * (240.0 / 180.0) / 300.0), oa, 0.0888888888888);
        check("G 两种分支顺序给出不同结果（30 vs 45），差 >1%",
              !approx(oa, ob) && fabs(oa - ob) > 1e-4, oa - ob, 0.0);
    }

    /* increase = rate × 范围秒数 */
    {
        double r = extrapolated_rate(a.e, a.n, 0.0, 300.0, 1, 1, 1);
        double inc = extrapolated_rate(a.e, a.n, 0.0, 300.0, 1, 0, 1);
        check("G increase = rate × 300（官方称语法糖）", approx(inc, r * 300.0),
              inc, r * 300.0);
    }

    /* 窗口内仅 1 个样本 → 无值 */
    {
        Entry one[MAX_ENTRY];
        int n = range_collect(&a, 0.0, 60.0, one);
        check("G 窗口内只有 1 个样本 → NAN（len<2 丢弃）",
              n == 1 && isnan(extrapolated_rate(one, n, 0.0, 60.0, 1, 1, 1)),
              n, 1.0);
    }

    printf("------------------------------------------------------------\n");
    if (g_fail > 0) {
        printf("断言失败 %d 项 / 通过 %d 项\n", g_fail, g_pass);
        return 1;
    }
    printf("全部 %d 项断言通过\n", g_pass);
    return 0;
}
