/* TCP 拥塞控制模拟：Reno(RFC 5681) vs CUBIC(RFC 9438)
 *
 * 纯窗口动力学模拟，不涉及 socket：按 RTT 轮次推进，每轮发送 cwnd 个 SMSS
 * 段；当 cwnd 超过链路饱和窗口 sat_window 时丢弃 1 段，发送方通过 3 个重复
 * ACK 触发快重传/快恢复。两种算法都保留慢启动，CUBIC 只替换拥塞避免阶段的
 * 窗口增长函数。
 *
 * 编译：gcc -O2 -Wall -Wextra -pedantic main.c -o cubic_demo -lm
 * 运行：./cubic_demo
 */

#include <math.h>
#include <stdio.h>
#include <string.h>

#define RTT 1.0        /* 归一化往返时延（秒），1 个 RTT = 1 个模拟轮 */
#define SMSS 1460      /* 最大段长度（字节） */
#define C_CUBIC 0.4    /* RFC 9438 §5：C SHOULD be set to 0.4 */
#define BETA_CUBIC 0.7 /* RFC 9438 §3.4：乘法递减因子 SHOULD be 0.7 */
/* RFC 9438 §4.3：为达到与 AIMD(1,0.5) 相同平均窗口，α = 3(1-β)/(1+β) */
#define ALPHA_CUBIC (3.0 * (1.0 - BETA_CUBIC) / (1.0 + BETA_CUBIC))
#define IW 10.0 /* 初始窗口（段），放大以便观察图形 */

static int failures = 0;
static int checks = 0;

#define CHECK(cond, msg)                                                       \
    do {                                                                       \
        checks++;                                                              \
        if (!(cond)) {                                                         \
            printf("  [FAIL] %s\n", (msg));                                    \
            failures++;                                                        \
        }                                                                      \
    } while (0)

/* ------------------------------------------------------------ CUBIC 数学 */
/* RFC 9438 §4.2：K = cbrt((W_max - cwnd_epoch)/C)，把窗口涨回 W_max 的时间 */
static double cubic_k(double w_max, double cwnd_epoch) {
    double delta = w_max - cwnd_epoch;
    if (delta < 0.0) delta = 0.0;
    return cbrt(delta / C_CUBIC);
}

/* RFC 9438 §4.2 Figure 1：W_cubic(t) = C*(t-K)^3 + W_max
 * t < K 为凹段（增速递减、逼近平台），t > K 转凸（增速递增、探测新带宽） */
static double w_cubic(double t, double k, double w_max) {
    double d = t - k;
    return C_CUBIC * d * d * d + w_max;
}

/* ------------------------------------------------------------------ Reno */
typedef struct {
    double cwnd, ssthresh, t;
    int losses, rto_events;
} Reno;

static void reno_init(Reno *r) {
    r->cwnd = IW;
    r->ssthresh = INFINITY;
    r->t = 0.0;
    r->losses = r->rto_events = 0;
}

/* 推进 1 个 RTT；loss = 快重传(3 dup ACK)，timeout = RTO */
static void reno_round(Reno *r, int loss, int timeout) {
    r->t += RTT;
    if (timeout) {
        /* RFC 5681 §3.1 公式(4)：ssthresh = max(FlightSize/2, 2*SMSS) */
        r->ssthresh = r->cwnd / 2.0 > 2.0 ? r->cwnd / 2.0 : 2.0;
        r->cwnd = 1.0; /* LW = 1 个满尺寸段，回到慢启动 */
        r->rto_events++;
        r->losses++;
        return;
    }
    if (loss) {
        r->losses++;
        r->ssthresh = r->cwnd / 2.0 > 2.0 ? r->cwnd / 2.0 : 2.0;
        /* §3.2 规则 3/6：膨胀 3 段后再放气，净效果 = 窗口乘性减半 */
        r->cwnd = r->ssthresh;
        return;
    }
    if (r->cwnd < r->ssthresh) {
        r->cwnd *= 2.0; /* 慢启动：一个 RTT 内窗口翻倍 */
    } else {
        r->cwnd += 1.0; /* 拥塞避免：每 RTT +1 段（加性增） */
    }
}

/* ----------------------------------------------------------------- CUBIC */
typedef struct {
    double cwnd, ssthresh, t;
    double w_max, cwnd_prior, cwnd_epoch, t_epoch, w_est, alpha, k;
    int losses, rto_events, entered_ca, fast_convergence;
} Cubic;

static void cubic_init(Cubic *c, int fast_convergence) {
    memset(c, 0, sizeof(*c));
    c->cwnd = IW;
    c->ssthresh = INFINITY;
    c->fast_convergence = fast_convergence;
}

