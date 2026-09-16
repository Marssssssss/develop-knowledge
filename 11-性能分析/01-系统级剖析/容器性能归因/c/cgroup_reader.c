/* cgroup_reader.c — cgroup v2 指标读取与归因(纯 C,只依赖 libc)
 *
 * 读的四个文件及其归因用途:
 *   cpu.stat        8 个字段:恒定 3(usage/user/system_usec,含后代)
 *                   + CFS 带宽 5(nr_periods/nr_throttled/throttled_usec/nr_bursts/burst_usec)
 *                   —— 后 5 个是**非层级**的,只算本 cgroup 自身 cap 造成的节流
 *   cpu.stat.local  本 cgroup runqueue 的**实际**节流时间,可能含祖先 cap 的牵连
 *   cpu.max         "<$MAX> <$PERIOD>",max 表示不限
 *   memory.events   层级式(low/high/max/oom/oom_kill/oom_group_kill);.local 才是本层
 *   io.stat         按 $MAJ:$MIN 分行的 rbytes/wbytes/rios/wios/dbytes/dios
 *
 * 解析函数收文本不收路径,故既能读 /sys/fs/cgroup,也能用内嵌样本自检。
 * 编译: gcc -O2 -Wall -Wextra -pedantic cgroup_reader.c -o cgroup_reader
 * 运行: ./cgroup_reader --selftest             # 自检解析器
 *       ./cgroup_reader /sys/fs/cgroup/xxx     # 读真实 cgroup
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define MAX_LINE 4096

/* ---------- 通用: "key value" 逐行取值 ---------- */
static long long flat_get(const char *text, const char *key, long long dflt)
{
    size_t klen = strlen(key);
    const char *p = text;
    while ((p = strstr(p, key)) != NULL) {
        if ((p == text || p[-1] == '\n') && p[klen] == ' ') {
            return strtoll(p + klen + 1, NULL, 10);
        }
        p += klen;
    }
    return dflt;
}

/* ---------- cpu.max: 返回 CPU 上限;*period_us 回填周期;返回 -1 表示不限 ---------- */
static double parse_cpu_max(const char *text, long long *period_us)
{
    char first[32];
    long long period = 100000;
    if (sscanf(text, "%31s %lld", first, &period) < 1) {
        return -1;
    }
    *period_us = period;
    if (strcmp(first, "max") == 0) {
        return -1;
    }
    return (double)strtoll(first, NULL, 10) / (double)period;
}

/* ---------- io.stat: 找某个 MAJ:MIN 的嵌套键 ---------- */
static long long io_stat_get(const char *text, const char *dev, const char *key)
{
    char line[MAX_LINE];
    const char *p = text;
    char pat[64];
    while (*p) {
        size_t n = 0;
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
        if (strncmp(line, dev, strlen(dev)) != 0 || line[strlen(dev)] != ' ') {
            continue;
        }
        snprintf(pat, sizeof pat, "%s=", key);
        {
            const char *q = strstr(line, pat);
            if (q != NULL) {
                return strtoll(q + strlen(pat), NULL, 10);
            }
        }
    }
    return -1;
}

/* ---------- 归因 ---------- */
enum verdict {
    V_CAP_THROTTLED = 0,
    V_ANCESTOR_THROTTLED,
    V_HOST_CONTENDED,
    V_APP_QUEUED,
    V_CPU_OK
};

static const char *verdict_name(enum verdict v)
{
    switch (v) {
    case V_CAP_THROTTLED:      return "被自己的 cpu.max 硬限流";
    case V_ANCESTOR_THROTTLED: return "被祖先 cgroup 的带宽限制牵连";
    case V_HOST_CONTENDED:     return "宿主/系统级争用(不是容器的配额问题)";
    case V_APP_QUEUED:         return "容器在等下游(应用自身排队,不是 CPU 不够)";
    default:                   return "CPU 侧未见瓶颈";
    }
}

