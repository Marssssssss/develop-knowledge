/* prediction.c — 客户端预测 / 服务端和解 / 延迟补偿 最小实现与自检
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic prediction.c -o prediction -lm
 *
 * 与 python/prediction.py 一一对应(机制与数值取自 Valve Developer Community
 * 《Source Multiplayer Networking》, 见 README):
 *   一、客户端预测 + 服务端和解(重放式 vs 吸附式, 后者症状是「输入丢失」);
 *   二、平滑(cl_smoothtime / cl_smooth 对单帧修正幅度与残余的影响);
 *   三、实体插值(cl_interp 100 ms 造成的恒定滞后);
 *   四、延迟补偿(把其他玩家回退到命令执行时刻:
 *       Command Execution Time = Now - RTT - Client View Interpolation)。
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#define TICK_MS     15.0          /* tickrate ~ 66.67/s */
#define CMD_EVERY   2             /* 每 2 tick 一个用户命令(30 ms) */
#define SNAP_EVERY  3             /* 每 3 tick 一个快照(45 ms) */
#define ONE_WAY     3             /* 单向延迟(3 tick = 45 ms) */
#define CMD_DT_MS   (TICK_MS * CMD_EVERY)
#define SPEED       250.0
#define BLOCKER_X   100.0
#define BLOCKER_TICK 26
#define SMOOTH_MS   100.0
#define SMOOTH_TAU  (SMOOTH_MS / 5.0)
#define INTERP_MS   100.0
#define HIST_CAP    128

static int failures;

static void check(int cond, const char *what)
{
    if (!cond) { printf("  [FAIL] %s\n", what); failures++; }
}

static double advance(double x, double dt_ms, int have_blocker, double blocker)
{
    double nx = x + SPEED * dt_ms / 1000.0;
    if (have_blocker && nx > blocker) return blocker;
    return nx;
}

/* ---------------- 一、预测 + 和解 ---------------- */
typedef struct {
    int    mode;                  /* 0 = replay(重放) 1 = snap(吸附) */
    double x;
    int    have_blocker;
    double blocker;
    int    seqs[HIST_CAP];
    double dts[HIST_CAP];
    int    hn;
    int    seq;
    double max_err;
    int    lost_inputs;
} Client;

static void client_init(Client *c, int mode)
{
    memset(c, 0, sizeof(*c));
    c->mode = mode;
}

static void client_issue(Client *c)
{
    c->seq++;
    if (c->hn < HIST_CAP) {
        c->seqs[c->hn] = c->seq;
        c->dts[c->hn] = CMD_DT_MS;
        c->hn++;
    }
    c->x = advance(c->x, CMD_DT_MS, c->have_blocker, c->blocker);  /* 立刻预测 */
}

static double client_reconcile(Client *c, double server_x, int acked,
                               int have_blocker, double blocker)
{
    double predicted = c->x, err;
    int pending_seqs[HIST_CAP];
    double pending_dts[HIST_CAP];
    int i, n = 0;

    if (have_blocker) { c->have_blocker = 1; c->blocker = blocker; }

    if (c->mode == 1) {
        c->lost_inputs = c->hn;          /* 未确认输入全部丢弃 */
        c->hn = 0;
        c->x = server_x;
    } else {
        for (i = 0; i < c->hn; i++)      /* 保留服务器尚未处理的输入 */
            if (c->seqs[i] > acked) {
                pending_seqs[n] = c->seqs[i];
                pending_dts[n] = c->dts[i];
                n++;
            }
        memcpy(c->seqs, pending_seqs, sizeof(int) * (size_t)n);
        memcpy(c->dts, pending_dts, sizeof(double) * (size_t)n);
        c->hn = n;
        c->x = server_x;                 /* 回到权威位置 */
        for (i = 0; i < n; i++)          /* 再按序重放 */
            c->x = advance(c->x, c->dts[i], c->have_blocker, c->blocker);
    }
    err = predicted - c->x;
    if (fabs(err) > c->max_err) c->max_err = fabs(err);
    return err;
}

typedef struct {
    double max_err, err_after_recon, client_x, server_x, gap;
    int    lost_inputs;
} PredResult;

