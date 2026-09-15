/* matchmaking.c — 匹配系统自检主程序(与 python/matchmaking.py 输出一致)
 *
 * 编译: gcc -std=c99 -O2 -Wall -Wextra -pedantic rating.c matching_algo.c matchmaking.c -o matchmaking -lm
 *
 * 五段自检:
 *   一、Elo 的 400 分标尺与 K 因子;
 *   二、TrueSkill 的 (mu, sigma)、保守估计与收敛场次;
 *   三、匹配质量: 为什么"展示分差"不等于"匹配好坏";
 *   四、匹配算法: 窗口扩张(等待 vs 公平);
 *   五、队伍平衡: 贪心 vs 局部搜索 vs 精确最优。
 */
#include "matching_algo.h"

#include <math.h>
#include <stdio.h>

static int failures;

static void check(int cond, const char *what)
{
    if (!cond) { printf("  [FAIL] %s\n", what); failures++; }
}

static void sec_elo(void)
{
    double w, l, w2, l2, e400 = elo_expect(1900, 1500), eneg = elo_expect(1100, 1500);
    double e_eq = elo_expect(1500, 1500);

    printf("== 一、Elo: 400 分标尺 + K 因子 ==\n");
    printf("  同分 -> 期望 %.3f; 高 400 分 -> %.3f; 低 400 分 -> %.3f\n", e_eq, e400, eneg);
    check(fabs(e_eq - 0.5) < 1e-12, "同分期望应为 0.5");
    check(fabs(e400 - 0.909) < 0.001 && fabs(eneg - 0.091) < 0.001, "400 分差应约 91%/9%");

    elo_update(1500, 1500, 1.0, &w, &l);
    printf("  K=%.0f, 1500 vs 1500 胜: 1500 -> %.0f; 负方 -> %.0f (-16)\n", ELO_K, w, l);
    check(fabs(w - 1516.0) < 1e-9 && fabs(l - 1484.0) < 1e-9, "同分对局应各变 16 分");

    elo_update(1500, 1900, 1.0, &w2, &l2);
    printf("  弱胜强(1500 赢 1900): +%.2f / %.2f —— 期望仅 %.3f, 故单局收益很大\n",
           w2 - 1500, l2 - 1900, elo_expect(1500, 1900));
    check(w2 - 1500 > 25.0 && l2 - 1900 < -25.0, "爆冷的单局变化应远大于 16");
    printf("  -> 400 分差只是'约 91%% 胜率', 不是必胜; 强方赢球几乎不加分, 输球重罚  OK\n");
}