static void cubic_on_loss(Cubic *c, int timeout) {
    c->losses++;
    if (timeout) {
        /* §4.8：cwnd 按 Reno 降到 1 段，但 ssthresh 用 β_cubic；K 置 0，
         * W_max = 本阶段起始 cwnd。 */
        c->cwnd_prior = c->cwnd;
        c->ssthresh = c->cwnd * BETA_CUBIC > 2.0 ? c->cwnd * BETA_CUBIC : 2.0;
        c->cwnd = 1.0;
        c->rto_events++;
        c->w_max = c->cwnd;
        c->cwnd_epoch = c->cwnd;
        c->k = 0.0;
    } else {
        /* §4.7 fast convergence：拥塞时若 cwnd < W_max，说明饱和点在下移，
         * 主动多让出带宽 → W_max 再乘 (1+β)/2。 */
        if (c->fast_convergence && c->w_max > 0.0 && c->cwnd < c->w_max) {
            c->w_max = c->cwnd * (1.0 + BETA_CUBIC) / 2.0;
        } else {
            c->w_max = c->cwnd;
        }
        if (c->entered_ca) c->cwnd_prior = c->cwnd;
        c->cwnd *= BETA_CUBIC; /* §4.6 乘性减（不是 Reno 的 0.5） */
        c->cwnd_epoch = c->cwnd;
        c->ssthresh = c->cwnd > 2.0 ? c->cwnd : 2.0;
        c->k = cubic_k(c->w_max, c->cwnd_epoch);
    }
    c->entered_ca = 1;
    c->t_epoch = c->t;
    c->w_est = c->cwnd; /* §4.3：W_est 初值 = cwnd_epoch */
    c->alpha = ALPHA_CUBIC;
}

static void cubic_round(Cubic *c, int loss, int timeout) {
    c->t += RTT;
    if (loss || timeout) {
        cubic_on_loss(c, timeout);
        return;
    }
    if (c->cwnd < c->ssthresh) { /* 慢启动不变 */
        c->cwnd *= 2.0;
        return;
    }
    {
        double elapsed = c->t - c->t_epoch;
        double target = w_cubic(elapsed, c->k, c->w_max);
        double upper = 1.5 * c->cwnd; /* 上界：增速不超过慢启动 */
        if (target > upper) target = upper;
        if (target > c->cwnd) c->cwnd = target; /* 下界：增速非递减 */
        /* §4.3 Reno-friendly：W_est 线性增长，追上 cwnd_prior 后 α 降为 1 */
        c->w_est += c->alpha;
        if (c->w_est >= c->cwnd_prior) c->alpha = 1.0;
        if (c->w_est > c->cwnd) c->cwnd = c->w_est;
    }
}

/* ------------------------------------------------------------ 模拟驱动 */
/* 跑到第一次丢包，返回丢包前的 cwnd */
static double run_first_loss(void *cc, double sat_window, int is_reno,
                            long *rounds_out) {
    long r = 0;
    double w_before;
    double cwnd = is_reno ? ((Reno *)cc)->cwnd : ((Cubic *)cc)->cwnd;
    while (cwnd <= sat_window && r < 100000) {
        if (is_reno) {
            reno_round((Reno *)cc, 0, 0);
            cwnd = ((Reno *)cc)->cwnd;
        } else {
            cubic_round((Cubic *)cc, 0, 0);
            cwnd = ((Cubic *)cc)->cwnd;
        }
        r++;
    }
    w_before = cwnd;
    if (is_reno) {
        reno_round((Reno *)cc, 1, 0);
    } else {
        cubic_round((Cubic *)cc, 1, 0);
    }
    if (rounds_out) *rounds_out = r;
    return w_before;
}

/* 窗口为 w_max 时丢 1 段，测量爬回 w_max 所需的 RTT 数 */
static int recovery_rtts(double w_max, int is_reno, void *out_cc) {
    int n = 0;
    if (is_reno) {
        Reno *r = (Reno *)out_cc;
        reno_init(r);
        r->cwnd = w_max;
        reno_round(r, 1, 0);
        while (r->cwnd < w_max && n < 100000) {
            reno_round(r, 0, 0);
            n++;
        }
    } else {
        Cubic *c = (Cubic *)out_cc;
        cubic_init(c, 1);
        c->cwnd = w_max;
        cubic_round(c, 1, 0);
        while (c->cwnd < w_max && n < 100000) {
            cubic_round(c, 0, 0);
            n++;
        }
    }
    return n;
}

static double avg_mbps(const double *traj, int n) {
    double s = 0.0;
    int i;
    for (i = 0; i < n; i++) s += traj[i];
    return s / n * SMSS * 8 / RTT / 1e6;
}

