/* Loki 条目模型、通配匹配、流选择器与行过滤、单位换算。
 *
 * 这是**实现头**（implementation header）：里面是 `static` 函数定义，由
 * loki_demo.c 做**文本级包含**，于是所有实现与 main() 处在同一个翻译单元里 ——
 * 不需要改构建命令、不需要管 static 的可见性，也不必维护第二份 .c 及其头文件。
 * 代价是它只能被包含一次（构建命令里不要把它单独编译）。
 *
 * 与 Python/Go 版的差异（README「各语言实现范围」已列）：
 *   - 没有正则库。流选择器的 =~ 用 `*` / `?` 通配的**完全匹配**代替，
 *     行过滤的 |~ 用 strstr **子串搜索**代替。要演示的锚定语义差异
 *     在这两者之间完整保留：同一个 ~ 在流选择器里锚定，在行过滤里不锚定。
 *   - 没有 JSON 库。解析器那一层用 "k=v" 键值对代替，演示的是
 *     「解析失败不丢行、只打错误标签」这条规则本身。
 *
 * 本文件不包含任何 I/O；printf 只在 demo 里出现。
 */

#ifndef LOKI_CORE_H
#define LOKI_CORE_H

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* 容量取舍：名称上限 1088 是为了让「标签名正好 1024 通过 / 1025 被拒」
 * 这条边界能被真正测出来（截断会让两者变得不可区分）；标签数上限 32
 * 高于默认限额 30，好让「恰好 30 通过 / 31 被拒」也能测。
 * 代价是单个 loki_entry 约 39 KB —— 所以 demo 里的大实例一律声明成
 * `static`（落到 BSS）而不是放在栈上，避免逼近 Windows 的 1 MB 默认栈。 */
#define LOKI_MAX_LABELS 32
#define LOKI_MAX_NAME 1088
#define LOKI_MAX_VALUE 128
#define LOKI_LINE_MAX 256

/* 官方文档正文把管线错误标签写作 error，实际实现用 __error__。 */
#define LOKI_ERROR_LABEL "__error__"
#define LOKI_ERR_JSON "JSONParserErr"
#define LOKI_ERR_SAMPLE "SampleExtractionErr"

/* ---------------------------------------------------------------- 条目 */

typedef struct {
    char name[LOKI_MAX_NAME];
    char value[LOKI_MAX_VALUE];
} loki_label;

typedef struct {
    loki_label labels[LOKI_MAX_LABELS];
    int nlabels;
    long long ts_ns;
    char line[LOKI_LINE_MAX];
    double value;
    int has_value;
} loki_entry;

static void entry_init(loki_entry *e, long long ts_ns, const char *line) {
    memset(e, 0, sizeof(*e));
    e->ts_ns = ts_ns;
    snprintf(e->line, sizeof(e->line), "%s", line);
}

/* 返回 NULL 表示该标签不存在（调用方按需决定是否当空串）。 */
static const char *entry_get(const loki_entry *e, const char *name) {
    for (int i = 0; i < e->nlabels; i++) {
        if (strcmp(e->labels[i].name, name) == 0) {
            return e->labels[i].value;
        }
    }
    return NULL;
}

/* 缺失标签按空字符串处理 —— 与 Prometheus / Loki 一致。
 * 这不是「取不到就不匹配」：正因为它当空串，{env!="prod"} 才会命中
 * 根本没有 env 标签的流。 */
static const char *entry_get_or_empty(const loki_entry *e, const char *name) {
    const char *v = entry_get(e, name);
    return v ? v : "";
}

static int entry_set(loki_entry *e, const char *name, const char *value) {
    for (int i = 0; i < e->nlabels; i++) {
        if (strcmp(e->labels[i].name, name) == 0) {
            snprintf(e->labels[i].value, LOKI_MAX_VALUE, "%s", value);
            return 0;
        }
    }
    if (e->nlabels >= LOKI_MAX_LABELS) {
        return -1;
    }
    snprintf(e->labels[e->nlabels].name, LOKI_MAX_NAME, "%s", name);
    snprintf(e->labels[e->nlabels].value, LOKI_MAX_VALUE, "%s", value);
    e->nlabels++;
    return 0;
}

static int entry_del(loki_entry *e, const char *name) {
    for (int i = 0; i < e->nlabels; i++) {
        if (strcmp(e->labels[i].name, name) == 0) {
            e->labels[i] = e->labels[e->nlabels - 1];
            e->nlabels--;
            return 1;
        }
    }
    return 0;
}

static int entry_has_error(const loki_entry *e) {
    return entry_get(e, LOKI_ERROR_LABEL) != NULL;
}

/* 打错误标签，**不丢行**。多个错误用逗号累加（合并顺序属实现细节）。 */
static void entry_append_error(loki_entry *e, const char *err) {
    const char *old = entry_get(e, LOKI_ERROR_LABEL);
    char merged[LOKI_MAX_VALUE];
    if (old) {
        snprintf(merged, sizeof(merged), "%s, %s", old, err);
    } else {
        snprintf(merged, sizeof(merged), "%s", err);
    }
    entry_set(e, LOKI_ERROR_LABEL, merged);
}

