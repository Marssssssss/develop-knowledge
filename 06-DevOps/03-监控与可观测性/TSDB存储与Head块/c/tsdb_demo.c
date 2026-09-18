/* Prometheus TSDB 存储模型 —— C 版自检。
 *
 * 编译：gcc -O2 -Wall -Wextra -pedantic -lm tsdb_demo.c -o tsdb_demo
 *
 * 覆盖：ULID 位布局与字典序 / 2h 块与 512MB chunk 段 / 压缩跨度上限与阶梯 /
 *       容量公式 / WAL 段数下限与重放分片 / 倒排索引 merge-join。
 */

#include <stdio.h>
#include <string.h>

#define BLOCK_DURATION 7200.0
#define CHUNK_SEGMENT_SIZE (512.0 * 1024 * 1024)
#define WAL_SEGMENT_SIZE (128.0 * 1024 * 1024)
#define MIN_WAL_SEGMENTS 3
#define DEFAULT_RETENTION (15.0 * 86400)
#define MAX_COMPACTED_SPAN (31.0 * 86400)
#define ULID_LEN 26

static const char CROCKFORD[] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

static int g_pass = 0;
static int g_fail = 0;

static void check(const char *label, int cond)
{
    if (cond) {
        g_pass++;
    } else {
        g_fail++;
        printf("FAIL  %s\n", label);
    }
}

static int approx(double a, double b) { return (a - b) < 1e-9 && (b - a) < 1e-9; }

/* ---------------------------------------------------------- ULID
 * 128 位 = 48 位毫秒时间戳 | 80 位随机数，编码成 26 个 Base32 字符
 * (26*5 = 130 位，最高 2 位恒为 0)。
 */
static void to_words(unsigned long long ts, unsigned long long rnd80,
                     unsigned long long *hi, unsigned long long *lo)
{
    *hi = (ts << 16) | (rnd80 >> 64);
    *lo = rnd80;
}

static unsigned int bits5(unsigned long long hi, unsigned long long lo, int p)
{
    if (p >= 64) return (unsigned int)((hi >> (p - 64)) & 0x1F);
    if (p + 5 <= 64) return (unsigned int)((lo >> p) & 0x1F);
    {
        int n = p + 5 - 64;
        unsigned long long high = hi & ((1ULL << n) - 1);
        return (unsigned int)((high << (64 - p)) | (lo >> p));
    }
}

static void set5(unsigned long long *hi, unsigned long long *lo, int p,
                 unsigned int v)
{
    if (p >= 64) {
        *hi |= (unsigned long long)(v & 0x1F) << (p - 64);
        return;
    }
    if (p + 5 <= 64) {
        *lo |= (unsigned long long)(v & 0x1F) << p;
        return;
    }
    {
        int n = p + 5 - 64;
        *hi |= ((unsigned long long)v >> (64 - p)) & ((1ULL << n) - 1);
        *lo |= ((unsigned long long)v & ((1ULL << (64 - p)) - 1)) << p;
    }
}

static void ulid_encode(unsigned long long ts, unsigned long long rnd80, char *out)
{
    unsigned long long hi, lo;
    int k;
    to_words(ts, rnd80, &hi, &lo);
    for (k = 0; k < ULID_LEN; k++) {
        out[k] = CROCKFORD[bits5(hi, lo, 125 - 5 * k)];
    }
    out[ULID_LEN] = '\0';
}

static int ulid_decode(const char *s, unsigned long long *ts,
                       unsigned long long *rnd80)
{
    unsigned long long hi = 0, lo = 0;
    int k, i;
    if (strlen(s) != ULID_LEN) return 0;
    for (k = 0; k < ULID_LEN; k++) {
        int idx = -1;
        for (i = 0; i < 32; i++) {
            if (CROCKFORD[i] == s[k]) { idx = i; break; }
        }
        if (idx < 0) return 0;
        set5(&hi, &lo, 125 - 5 * k, (unsigned int)idx);
    }
    *ts = hi >> 16;
    *rnd80 = ((hi & 0xFFFF) << 64) | lo;
    return 1;
}

/* ------------------------------------------------------ 压缩计划 */
static double max_compacted_span(double retention_s)
{
    double by_pct = 0.10 * retention_s;
    return by_pct < MAX_COMPACTED_SPAN ? by_pct : MAX_COMPACTED_SPAN;
}

static int compaction_ladder(double retention_s, double *out, int cap)
{
    double limit = max_compacted_span(retention_s);
    double d = BLOCK_DURATION;
    int n = 0;
    while (d <= limit + 1e-9 && n < cap) {
        out[n++] = d;
        d *= 2.0;
    }
    return n;
}

/* ---------------------------------------------------------- 容量 */
static double needed_disk_space(double retention_s, double samples_per_s,
                                double bytes_per_sample)
{
    return retention_s * samples_per_s * bytes_per_sample;
}

static int wal_segments_for(double seconds, double samples_per_s,
                            double wal_bytes_per_sample)
{
    double raw = seconds * samples_per_s * wal_bytes_per_sample;
    int n = (int)(raw / WAL_SEGMENT_SIZE);
    if ((double)n * WAL_SEGMENT_SIZE < raw) n++;   /* ceil */
    return n < MIN_WAL_SEGMENTS ? MIN_WAL_SEGMENTS : n;
}

/* ---------------------------------------------- 倒排索引 merge-join */
static int merge_join(const int *a, int na, const int *b, int nb, int *out,
                      int *comparisons)
{
    int i = 0, j = 0, n = 0;
    *comparisons = 0;
    while (i < na && j < nb) {
        (*comparisons)++;
        if (a[i] == b[j]) { out[n++] = a[i]; i++; j++; }
        else if (a[i] < b[j]) i++;
        else j++;
    }
    return n;
}

