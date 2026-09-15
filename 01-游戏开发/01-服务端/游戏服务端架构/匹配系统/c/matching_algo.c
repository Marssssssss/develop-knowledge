/* matching_algo.c — 匹配算法实现: 排队-配对-对战-更新 + 队伍平衡 */
#include "matching_algo.h"

#include <math.h>
#include <string.h>

/* =====================================================================
 * 一、匹配循环
 * 每个 tick 放 2 名冷却结束的玩家入队(制造队列积压); 匹配器尽量成对。
 * window = base + rate * wait: 等待越久窗口越宽, 防止高/低分玩家饿死。
 * by_quality=1 在窗口内挑匹配质量最大的对手; =0 走 FIFO 先到先配。
 * ===================================================================== */
SimResult simulate(int n_players, int ticks, double base, double rate,
                   int by_quality, unsigned int seed, int cooldown)
{
    MPlayer ps[MAX_PLAYERS];
    int queue[MAX_QUEUE];
    double tg[MAX_QUEUE];
    unsigned char inq[MAX_PLAYERS], matched[MAX_PLAYERS];
    SimResult out;
    Rng rng;
    int i, j, t, qn = 0, nm = 0, half;
    double swait = 0.0, sgap = 0.0, stg = 0.0, sq = 0.0, e = 0.0, la = 0.0, ss = 0.0;

    rng_init(&rng, seed);
    memset(inq, 0, sizeof(inq));
    memset(matched, 0, sizeof(matched));
    for (i = 0; i < n_players; i++) {
        ps[i].pid = i;
        ps[i].arrive = 0;
        ps[i].skill = MU0 + 6.0 * rng_normal(&rng);
        ps[i].r.mu = MU0;
        ps[i].r.sigma = SIGMA0;
        ps[i].idle_after = 0;
    }

    for (t = 0; t < ticks; t++) {
        for (i = 0; i < 2; i++) {                 /* 到达: 2 名/tick */
            int tries, pid = -1;
            for (tries = 0; tries < 8; tries++) {
                pid = (int)(rng_u32(&rng) % (unsigned)n_players);
                if (ps[pid].idle_after <= t && !inq[pid]) break;
                pid = -1;
            }
            if (pid >= 0) {
                ps[pid].arrive = t;
                inq[pid] = 1;
                if (qn < MAX_QUEUE) queue[qn++] = pid;
            }
        }

        for (i = 0; i < qn; i++) {                /* 配对: 按入队顺序 */
            int p = queue[i], best = -1;
            double bestq = 0.0, wait;
            if (matched[p]) continue;
            wait = (double)(t - ps[p].arrive);
            if (by_quality) {
                double window = base + rate * wait;
                for (j = 0; j < qn; j++) {
                    int q = queue[j];
                    double g, qq;
                    if (q == p || matched[q]) continue;
                    g = fabs(ts_display(ps[q].r.mu, ps[q].r.sigma) -
                             ts_display(ps[p].r.mu, ps[p].r.sigma));
                    if (g > window) continue;     /* 出窗口: 宁可继续等 */
                    qq = match_quality(ps[p].r, ps[q].r);
                    if (best < 0 || qq > bestq) { best = q; bestq = qq; }
                }
            } else {
                for (j = 0; j < qn; j++) {
                    int q = queue[j];
                    if (q != p && !matched[q]) { best = q; break; }
                }
            }
            if (best < 0) continue;

            matched[p] = 1;
            matched[best] = 1;
            swait += wait + (double)(t - ps[best].arrive);
            sgap += fabs(ps[p].r.mu - ps[best].r.mu);
            stg += fabs(ps[p].skill - ps[best].skill);
            if (nm < MAX_QUEUE) tg[nm] = fabs(ps[p].skill - ps[best].skill);
            sq += match_quality(ps[p].r, ps[best].r);
            nm++;

            {   /* 对战: 隐藏技能决定结果 */
                double pa = norm_cdf((ps[p].skill - ps[best].skill) / sqrt(2.0 * BETA * BETA));
                int w = (rng_u(&rng) < pa) ? p : best;
                int l = (w == p) ? best : p;
                Rating nw, nl;
                ts_1v1(ps[w].r, ps[l].r, &nw, &nl);
                ps[w].r = nw;
                ps[l].r = nl;
                ps[p].idle_after = t + cooldown;
                ps[best].idle_after = t + cooldown;
            }
        }

        {   /* 出队 + 重置标记 */
            int keep = 0;
            for (i = 0; i < qn; i++)
                if (!matched[queue[i]]) queue[keep++] = queue[i];
            qn = keep;
            for (i = 0; i < n_players; i++) { matched[i] = 0; inq[i] = 0; }
            for (i = 0; i < qn; i++) inq[queue[i]] = 1;
        }
    }

    half = nm / 2;
    for (i = 0; i < half; i++) e += tg[i];
    for (i = half; i < nm; i++) la += tg[i];
    for (i = 0; i < n_players; i++) ss += ps[i].r.sigma;

    out.matches = nm;
    out.wait = nm ? swait / (2.0 * nm) : 0.0;
    out.gap = nm ? sgap / nm : 0.0;
    out.true_gap = nm ? stg / nm : 0.0;
    out.quality = nm ? sq / nm : 0.0;
    out.tg_early = half ? e / half : 0.0;
    out.tg_late = (nm - half) ? la / (nm - half) : 0.0;
    out.left = qn;
    out.sigma = ss / n_players;
    return out;
}