/* 排除法:自己的 cap -> 祖先的 cap -> 系统争用 -> 下游 */
static enum verdict diagnose_cpu(long long own, long long local, long long usage_delta,
                                 double press_some)
{
    if (own > 0 && usage_delta > 0 && (double)own / (double)usage_delta > 0.05) {
        return V_CAP_THROTTLED;
    }
    if (local > 0 && own == 0) {
        return V_ANCESTOR_THROTTLED;
    }
    if (press_some > 10.0) {
        return V_HOST_CONTENDED;
    }
    if (press_some <= 1.0) {
        return V_APP_QUEUED;
    }
    return V_CPU_OK;
}

/* official: 上限 = 100% x shares/总忙份额(bursting);保底 = 100% x shares/总分配份额 */
static void shares_limits(long long shares, long long alloc, long long busy,
                          double *hi, double *lo)
{
    *hi = busy > 0 ? 100.0 * (double)shares / (double)busy : 0.0;
    *lo = alloc > 0 ? 100.0 * (double)shares / (double)alloc : 0.0;
}

static int fails = 0;

static void ck(const char *label, int cond, const char *detail)
{
    printf("  [%s] %s%s%s\n", cond ? "PASS" : "FAIL", label,
           (detail && *detail) ? "  <- " : "", detail ? detail : "");
    if (!cond) {
        fails++;
    }
}

static const char *CPU_STAT =
    "usage_usec 8000000\nuser_usec 5000000\nsystem_usec 3000000\n"
    "nr_periods 100\nnr_throttled 0\nthrottled_usec 0\nnr_bursts 2\nburst_usec 1500\n";
static const char *CPU_LOCAL = "throttled_usec 1800000\n";
static const char *CPU_MAX_TXT = "400000 100000\n";
static const char *PRESSURE =
    "some avg10=0.60 avg60=1.20 avg300=2.40 total=987654\n"
    "full avg10=0.20 avg60=0.40 avg300=0.80 total=123456\n";
static const char *MEM_EVENTS =
    "low 0\nhigh 128\nmax 4\noom 2\noom_kill 1\noom_group_kill 0\n";
static const char *MEM_LOCAL =
    "low 0\nhigh 12\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n";
static const char *IO_STAT =
    "8:16 rbytes=1459200 wbytes=314773504 rios=192 wios=353 dbytes=0 dios=0\n"
    "8:0 rbytes=90430464 wbytes=299008000 rios=8950 wios=1252 dbytes=50331648 dios=3021\n";

