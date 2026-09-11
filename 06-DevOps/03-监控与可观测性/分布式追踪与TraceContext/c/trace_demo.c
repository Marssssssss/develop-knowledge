/* 分布式追踪与 W3C Trace Context —— demo (C99, no deps)
 * 依据 https://www.w3.org/TR/2020/REC-trace-context-1-20200206
 * 编译: gcc -O2 -Wall -Wextra -o trace_demo trace_demo.c */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TRACE_ID_LEN 33 /* 32 hex + NUL */
#define SPAN_ID_LEN  17 /* 16 hex + NUL */

typedef struct {
    char version[3];
    char trace_id[TRACE_ID_LEN];
    char parent_id[SPAN_ID_LEN];
    int flags;
} TraceParent;

typedef struct {
    char trace_id[TRACE_ID_LEN];
    char span_id[SPAN_ID_LEN];
    char parent_id[SPAN_ID_LEN]; /* 空串 = root */
    char name[16];
} Span;

static const char *HEX = "0123456789abcdef";

/* 内置 LCG（固定种子，输出可复现），避免依赖 POSIX rand_r */
static unsigned int g_lcg = 42;
static unsigned int lcg_next(void)
{
    g_lcg = g_lcg * 1103515245u + 12345u;
    return g_lcg >> 16;
}

/* 规范建议至少右 7 字节随机；demo 用全随机并保证非全零 */
static void new_id(char *out, int nhex)
{
    do {
        for (int i = 0; i < nhex; i++)
            out[i] = HEX[lcg_next() % 16];
        out[nhex] = '\0';
    } while (strspn(out, "0") == (size_t)nhex); /* 全零非法，重生成 */
}

static int all_lower_hex(const char *s, size_t n)
{
    for (size_t i = 0; i < n; i++)
        if (strchr(HEX, s[i]) == NULL)
            return 0;
    return 1;
}

/* 校验 version-32hex-16hex-2hex；全零/ff/大写均非法（MUST ignore）
 * 返回 0 成功；-1 非法 */
static int parse_traceparent(const char *tp, TraceParent *out)
{
    char copy[128];
    snprintf(copy, sizeof copy, "%s", tp);
    char *parts[8];
    int np = 0;
    for (char *t = strtok(copy, "-"); t != NULL && np < 8; t = strtok(NULL, "-"))
        parts[np++] = t;
    if (np != 4)
        return -1;
    if (strlen(parts[0]) != 2 || strlen(parts[1]) != 32 ||
        strlen(parts[2]) != 16 || strlen(parts[3]) != 2)
        return -1;
    for (int i = 0; i < 4; i++)
        if (!all_lower_hex(parts[i], strlen(parts[i]))) /* HEXDIGLC：仅小写 */
            return -1;
    if (strcmp(parts[0], "ff") == 0) /* version ff 禁止 */
        return -1;
    if (strspn(parts[1], "0") == 32 || strspn(parts[2], "0") == 16)
        return -1; /* trace-id / parent-id 全零非法 */
    snprintf(out->version, 3, "%s", parts[0]);
    snprintf(out->trace_id, TRACE_ID_LEN, "%s", parts[1]);
    snprintf(out->parent_id, SPAN_ID_LEN, "%s", parts[2]);
    out->flags = (int)strtol(parts[3], NULL, 16);
    return 0;
}

static void serialize_traceparent(const TraceParent *tp, char *buf, size_t cap)
{
    snprintf(buf, cap, "%s-%s-%s-%02x", tp->version, tp->trace_id, tp->parent_id, tp->flags);
}

/* 位掩码；禁止 flags == 1 判等 */
static int is_sampled(int flags) { return (flags & 0x01) == 0x01; }

