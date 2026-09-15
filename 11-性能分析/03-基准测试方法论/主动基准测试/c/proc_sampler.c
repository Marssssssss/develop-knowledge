/* proc_sampler.c — 主动基准测试的"运行中采样"工具侧(C 实现)。
 *
 * 对应 ../python/active_benchmarking_check.py 的输入: 该判定器消费的"多口径证据"正是
 * 本程序在一个基准运行期间采出来的。参考 Gregg 的做法(在基准还在跑时用 iostat/vmstat/
 * pidstat/perf 观察整个系统), 这里只用 /proc 与 /sys 纯文件接口实现最小采样器:
 *
 *   /proc/stat                 整机 CPU 时间 -> 忙碌比例与"其它进程占用"的粗估
 *   /proc/<pid>/stat           被测进程 utime/stime -> 进程占用的核数
 *   /proc/<pid>/status         Threads -> 是否单线程(陷阱 #4)
 *   /proc/<pid>/io             rchar(文件系统层读) / read_bytes(块设备层读) -> 陷阱 #6
 *   /proc/cpuinfo              cpu MHz -> 是否热降频
 *   /sys/fs/cgroup/.../cpu.max 配额 -> 是否被软件资源控制限流(陷阱 #2, cgroup v2)
 *
 * 输出 TSV: time_s proc_cores other_cpu_pct threads cpu_mhz proc_fs_MB proc_disk_MB
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic -o proc_sampler proc_sampler.c
 * 运行: ./proc_sampler <pid> [轮数] [间隔秒]
 */
#define _GNU_SOURCE

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAX_LINE 4096
#define CPU_FIELDS 8 /* user nice system idle iowait irq softirq steal */

struct cpu_times {
    unsigned long long f[CPU_FIELDS];
    int valid;
};

struct task_info {
    unsigned long long utime;
    unsigned long long stime;
    unsigned long long threads;
    unsigned long long rchar;      /* 文件系统层读(含 page cache) */
    unsigned long long read_bytes; /* 块设备层读(真实磁盘 I/O) */
};

static int read_cpu_times(int cpu_index, struct cpu_times *out)
{
    FILE *fp;
    char line[MAX_LINE];
    char want[32];

    memset(out, 0, sizeof(*out));
    if (cpu_index < 0) {
        snprintf(want, sizeof(want), "cpu ");
    } else {
        snprintf(want, sizeof(want), "cpu%d ", cpu_index);
    }
    fp = fopen("/proc/stat", "r");
    if (fp == NULL) {
        return -1;
    }
    while (fgets(line, sizeof(line), fp) != NULL) {
        if (strncmp(line, want, strlen(want)) != 0) {
            continue;
        }
        /* 取前 8 个字段; 内核后续新增字段(guest 等)忽略 */
        if (sscanf(line + strlen(want), "%llu %llu %llu %llu %llu %llu %llu %llu",
                   &out->f[0], &out->f[1], &out->f[2], &out->f[3],
                   &out->f[4], &out->f[5], &out->f[6], &out->f[7]) < 4) {
            fclose(fp);
            return -1;
        }
        out->valid = 1;
        fclose(fp);
        return 0;
    }
    fclose(fp);
    return -1;
}

/* busy / (busy + idle + iowait): 区间内的忙碌比例 */
static double cpu_busy(const struct cpu_times *a, const struct cpu_times *b)
{
    unsigned long long busy = 0, idle = 0;
    int i;
    if (!a->valid || !b->valid) {
        return -1.0;
    }
    for (i = 0; i < CPU_FIELDS; ++i) {
        unsigned long long d = b->f[i] - a->f[i];
        if (i == 3 || i == 4) {
            idle += d;
        } else {
            busy += d;
        }
    }
    if (busy + idle == 0) {
        return 0.0;
    }
    return (double)busy / (double)(busy + idle);
}

static int read_task_info(long pid, struct task_info *info)
{
    char path[128], line[MAX_LINE], *rest, *end;
    FILE *fp;
    int i;
    const int utime_offset = 11; /* 最后一个 ')' 之后: state(0) ... utime(11) stime(12) */

    memset(info, 0, sizeof(*info));
    /* /proc/<pid>/stat: comm 字段可能含空格与 ')', 必须用最后一个 ')' 定位后续字段 */
    snprintf(path, sizeof(path), "/proc/%ld/stat", pid);
    fp = fopen(path, "r");
    if (fp == NULL) {
        return -1;
    }
    if (fgets(line, sizeof(line), fp) == NULL) {
        fclose(fp);
        return -1;
    }
    fclose(fp);
    end = strrchr(line, ')');
    if (end == NULL) {
        return -1;
    }
    rest = end + 2; /* 跳过 ") " */
    for (i = 0; i <= utime_offset + 1; ++i) {
        char *next = NULL;
        unsigned long long value = strtoull(rest, &next, 10);
        if (next == rest) {
            return -1;
        }
        rest = next;
        if (i == utime_offset) {
            info->utime = value;
        } else if (i == utime_offset + 1) {
            info->stime = value;
        }
    }
    snprintf(path, sizeof(path), "/proc/%ld/status", pid);
    fp = fopen(path, "r");
    if (fp != NULL) {
        while (fgets(line, sizeof(line), fp) != NULL) {
            if (strncmp(line, "Threads:", 8) == 0) {
                info->threads = strtoull(line + 8, NULL, 10);
                break;
            }
        }
        fclose(fp);
    }
    snprintf(path, sizeof(path), "/proc/%ld/io", pid);
    fp = fopen(path, "r");
    if (fp != NULL) {
        while (fgets(line, sizeof(line), fp) != NULL) {
            if (strncmp(line, "rchar:", 6) == 0) {
                info->rchar = strtoull(line + 6, NULL, 10);
            } else if (strncmp(line, "read_bytes:", 11) == 0) {
                info->read_bytes = strtoull(line + 11, NULL, 10);
            }
        }
        fclose(fp);
    }
    return 0;
}