/* ----------------------------------------------------------------- 自检 */
static void self_check(void) {
    int i;
    Reno r;
    Cubic c;

    /* 1. K 自洽：W_cubic(K) == W_max */
    {
        double ws[3] = {50.0, 200.0, 1000.0};
        int all_ok = 1;
        for (i = 0; i < 3; i++) {
            double k = cubic_k(ws[i], ws[i] * BETA_CUBIC);
            if (fabs(w_cubic(k, k, ws[i]) - ws[i]) > 1e-9) all_ok = 0;
        }
        CHECK(all_ok, "W_cubic(K) 应正好等于 W_max");
    }

    /* 2. 拥塞事件瞬间 cwnd == W_max * beta_cubic */
    cubic_init(&c, 1);
    run_first_loss(&c, 200.0, 0, NULL);
    CHECK(fabs(c.cwnd - c.w_max * BETA_CUBIC) < 1e-9, "beta_cubic 不是 0.7");
    CHECK(fabs(c.cwnd_epoch - c.cwnd) < 1e-9, "cwnd_epoch 应为减后的 cwnd");

    /* 3. Reno 拥塞避免每 RTT 恰好 +1 段 */
    reno_init(&r);
    r.cwnd = 100.0;
    r.ssthresh = 1.0;
    reno_round(&r, 0, 0);
    CHECK(fabs(r.cwnd - 101.0) < 1e-9, "Reno 拥塞避免增长不等于 +1/RTT");

    /* 4. Reno 快重传减半 */
    reno_init(&r);
    r.cwnd = 100.0;
    reno_round(&r, 1, 0);
    CHECK(r.ssthresh == 50.0 && r.cwnd == 50.0, "Reno 快恢复减半错误");

    /* 5. 慢启动翻倍 */
    reno_init(&r);
    r.cwnd = 8.0;
    r.ssthresh = 1e9;
    reno_round(&r, 0, 0);
    CHECK(r.cwnd == 16.0, "慢启动未翻倍");

    /* 6. CUBIC 凹段初期增速快于 Reno 的 +1 段/RTT */
    {
        Cubic c2;
        double start, gain;
        cubic_init(&c2, 1);
        run_first_loss(&c2, 400.0, 0, NULL);
        start = c2.cwnd;
        cubic_round(&c2, 0, 0);
        gain = c2.cwnd - start;
        CHECK(gain > 1.0, "CUBIC 凹段初期增速应 > 1 段/RTT");
    }

    /* 7. CUBIC 恰好花 K 个 RTT 回到平台 W_max */
    {
        Cubic c3;
        int reached = -1, j;
        cubic_init(&c3, 1);
        run_first_loss(&c3, 400.0, 0, NULL);
        for (j = 0; j < 200; j++) {
            cubic_round(&c3, 0, 0);
            if (c3.cwnd >= c3.w_max - 1e-6) {
                reached = j + 1;
                break;
            }
        }
        CHECK(reached > 0 && fabs((double)reached - c3.k) <= 1.0,
              "回到 W_max 的 RTT 数应近似等于 K");
    }

    /* 8. fast convergence：cwnd < W_max 时再丢包会额外收缩 W_max */
    {
        Cubic c5, c6;
        double w1, w_after;
        cubic_init(&c5, 1);
        run_first_loss(&c5, 400.0, 0, NULL);
        w1 = c5.w_max;
        cubic_round(&c5, 0, 0);
        w_after = c5.cwnd;
        cubic_round(&c5, 1, 0);
        CHECK(c5.w_max < w1, "fast convergence 未收缩 W_max");
        CHECK(fabs(c5.w_max - w_after * (1.0 + BETA_CUBIC) / 2.0) < 1e-9,
              "fast convergence 公式错误");
        cubic_init(&c6, 0);
        run_first_loss(&c6, 400.0, 0, NULL);
        cubic_round(&c6, 0, 0);
        w_after = c6.cwnd;
        cubic_round(&c6, 1, 0);
        CHECK(fabs(c6.w_max - w_after) < 1e-9, "关闭 FC 后 W_max 应取当前 cwnd");
    }

    /* 9. RTO：两者 cwnd 落到 1 段；CUBIC 的 ssthresh 用 β_cubic */
    reno_init(&r);
    r.cwnd = 80.0;
    reno_round(&r, 0, 1);
    CHECK(r.cwnd == 1.0 && r.ssthresh == 40.0, "Reno RTO 处理错误");
    {
        Cubic c7;
        cubic_init(&c7, 1);
        run_first_loss(&c7, 400.0, 0, NULL);
        c7.cwnd = 80.0;
        cubic_round(&c7, 0, 1);
        CHECK(c7.cwnd == 1.0, "CUBIC RTO 后 cwnd 应为 1 段");
        CHECK(fabs(c7.ssthresh - 56.0) < 1e-9, "CUBIC RTO 的 ssthresh 应用 β_cubic");
        CHECK(c7.k == 0.0, "RTO 后 K 应置 0");
    }

    /* 10. 高 BDP：CUBIC 平均窗口 > Reno */
    {
        double rt[400], ct[400];
        Reno rr;
        Cubic cc;
        int j;
        double rs = 0.0, cs = 0.0;
        reno_init(&rr);
        cubic_init(&cc, 1);
        for (j = 0; j < 400; j++) {
            reno_round(&rr, rr.cwnd > 400.0, 0);
            cubic_round(&cc, cc.cwnd > 400.0, 0);
            rt[j] = rr.cwnd;
            ct[j] = cc.cwnd;
        }
        for (j = 0; j < 400; j++) {
            rs += rt[j];
            cs += ct[j];
        }
        CHECK(cs / 400.0 > rs / 400.0, "高 BDP 下 CUBIC 平均窗口应超过 Reno");
    }

    /* 11. 低 BDP：CUBIC 不应明显劣于 Reno（Reno-friendly 区域） */
    {
        Reno rr;
        Cubic cc;
        int j;
        double rs = 0.0, cs = 0.0;
        reno_init(&rr);
        cubic_init(&cc, 1);
        for (j = 0; j < 400; j++) {
            reno_round(&rr, rr.cwnd > 40.0, 0);
            cubic_round(&cc, cc.cwnd > 40.0, 0);
            rs += rr.cwnd;
            cs += cc.cwnd;
        }
        CHECK(cs >= rs * 0.98, "小 BDP 下 CUBIC 不应明显差于 Reno");
    }

    printf("[self-check] %d 项断言, %d 项失败\n", checks, failures);
}

