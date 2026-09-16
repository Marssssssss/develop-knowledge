/* runqlat_check.c — 运行队列延迟(run queue latency)与调度器统计
 *
 * run queue latency:线程从「变为可运行」到「真的开始在某个 CPU 上运行」之间的时间。
 * 本文件是 python/runqlat_check.py 与 go/runqlat_check.go 的 C 姊妹实现,同一组断言。
 *
 * 两条观测路径:
 *   1. sched_wakeup / sched_switch 配对 -> power-of-2 微秒直方图(复刻 runqlat 输出格式)
 *   2. /proc/schedstat 的 rq_cpu_time(字段 7)与 run_delay(字段 8)两次采样求差
 *
 * 编译: cc -O2 -o runqlat_check runqlat_check.c -lm
 * 运行: ./runqlat_check          (自检,打印重放直方图)
 *       ./runqlat_check --real   (额外读本机 /proc/schedstat,需 Linux)
 */
#include "runqlat_impl.h"

/* ---------------------------------------------------------------- 自检 */
static int g_ok = 1, g_pass = 0, g_total = 0;
static void check(const char *label, int cond, const char *detail) {
    g_total++;
    if (cond) g_pass++; else g_ok = 0;
    printf("  [%s] %s%s%s\n", cond ? "PASS" : "FAIL", label,
           (detail && *detail) ? "  <- " : "", (detail && *detail) ? detail : "");
}

static void print_hist(const int *b, int nb, const char *unit) {
    char line[256];
    int i, last = hist_last(b, nb), mx = hist_max(b, nb);
    if (last < 0) { printf("  (%s 无样本)\n", unit); return; }
    snprintf(line, sizeof line, "%10s%15s: count     distribution", unit, "");
    printf("  %s\n", line);
    for (i = 0; i <= last; i++) { row_str(line, sizeof line, i, b[i], mx); printf("  %s\n", line); }
}

