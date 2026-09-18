/* Loki 查询侧的解析与范围聚合 —— C 版实现头（第三个，与 core/storage 同属
 * 一个翻译单元，由 loki_demo.c 文本级包含；不要单独编译）。
 *
 * 与 Python/Go 版的差异：
 *   - 没有完整的 LogQL 解析器（那个在 Python/Go 版里各有一千多行），
 *     这里只保留范围聚合这一层，因为它能给出**可精确断言的数值**：
 *     线性插值分位数、总体方差、rate 按窗口秒数换算。
 *   - 「解析失败不丢行、只打 __error__」这条规则用 "k=v" 键值对代替 JSON
 *     解析器来演示 —— 演示的是规则本身，不是解析器实现。
 *   - 指标查询的门禁（只要窗口里残留 __error__ 就整条查询报错）单独成
 *     一个函数，因为它是「unwrap 后必须补 | __error__ = ""」的根因。
 */

#ifndef LOKI_QUERY_H
#define LOKI_QUERY_H

#include "loki_core.h"

/* 从 "k=v" 形式里取数值。取不到（键不存在，或值不是完整的一个数字）返回 -1，
 * 由调用方决定是打 __error__ 还是丢弃 —— 本实现一律**不丢行**。 */
static int extract_number(const char *line, const char *key, double *out) {
    size_t klen = strlen(key);
    const char *p = line;
    while ((p = strstr(p, key)) != NULL) {
        int at_start = (p == line) || isspace((unsigned char)p[-1]);
        if (at_start && p[klen] == '=') {
            char *end = NULL;
            double v = strtod(p + klen + 1, &end);
            if (end != p + klen + 1 && (end == NULL || *end == '\0' || isspace((unsigned char)*end))) {
                *out = v;
                return 0;
            }
            return -1; /* 值不是数字 → SampleExtractionErr 那一类 */
        }
        p += klen;
    }
    return -1;
}

/* 指标查询的门禁：窗口里只要残留一条 __error__ 就打不开。
 * 这就是 `| unwrap ...` 后面必须补 `| __error__ = ""` 的全部原因。 */
static int metric_query_allowed(const loki_entry *entries, int n) {
    for (int i = 0; i < n; i++) {
        if (entry_has_error(&entries[i])) {
            return 0;
        }
    }
    return 1;
}

static int cmp_double_asc(const void *a, const void *b) {
    double x = *(const double *)a;
    double y = *(const double *)b;
    return (x > y) - (x < y);
}

/* 分位数走**线性插值**：rank = φ·(N−1)，落在两个样本之间就插值。
 * 所以 [10,20,30,40] 的 0.5 分位是 25 —— 既不是 20 也不是 30。
 * 这是 Prometheus 口径，Loki 的 unwrap 分位聚合同口径。 */
static double prom_quantile(double phi, const double *values, int n) {
    if (n <= 0) {
        return NAN;
    }
    double *sorted = (double *)malloc(sizeof(double) * (size_t)n);
    if (sorted == NULL) {
        return NAN;
    }
    memcpy(sorted, values, sizeof(double) * (size_t)n);
    qsort(sorted, (size_t)n, sizeof(double), cmp_double_asc);
    double rank = phi * (double)(n - 1);
    int lo = (int)floor(rank);
    int hi = (lo + 1 < n) ? lo + 1 : lo;
    double out = sorted[lo] + (sorted[hi] - sorted[lo]) * (rank - (double)lo);
    free(sorted);
    return out;
}

/* stddev/stdvar 用**总体**方差（除以 N），不是样本方差（除以 N−1）。 */
static double population_stdvar(const double *values, int n) {
    if (n <= 0) {
        return NAN;
    }
    double sum = 0.0;
    for (int i = 0; i < n; i++) {
        sum += values[i];
    }
    double mean = sum / (double)n;
    double acc = 0.0;
    for (int i = 0; i < n; i++) {
        double d = values[i] - mean;
        acc += d * d;
    }
    return acc / (double)n;
}

/* 日志范围向量只数**行**，不碰行里的数值。 */
static double range_count_over_time(const loki_entry *entries, int n) {
    (void)entries; /* 行数即样本数，内容不参与 */
    return (double)n;
}

/* rate 是「窗口内样本数 / 窗口秒数」，不做采样点外推（那是 PromQL 的 rate）。 */
static double range_rate(const loki_entry *entries, int n, double window_s) {
    if (window_s <= 0.0) {
        return NAN;
    }
    return range_count_over_time(entries, n) / window_s;
}

/* unwrap 之后的数值范围向量走这里；count_over_time **不**接受这种输入。 */
static double range_sum_over_time(const double *values, int n) {
    double sum = 0.0;
    for (int i = 0; i < n; i++) {
        sum += values[i];
    }
    return sum;
}

#endif /* LOKI_QUERY_H */
