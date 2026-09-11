/* Prometheus text exposition format 0.0.4 parser demo (C99, no deps)
 * 依据 https://prometheus.io/docs/instrumenting/exposition_formats/
 * 编译: gcc -O2 -Wall -Wextra -o expose_demo expose_demo.c */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_LINE   512
#define MAX_FAM    24
#define MAX_SAMP   80
#define MAX_LABELS 8
#define NAME_LEN   96
#define VAL_LEN    128

typedef struct {
    char key[NAME_LEN];
    char value[VAL_LEN];
} Label;

typedef struct {
    char name[NAME_LEN];
    Label labels[MAX_LABELS];
    int nlabels;
    double value;
    long long ts;
    int has_ts;
} Sample;

typedef struct {
    char name[NAME_LEN];
    char type[16];
    char help[MAX_LINE];
    Sample samples[MAX_SAMP];
    int nsamples;
} Family;

static Family g_fams[MAX_FAM];
static int g_nfams = 0;

/* 标签值只定义三种转义: \\ \" \n —— 官方规范明文列举 */
static void unescape(const char *src, char *dst, size_t cap)
{
    size_t o = 0;
    for (size_t i = 0; src[i] != '\0' && o + 1 < cap; i++) {
        if (src[i] == '\\' && src[i + 1] != '\0') {
            char c = src[i + 1];
            dst[o++] = (c == 'n') ? '\n' : c;
            i++;
        } else {
            dst[o++] = src[i];
        }
    }
    dst[o] = '\0';
}

/* strtod 原生接受 NaN/INF(大小写不敏感), 与 Go ParseFloat 语义一致 */
static int parse_value(const char *tok, double *out)
{
    char *end = NULL;
    *out = strtod(tok, &end);
    return (end != NULL && *end == '\0');
}

static Family *find_or_create(const char *name)
{
    for (int i = 0; i < g_nfams; i++)
        if (strcmp(g_fams[i].name, name) == 0)
            return &g_fams[i];
    if (g_nfams >= MAX_FAM) return NULL;
    Family *f = &g_fams[g_nfams++];
    snprintf(f->name, sizeof f->name, "%s", name);
    strcpy(f->type, "untyped"); /* 无 TYPE 行 => untyped */
    f->help[0] = '\0';
    f->nsamples = 0;
    return f;
}

/* 解析 'name{k="v",...}' 部分 */
static int parse_metric_part(char *part, char *name, Label *labels, int *nlabels)
{
    char *brace = strchr(part, '{');
    if (brace == NULL) { /* 无标签 */
        snprintf(name, NAME_LEN, "%s", part);
        *nlabels = 0;
        return 0;
    }
    *brace = '\0';
    snprintf(name, NAME_LEN, "%s", part);
    char *body = brace + 1;
    char *close = strrchr(body, '}');
    if (close == NULL) return -1; /* 括号未闭合 */
    *close = '\0';
    int n = 0;
    char *p = body;
    while (*p != '\0') {
        char *eq = strchr(p, '=');
        if (eq == NULL || eq[1] != '"') return -1;
        char *j = eq + 2;
        while (*j != '\0') { /* 找未转义的收尾引号 */
            if (*j == '"' && *(j - 1) != '\\') break;
            j++;
        }
        if (*j != '"') return -1;
        if (n >= MAX_LABELS) return -1;
        size_t klen = (size_t)(eq - p);
        if (klen >= NAME_LEN) klen = NAME_LEN - 1;
        memcpy(labels[n].key, p, klen);
        labels[n].key[klen] = '\0';
        *j = '\0';
        unescape(eq + 2, labels[n].value, VAL_LEN);
        n++;
        p = j + 1;
        while (*p == ',' || *p == ' ') p++;
    }
    *nlabels = n;
    return 0;
}

