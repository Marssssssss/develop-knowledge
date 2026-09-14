/*
 * pmu_group.c — 用 perf_event_open(2) 开一个事件组并做缩放读(C 版,教学用)
 *
 * 演示:
 *   1) group leader(group_fd = -1)+ 成员(用 leader 的 fd 当 group_fd)组队
 *   2) read_format = GROUP | TOTAL_TIME_ENABLED | TOTAL_TIME_RUNNING,一次读整组
 *   3) time_enabled != time_running 时按 man page 的整数公式缩放;算 IPC / 未命中率
 *   4) mmap 元数据页,检查 cap_user_rdpmc(能否走 rdpmc 无系统调用快路径)
 *
 * 权威依据:man7 perf_event_open(2)(pid/cpu 语义、perf_event_attr、read_format 与缩放、
 *           mmap 元数据页与 cap_user_rdpmc)、linux/perf_event.h
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic pmu_group.c -o pmu_group
 * 运行: ./pmu_group        # 需要 CAP_PERFMON(或 CAP_SYS_ADMIN);EPERM 时给出指引
 */

#define _GNU_SOURCE
#include <errno.h>
#include <linux/perf_event.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

#define N_EVENTS 3
#define SLOTS_HINT 4 /* 槽位数量由微架构决定;此处只用于解释缩放比 */

static int perf_open(struct perf_event_attr *attr, pid_t pid, int cpu,
                     int group_fd, unsigned long flags)
{
    return (int)syscall(SYS_perf_event_open, attr, pid, cpu, group_fd, flags);
}

/* read_format 布局:指定 PERF_FORMAT_GROUP 且不带 FORMAT_ID/LOST 时,就是
 *   struct { u64 nr; u64 time_enabled; u64 time_running; u64 values[nr]; };      */
struct read_group {
    uint64_t nr;
    uint64_t time_enabled;
    uint64_t time_running;
    uint64_t values[N_EVENTS];
};

static const char *ev_names[N_EVENTS] = {"INSTRUCTIONS", "CPU_CYCLES", "CACHE_MISSES"};
static const uint64_t ev_config[N_EVENTS] = {
    PERF_COUNT_HW_INSTRUCTIONS,
    PERF_COUNT_HW_CPU_CYCLES,
    PERF_COUNT_HW_CACHE_MISSES,
};

/* 模拟一段可测量的负载:整数运算 + 数组遍历,产生真实的指令/周期/缓存事件 */
static uint64_t busy_work(int rounds)
{
    static volatile uint64_t sink;
    uint64_t acc = 1;
    for (int r = 0; r < rounds; r++) {
        for (int i = 1; i <= 200000; i++)
            acc = acc * 31 + (uint64_t)i;
    }
    sink = acc;
    return acc;
}

static void explain_permission(void)
{
    puts("! perf_event_open 被拒绝,说明当前进程缺少权限。");
    puts("  man7 perf_event_open(2):测量指定进程需要 CAP_PERFMON(Linux 5.9 起)");
    puts("  或 CAP_SYS_ADMIN;pid=-1(整机)还要求 perf_event_paranoid < 1。");
    puts("  可以:  sudo sysctl kernel.perf_event_paranoid=1      # 权衡安全后放开");
    puts("         或直接用 sudo 运行本程序");
    puts("  perf stat / perf record 底层走的就是这个系统调用。");
}

/* 整数缩放公式(与 man page 的 rdpmc 代码一致):
 *   quot = count / running; rem = count % running;
 *   count = quot * enabled + (rem * enabled) / running;
 */
static uint64_t scale_int(uint64_t count, uint64_t enabled, uint64_t running)
{
    uint64_t quot, rem;
    if (running == 0)
        return 0;
    quot = count / running;
    rem = count % running;
    return quot * enabled + (rem * enabled) / running;
}