static void sec_trueskill(void)
{
    Rating n = {MU0, SIGMA0}, w, l;
    Rating a = {MU0, SIGMA0}, b = {MU0, SIGMA0};
    Rating nw, nl, cur = {MU0, SIGMA0}, opp = {MU0, SIGMA0};
    int i;

    printf("\n== 二、TrueSkill: (mu, sigma) 与保守估计 ==\n");
    printf("  新玩家 mu=%.0f, sigma=%.3f -> 展示分 %.1f; 区间 [mu-3sigma, mu+3sigma] = [%.0f, %.0f]\n",
           MU0, SIGMA0, ts_display(MU0, SIGMA0), ts_display(MU0, SIGMA0), MU0 + 3 * SIGMA0);
    check(fabs(ts_display(MU0, SIGMA0)) < 1e-9, "新玩家展示分应为 0");

    ts_1v1(a, b, &w, &l);
    printf("  新手 vs 新手(一方赢): 胜者 mu %.2f->%.2f, sigma %.2f->%.2f, 展示分 0.0 -> %.2f\n",
           MU0, w.mu, SIGMA0, w.sigma, ts_display(w.mu, w.sigma));
    printf("                        负者 mu %.2f->%.2f, sigma %.2f->%.2f, 展示分 0.0 -> %.2f\n",
           MU0, l.mu, SIGMA0, l.sigma, ts_display(l.mu, l.sigma));
    check(w.mu > MU0 && w.sigma < SIGMA0 && l.mu < MU0 && l.sigma < SIGMA0, "胜者升负者降且 sigma 都下降");
    printf("  两者展示分之和 = %.2f != 0 —— TrueSkill **不是零和**(不确定性下降本身就是收益)\n",
           ts_display(w.mu, w.sigma) + ts_display(l.mu, l.sigma));
    check(fabs(ts_display(w.mu, w.sigma) + ts_display(l.mu, l.sigma)) > 1e-9,
          "展示分之和不应为 0");

    {   /* 新手爆冷击败老手: sigma 大的一方动得多 */
        Rating old = {30.0, 2.0};
        ts_1v1(n, old, &nw, &nl);
        printf("  新手爆冷击败老手(mu=30, sigma=2.0): 新手 mu %.2f->%.2f (+%.2f), sigma %.2f->%.2f\n",
               MU0, nw.mu, nw.mu - MU0, SIGMA0, nw.sigma);
        printf("                                   老手 mu 30.00->%.2f (%+.2f), sigma 2.00->%.2f\n",
               nl.mu, nl.mu - 30.0, nl.sigma);
        check(nw.mu - MU0 > 30.0 - nl.mu, "sigma 大的一方变化应更大");
        printf("  -> 更新权重 ~ sigma^2/(2beta^2+sigma_w^2+sigma_l^2): 同样的胜负,"
               " 新手动 %.2f 分, 老手只动 %.2f 分  OK\n", nw.mu - MU0, 30.0 - nl.mu);
    }

    for (i = 0; i < 12; i++) {
        ts_1v1(cur, opp, &nw, &nl);
        cur = nw;
    }
    printf("  连续胜 12 局(对手始终是新手): mu %.2f->%.2f, sigma %.3f->%.3f (展示分 %.2f)\n",
           MU0, cur.mu, SIGMA0, cur.sigma, ts_display(cur.mu, cur.sigma));
    check(cur.sigma < SIGMA0 * 0.75, "12 局后 sigma 应明显收缩");
    printf("  注: sigma 每次赛前 +tau=%.4f 的'动量', 因此**永不归零**(技能会随时间变化)  OK\n", TAU);
    printf("  对照 MSR 页面给出的收敛场次: 2 人 12 局、2v2*2 队 10 局、4v4 46 局"
           " —— 人越多/队越大, 单次结果信息量越少\n");
}

static void sec_quality(void)
{
    Rating n = {MU0, SIGMA0}, vet = {31.0, 2.0}, bad = {2.5, 0.5};
    double q[4], gap[4];
    const char *names[4] = {
        "新手(展示 0) vs 老手(展示 25, sigma=2)",
        "新手(展示 0) vs 差手(展示 1, sigma=0.5)",
        "两个新手(展示 0) vs (0)",
        "两个老手(展示 25) vs (25), sigma=2"};
    Rating pa[4], pb[4];
    int i;

    pa[0] = n;   pb[0] = vet;
    pa[1] = n;   pb[1] = bad;
    pa[2] = n;   pb[2] = n;
    pa[3] = vet; pb[3] = vet;

    printf("\n== 三、匹配质量: 展示分差 != 匹配好坏 ==\n");
    for (i = 0; i < 4; i++) {
        q[i] = match_quality(pa[i], pb[i]);
        gap[i] = fabs(ts_display(pa[i].mu, pa[i].sigma) - ts_display(pb[i].mu, pb[i].sigma));
        printf("  %-40s 展示分差 %5.1f -> 质量 %.3f\n", names[i], gap[i], q[i]);
    }
    check(q[0] > q[1], "展示分差大的匹配质量反而应更高(不确定性主导)");
    printf("  -> 反直觉但正确: 展示分差大(25 级)的一方质量 %.3f, 反而高于展示分差仅 1 级的 %.3f\n",
           q[0], q[1]);
    printf("     原因: 差手已高度确定(sigma=0.5, 真实 mu 只有 2.5), 新手对他毫无可学之物  OK\n");
    check(q[3] > q[2], "mu 相同时 sigma 越小质量越高");
    printf("  -> mu 完全相同(mu=25)时: 两个新手质量仅 %.3f, 两个老手 %.3f; sigma 大 => 质量明显小于 1  OK\n",
           q[2], q[3]);
    printf("  -> MSR 页面例子同此规律: 新手 vs 老手 展示分差 23 级仍有 57.6%% 质量,\n");
    printf("     而新手 vs '摆烂老手' 展示分差仅 1 级、质量只有 5.7%% (不确定性主导) \n");
}