/* =====================================================================
 * 二、队伍平衡: 把 2n 人分成两队, 使两队总分差最小(划分问题, NP-hard)
 * ===================================================================== */
static int cmp_desc(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;
    return (x < y) - (x > y);
}

static double vsum(const double *v, int n)
{
    double s = 0.0;
    int i;
    for (i = 0; i < n; i++) s += v[i];
    return s;
}

void team_split(const double *ratings, int n,
                double *greedy_diff, double *ls_diff, double *exact_diff)
{
    double s[MAX_TEAM], a[MAX_TEAM], b[MAX_TEAM];
    int na = 0, nb = 0, i, j, improved;

    memcpy(s, ratings, sizeof(double) * (size_t)n);
    qsort(s, (size_t)n, sizeof(double), cmp_desc);
    for (i = 0; i < n; i++) {                     /* 贪心: 丢给总分较低的队 */
        if (vsum(a, na) <= vsum(b, nb)) a[na++] = s[i];
        else                            b[nb++] = s[i];
    }
    *greedy_diff = fabs(vsum(a, na) - vsum(b, nb));

    improved = 1;                                 /* 局部搜索: 交换改进即可 */
    while (improved) {
        improved = 0;
        for (i = 0; i < na; i++) {
            for (j = 0; j < nb; j++) {
                double cur = fabs(vsum(a, na) - vsum(b, nb)), nw, tmp;
                tmp = a[i]; a[i] = b[j]; b[j] = tmp;
                nw = fabs(vsum(a, na) - vsum(b, nb));
                if (nw < cur) {
                    improved = 1;
                } else {
                    tmp = a[i]; a[i] = b[j]; b[j] = tmp;
                }
            }
        }
    }
    *ls_diff = fabs(vsum(a, na) - vsum(b, nb));

    {   /* 精确最优: 2^n 枚举(仅小规模参照) */
        double best = -1.0;
        int mask, total = 1 << n;
        for (mask = 0; mask < total; mask++) {
            double sa = 0.0, sb = 0.0;
            int cnt = 0;
            for (i = 0; i < n; i++) {
                if ((mask >> i) & 1) { cnt++; sa += ratings[i]; }
                else                 { sb += ratings[i]; }
            }
            if (cnt != n / 2) continue;
            if (best < 0.0 || fabs(sa - sb) < best) best = fabs(sa - sb);
        }
        *exact_diff = best < 0.0 ? 0.0 : best;
    }
}