static double read_freq_mhz(void)
{
    FILE *fp = fopen("/proc/cpuinfo", "r");
    char line[MAX_LINE];
    double mhz = -1.0;

    if (fp == NULL) {
        return -1.0;
    }
    while (fgets(line, sizeof(line), fp) != NULL) {
        char *colon = strchr(line, ':');
        if (colon != NULL && strstr(line, "cpu MHz") != NULL) {
            mhz = strtod(colon + 1, NULL);
            break;
        }
    }
    fclose(fp);
    return mhz;
}

/* cgroup v2 配额: /proc/<pid>/cgroup 形如 "0::/path"; <mount>/cpu.max = "max 100000" 或 "20000 100000" */
static double read_cgroup_quota_cores(long pid)
{
    FILE *fp;
    char line[MAX_LINE], path[512], buf[128];
    char *slash;
    double quota = -1.0;

    snprintf(path, sizeof(path), "/proc/%ld/cgroup", pid);
    fp = fopen(path, "r");
    if (fp == NULL) {
        return -1.0;
    }
    if (fgets(line, sizeof(line), fp) == NULL) {
        fclose(fp);
        return -1.0;
    }
    fclose(fp);
    slash = strchr(line, '/');
    if (slash == NULL) {
        return -1.0;
    }
    slash[strcspn(slash, "\n")] = '\0';
    snprintf(path, sizeof(path), "/sys/fs/cgroup%s/cpu.max", slash);
    fp = fopen(path, "r");
    if (fp == NULL) {
        return -1.0;
    }
    if (fgets(buf, sizeof(buf), fp) != NULL && strncmp(buf, "max", 3) != 0) {
        double q = strtod(buf, NULL);
        double period = 100000.0;
        char *space = strchr(buf, ' ');
        if (space != NULL) {
            period = strtod(space + 1, NULL);
        }
        if (period > 0) {
            quota = q / period;
        }
    }
    fclose(fp);
    return quota;
}

int main(int argc, char **argv)
{
    long pid;
    int rounds = 10;
    double interval = 1.0;
    long hz, cores;
    int i;
    struct cpu_times prev_total, cur_total;
    struct task_info prev_task, cur_task;
    double quota;

    if (argc < 2) {
        fprintf(stderr, "usage: %s <pid> [rounds] [interval_sec]\n", argv[0]);
        return 2;
    }
    pid = strtol(argv[1], NULL, 10);
    if (argc >= 3) {
        rounds = (int)strtol(argv[2], NULL, 10);
    }
    if (argc >= 4) {
        interval = strtod(argv[3], NULL);
    }
    if (pid <= 0 || rounds <= 0 || interval <= 0.0) {
        fprintf(stderr, "invalid arguments\n");
        return 2;
    }
    hz = sysconf(_SC_CLK_TCK);
    if (hz <= 0) {
        hz = 100;
    }
    cores = sysconf(_SC_NPROCESSORS_ONLN);
    if (cores <= 0) {
        cores = 1;
    }
    quota = read_cgroup_quota_cores(pid);

    if (read_cpu_times(-1, &prev_total) != 0 || read_task_info(pid, &prev_task) != 0) {
        fprintf(stderr, "cannot read /proc for pid %ld: %s\n", pid, strerror(errno));
        return 1;
    }

    printf("# pid=%ld clk_tck=%ld online_cores=%ld cgroup_quota_cores=%.2f\n",
           pid, hz, cores, quota);
    printf("time_s\tproc_cores\tother_cpu_pct\tthreads\tcpu_mhz\tproc_fs_MB\tproc_disk_MB\n");

    for (i = 0; i < rounds; ++i) {
        double proc_cores, total_util, other, fs_mb, disk_mb;

        usleep((useconds_t)(interval * 1000000.0));
        if (read_cpu_times(-1, &cur_total) != 0 || read_task_info(pid, &cur_task) != 0) {
            fprintf(stderr, "sampling failed at round %d\n", i);
            return 1;
        }
        /* 进程占用核数 = (utime + stime) 增量 / 墙钟时间 */
        proc_cores = ((double)(cur_task.utime - prev_task.utime) / (double)hz +
                      (double)(cur_task.stime - prev_task.stime) / (double)hz) / interval;
        total_util = cpu_busy(&prev_total, &cur_total);
        other = total_util - proc_cores / (double)cores; /* 其它进程占用整机算力的粗估 */
        if (other < 0.0) {
            other = 0.0;
        }
        fs_mb = (double)(cur_task.rchar - prev_task.rchar) / (1024.0 * 1024.0);
        disk_mb = (double)(cur_task.read_bytes - prev_task.read_bytes) / (1024.0 * 1024.0);

        printf("%.1f\t%.2f\t%.1f\t%llu\t%.0f\t%.3f\t%.3f\n",
               (i + 1) * interval, proc_cores, other * 100.0,
               cur_task.threads, read_freq_mhz(), fs_mb, disk_mb);

        prev_total = cur_total;
        prev_task = cur_task;
    }
    return 0;
}