int main(void)
{
    char ulid[ULID_LEN + 1];
    unsigned long long ts, rnd;

    /* ---- A ULID ---- */
    ulid_encode(1625000000000ULL, 0ULL, ulid);
    check("A ULID 为 26 字符", strlen(ulid) == 26);
    check("A 编解码往返一致",
          ulid_decode(ulid, &ts, &rnd) && ts == 1625000000000ULL && rnd == 0ULL);
    ulid_encode((1ULL << 48) - 1, (1ULL << 63) - 1, ulid);
    check("A 最高位时间戳首字符为 7（130 位中高 2 位留空）", ulid[0] == '7');
    check("A 非法长度被拒绝", !ulid_decode("short", &ts, &rnd));
    {
        char lo_ulid[ULID_LEN + 1], hi_ulid[ULID_LEN + 1];
        ulid_encode(1625000000000ULL, 0ULL, lo_ulid);
        ulid_encode(1625000000001ULL, 0ULL, hi_ulid);
        check("A 时间戳大者 ULID 字典序也大（块名可按名排序）",
              strcmp(lo_ulid, hi_ulid) < 0);
        ulid_encode(1625000000000ULL, 1ULL, hi_ulid);
        check("A 同毫秒内随机数破解并列",
              strcmp(lo_ulid, hi_ulid) < 0);
    }

    /* ---- B 常量与块 ---- */
    check("B 块时间窗 = 2h", approx(BLOCK_DURATION, 7200.0));
    check("B chunk 段 512MB", approx(CHUNK_SEGMENT_SIZE, 536870912.0));
    check("B WAL 段 128MB 且至少 3 段 = 384MiB",
          approx(WAL_SEGMENT_SIZE, 134217728.0) && MIN_WAL_SEGMENTS == 3
          && approx(MIN_WAL_SEGMENTS * WAL_SEGMENT_SIZE, 384.0 * 1024 * 1024));
    check("B 默认保留 15d", approx(DEFAULT_RETENTION, 1296000.0));

    /* ---- C 压缩 ---- */
    check("C 保留 15d → 跨度上限 36h", approx(max_compacted_span(15 * 86400), 36 * 3600));
    check("C 保留 90d → 上限 9d", approx(max_compacted_span(90 * 86400), 9 * 86400));
    check("C 保留 365d → 取顶 31d",
          approx(max_compacted_span(365 * 86400), MAX_COMPACTED_SPAN));
    {
        double ladder[16];
        int n = compaction_ladder(15 * 86400, ladder, 16);
        check("C 15d 阶梯共 5 级", n == 5);
        check("C 阶梯 = 2/4/8/16/32h",
              approx(ladder[0], 7200) && approx(ladder[1], 14400)
              && approx(ladder[2], 28800) && approx(ladder[3], 57600)
              && approx(ladder[4], 115200));
        check("C 末级 ≤ 上限 < 末级×2",
              ladder[n - 1] <= max_compacted_span(15 * 86400)
              && max_compacted_span(15 * 86400) < ladder[n - 1] * 2);
        check("C 保留 4h 时上限 0.4h < 基础块 2h → 阶梯为空",
              compaction_ladder(4 * 3600, ladder, 16) == 0);
        check("C 365d 阶梯 9 级", compaction_ladder(365 * 86400, ladder, 16) == 9);
    }

    /* ---- D 容量 ---- */
    check("D 15d/10 万样本每秒/1.5B → 194.4 GB",
          approx(needed_disk_space(15 * 86400, 100000, 1.5), 1.944e11));
    check("D 1B → 129.6 GB", approx(needed_disk_space(15 * 86400, 100000, 1.0), 1.296e11));
    check("D 2B → 259.2 GB", approx(needed_disk_space(15 * 86400, 100000, 2.0), 2.592e11));
    check("D 上下界比恰为 2",
          approx(needed_disk_space(1296000, 1, 2.0) / needed_disk_space(1296000, 1, 1.0), 2.0));

    /* ---- E WAL ---- */
    check("E 2h 原始数据（10 万/s、每样本 2B）→ 11 段",
          wal_segments_for(2 * 3600, 100000, 2.0) == 11);
    check("E 1h 数据 → 6 段", wal_segments_for(3600, 100000, 2.0) == 6);
    check("E 空负载回落到 3 段下限", wal_segments_for(0, 0, 2.0) == 3);
    {
        int refs[10], shard[4] = {0, 0, 0, 0}, i;
        for (i = 0; i < 10; i++) { refs[i] = i; shard[i % 4]++; }
        check("E 4 个 worker 分别拿 3/3/2/2 条",
              shard[0] == 3 && shard[1] == 3 && shard[2] == 2 && shard[3] == 2);
        check("E 分片是划分：总数守恒", shard[0] + shard[1] + shard[2] + shard[3] == 10);
    }

    /* ---- G 倒排索引 ---- */
    {
        const int get_list[5] = {1, 5, 12, 47, 103};
        const int api_list[4] = {1, 2, 5, 8};
        int out[8], comps, n = merge_join(api_list, 4, get_list, 5, out, &comps);
        check("G 交集 = [1, 5]", n == 2 && out[0] == 1 && out[1] == 5);
        check("G merge-join 比较 4 次 ≤ n+m = 9", comps == 4 && comps <= 9);
        {
            const int post[1] = {99};
            int comps2;
            n = merge_join(post, 1, api_list, 4, out, &comps2);
            check("G 无交集 → 空结果", n == 0);
        }
    }

    printf("------------------------------------------------------------\n");
    if (g_fail > 0) {
        printf("断言失败 %d 项 / 通过 %d 项\n", g_fail, g_pass);
        return 1;
    }
    printf("全部 %d 项断言通过\n", g_pass);
    return 0;
}
