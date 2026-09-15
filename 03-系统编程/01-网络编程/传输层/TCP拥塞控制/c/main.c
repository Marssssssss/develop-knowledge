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
#include "cc_impl.h"
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
