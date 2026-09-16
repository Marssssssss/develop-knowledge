/* use_collect.c — USE 方法「如何获取」那一列的 C 实现
 *
 * 官方 checklist 的第三列是「用哪个工具、读哪个统计量」。本文件把其中
 * **纯文件接口**的部分落成代码(不需要任何外部工具):
 *
 *   CPU utilization        /proc/stat        -> 除 idle/iowait 外全部字段求和 / 全部
 *   CPU saturation         /proc/loadavg     -> running - CPU 数
 *   Memory utilization     /proc/meminfo     -> 1 - MemAvailable/MemTotal
 *   Memory saturation      /proc/vmstat      -> pswpin + pswpout 增量(本文件只演示口径)
 *   Network util/sat/err   /proc/net/dev     -> bytes / drop+fifo / errs
 *   Storage I/O util/sat   /proc/diskstats   -> 忙时间、加权 I/O 时间 / 墙钟时间
 *   Storage capacity       statvfs()         -> 已用/容量(population 口径)
 *
 * 解析函数都接收**文本**而非直接读文件,既能读真 /proc,也能用内嵌样本自测。
 * /proc/diskstats 的列数随内核版本增长(内核 iostats 文档 v5.3 时仍是 11 列,
 * 新版追加了 discard/flush),故按绝对下标取用到的 4 列、其余位置忽略,
 * 换内核只需复核下标 3/7/12/13 是否仍是 read_ios/write_ios/io_ms/weighted_io_ms。
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic use_collect.c -o use_collect
 * 运行: ./use_collect            # 读真实 /proc
 *       ./use_collect --selftest # 用内嵌样本自检解析器
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/statvfs.h>

#define MAX_LINE 4096

#include "use_parse.h"

/* ---------- 自检 ---------- */
static int fails = 0;

static void ck(const char *label, int cond, const char *detail)
{
    printf("  [%s] %s%s%s\n", cond ? "PASS" : "FAIL", label,
           (detail && *detail) ? "  <- " : "", detail ? detail : "");
    if (!cond) {
        fails++;
    }
}

/* 样本按「从零开始的一次 10 s 观测」构造: 第一份是 t0,第二份是 t1。 */
static const char *STAT_A =
    "cpu  100 0 50 800 100 5 10 35 0 0\n"
    "cpu0 50 0 25 400 50 2 5 17 0 0\n";
static const char *STAT_B =
    "cpu  3700 0 2200 13900 900 50 240 35 0 0\n"
    "cpu0 1850 0 1100 6950 450 25 120 17 0 0\n";
static const char *LOADAVG = "9.50 6.50 4.00 12/500 4242\n";
static const char *MEMINFO =
    "MemTotal:       16384000 kB\n"
    "MemFree:         1048576 kB\n"
    "MemAvailable:    1228800 kB\n"
    "SwapTotal:       4194304 kB\n";
static const char *NETDEV =
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
    "  eth0: 99000000000 10000000 0 37 12 0 0 0 2000000000 5000000 0 0 0 0 0 0\n";
static const char *DISKSTATS =
    "   8       0 sda 140000 0 1000000 3000 72000 0 500000 6000 0 9400 34000 0 0 0 0 0 0\n"
    "   8      16 sdb 10 0 80 1 20 0 160 2 0 3 3 0 0 0 0 0 0\n";