/* ---------------------------------------------------------------- 通配 */

/* 完全匹配：`*` 匹配任意串，`?` 匹配单个字符，整体隐式 ^...$。
 * 这是流选择器 =~ 在 C 版里的等价物 —— 关键在「完全」，不是「包含」。 */
static int glob_fullmatch(const char *pat, const char *text) {
    const char *star = NULL;
    const char *backtrack = NULL;
    while (*text) {
        if (*pat == '?' || *pat == *text) {
            pat++;
            text++;
        } else if (*pat == '*') {
            star = pat++;
            backtrack = text;
        } else if (star) {
            pat = star + 1;
            text = ++backtrack;
        } else {
            return 0;
        }
    }
    while (*pat == '*') {
        pat++;
    }
    return *pat == '\0';
}

/* ---------------------------------------------------------------- 选择器 */

typedef struct {
    const char *label;
    const char *op;
    const char *value;
} loki_matcher;

static int matcher_matches(const loki_matcher *m, const loki_entry *e) {
    const char *cand = entry_get_or_empty(e, m->label);
    if (strcmp(m->op, "=") == 0) {
        return strcmp(cand, m->value) == 0;
    }
    if (strcmp(m->op, "!=") == 0) {
        return strcmp(cand, m->value) != 0;
    }
    if (strcmp(m->op, "=~") == 0) {
        return glob_fullmatch(m->value, cand); /* 完全锚定 */
    }
    if (strcmp(m->op, "!~") == 0) {
        return !glob_fullmatch(m->value, cand);
    }
    return 0;
}

typedef struct {
    const loki_matcher *matchers;
    int count;
} loki_selector;

static int selector_matches(const loki_selector *s, const loki_entry *e) {
    for (int i = 0; i < s->count; i++) {
        if (!matcher_matches(&s->matchers[i], e)) {
            return 0; /* 所有 matcher 之间是 AND */
        }
    }
    return 1;
}

/* ---------------------------------------------------------------- 行过滤 */

#define LOKI_LINE_CONTAINS 0     /* |= */
#define LOKI_LINE_NOT_CONTAINS 1 /* != */
#define LOKI_LINE_SEARCH 2       /* |~  子串搜索，**非锚定** */
#define LOKI_LINE_NOT_SEARCH 3   /* !~ */

static int line_filter_accepts(const char *line, const char *value, int op) {
    switch (op) {
    case LOKI_LINE_CONTAINS:
    case LOKI_LINE_SEARCH:
        return strstr(line, value) != NULL;
    case LOKI_LINE_NOT_CONTAINS:
    case LOKI_LINE_NOT_SEARCH:
        return strstr(line, value) == NULL;
    default:
        return 0;
    }
}

/* ---------------------------------------------------------------- 单位 */

typedef struct {
    const char *unit;
    double factor;
} loki_unit;

static const loki_unit LOKI_DURATION_UNITS[] = {
    {"ns", 1e-9},   {"us", 1e-6},    {"ms", 1e-3},    {"s", 1.0},     {"m", 60.0},
    {"h", 3600.0},  {"d", 86400.0},  {"w", 604800.0}, {"y", 31536000.0},
    {NULL, 0.0},
};

/* 字节单位是 1024 进制：官方把 chunk_target_size 的 1572864 注释为 1.5 MB。 */
static const loki_unit LOKI_BYTE_UNITS[] = {
    {"b", 1.0},        {"kb", 1024.0},     {"kib", 1024.0},     {"mb", 1048576.0},
    {"mib", 1048576.0}, {"gb", 1073741824.0},
    {NULL, 0.0},
};

static double unit_factor(const loki_unit *table, const char *unit) {
    for (int i = 0; table[i].unit; i++) {
        if (strcmp(table[i].unit, unit) == 0) {
            return table[i].factor;
        }
    }
    return -1.0;
}

/* 解析 "5m" / "1h30m" / "256KB" / "1.5MB"。返回 0 表示成功，-1 表示缺数字、
 * 缺单位或单位不可识别（三者不是一回事，但对外只需一个失败信号）。 */
static int parse_quantity(const char *text, const loki_unit *table, double *out) {
    double total = 0.0;
    const char *p = text;
    int segments = 0;
    if (p == NULL || *p == '\0') {
        return -1;
    }
    while (*p) {
        char *end = NULL;
        double value = strtod(p, &end);
        if (end == p) {
            return -1;
        }
        p = end;
        char unit[16];
        size_t n = 0;
        while (*p && isalpha((unsigned char)*p) && n + 1 < sizeof(unit)) {
            unit[n++] = (char)tolower((unsigned char)*p);
            p++;
        }
        unit[n] = '\0';
        if (n == 0) {
            return -1;
        }
        double factor = unit_factor(table, unit);
        if (factor < 0.0) {
            return -1;
        }
        total += value * factor;
        segments++;
    }
    if (segments == 0) {
        return -1;
    }
    *out = total;
    return 0;
}

#endif /* LOKI_CORE_H */