/* 样本行: metric_name_or_labels value [timestamp] —— 从右往左切最稳 */
static int parse_sample_line(char *line)
{
    char *save = NULL;
    /* 复制 token 列表, 从尾部识别 [ts] 和 value */
    char *toks[16];
    int nt = 0;
    for (char *t = strtok_r(line, " \t", &save); t != NULL && nt < 16; t = strtok_r(NULL, " \t", &save))
        toks[nt++] = t;
    if (nt < 2) return -1;

    char *last = toks[nt - 1];
    int is_int = 1;
    for (char *c = last; *c != '\0'; c++)
        if (!isdigit((unsigned char)*c) && *c != '-' && *c != '+') { is_int = 0; break; }

    char *val_tok, *ts_tok = NULL;
    if (nt >= 3 && is_int) {
        ts_tok = last;
        val_tok = toks[nt - 2];
        toks[nt - 2] = NULL; /* 截断, 剩余为指标部分 */
    } else {
        val_tok = last;
        toks[nt - 1] = NULL;
    }
    /* 重组指标部分(标签值内可含空格, 不能整体按空格切 —— 用原始内存拼接) */
    char part[MAX_LINE] = "";
    size_t off = 0;
    for (int i = 0; toks[i] != NULL; i++) {
        size_t l = strlen(toks[i]);
        if (off + l + 2 >= sizeof part) return -1;
        if (i > 0) part[off++] = ' ';
        memcpy(part + off, toks[i], l);
        off += l;
    }
    part[off] = '\0';

    char name[NAME_LEN];
    Label labels[MAX_LABELS];
    int nlabels = 0;
    if (parse_metric_part(part, name, labels, &nlabels) != 0) return -1;

    double value;
    if (!parse_value(val_tok, &value)) return -1;

    Family *f = find_or_create(name);
    if (f == NULL || f->nsamples >= MAX_SAMP) return -1;
    Sample *s = &f->samples[f->nsamples++];
    snprintf(s->name, sizeof s->name, "%s", name);
    memcpy(s->labels, labels, sizeof labels);
    s->nlabels = nlabels;
    s->value = value;
    s->has_ts = (ts_tok != NULL);
    s->ts = ts_tok ? strtoll(ts_tok, NULL, 10) : 0;
    return 0;
}

static void parse_exposition(const char *text)
{
    char buf[MAX_LINE];
    const char *p = text;
    while (*p != '\0') {
        size_t i = 0;
        while (*p != '\0' && *p != '\n' && i < sizeof buf - 1)
            buf[i++] = *p++;
        buf[i] = '\0';
        if (*p == '\n') p++;

        char *line = buf;
        while (isspace((unsigned char)*line)) line++;
        char *end = line + strlen(line);
        while (end > line && isspace((unsigned char)end[-1])) *--end = '\0';
        if (*line == '\0') continue; /* 空行忽略 */

        if (*line == '#') {
            char *rest = line + 1;
            while (isspace((unsigned char)*rest)) rest++;
            char *save = NULL;
            char *kw = strtok_r(rest, " \t", &save);
            if (kw == NULL) continue; /* 普通注释 */
            if (strcmp(kw, "HELP") == 0) {
                char *name = strtok_r(NULL, " \t", &save);
                char *doc = strtok_r(NULL, "", &save);
                Family *f = find_or_create(name);
                if (f != NULL && doc != NULL) {
                    char tmp[MAX_LINE];
                    unescape(doc, tmp, sizeof tmp); /* HELP 只需转义 \\ 和 \n */
                    snprintf(f->help, sizeof f->help, "%s", tmp);
                }
            } else if (strcmp(kw, "TYPE") == 0) {
                char *name = strtok_r(NULL, " \t", &save);
                char *type = strtok_r(NULL, " \t", &save);
                if (type == NULL ||
                    (strcmp(type, "counter") && strcmp(type, "gauge") &&
                     strcmp(type, "histogram") && strcmp(type, "summary") &&
                     strcmp(type, "untyped"))) {
                    fprintf(stderr, "bad TYPE line: %s\n", line);
                    exit(1);
                }
                Family *f = find_or_create(name);
                if (f != NULL) {
                    if (f->nsamples > 0) { /* TYPE 必须在首个样本之前 */
                        fprintf(stderr, "TYPE after first sample: %s\n", name);
                        exit(1);
                    }
                    snprintf(f->type, sizeof f->type, "%s", type);
                }
            }
            continue;
        }
        if (parse_sample_line(line) != 0) {
            fprintf(stderr, "bad sample line: %s\n", line);
            exit(1);
        }
    }
}

static const char *label_of(Sample *s, const char *key)
{
    for (int i = 0; i < s->nlabels; i++)
        if (strcmp(s->labels[i].key, key) == 0)
            return s->labels[i].value;
    return NULL;
}