/* 一个服务节点：提取 traceparent -> 校验 -> 开 span -> 注出给下游 */
static Span *start_service(const char *name, const char *incoming_tp,
                           Span *spans, int *nspans)
{
    TraceParent tp;
    Span *s = &spans[(*nspans)++];
    snprintf(s->name, sizeof s->name, "%s", name);

    if (incoming_tp == NULL || parse_traceparent(incoming_tp, &tp) != 0) {
        if (incoming_tp != NULL)
            printf("    [%s] invalid traceparent ignored, starting new trace\n", name);
        /* 规范：非法 traceparent MUST ignore —— 当作无上游，另起新 trace */
        new_id(s->trace_id, 32);
        s->parent_id[0] = '\0';
        snprintf(tp.version, 3, "00");
        snprintf(tp.trace_id, TRACE_ID_LEN, "%s", s->trace_id);
        tp.flags = 0x01;
    } else {
        snprintf(s->trace_id, TRACE_ID_LEN, "%s", tp.trace_id);
        snprintf(s->parent_id, SPAN_ID_LEN, "%s", tp.parent_id);
    }
    new_id(s->span_id, 16); /* 每跳生成新 span_id */

    char outgoing[128];
    TraceParent next;
    snprintf(next.version, 3, "%s", tp.version);
    snprintf(next.trace_id, TRACE_ID_LEN, "%s", s->trace_id);
    snprintf(next.parent_id, SPAN_ID_LEN, "%s", s->span_id); /* parent-id 换成自己的 span_id */
    next.flags = tp.flags; /* flags 全链路传递 */
    serialize_traceparent(&next, outgoing, sizeof outgoing);
    printf("    %s: outgoing traceparent = %s\n", name, outgoing);
    return s;
}

/* 按 parent_id 关系把 Span 列表还原成树（trace = span 的 DAG） */
static void walk_tree(const Span *spans, int nspans, const char *parent_id, int depth)
{
    for (int i = 0; i < nspans; i++) {
        if (strcmp(spans[i].parent_id, parent_id) != 0)
            continue;
        printf("%*s└─ %s (span=%.8s…%s)\n", depth * 2, "", spans[i].name,
               spans[i].span_id,
               spans[i].parent_id[0] ? "" : " root");
        walk_tree(spans, nspans, spans[i].span_id, depth + 1);
    }
}

int main(void)
{
    printf("== demo 1: client -> A -> B -> C propagation ==\n");
    Span spans[8];
    int nspans = 0;
    const char *tp = NULL;
    const char *svcs[] = {"svc-A", "svc-B", "svc-C"};
    static char hop[3][128]; /* 模拟 HTTP 注入/提取：每跳头需要独立存储 */
    for (size_t i = 0; i < sizeof svcs / sizeof svcs[0]; i++) {
        Span *last = start_service(svcs[i], tp, spans, &nspans);
        snprintf(hop[i], 128, "00-%s-%s-01", last->trace_id, last->span_id);
        tp = hop[i];
    }
    printf("  span tree reconstructed from parent_id links:\n");
    walk_tree(spans, nspans, "", 1);
    for (int i = 1; i < nspans; i++)
        if (strcmp(spans[i].trace_id, spans[0].trace_id) != 0) {
            printf("  FAIL: trace_id changed across hops\n");
            return 1;
        }
    printf("  trace_id constant across all hops: OK\n");

    printf("\n== demo 2: validation rules (MUST ignore cases) ==\n");
    const char *bad[] = {
        "00-00000000000000000000000000000000-abcdef0123456789-01", /* all-zero trace-id */
        "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01", /* all-zero parent-id */
        "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", /* version ff */
        "00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01", /* uppercase hex */
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7",    /* missing flags */
    };
    const char *why[] = {"all-zero trace-id", "all-zero parent-id", "version ff",
                         "uppercase hex", "missing flags"};
    TraceParent dummy;
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        if (parse_traceparent(bad[i], &dummy) == 0) {
            printf("  FAIL: should be rejected: %s\n", why[i]);
            return 1;
        }
        printf("    rejected: %s\n", why[i]);
    }
    if (parse_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", &dummy) != 0) {
        printf("  FAIL: spec example should parse\n");
        return 1;
    }
    printf("    accepted: spec example 00-4bf92f35…-00f067aa…-01\n");

    printf("\n== demo 3: trace-flags bit masking ==\n");
    if (!is_sampled(0x01) || !is_sampled(0x03) || is_sampled(0x00) || is_sampled(0x02)) {
        printf("  FAIL: flag masking wrong\n");
        return 1;
    }
    printf("    flags 0x01/0x03 sampled=True, 0x00/0x02 sampled=False (mask, not equality)\n");

    printf("\n== demo 4: invalid upstream starts a fresh trace ==\n");
    Span spans2[4];
    int nspans2 = 0;
    Span *root = start_service("svc-X",
                               "00-00000000000000000000000000000000-abcdef0123456789-01",
                               spans2, &nspans2);
    if (root->parent_id[0] != '\0') {
        printf("  FAIL: fresh root expected after ignored traceparent\n");
        return 1;
    }
    printf("    svc-X became root with new trace %.8s…\n", root->trace_id);

    printf("\nALL CHECKS PASSED\n");
    return 0;
}