static void run_selftest(void)
{
    long long period = 0;
    double quota = parse_cpu_max(CPU_MAX_TXT, &period);
    ck("cpu.stat 8 字段全部可读(恒定 3 + CFS 带宽 5)",
       flat_get(CPU_STAT, "usage_usec", -1) == 8000000 &&
       flat_get(CPU_STAT, "nr_bursts", -1) == 2 &&
       flat_get(CPU_STAT, "user_usec", -1) == 5000000 &&
       flat_get(CPU_STAT, "system_usec", -1) == 3000000 &&
       flat_get(CPU_STAT, "nr_periods", -1) == 100 &&
       flat_get(CPU_STAT, "nr_throttled", -1) == 0 &&
       flat_get(CPU_STAT, "throttled_usec", -1) == 0 &&
       flat_get(CPU_STAT, "burst_usec", -1) == 1500, "");
    ck("cpu.max '400000 100000' = 4 CPU / 100 ms", quota == 4.0 && period == 100000, "");
    ck("cpu.max 'max' 表示不限(返回 -1)",
       parse_cpu_max("max 100000\n", &period) == -1, "");
    ck("cpu.stat.local 的 throttled_usec 单独可读",
       flat_get(CPU_LOCAL, "throttled_usec", -1) == 1800000, "");
    ck("反向诊断: 自身 0 + 祖先 1800000 -> 被祖先带宽牵连",
       diagnose_cpu(0, 1800000, 8000000, 0.60) == V_ANCESTOR_THROTTLED, "");
    ck("反向诊断: 自身占比 25% -> 被自己 cap 限流",
       diagnose_cpu(2000000, 1800000, 8000000, 0.10) == V_CAP_THROTTLED, "");
    ck("反向诊断: 自身 1.25% 未过 5% 且 PSI 42% -> 宿主争用",
       diagnose_cpu(100000, 0, 8000000, 42.0) == V_HOST_CONTENDED, "");
    ck("反向诊断: 全部为 0 且 PSI 0.2% -> 转向下游",
       diagnose_cpu(0, 0, 8000000, 0.2) == V_APP_QUEUED, "");
    ck("usage_delta=0 时不除零",
       diagnose_cpu(5, 0, 0, 0.2) == V_APP_QUEUED, "");
    {
        double hi = 0, lo = 0;
        shares_limits(1024, 4096, 2048, &hi, &lo);
        ck("shares 双公式: 上限 50%(busy 口径) / 保底 25%(alloc 口径)",
           hi > 49.99 && hi < 50.01 && lo > 24.99 && lo < 25.01, "");
    }
    ck("memory.events 层级 high=128 > local high=12",
       flat_get(MEM_EVENTS, "high", -1) == 128 && flat_get(MEM_LOCAL, "high", -1) == 12, "");
    ck("memory.events.oom_kill=1 / local=0 -> 被杀的是后代",
       flat_get(MEM_EVENTS, "oom_kill", -1) == 1 && flat_get(MEM_LOCAL, "oom_kill", -1) == 0, "");
    ck("memory.events.max=4 > local=0 -> 顶到上限的是后代",
       flat_get(MEM_EVENTS, "max", -1) == 4 && flat_get(MEM_LOCAL, "max", -1) == 0, "");
    ck("io.stat 按设备定位: 8:16 wbytes=314773504 / 8:0 dbytes=50331648",
       io_stat_get(IO_STAT, "8:16", "wbytes") == 314773504 &&
       io_stat_get(IO_STAT, "8:16", "rios") == 192 &&
       io_stat_get(IO_STAT, "8:0", "dbytes") == 50331648, "");
    ck("io.stat 无此设备时返回 -1", io_stat_get(IO_STAT, "9:9", "wbytes") == -1, "");
    ck("io.stat 的 '8:1' 不会误命中 '8:16' 行",
       io_stat_get(IO_STAT, "8:1", "wbytes") == -1, "");
    ck("cpu.pressure 的 some/full 两行都在容器级存在",
       strstr(PRESSURE, "some ") == PRESSURE && strstr(PRESSURE, "\nfull ") != NULL, "");
}

static void read_real(const char *cg)
{
    static const char *files[] = {"cpu.stat", "cpu.stat.local", "cpu.max", "cpu.pressure",
                                  "memory.current", "memory.max", "memory.high",
                                  "memory.events", "memory.events.local", "io.stat",
                                  "pids.current", "pids.max", "pids.events"};
    size_t i;
    char path[512];
    for (i = 0; i < sizeof files / sizeof files[0]; i++) {
        FILE *f;
        snprintf(path, sizeof path, "%s/%s", cg, files[i]);
        f = fopen(path, "r");
        printf("  %-22s: %s\n", files[i], f ? "可读" : "?");
        if (f) {
            char buf[MAX_LINE];
            if (fgets(buf, sizeof buf, f) != NULL) {
                buf[strcspn(buf, "\n")] = '\0';
                printf("      %s\n", buf);
            }
            fclose(f);
        }
    }
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--selftest") == 0) {
        printf("== cgroup v2 解析器自检(内嵌样本) ==\n");
        run_selftest();
        printf("\n%s(失败 %d 项)\n", fails == 0 ? "全部通过" : "存在失败项", fails);
        return fails == 0 ? 0 : 1;
    }
    if (argc > 1) {
        printf("== 读 %s ==\n", argv[1]);
        read_real(argv[1]);
        return 0;
    }
    printf("用法: %s --selftest | %s <cgroup 目录>\n", argv[0], argv[0]);
    printf("容器里通常是 /sys/fs/cgroup(自己的 cgroup),宿主上是 /sys/fs/cgroup/<path>\n");
    printf("注意: 容器内 /proc/meminfo、/proc/stat 报的是**宿主**数据,归因必须看 cgroup 文件\n");
    return 0;
}