static int hist_check(const char *base)
{
    char bname[NAME_LEN * 2];
    snprintf(bname, sizeof bname, "%s_bucket", base);
    Family *bf = find_or_create(bname);
    printf("  histogram '%s' buckets:\n", base);
    double prev = -1.0;
    for (int i = 0; i < bf->nsamples; i++) {
        Sample *s = &bf->samples[i];
        const char *le = label_of(s, "le");
        if (le == NULL) continue;
        double bound = (strcmp(le, "+Inf") == 0) ? 1.0 / 0.0 : strtod(le, NULL);
        printf("    le=%-6s count=%g\n", le, s->value);
        if (bound < prev) { printf("    FAIL: buckets out of order\n"); return 1; } /* 校验 3 */
        if (s->value < 0) { printf("    FAIL: negative count\n"); return 1; }
        prev = bound;
        if (strcmp(le, "+Inf") == 0) { /* 校验 2 */
            snprintf(bname, sizeof bname, "%s_count", base);
            Family *cf = find_or_create(bname);
            if (cf->nsamples == 0 || cf->samples[0].value != s->value) {
                printf("    FAIL: +Inf bucket != _count\n");
                return 1;
            }
        }
    }
    return 0;
}

int main(void)
{
    /* 标签值中的 \n \" 在文本里是两字符序列, C 源码需再转义一层 */
    const char *EXPO =
        "# HELP http_requests_total The total number of HTTP requests.\n"
        "# TYPE http_requests_total counter\n"
        "http_requests_total{method=\"post\",code=\"200\"} 1027 1395066363000\n"
        "http_requests_total{method=\"post\",code=\"400\"}    3 1395066363000\n"
        "escapeme{label=\"a\\nb \\\" c\"} 1\n"
        "# HELP http_request_duration_seconds A histogram of the request duration.\n"
        "# TYPE http_request_duration_seconds histogram\n"
        "http_request_duration_seconds_bucket{le=\"0.05\"} 24054\n"
        "http_request_duration_seconds_bucket{le=\"0.1\"} 33444\n"
        "http_request_duration_seconds_bucket{le=\"0.2\"} 100392\n"
        "http_request_duration_seconds_bucket{le=\"0.5\"} 129389\n"
        "http_request_duration_seconds_bucket{le=\"1\"} 133988\n"
        "http_request_duration_seconds_bucket{le=\"+Inf\"} 144320\n"
        "http_request_duration_seconds_sum 53423\n"
        "http_request_duration_seconds_count 144320\n"
        "# HELP rpc_duration_seconds A summary of the RPC duration in seconds.\n"
        "# TYPE rpc_duration_seconds summary\n"
        "rpc_duration_seconds{quantile=\"0.01\"} 3102\n"
        "rpc_duration_seconds{quantile=\"0.5\"} 4773\n"
        "rpc_duration_seconds{quantile=\"0.99\"} 76656\n"
        "rpc_duration_seconds_sum 1.7560433e+07\n"
        "rpc_duration_seconds_count 2693\n"
        "# no TYPE line below -> defaults to untyped\n"
        "some_gauge 42\n"
        "weird_value NaN\n"
        "another_inf +Inf\n";

    printf("== demo 1: parse official-style exposition ==\n");
    parse_exposition(EXPO);
    for (int i = 0; i < g_nfams; i++)
        printf("family=%-36s type=%-9s samples=%d\n",
               g_fams[i].name, g_fams[i].type, g_fams[i].nsamples);

    printf("\n== demo 2: histogram invariants ==\n");
    if (hist_check("http_request_duration_seconds") != 0) return 1;
    printf("  all invariants hold (has +Inf / == count / ordered)\n");

    printf("\n== demo 3: label escaping round-trip ==\n");
    Family *ef = find_or_create("escapeme");
    if (ef->nsamples == 0) return 1;
    const char *lv = label_of(&ef->samples[0], "label");
    /* 期望: a <LF> b <space> " <space> c */
    if (lv == NULL || strcmp(lv, "a\nb \" c") != 0) {
        printf("  FAIL: unescaped mismatch: '%s'\n", lv ? lv : "(null)");
        return 1;
    }
    printf("  label value unescaped OK (real newline + quote survived)\n");

    printf("\n== demo 4: missing TYPE -> untyped ==\n");
    Family *gf = find_or_create("some_gauge");
    if (strcmp(gf->type, "untyped") != 0) return 1;
    printf("  some_gauge type = untyped (spec: no TYPE line => untyped)\n");

    printf("\n== demo 5: NaN / +Inf values ==\n");
    Family *wf = find_or_create("weird_value");
    if (!isnan(wf->samples[0].value)) { printf("  FAIL: NaN\n"); return 1; }
    Family *af = find_or_create("another_inf");
    if (!(af->samples[0].value > 1e308)) { printf("  FAIL: +Inf\n"); return 1; }
    printf("  weird_value = NaN, another_inf = +Inf (valid per spec)\n");

    printf("\nALL CHECKS PASSED\n");
    return 0;
}