/* ------------------------------------------------------------------ main */
int main(void) {
    double rt[400], ct[400];
    Reno r;
    Cubic c;
    int i;
    double rs = 0.0, cs = 0.0, w_max;
    int reno_need, cubic_need;

    self_check();

    /* 轨迹对照 */
    reno_init(&r);
    cubic_init(&c, 1);
    for (i = 0; i < 400; i++) {
        reno_round(&r, r.cwnd > 400.0, 0);
        cubic_round(&c, c.cwnd > 400.0, 0);
        rt[i] = r.cwnd;
        ct[i] = c.cwnd;
    }
    for (i = 0; i < 400; i++) {
        rs += rt[i];
        cs += ct[i];
    }
    printf("\n=== 1) 高 BDP 长肥管道（饱和窗口 400 段）窗口轨迹 ===\n");
    printf("%4s %9s %9s\n", "RTT", "Reno", "CUBIC");
    for (i = 0; i < 400; i += 20) {
        printf("%4d %9.1f %9.1f\n", i, rt[i], ct[i]);
    }
    printf("\n平均窗口 Reno %.1f 段 (%.2f Mbit/s) | CUBIC %.1f 段 (%.2f Mbit/s) | +%.1f%%\n",
           rs / 400.0, avg_mbps(rt, 400), cs / 400.0, avg_mbps(ct, 400),
           (cs / rs - 1.0) * 100.0);

    /* 恢复代价 */
    {
        Reno rr;
        Cubic cc;
        reno_init(&rr);
        w_max = run_first_loss(&rr, 400.0, 1, NULL);
        reno_need = recovery_rtts(w_max, 1, &rr);
        cubic_need = recovery_rtts(w_max, 0, &cc);
        printf("\n=== 2) 丢包后爬回原窗口需要多少 RTT（W_max = %.0f 段）===\n",
               w_max);
        printf("  Reno : %3d 个 RTT（掉到 W_max/2，拥塞避免每 RTT 只 +1 段）\n",
               reno_need);
        printf("  CUBIC: %3d 个 RTT（只掉到 0.7*W_max，K = cbrt(0.3*W_max/C)）\n",
               cubic_need);
        printf("  加速比约 %.0f 倍\n", (double)reno_need / (cubic_need ? cubic_need : 1));
    }

    /* RTO 对照 */
    printf("\n=== 3) 丢包检测方式的影响（饱和窗口 200 段）===\n");
    reno_init(&r);
    cubic_init(&c, 1);
    while (r.cwnd <= 200.0) reno_round(&r, 0, 0);
    while (c.cwnd <= 200.0) cubic_round(&c, 0, 0);
    {
        double rb = r.cwnd, cb = c.cwnd;
        reno_round(&r, 0, 1);
        cubic_round(&c, 0, 1);
        printf("  Reno  cwnd %6.1f -> RTO -> cwnd %.0f, ssthresh %.1f\n", rb,
               r.cwnd, r.ssthresh);
        printf("  CUBIC cwnd %6.1f -> RTO -> cwnd %.0f, ssthresh %.1f\n", cb,
               c.cwnd, c.ssthresh);
    }

    if (failures) {
        printf("\n有 %d 项自检失败\n", failures);
        return 1;
    }
    return 0;
}