int main(int argc, char **argv)
{
    int selftest = (argc > 1 && strcmp(argv[1], "--selftest") == 0);
    struct cpu_times a, b;
    struct dev_stats d0, d1;
    unsigned long long rxb = 0, txb = 0, rxdrop = 0, rxerr = 0;
    unsigned long long memtotal = 0, memavail = 0;
    double load1 = 0, cpu_pct = 0, mem_pct, net_pct, util, qu;
    int running = 0, total = 0;
    const double interval = 10.0;

    printf("== 解析器自检(内嵌样本,不依赖真实 /proc) ==\n");
    ck("parse_proc_stat 读出 8 字段且 steal 单列存在",
       parse_proc_stat(STAT_A, &a) == 0 && a.steal == 35, "");
    ck("parse_proc_stat 只认汇总行 cpu ,不误取 cpu0",
       parse_proc_stat(STAT_B, &b) == 0 && b.user == 3700, "");
    ck("parse_proc_stat 对非法输入返回 -1",
       parse_proc_stat("cpu0 1 2 3 4 5 6 7 8\n", &b) == -1, "");
    if (parse_proc_stat(STAT_A, &a) == 0 && parse_proc_stat(STAT_B, &b) == 0) {
        char buf[64];
        cpu_pct = cpu_busy_pct(&a, &b);
        snprintf(buf, sizeof buf, "%.3f%%", cpu_pct);
        ck("CPU 忙 = (us+nice+sy+irq+softirq+steal)/全部 ≈ 30.238%",
           cpu_pct > 30.2 && cpu_pct < 30.3, buf);
    }
    ck("parse_loadavg 读出 running=12 / total=500",
       parse_loadavg(LOADAVG, &load1, &running, &total) == 0 &&
       running == 12 && total == 500 && load1 > 9.4, "");
    ck("饱和度 = running - CPU 数 = 12 - 8 = 4 > 0", running - 8 == 4, "");
    ck("find_kb 命中 MemTotal / MemAvailable(且不被 MemFree 前缀干扰)",
       find_kb(MEMINFO, "MemTotal", &memtotal) == 0 && memtotal == 16384000 &&
       find_kb(MEMINFO, "MemAvailable", &memavail) == 0 && memavail == 1228800, "");
    mem_pct = 100.0 * (double)(memtotal - memavail) / (double)memtotal;
    {
        char buf[64];
        snprintf(buf, sizeof buf, "%.2f%%", mem_pct);
        ck("内存利用率 = (MemTotal-MemAvailable)/MemTotal = 92.50%",
           mem_pct > 92.49 && mem_pct < 92.51, buf);
    }
    ck("parse_netdev: drop = rx_drop+rx_fifo = 49,errs = 0",
       parse_netdev(NETDEV, "eth0", &rxb, &txb, &rxdrop, &rxerr) == 0 &&
       rxdrop == 49 && rxerr == 0, "");
    net_pct = 100.0 * (double)(rxb + txb) / (12.5e9 * interval);
    {
        char buf[64];
        snprintf(buf, sizeof buf, "%.2f%%", net_pct);
        ck("网卡利用率 = 95e9 B / 10 s / 12.5 GB/s(100GbE) = 76.00%",
           net_pct > 75.99 && net_pct < 76.01, buf);
    }
    ck("parse_netdev 对不存在的接口返回 -1",
       parse_netdev(NETDEV, "eth9", &rxb, &txb, &rxdrop, &rxerr) == -1, "");
    ck("parse_diskstats 取到 tok3/7/12/13 = 140000/72000/9400/34000",
       parse_diskstats(DISKSTATS, "sda", &d1) == 0 && d1.read_ios == 140000 &&
       d1.write_ios == 72000 && d1.io_ms == 9400 && d1.weighted_ms == 34000, "");
    if (parse_diskstats(DISKSTATS, "sda", &d1) == 0) {
        char buf[80];
        util = 100.0 * (double)d1.io_ms / (interval * 1000.0);
        qu = (double)d1.weighted_ms / (interval * 1000.0);
        snprintf(buf, sizeof buf, "%%util=%.1f%%  avgqu-sz=%.2f", util, qu);
        ck("%util = 忙时间/墙钟 = 94%;avgqu-sz = 加权时间/墙钟 = 3.40 > 1(有排队)",
           util > 93.9 && util < 94.1 && qu > 3.39 && qu < 3.41, buf);
    }
    ck("parse_diskstats 对不存在的设备返回 -1",
       parse_diskstats(DISKSTATS, "sdz", &d0) == -1, "");
    ck("多设备文件的第二行(sdb)互不串味",
       parse_diskstats(DISKSTATS, "sdb", &d0) == 0 && d0.io_ms == 3, "");

    if (selftest) {
        printf("\n%s(失败 %d 项)\n", fails == 0 ? "全部通过" : "存在失败项", fails);
        return fails == 0 ? 0 : 1;
    }

    printf("\n== 读真实 /proc(非 Linux 一律记 '?',不猜值) ==\n");
    {
        static const char *paths[] = {"/proc/stat", "/proc/meminfo", "/proc/net/dev",
                                      "/proc/diskstats", "/proc/vmstat", "/proc/loadavg"};
        size_t i;
        for (i = 0; i < sizeof paths / sizeof paths[0]; i++) {
            FILE *f = fopen(paths[i], "r");
            printf("  %-18s: %s\n", paths[i], f ? "可读" : "?");
            if (f) {
                fclose(f);
            }
        }
    }
    {
        struct statvfs vfs;
        if (statvfs("/", &vfs) == 0 && vfs.f_blocks > 0) {
            printf("  Storage capacity  : %.2f%%(population 口径,statvfs)\n",
                   100.0 * (1.0 - (double)vfs.f_bavail / (double)vfs.f_blocks));
        } else {
            printf("  Storage capacity  : ?(statvfs 不可用)\n");
        }
    }
    return fails == 0 ? 0 : 1;
}
