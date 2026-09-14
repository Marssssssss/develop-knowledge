/*
 * psi_monitor.c — PSI(Pressure Stall Information)读取与阈值监控(C 版,教学用)
 *
 * 演示:
 *   1) 解析 /proc/pressure/{cpu,memory,io} 的 some/full 行(avg10/60/300 + total us)
 *   2) 用两次采样求 total 的增量,算出"自定义窗口"内的停顿占比
 *   3) 注册 PSI trigger 并用 poll() 等唤醒(内核官方示例的写法)
 *
 * 权威依据:docs.kernel.org/accounting/psi.html
 *   * some = 至少有【部分】任务在该资源上停顿的时间占比
 *   * full = 【所有】非 idle 任务同时停顿的时间占比(此时 CPU 周期真的在浪费,
 *            长时间处于该状态即 thrashing)
 *   * CPU full 在系统级未定义,但自 5.13 起会报告出来(为兼容性填 0)
 *   * trigger 格式 "<some|full> <停顿量 us> <窗口 us>";窗口允许 500ms~10s
 *     (非特权用户要求窗口是 2s 的整数倍);对已写过 trigger 的 fd 再写会 EBUSY
 *   * 监控在进入 stall 态时激活、退出时去激活;通知速率限制为每个窗口一次
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic psi_monitor.c -o psi_monitor
 * 运行: ./psi_monitor            # 读 /proc/pressure/* 需要 Ubuntu 4.20+/CONFIG_PSI=y
 *       ./psi_monitor 500000     # 自定义阈值(us)
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAXLINE 256
#define N_RES 3

static const char *res_names[N_RES] = {"cpu", "memory", "io"};

struct sample {
    double avg10, avg60, avg300;
    unsigned long long total; /* 累计停顿时间,微秒 */
    int valid;
};

static int parse_pressure(const char *res, struct sample *some, struct sample *full)
{
    char path[128], line[MAXLINE];
    FILE *f;
    int have_some = 0, have_full = 0;

    snprintf(path, sizeof(path), "/proc/pressure/%s", res);
    f = fopen(path, "r");
    if (!f)
        return -1;
    while (fgets(line, sizeof(line), f)) {
        struct sample s;
        char kind[16] = {0};
        if (sscanf(line, "%15s avg10=%lf avg60=%lf avg300=%lf total=%llu",
                   kind, &s.avg10, &s.avg60, &s.avg300, &s.total) != 5)
            continue;
        s.valid = 1;
        if (strcmp(kind, "some") == 0) {
            *some = s;
            have_some = 1;
        } else if (strcmp(kind, "full") == 0) {
            *full = s;
            have_full = 1;
        }
    }
    fclose(f);
    return (have_some && have_full) ? 0 : 1;
}

static void print_header(void)
{
    puts("PSI = Pressure Stall Information(:资源争用导致的\"停顿\"统计)");
    puts("  some:至少有一部分任务在该资源上停顿的时间占比");
    puts("  full:所有非 idle 任务【同时】停顿的时间占比 -> 此时 CPU 周期真的在浪费");
    puts("  三条平均线 avg10/avg60/avg300 看趋势,total 是累计停顿微秒数,");
    puts("  用 total 的增量可以自己算任意窗口的百分比(识别瞬时尖峰)。\n");
}

static void read_and_show(void)
{
    struct sample prev_some[N_RES], prev_full[N_RES];
    int ok[N_RES];
    struct timespec ts = {0, 500 * 1000 * 1000}; /* 500 ms 采样间隔 */

    puts("=== 第 1 次采样 ===");
    for (int i = 0; i < N_RES; i++) {
        ok[i] = (parse_pressure(res_names[i], &prev_some[i], &prev_full[i]) == 0);
        if (!ok[i]) {
            printf("  %-7s 不可读(内核未开 CONFIG_PSI,或 /proc/pressure 不存在)\n",
                   res_names[i]);
            continue;
        }
        printf("  %-7s some: avg10=%5.2f avg60=%5.2f avg300=%5.2f total=%llu us\n",
               res_names[i], prev_some[i].avg10, prev_some[i].avg60,
               prev_some[i].avg300, prev_some[i].total);
        printf("  %-7s full: avg10=%5.2f avg60=%5.2f avg300=%5.2f total=%llu us%s\n",
               res_names[i], prev_full[i].avg10, prev_full[i].avg60,
               prev_full[i].avg300, prev_full[i].total,
               (strcmp(res_names[i], "cpu") == 0) ? "   <- CPU full 系统级未定义" : "");
    }
    nanosleep(&ts, NULL);

    puts("\n=== 第 2 次采样(间隔 500 ms),并计算该窗口内的停顿占比 ===");
    for (int i = 0; i < N_RES; i++) {
        struct sample s, f;
        if (!ok[i] || parse_pressure(res_names[i], &s, &f) != 0)
            continue;
        unsigned long long d_some = s.total - prev_some[i].total;
        unsigned long long d_full = f.total - prev_full[i].total;
        double pct_some = 100.0 * (double)d_some / (500.0 * 1000.0);
        double pct_full = 100.0 * (double)d_full / (500.0 * 1000.0);
        printf("  %-7s Δtotal(some)=%8llu us -> some 停顿 %6.2f%%    "
               "Δtotal(full)=%8llu us -> full 停顿 %6.2f%%\n",
               res_names[i], d_some, pct_some, d_full, pct_full);
    }
    puts("  (window 内占比是\"真实发生\"的停顿,不受内核 avg 窗口与更新频率影响)");
}

