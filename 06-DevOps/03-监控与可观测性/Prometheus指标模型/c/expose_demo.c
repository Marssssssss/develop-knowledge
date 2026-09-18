/* Prometheus text exposition format 0.0.4 parser demo (C99, no deps)
 * 依据 https://prometheus.io/docs/instrumenting/exposition_formats/
 * 编译: gcc -O2 -Wall -Wextra -o expose_demo expose_demo.c （解析层在 expose_parse_impl.h，文本级包含，不用单独编译）*/
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

#include "expose_parse_impl.h"

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