static PredResult run_prediction(int mode, int ticks, int has_blocker,
                                 int blocker_tick)
{
    Client cli;
    double srv = 0.0, err_after = 0.0;
    int srv_arrive[256], srv_seq[256], sn = 0;
    int snap_arrive[256], snap_acked[256], sn2 = 0;
    double snap_x[256], snap_blocker[256];
    int snap_has[256];
    int ack = 0, tick, i;
    PredResult r;

    client_init(&cli, mode);
    for (tick = 0; tick < ticks; tick++) {
        int active = has_blocker && tick >= blocker_tick;
        if (tick % CMD_EVERY == 0) {
            client_issue(&cli);
            srv_arrive[sn] = tick + ONE_WAY;
            srv_seq[sn] = cli.seq;
            sn++;
        }
        for (i = 0; i < sn; i++) {
            if (srv_arrive[i] == tick) {
                srv = advance(srv, CMD_DT_MS, active, BLOCKER_X);
                if (srv_seq[i] > ack) ack = srv_seq[i];
                srv_arrive[i] = srv_arrive[sn - 1];
                srv_seq[i] = srv_seq[sn - 1];
                sn--; i--;
            }
        }
        if (tick % SNAP_EVERY == 0) {
            snap_arrive[sn2] = tick + ONE_WAY;
            snap_x[sn2] = srv;
            snap_acked[sn2] = ack;
            snap_has[sn2] = active;
            snap_blocker[sn2] = BLOCKER_X;
            sn2++;
        }
        for (i = 0; i < sn2; i++) {
            if (snap_arrive[i] == tick) {
                client_reconcile(&cli, snap_x[i], snap_acked[i],
                                 snap_has[i], snap_blocker[i]);
                err_after = cli.x - snap_x[i];
                snap_arrive[i] = snap_arrive[sn2 - 1];
                snap_x[i] = snap_x[sn2 - 1];
                snap_acked[i] = snap_acked[sn2 - 1];
                snap_has[i] = snap_has[sn2 - 1];
                snap_blocker[i] = snap_blocker[sn2 - 1];
                sn2--; i--;
            }
        }
    }
    r.max_err = cli.max_err;
    r.err_after_recon = err_after;
    r.lost_inputs = cli.lost_inputs;
    r.client_x = cli.x;
    r.server_x = srv;
    r.gap = cli.x - srv;
    return r;
}

/* ---------------- 二、平滑 ---------------- */
static void run_smoothing(double err, int smooth, double *max_frame,
                          double *residual, int *frames)
{
    double remaining = err, mf = 0.0;
    int n = (int)(SMOOTH_MS / TICK_MS) + 2, i;
    for (i = 0; i < n; i++) {
        double step = smooth ? remaining * (1.0 - exp(-TICK_MS / SMOOTH_TAU))
                             : remaining;
        if (fabs(step) > mf) mf = fabs(step);
        remaining -= step;
    }
    *max_frame = mf;
    *residual = fabs(remaining);
    *frames = n;
}

/* ---------------- 四、延迟补偿 ---------------- */
typedef struct {
    double rewind_ms, rewind_distance, naive_offset, rewound_offset, radius;
    int    naive_hit, rewound_hit;
} LagComp;

static LagComp run_lag_compensation(double rtt_ms, double interp_ms,
                                    double target_speed, double radius)
{
    LagComp r;
    double y_server_now, y_client_view = 0.0, y_rewound = 0.0;
    r.rewind_ms = rtt_ms + interp_ms;
    r.rewind_distance = target_speed * r.rewind_ms / 1000.0;
    y_server_now = target_speed * r.rewind_ms / 1000.0;     /* 服务器当前时刻 */
    r.naive_offset = y_server_now - y_client_view;
    r.naive_hit = r.naive_offset <= radius;
    r.rewound_offset = y_rewound - y_client_view;
    r.rewound_hit = r.rewound_offset <= radius;
    r.radius = radius;
    return r;
}