static void monitor_trigger(unsigned long long threshold_us)
{
    struct pollfd fds;
    const char *path = "/proc/pressure/memory";
    char trig[64];
    int n;

    printf("\n=== 注册 PSI trigger: %s 阈值 %llu us / 窗口 1000000 us ===\n",
           path, threshold_us);
    fds.fd = open(path, O_RDWR | O_NONBLOCK);
    if (fds.fd < 0) {
        printf("  open(%s) 失败: %s\n", path, strerror(errno));
        puts("  原因可能是:内核未开 CONFIG_PSI(或用 psi= 引导参数关掉了),");
        puts("  或 cgroup v2 未挂载。无 PSI 时可用 loadavg + vmstat r 列替代,");
        puts("  但 loadavg 混合了 CPU/磁盘/锁,区分能力弱 —— 这正是 PSI 要解决的问题。");
        return;
    }
    snprintf(trig, sizeof(trig), "some %llu 1000000", threshold_us);
    if (write(fds.fd, trig, strlen(trig) + 1) < 0) {
        printf("  write(\"%s\") 失败: %s\n", trig, strerror(errno));
        if (errno == EBUSY)
            puts("  该 fd 上已有 trigger:同一 fd 只能有一个,要多个必须多开 fd。");
        if (errno == EINVAL)
            puts("  EINVAL:窗口必须是 500 ms~10 s;非特权用户还要求窗口为 2 s 的整数倍。");
        close(fds.fd);
        return;
    }
    fds.events = POLLPRI;

    puts("  已注册。窗口与通知速率的内核约束:");
    puts("    · 窗口 500 ms ~ 10 s(最小更新间隔 50 ms,最大 1 s)");
    puts("    · 非特权用户:窗口必须是 2 s 的整数倍");
    puts("    · 进入 stall 才激活,且至少保持一个窗口;通知每窗口最多一次");
    puts("    · 关闭 fd 即注销 trigger");

    n = poll(&fds, 1, 3000); /* 3 秒超时,避免无人值守时永久阻塞 */
    if (n < 0) {
        printf("  poll() 错误: %s\n", strerror(errno));
    } else if (n == 0) {
        puts("  3 秒内没有触发(系统内存压力未超过阈值)—— 这本身是有用的结论:");
        puts("  阈值不触发说明当前没有值得关注的 memory 停顿。");
    } else if (fds.revents & POLLERR) {
        puts("  POLLERR:事件源消失(常见于 cgroup 被删除)。");
    } else if (fds.revents & POLLPRI) {
        puts("  >>> 触发了:观察到超过阈值的 memory 停顿,应检查回收/OOM/THP。");
    } else {
        printf("  收到未知事件 0x%x\n", fds.revents);
    }
    close(fds.fd);
}

static void cgroup_note(void)
{
    puts("\n=== cgroup v2 接口 ===");
    puts("  每个 cgroup 目录下同样有 cpu.pressure / memory.pressure / io.pressure,");
    puts("  root 需以 cgroup 相对路径访问。容器场景就是这样把\"停顿\"归因到单个容器:");
    puts("  这是 loadavg 做不到的事(loadavg 只有系统级全局值)。");
}

int main(int argc, char **argv)
{
    unsigned long long threshold = (argc > 1) ? strtoull(argv[1], NULL, 10) : 150000ULL;

    print_header();
    read_and_show();
    monitor_trigger(threshold);
    cgroup_note();
    puts("\n结论:loadavg 回答\"需求有多少\",PSI 回答\"有多少时间真的被卡住了\"——");
    puts("      后者才能区分\"忙\"(有用功)与\"卡\"(thrashing)。");
    return 0;
}
