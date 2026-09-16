/* use_parse.h — use_collect.c 的解析部分(文本级包含,不是独立翻译单元)
 *
 * 包含:struct cpu_times / struct dev_stats、/proc/stat、/proc/loadavg、
 *       /proc/meminfo、/proc/net/dev、/proc/diskstats 的文本解析。
 *       自检与 main 在 use_collect.c。
 *
 * 为什么用 #include 而不是拆成第二个 .c:本机没有 C 工具链,拆成两个翻译单元就
 * 必须同步改 static/原型,改错也编不出来、发现不了。文本包含让所有 static 定义
 * 仍留在同一个 TU 里,零链接风险,只是让单文件行数落到 300 行以内。
 *
 * 编译入口始终是 use_collect.c。
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

/* ---------- /proc/stat ---------- */
struct cpu_times {
    unsigned long long user, nice, system, idle, iowait, irq, softirq, steal;
};

static int parse_proc_stat(const char *text, struct cpu_times *t)
{
    char line[MAX_LINE];
    size_t n = 0;
    const char *p = text;
    while (*p && *p != '\n' && n < sizeof line - 1) {
        line[n++] = *p++;
    }
    line[n] = '\0';
    if (strncmp(line, "cpu ", 4) != 0) {
        return -1;               /* 只要汇总行,跳过 cpu0/cpu1… */
    }
    /* 字段序固定: user nice system idle iowait irq softirq steal [guest guest_nice] */
    if (sscanf(line + 4, "%llu %llu %llu %llu %llu %llu %llu %llu",
               &t->user, &t->nice, &t->system, &t->idle, &t->iowait,
               &t->irq, &t->softirq, &t->steal) != 8) {
        return -1;
    }
    return 0;
}

/* 官方口径: 忙 = us + nice + sy + irq + softirq + steal —— 刻意**不含** iowait 与 idle */
static double cpu_busy_pct(const struct cpu_times *a, const struct cpu_times *b)
{
    unsigned long long busy = (b->user - a->user) + (b->nice - a->nice) +
                              (b->system - a->system) + (b->irq - a->irq) +
                              (b->softirq - a->softirq) + (b->steal - a->steal);
    unsigned long long total = busy + (b->idle - a->idle) + (b->iowait - a->iowait);
    return total ? 100.0 * (double)busy / (double)total : 0.0;
}

/* ---------- /proc/loadavg: 1min 5min 15min running/total 最近PID ---------- */
static int parse_loadavg(const char *text, double *load1, int *running, int *total)
{
    return sscanf(text, "%lf %*f %*f %d/%d", load1, running, total) == 3 ? 0 : -1;
}

/* ---------- /proc/meminfo: 按「行首 key:」定位,避免子串误命中 ---------- */
static int find_kb(const char *text, const char *key, unsigned long long *out)
{
    size_t klen = strlen(key);
    const char *p = text;
    while ((p = strstr(p, key)) != NULL) {
        if ((p == text || p[-1] == '\n') && p[klen] == ':') {
            return sscanf(p + klen + 1, "%llu", out) == 1 ? 0 : -1;
        }
        p += klen;
    }
    return -1;
}

/* ---------- /proc/net/dev: "iface:" 后 16 列(rx 8 + tx 8) ---------- */
static int parse_netdev(const char *text, const char *iface,
                        unsigned long long *rxb, unsigned long long *txb,
                        unsigned long long *rxdrop, unsigned long long *rxerr)
{
    const char *p = strstr(text, iface);
    unsigned long long rxp, rxe, rxd, rxf;
    unsigned long long txp, txe, txd, txf;
    int n;
    if (p == NULL) {
        return -1;
    }
    p = strchr(p, ':');
    if (p == NULL) {
        return -1;
    }
    /* rx: bytes packets errs drop fifo frame compressed multicast
       tx: bytes packets errs drop fifo colls carrier compressed          */
    n = sscanf(p + 1, "%llu %llu %llu %llu %llu %*u %*u %*u "
                      "%llu %llu %llu %llu %llu %*u %*u %*u",
               rxb, &rxp, &rxe, &rxd, &rxf, txb, &txp, &txe, &txd, &txf);
    if (n < 10) {
        return -1;
    }
    /* 官方脚注 7: dropped 同时算 saturation 与 errors;fifo 溢出也是饱和信号 */
    *rxdrop = rxd + rxf;
    *rxerr = rxe;
    return 0;
}

/* ---------- /proc/diskstats ----------
 * 下标(tok[0] = major):
 *   0 major 1 minor 2 name 3 read_ios 4 read_merges 5 read_sectors 6 read_ms
 *   7 write_ios 8 write_merges 9 write_sectors 10 write_ms
 *   11 in_flight 12 io_ms(忙时间) 13 weighted_io_ms 14+ discard/flush…
 */
struct dev_stats {
    unsigned long long read_ios, write_ios, io_ms, weighted_ms;
};

static int parse_diskstats(const char *text, const char *dev, struct dev_stats *d)
{
    char line[MAX_LINE];
    const char *p = text;
    while (*p) {
        size_t n = 0;
        char *tok[24];
        char *q;
        int nt = 0;
        while (*p && *p != '\n' && n < sizeof line - 1) {
            line[n++] = *p++;
        }
        if (*p == '\n') {
            p++;
        }
        line[n] = '\0';
        if (n == 0) {
            continue;
        }
        q = line;
        while (nt < (int)(sizeof tok / sizeof tok[0])) {
            char *s = q;
            while (*s == ' ' || *s == '\t') {
                s++;
            }
            if (*s == '\0') {
                break;
            }
            tok[nt++] = s;
            while (*s && *s != ' ' && *s != '\t') {
                s++;
            }
            if (*s == '\0') {
                break;
            }
            *s = '\0';
            q = s + 1;
        }
        if (nt >= 14 && strcmp(tok[2], dev) == 0) {
            d->read_ios = strtoull(tok[3], NULL, 10);
            d->write_ios = strtoull(tok[7], NULL, 10);
            d->io_ms = strtoull(tok[12], NULL, 10);
            d->weighted_ms = strtoull(tok[13], NULL, 10);
            return 0;
        }
    }
    return -1;
}