int main(void)
{
    PredResult rep, snp, prep, psnp;
    double mf_s, res_s, mf_r, res_r, init;
    int frames_s, frames_r;
    LagComp lc;
    double lag_distance;
    double rtt = 2.0 * ONE_WAY * TICK_MS;

    printf("tick %.0f ms / 命令每 %d tick / 快照每 %d tick / 单向 %d tick -> RTT %.0f ms\n",
           TICK_MS, CMD_EVERY, SNAP_EVERY, ONE_WAY, rtt);
    printf("速度 %.0f u/s -> 每个用户命令位移 %.1f u; 阻挡者在 x=%.0f\n\n",
           SPEED, SPEED * CMD_DT_MS / 1000.0, BLOCKER_X);

    printf("== 一、客户端预测 + 服务端和解 ==\n");
    rep = run_prediction(0, 40, 1, BLOCKER_TICK);
    snp = run_prediction(1, 40, 1, BLOCKER_TICK);
    prep = run_prediction(0, 40, 0, BLOCKER_TICK);
    psnp = run_prediction(1, 40, 0, BLOCKER_TICK);
    printf("  [有阻挡] 重放式: 最大预测误差 %.2f u, 和解后偏差 %+.2f u, "
           "丢弃输入 %d 条, 末态 client=%.2f / server=%.2f\n",
           rep.max_err, rep.err_after_recon, rep.lost_inputs,
           rep.client_x, rep.server_x);
    printf("  [有阻挡] 吸附式: 最大预测误差 %.2f u, 和解后偏差 %+.2f u, "
           "丢弃输入 %d 条, 末态 client=%.2f / server=%.2f\n",
           snp.max_err, snp.err_after_recon, snp.lost_inputs,
           snp.client_x, snp.server_x);
    printf("  [无阻挡] 重放式 client=%.2f (领先 %+.2f u = 尚在路上的输入)\n",
           prep.client_x, prep.gap);
    printf("  [无阻挡] 吸附式 client=%.2f (落后 %+.2f u = 被吞掉的输入)\n",
           psnp.client_x, psnp.gap);
    check(rep.max_err > 0.0 && snp.max_err > 0.0, "两条路径都应出现预测误差");
    check(fabs(rep.err_after_recon) < 1e-9 && fabs(snp.err_after_recon) < 1e-9,
          "和解后应与权威快照一致");
    check(snp.lost_inputs > 0, "吸附式必然吞掉未确认输入");
    check(prep.gap > psnp.gap, "重放式应保留乐观领先, 吸附式掉队");

    printf("\n== 二、平滑(cl_smoothtime = cl_smooth = 100 ms) ==\n");
    init = rep.max_err;
    run_smoothing(init, 1, &mf_s, &res_s, &frames_s);
    run_smoothing(init, 0, &mf_r, &res_r, &frames_r);
    printf("  初始视觉误差 %.2f u; 不开启平滑: 单帧修正 %.2f u = 误差的 %.0f%%\n",
           init, mf_r, 100.0 * mf_r / init);
    printf("  开启平滑(tau=%.0f ms): 单帧最大修正 %.2f u = 误差的 %.0f%%, "
           "%d 帧摊完, 残余 %.4f u = %.2f%%\n",
           SMOOTH_TAU, mf_s, 100.0 * mf_s / init, frames_s, res_s,
           100.0 * res_s / init);
    check(mf_r >= init - 1e-9, "不平滑应一帧吃掉全部误差");
    check(mf_s <= 0.60 * init, "平滑应显著降低单帧修正幅度");
    check(res_s <= 0.01 * init, "平滑后残余应小于 1%");

    printf("\n== 三、实体插值(cl_interp 100 ms) ==\n");
    lag_distance = 300.0 * INTERP_MS / 1000.0;
    printf("  远端 300 u/s, 快照间隔 %.0f ms; 渲染滞后 %.0f ms -> 位置差 %.1f u "
           "(缓冲里约 %.1f 个快照)\n",
           SNAP_EVERY * TICK_MS, INTERP_MS, lag_distance,
           INTERP_MS / (SNAP_EVERY * TICK_MS));
    check(fabs(lag_distance - 30.0) < 1e-9, "插值滞后距离应为 30 u");

    printf("\n== 四、延迟补偿(回退其他玩家) ==\n");
    lc = run_lag_compensation(rtt, INTERP_MS, 300.0, 20.0);
    printf("  历史缓冲 = 最近 1 s; 命令执行时刻 = 现在 - RTT - 插值 = %.0f ms "
           "-> 目标回退 %.1f u\n", lc.rewind_ms, lc.rewind_distance);
    printf("  不回退: 目标偏移 %.1f u, 命中半径 %.0f u -> %s\n",
           lc.naive_offset, lc.radius, lc.naive_hit ? "命中" : "脱靶");
    printf("  回退后: 目标偏移 %.1f u -> %s\n",
           lc.rewound_offset, lc.rewound_hit ? "命中" : "脱靶");
    check(!lc.naive_hit && lc.rewound_hit, "回退才应判中");

    printf("\n%s (failures=%d)\n", failures ? "存在失败项" : "全部自检通过。", failures);
    return failures ? 1 : 0;
}