int main(int argc, char **argv) {
    char d[160];
    int i, segs[8][2], ns;
    int b[16];

    memset(segs, 0, sizeof segs);   /* 断言失败时也会读它拼详情串,先清零 */

    printf("== 1. runqlat 的桶边界是 2 的幂 ==\n");
    {
        static const double probe[] = {0, 1, 2, 3, 15, 16, 31};
        static const int want[] = {0, 0, 1, 1, 3, 4, 4};
        int got[7], eq = 1;
        for (i = 0; i < 7; i++) { got[i] = bucket_index(probe[i]); if (got[i] != want[i]) eq = 0; }
        snprintf(d, sizeof d, "%d %d %d %d %d %d %d",
                 got[0], got[1], got[2], got[3], got[4], got[5], got[6]);
        check("0->0,1->0,2->1,3->1,15->3,16->4,31->4:数值落在 [2^i, 2^(i+1)) 里", eq, d);
    }
    check("标签是 '0 -> 1' / '8 -> 15' / '16384 -> 32767'(第 0 桶特例为 [0,1])",
          bucket_low(0) == 0 && bucket_high(0) == 1 && bucket_low(3) == 8 &&
          bucket_high(3) == 15 && bucket_low(14) == 16384 && bucket_high(14) == 32767, NULL);

    printf("== 2. 输出列宽与官方样例逐字符一致 ==\n");
    /* 只保留承载两个峰的桶,让断言可读;官方样例的完整 16 桶见 README */
    memset(b, 0, sizeof b);
    b[0] = 233; b[1] = 742; b[2] = 203; b[3] = 173; b[14] = 809; b[15] = 64;
    {
        char row[256], head[256], *p1, *p2;
        int mx = 0, stars = 0;
        row_str(row, sizeof row, 0, 233, 809);
        snprintf(head, sizeof head, "%10s%15s: count     distribution", "usecs", "");
        check("表头 = 'usecs' 右对齐 10 列 + 15 空格 + ': count     distribution'",
              strcmp(head, "     usecs               : count     distribution") == 0, head);
        check("数据行前 27 列 = '         0 -> 1          : '",
              strncmp(row, "         0 -> 1          : ", 27) == 0, row);
        check("计数左对齐 9 列后再接 '|'", strncmp(row + 27, "233      ", 9) == 0, row + 27);
        p1 = strchr(row, '|'); p2 = strrchr(row, '|');
        check("柱区总宽恒为 40", p2 && (p2 - p1 - 1) == BAR_W, NULL);
        for (i = 0; i <= 15; i++) {
            char r[256];
            row_str(r, sizeof r, i, b[i], 809);
            stars = count_char(r, '*');
            if (stars > mx) mx = stars;
        }
        check("最大桶占满 40 个 *(官方样例 809 占满)", mx == BAR_W, NULL);
        snprintf(d, sizeof d, "%d", count_char(row, '*'));
        check("233 相对 809 -> 233*40/809 = 11 个 *(与官方样例的 11 个一致)",
              count_char(row, '*') == 233 * BAR_W / 809 && 233 * BAR_W / 809 == 11, d);
    }

    printf("== 3. 双峰识别 ==\n");
    ns = modes(b, 16, 0.05, segs, 8);
    snprintf(d, sizeof d, "段数=%d 首段=(%d,%d)", ns, segs[0][0], segs[0][1]);
    check("识别出 2 个模式(双峰)", ns == 2, d);
    check("第一峰在桶 0..3(0~15 us)", ns == 2 && segs[0][0] == 0 && segs[0][1] == 3, d);
    snprintf(d, sizeof d, "(%d,%d)", segs[1][0], segs[1][1]);
    check("第二峰起点在桶 14(16384~32767 us,即 16~32 ms)", ns == 2 && segs[1][0] == 14, d);
    check("5% 阈值把桶 15(64/2224=2.9%)排除在峰外,第二峰收缩为 (14,14)",
          ns == 2 && segs[1][1] == 14, d);
    {
        int s2[8][2]; int n2 = modes(b, 16, 0.02, s2, 8);
        snprintf(d, sizeof d, "(%d,%d)", s2[1][0], s2[1][1]);
        check("阈值降到 2% 就把整段慢峰恢复成 14..15",
              n2 == 2 && s2[1][0] == 14 && s2[1][1] == 15, d);
    }
    {
        int fast[16], nf;
        memset(fast, 0, sizeof fast);
        for (i = 0; i <= 3; i++) fast[i] = b[i];
        nf = modes(fast, 16, 0.05, segs, 8);
        check("只有快路径时退化成单峰(对照机就该长这样)", nf == 1, NULL);
    }

    printf("== 4. wakeup / switch 配对 ==\n");
    {
        static ev_t evbuf[8192];
        int buckets[16], buckets2[16], paired, orphan, p2, o2;
        int n = build_replay(evbuf, 8192);
        memset(buckets, 0, sizeof buckets);
        pair_latencies(evbuf, n, buckets, 16, &paired, &orphan);
        snprintf(d, sizeof d, "orphan=%d", orphan);
        check("全部 switch 都配上了 wakeup(重放序列成对构造)", orphan == 0, d);
        snprintf(d, sizeof d, "paired=%d", paired);
        check("配对数 = 各桶计数之和 = 2224",
              paired == 2224 && buckets[0] == 233 && buckets[1] == 742 &&
              buckets[2] == 203 && buckets[3] == 173 &&
              buckets[14] == 809 && buckets[15] == 64, d);
        ns = modes(buckets, 16, 0.05, segs, 8);
        check("重放后仍是双峰",
              ns == 2 && segs[0][0] == 0 && segs[0][1] == 3 && segs[1][0] == 14, NULL);
        memset(buckets2, 0, sizeof buckets2);
        pair_latencies(evbuf, n, buckets2, 16, &p2, &o2);
        check("重放是确定性的,两次结果完全一致",
              memcmp(buckets, buckets2, sizeof buckets) == 0 && p2 == paired && o2 == orphan, NULL);
        {
            ev_t one[1]; int bo[16], po, oo;
            one[0].kind = 1; one[0].ts = 5000000; one[0].tid = 42;
            memset(bo, 0, sizeof bo);
            pair_latencies(one, 1, bo, 16, &po, &oo);
            check("没有 wakeup 的首次上 CPU 记成 orphan,而非制造 now-0 巨值",
                  po == 0 && oo == 1, NULL);
        }
        {
            ev_t wo[1]; int bw[16], pw, ow;
            wo[0].kind = 0; wo[0].ts = 1; wo[0].tid = 7;
            memset(bw, 0, sizeof bw);
            pair_latencies(wo, 1, bw, 16, &pw, &ow);
            check("有 wakeup 但无 switch -> 不产生样本", pw == 0, NULL);
        }
        print_hist(buckets, 16, "usecs");
    }

    printf("== 5. /proc/schedstat: 9 个字段、等待/运行比 ==\n");
    {
        static const char *S0 = "version 17\ncpu0 0 0 402267 147161 236309 1062 "
                                "7000000000 3000000000 255035\ndomain0 SMT ff 1 2 3\n";
        static const char *S1 = "version 17\ncpu0 0 0 402267 147161 236309 1062 "
                                "7083791148 3449973971 255035\ndomain0 SMT ff 1 2 3\n";
        long long a[9], bb[9], dum[9];
        double ratio = 0.0, want, single;
        int okA = parse_schedstat(S0, a), okB = parse_schedstat(S1, bb);
        check("cpu0 行读出 9 个具名字段", okA && okB && a[2] == 402267 && a[8] == 255035, NULL);
        check("字段 7/8 分别是 rq_cpu_time 与 run_delay",
              a[6] == 7000000000LL && a[7] == 3000000000LL, NULL);
        check("字段 2(array_exp)是 O(1) 调度器的遗留,恒为 0", a[1] == 0, NULL);
        check("只有 domain 行时明确报错,不会把 domain 当成 CPU",
              parse_schedstat("version 17\ndomain0 SMT ff 1 2 3\n", dum) == 0, NULL);
        want = 100.0 * 449973971.0 / 83791148.0;
        (void)run_delay_ratio(a, bb, &ratio);
        snprintf(d, sizeof d, "%.2f%%", ratio);
        check("等待/运行 = (3449973971-3000000000)/(7083791148-7000000000) = 537.02%",
              fabs(ratio - want) < 1e-9, d);
        single = 100.0 * (double)a[7] / (double)a[6];
        snprintf(d, sizeof d, "单点=%.2f%% 差值法=%.2f%%", single, ratio);
        check("计数器只增不减,必须两次采样求差:单点读值会得到完全不同的数",
              fabs(single - 42.86) < 0.01 && fabs(ratio - 42.86) > 100, d);
    }

    printf("== 6. sched_schedstats 开关:0 表示「没统计」而不是「没等待」 ==\n");
    {
        static const char *off  = "cpu0 0 0 402267 147161 236309 1062 7000000000 0 255035\n";
        static const char *off2 = "cpu0 0 0 480000 147161 236309 1062 7300000000 0 255035\n";
        long long x[9], y[9];
        double r2 = 0.0;
        int vx = parse_schedstat(off, x), vy = parse_schedstat(off2, y);
        check("sysctl=0 时 run_delay 恒为 0", vx && vy && x[7] == 0 && y[7] == 0, NULL);
        check("此时 sched_count 与 rq_cpu_time 照常推进,证明文件在动、只是没统计",
              y[2] > x[2] && y[6] > x[6], NULL);
        check("两次采样 run_delay 都是 0 -> 比值判定为「未统计」,不是 0%",
              run_delay_ratio(x, y, &r2) == 0, NULL);
    }

    printf("== 7. /proc/<pid>/schedstat 三字段 ==\n");
    {
        long long ps[3];
        int vp = parse_pid_schedstat("1234567890 98765432 1042\n", ps);
        snprintf(d, sizeof d, "%lld %lld %lld", ps[0], ps[1], ps[2]);
        check("1=CPU 上时间 2=运行队列等待时间 3=被调度次数",
              vp && ps[0] == 1234567890LL && ps[1] == 98765432LL && ps[2] == 1042, d);
        check("进程级等待时间 98765432 ns = 98.77 ms 换算正确",
              fabs((double)ps[1] / 1e6 - 98.765432) < 1e-6, NULL);
    }

    printf("== 8. 为什么饱和度是非线性的 ==\n");
    {
        /* 0.9/0.1 在 IEEE754 下是 8.999999999999998,断言必须带容差 */
        double w50 = mmc_wait(0.5), w90 = mmc_wait(0.9), w98 = mmc_wait(0.98);
        snprintf(d, sizeof d, "%g", w90);
        check("ρ=0.5 -> 等待 1 个服务时间(Wq = ρ/(1-ρ))", fabs(w50 - 1.0) < 1e-9, NULL);
        check("ρ=0.9 -> 9 倍服务时间", fabs(w90 - 9.0) < 1e-9, d);
        snprintf(d, sizeof d, "%g", w98);
        check("ρ=0.98 -> 49 倍", fabs(w98 - 49.0) < 1e-9, d);
        check("饱和度 0.9→0.98 只涨 8.9%,等待时间却从 9 涨到 49(5.4 倍)",
              fabs((0.98 - 0.9) / 0.9 - 0.0889) < 0.001 && fabs(w98 / w90 - 49.0 / 9.0) < 1e-9, NULL);
        check("ρ→1 时等待时间发散(运行队列长 1 与长 10 完全不是一回事)",
              mmc_wait(1.0) > 1e300 && mmc_wait(0) == 0, NULL);
    }

    printf("== 9. run queue latency 的定位 ==\n");
    {
        ev_t e1[2]; int h1[16], h2[16], q, r;
        e1[0].kind = 0; e1[0].ts = 0;   e1[0].tid = 7;
        e1[1].kind = 1; e1[1].ts = 9000; e1[1].tid = 7;
        memset(h1, 0, sizeof h1);
        pair_latencies(e1, 2, h1, 16, &q, &r);
        e1[1].ts = 90000;
        memset(h2, 0, sizeof h2);
        pair_latencies(e1, 2, h2, 16, &q, &r);
        check("它测的是「可运行 -> 真正在 CPU 上运行」:同一 wakeup 换个 switch 时刻,延迟就变",
              h1[3] == 1 && h2[6] == 1, NULL);
    }
    check("它不回答「在 CPU 上待了多久」(那是 cpudist 的视角):重放里最短 0~1us、最长 32~65ms",
          bucket_index(0) == 0 && bucket_index(40000) == 15 && CPU_COUNT == 8, NULL);

    printf("\n%s(%d/%d)\n", g_ok ? "全部通过" : "存在失败项", g_pass, g_total);

    if (argc > 1 && strcmp(argv[1], "--real") == 0) {
        FILE *fp = fopen("/proc/schedstat", "r");
        if (fp) {
            char buf[4096];
            size_t n = fread(buf, 1, sizeof buf - 1, fp);
            long long a[9];
            buf[n] = '\0';
            fclose(fp);
            if (parse_schedstat(buf, a))
                printf("\n-- 本机 /proc/schedstat cpu0 --\n  rq_cpu_time=%lld ns  run_delay=%lld ns\n"
                       "  (run_delay 为 0 通常意味着 kernel.sched_schedstats=0,即统计未开)\n",
                       a[6], a[7]);
            else
                printf("\n读到了 /proc/schedstat 但没有 cpu<N> 行\n");
        } else {
            printf("\n--real 需要 Linux;/proc/schedstat 不可读\n");
        }
    }
    return g_ok ? 0 : 1;
}