static void sec_match(void)
{
    static const char *labels[3] = {"FIFO(先到先配)", "窗口 8+0.5*t 最大质量",
                                    "窗口 3+0.2*t 最大质量"};
    static const int    byq[3]   = {0, 1, 1};
    static const double base[3]  = {0.0, 8.0, 3.0};
    static const double rate[3]  = {0.0, 0.5, 0.2};
    SimResult r[3];
    int i;

    printf("\n== 四、匹配算法: 窗口扩张(等待 vs 公平) ==\n");
    printf("  %-24s%6s%10s%11s%11s%10s%6s%9s\n", "策略", "对局", "平均等待",
           "|d展示分|", "|d真技能|", "平均质量", "余留", "均sigma");
    for (i = 0; i < 3; i++) {
        r[i] = simulate(120, 1500, base[i], rate[i], byq[i], 12345u, 5);
        printf("  %-24s%6d%10.2f%11.2f%11.2f%10.3f%6d%9.3f\n", labels[i], r[i].matches,
               r[i].wait, r[i].gap, r[i].true_gap, r[i].quality, r[i].left, r[i].sigma);
    }
    check(r[2].true_gap < r[1].true_gap && r[1].true_gap < r[0].true_gap,
          "窗口越紧真实技能差应越小");
    check(r[2].wait > r[0].wait && r[2].quality > r[0].quality, "越公平代价是等待更长");
    printf("  -> 窗口越紧: 真实技能差 %.2f -> %.2f -> %.2f(越公平), 代价是等待 %.2f -> %.2f tick\n",
           r[0].true_gap, r[1].true_gap, r[2].true_gap, r[0].wait, r[2].wait);
    printf("  -> sigma 从 %.2f 收敛到 %.2f(120 名玩家 / 1500 tick), 估计越准匹配越准:\n",
           SIGMA0, r[2].sigma);
    printf("     窗口 3 策略前半程 |d真技能| %.2f vs 后半程 %.2f  OK\n",
           r[2].tg_early, r[2].tg_late);
    check(r[2].tg_late < r[2].tg_early, "收敛后应比冷启动更准");
    printf("  注: FIFO 的 |d展示分| 与 |d真技能| 都最大 —— 先到先配等价于随机配,"
           " 排名系统再好也白搭\n");
}

static void sec_team(void)
{
    Rng rng;
    double ratings[MAX_TEAM];
    double g, ls, ex;
    int n, i;

    printf("\n== 五、队伍平衡: 贪心 vs 局部搜索 vs 精确最优 ==\n");
    rng_init(&rng, 999u);
    for (n = 10; n <= 14; n += 2) {
        for (i = 0; i < n; i++) {
            double v = 1000.0 + 400.0 * rng_normal(&rng);
            ratings[i] = round(v * 10.0) / 10.0;
        }
        team_split(ratings, n, &g, &ls, &ex);
        printf("  %d 人分两队(评分 1000+-400): 贪心差 %7.1f | 局部搜索差 %7.1f | 精确最优 %7.1f\n",
               n, g, ls, ex);
        check(ex <= ls + 1e-6, "精确最优不应差于局部搜索");
        check(ls <= g + 1e-9, "局部搜索不应差于贪心");
    }
    printf("  -> 贪心(排序后交替分发)偏差最大; 一个交换式局部搜索即可逼近 2^n 枚举的最优  OK\n");
}

int main(void)
{
    sec_elo();
    sec_trueskill();
    sec_quality();
    sec_match();
    sec_team();
    printf("\n%s (failures=%d)\n", failures ? "存在失败项" : "全部自检通过。", failures);
    return failures ? 1 : 0;
}