int main(void)
{
    struct perf_event_attr attr;
    int fds[N_EVENTS];
    struct read_group rg;
    uint64_t work;
    int leader;

    printf("=== 组队开 3 个硬件事件(槽位数量由微架构决定,本 demo 假设 %d 个)===\n",
           SLOTS_HINT);
    printf("  事件:%s, %s, %s\n", ev_names[0], ev_names[1], ev_names[2]);

    memset(&attr, 0, sizeof(attr));
    attr.size = sizeof(attr);
    attr.type = PERF_TYPE_HARDWARE;
    attr.config = ev_config[0];
    attr.read_format = PERF_FORMAT_GROUP | PERF_FORMAT_TOTAL_TIME_ENABLED |
                       PERF_FORMAT_TOTAL_TIME_RUNNING;
    attr.disabled = 1; /* leader 先禁用,组一次性开启,保证计数窗口一致 */

    leader = perf_open(&attr, 0, -1, -1, 0);
    if (leader < 0) {
        fprintf(stderr, "perf_event_open(leader) 失败: %s\n", strerror(errno));
        explain_permission();
        return 0;
    }
    fds[0] = leader;
    for (int i = 1; i < N_EVENTS; i++) {
        attr.config = ev_config[i];
        attr.disabled = 0;
        fds[i] = perf_open(&attr, 0, -1, leader, 0); /* group_fd = leader */
        if (fds[i] < 0) {
            fprintf(stderr, "perf_event_open(%s) 失败: %s\n", ev_names[i], strerror(errno));
            return 1;
        }
    }

    /* mmap 元数据页:大小必须是 1 + 2^n 页;这里只看结构体,不消费样本 */
    void *meta = mmap(NULL, 2 * 4096, PROT_READ | PROT_WRITE, MAP_SHARED, leader, 0);
    if (meta != MAP_FAILED) {
        struct perf_event_mmap_page *pc = (struct perf_event_mmap_page *)meta;
        printf("\n=== mmap 元数据页(struct perf_event_mmap_page)===\n");
        printf("  version=%u  index=%u  offset=%llu  pmc_width=%u\n",
               pc->version, pc->index, (unsigned long long)pc->offset, pc->pmc_width);
        printf("  cap_user_rdpmc=%u  cap_user_time=%u  cap_user_time_zero=%u\n",
               pc->cap_user_rdpmc, pc->cap_user_time, pc->cap_user_time_zero);
        if (pc->cap_user_rdpmc)
            puts("  -> 可用 rdpmc 在用户态直接读计数器(绕开 read(2) 系统调用)");
        else
            puts("  -> 该内核/CPU 不支持用户态 rdpmc,只能走 read(2)");
        munmap(meta, 2 * 4096);
    }

    ioctl(leader, PERF_EVENT_IOC_RESET, PERF_IOC_FLAG_GROUP);
    ioctl(leader, PERF_EVENT_IOC_ENABLE, PERF_IOC_FLAG_GROUP);
    work = busy_work(30);
    ioctl(leader, PERF_EVENT_IOC_DISABLE, PERF_IOC_FLAG_GROUP);

    memset(&rg, 0, sizeof(rg));
    if (read(leader, &rg, sizeof(rg)) < 0) {
        fprintf(stderr, "read(group) 失败: %s(缓冲区不足会返回 ENOSPC)\n", strerror(errno));
        return 1;
    }

    printf("\n=== read(leader) 一次读整组(nr=%llu)===\n", (unsigned long long)rg.nr);
    printf("  time_enabled = %llu us, time_running = %llu us\n",
           (unsigned long long)rg.time_enabled, (unsigned long long)rg.time_running);
    int multiplexed = (rg.time_enabled != rg.time_running);
    printf("  是否发生多路复用: %s\n", multiplexed ? "是(必须缩放)" : "否(缩放为恒等)");

    uint64_t scaled[N_EVENTS];
    printf("\n  %-16s %14s %14s %12s\n", "event", "raw", "scaled", "缩放比");
    for (uint64_t i = 0; i < rg.nr && i < N_EVENTS; i++) {
        scaled[i] = scale_int(rg.values[i], rg.time_enabled, rg.time_running);
        double ratio = rg.time_running ? (double)rg.time_enabled / (double)rg.time_running : 0.0;
        printf("  %-16s %14llu %14llu %12.3f\n", ev_names[i],
               (unsigned long long)rg.values[i],
               (unsigned long long)scaled[i], ratio);
    }

    printf("\n=== 派生指标(缩放后才是可比的)===\n");
    if (scaled[1] > 0)
        printf("  IPC              = %.3f  (INSTRUCTIONS / CPU_CYCLES)\n",
               (double)scaled[0] / (double)scaled[1]);
    if (scaled[0] > 0)
        printf("  CPI              = %.3f\n", (double)scaled[1] / (double)scaled[0]);
    printf("  work(防优化)      = %llu\n", (unsigned long long)work);
    puts("  注意:CACHE_MISSES 的语义是\"通常指 LLC 未命中\",未命中率还需要一个");
    puts("        ACCESS 侧事件(CACHE_REFERENCES 或 PERF_TYPE_HW_CACHE 的 ACCESS);");
    puts("        本 demo 只开了 MISS 侧,故不打印比率——这正是\"事件选型\"的坑。");

    for (int i = 0; i < N_EVENTS; i++)
        close(fds[i]);
    puts("\n提示:若缩放比明显 > 1,说明事件数超过槽位,计数只是部分时间的累计;");
    puts("      此时跨事件比值(IPC、未命中率)可信度下降,应减少事件数或固定 CPU/频率。");
    return 0;
}
