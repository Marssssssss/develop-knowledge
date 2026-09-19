/*
 * Lucene / ES bool 查询(C),与 python/bool_query.py 同构。
 *
 * 权威来源(实际读过):
 *   1. .../query-dsl-bool-query.html —— must/should 计分并**相加**;filter/must_not 走
 *      filter context 不计分、可缓存;纯 filter ⇒ _score=0;must:match_all+filter ⇒ 1.0
 *   2. .../query-dsl-minimum-should-match.html —— 默认值规则;百分比向下取整;
 *      clamp 到 [1,n];无 required 子句时仍须匹配 ≥1 个 optional 子句
 *   3. BooleanQuery.java —— bool query 映射到 Lucene BooleanQuery
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define MAXC 8
#define MAXD 8
#define EPS 1e-9

enum { MUST, SHOULD, FILTER, MUST_NOT };
static const char *OCCUR[4] = {"must", "should", "filter", "must_not"};

static int g_ok = 0, g_fail = 0;
static void check(int cond, const char *msg) {
    if (cond) { g_ok++;   printf("  [ok]   %s\n", msg); }
    else      { g_fail++; printf("  [FAIL] %s\n", msg); }
}

static int is_scoring(int o)  { return o == MUST || o == SHOULD; }
static int is_required(int o) { return o == MUST || o == FILTER; }

/* 简易 strtok(前向声明:parse_msm 会用到) */
static char *strtok_r_simple(char *s, const char *delim, char **save);

typedef struct {
    int occur;
    int hits[MAXD];      int nhits;
    int scored[MAXD];    double score[MAXD]; int nscore;
} Clause;

typedef struct {
    Clause c[MAXC];
    int nc;
    int has_msm;         /* 0 = 走默认值 */
    char msm[32];        /* 规格串 */
    int msm_int;         /* 直接给整数时用 */
    int msm_is_int;
} BoolQuery;

static int cl_hit(const Clause *c, int d) {
    for (int i = 0; i < c->nhits; i++) if (c->hits[i] == d) return 1;
    return 0;
}

static double cl_score(const Clause *c, int d) {
    for (int i = 0; i < c->nscore; i++) if (c->scored[i] == d) return c->score[i];
    return 0.0;
}

static int count_occur(const BoolQuery *q, int o) {
    int n = 0;
    for (int i = 0; i < q->nc; i++) if (q->c[i].occur == o) n++;
    return n;
}

/* ---------------------------------------------------- msm 解析 */
static int apply_one(const char *spec, int n) {
    size_t L = strlen(spec);
    if (L && spec[L - 1] == '%') {
        double v = atof(spec);
        if (v >= 0) return (int)floor(n * v / 100.0);
        return n - (int)floor(n * (-v) / 100.0);
    }
    int v = atoi(spec);
    return (v >= 0) ? v : n + v;
}

/* 支持 `3<90%` 与多重组合 `2<-25% 9<-3` */
static int parse_msm(const char *spec, int nopt) {
    if (nopt == 0) return 0;
    char buf[64];
    snprintf(buf, sizeof buf, "%s", spec);
    int nparts = 0;
    char *parts[6];
    char *save = NULL;
    for (char *p = strtok_r_simple(buf, " ", &save); p && nparts < 6;
         p = strtok_r_simple(NULL, " ", &save))
        parts[nparts++] = p;
    int v = nopt;
    if (nparts == 1 && !strchr(parts[0], '<')) {
        v = apply_one(parts[0], nopt);
    } else {
        const char *chosen = NULL;
        for (int i = 0; i < nparts; i++) {
            char *lt = strchr(parts[i], '<');
            if (lt) {
                *lt = '\0';
                int thr = atoi(parts[i]);
                if (nopt > thr) chosen = lt + 1;
            } else {
                chosen = parts[i];
            }
        }
        if (chosen) v = apply_one(chosen, nopt);
    }
    if (v < 1) return 1;
    if (v > nopt) return nopt;
    return v;
}

static int effective_msm(const BoolQuery *q) {
    int ns = count_occur(q, SHOULD), nm = count_occur(q, MUST), nf = count_occur(q, FILTER);
    int base = 0;
    if (!q->has_msm) {
        if (ns >= 1 && nm == 0 && nf == 0) base = 1;
    } else if (q->msm_is_int) {
        base = q->msm_int;
    } else {
        base = parse_msm(q->msm, ns);
    }
    if (nm == 0 && nf == 0) {
        if (ns == 0) return 0;
        if (base < 1) return 1;      /* 无 required ⇒ 至少匹配一个 optional */
    }
    return base;
}

static int matches(const BoolQuery *q, int d) {
    for (int i = 0; i < q->nc; i++) {
        const Clause *c = &q->c[i];
        if (is_required(c->occur) && !cl_hit(c, d)) return 0;
        if (c->occur == MUST_NOT && cl_hit(c, d)) return 0;
    }
    int n = 0;
    for (int i = 0; i < q->nc; i++)
        if (q->c[i].occur == SHOULD && cl_hit(&q->c[i], d)) n++;
    return n >= effective_msm(q);
}

static double score(const BoolQuery *q, int d) {
    if (!matches(q, d)) return 0.0;
    double s = 0.0;
    for (int i = 0; i < q->nc; i++)
        if (is_scoring(q->c[i].occur)) s += cl_score(&q->c[i], d);
    return s;
}

/* 简易 strtok(单线程、够用) */
static char *strtok_r_simple(char *s, const char *delim, char **save) {
    char *start;
    if (s) start = s; else if (*save) start = *save; else return NULL;
    while (*start && strchr(delim, *start)) start++;
    if (!*start) { *save = NULL; return NULL; }
    char *end = start;
    while (*end && !strchr(delim, *end)) end++;
    if (*end) { *end = '\0'; *save = end + 1; } else { *save = NULL; }
    return start;
}

static Clause mk(int occur, const int *hits, int nh,
                 const int *sd, const double *sc, int ns) {
    Clause c; memset(&c, 0, sizeof c);
    c.occur = occur;
    for (int i = 0; i < nh && i < MAXD; i++) c.hits[c.nhits++] = hits[i];
    for (int i = 0; i < ns && i < MAXD; i++) { c.scored[c.nscore] = sd[i]; c.score[c.nscore] = sc[i]; c.nscore++; }
    return c;
}

int main(void) {
    int docs[4] = {1, 2, 3, 4};
    printf("== Demo 1 · 四类 occurrence ==\n");
    BoolQuery q; memset(&q, 0, sizeof q);
    int h1[3] = {1, 2, 3}, s1d[3] = {1, 2, 3}; double s1v[3] = {1.0, 2.0, 0.5};
    int h2[2] = {1, 2}; int h3[1] = {3};
    int h4[2] = {1, 4}, s4d[2] = {1, 4}; double s4v[2] = {0.3, 0.3};
    q.c[q.nc++] = mk(MUST, h1, 3, s1d, s1v, 3);
    q.c[q.nc++] = mk(FILTER, h2, 2, NULL, NULL, 0);
    q.c[q.nc++] = mk(MUST_NOT, h3, 1, NULL, NULL, 0);
    q.c[q.nc++] = mk(SHOULD, h4, 2, s4d, s4v, 2);
    int hit[4], nh = 0;
    for (int i = 0; i < 4; i++) if (matches(&q, docs[i])) hit[nh++] = docs[i];
    printf("   命中:"); for (int i = 0; i < nh; i++) printf(" %d", hit[i]); printf("\n");
    check(nh == 2 && hit[0] == 1 && hit[1] == 2, "must∩filter 且排除 must_not ⇒ {1,2}");
    check(!matches(&q, 3), "doc3 命中 must_not ⇒ 被排除");
    check(!matches(&q, 4), "有 must/filter 时 should 不会把 doc4 带进来");

    printf("\n== Demo 2 · 评分求和 ==\n");
    check(fabs(score(&q, 1) - 1.3) < EPS, "doc1 = must 1.0 + should 0.3 = 1.3");
    check(fabs(score(&q, 2) - 2.0) < EPS, "doc2 只命中 must(2.0)");

    printf("\n== Demo 3 · filter context 不计分 ==\n");
    BoolQuery of; memset(&of, 0, sizeof of);
    of.c[of.nc++] = mk(FILTER, h2, 2, NULL, NULL, 0);
    BoolQuery ma; memset(&ma, 0, sizeof ma);
    int allh[2] = {1, 2}, alld[2] = {1, 2}; double allv[2] = {1.0, 1.0};
    ma.c[ma.nc++] = mk(MUST, allh, 2, alld, allv, 2);
    ma.c[ma.nc++] = mk(FILTER, h2, 2, NULL, NULL, 0);
    check(score(&of, 1) == 0.0 && score(&of, 2) == 0.0, "纯 filter ⇒ _score=0");
    check(fabs(score(&ma, 1) - 1.0) < EPS && fabs(score(&ma, 2) - 1.0) < EPS,
          "match_all+filter ⇒ 1.0");

    printf("\n== Demo 4 · msm 默认值 ==\n");
    BoolQuery so; memset(&so, 0, sizeof so);
    int one[1] = {1};
    so.c[so.nc++] = mk(SHOULD, one, 1, NULL, NULL, 0);
    BoolQuery sm; memset(&sm, 0, sizeof sm);
    int two[2] = {1, 2};
    sm.c[sm.nc++] = mk(SHOULD, one, 1, NULL, NULL, 0);
    sm.c[sm.nc++] = mk(MUST, two, 2, NULL, NULL, 0);
    check(effective_msm(&so) == 1, "只有 should ⇒ 默认 1");
    check(effective_msm(&sm) == 0, "有 must ⇒ 默认 0");
    BoolQuery z; memset(&z, 0, sizeof z);
    z.c[z.nc++] = mk(SHOULD, one, 1, NULL, NULL, 0);
    z.has_msm = 1; snprintf(z.msm, sizeof z.msm, "0%%");
    check(effective_msm(&z) == 1, "算出 0 也兜底成 1");

    printf("\n== Demo 5 · msm 规格 ==\n");
    check(parse_msm("3", 5) == 3 && parse_msm("-2", 5) == 3, "整数 / 负整数");
    check(parse_msm("75%", 4) == 3 && parse_msm("-25%", 4) == 3, "4 子句:75% == -25% == 3");
    check(parse_msm("75%", 5) == 3 && parse_msm("-25%", 5) == 4, "5 子句:75%⇒3,-25%⇒4");
    check(parse_msm("3<90%", 3) == 3 && parse_msm("3<90%", 4) == 3 && parse_msm("3<90%", 10) == 9,
          "组合 3<90%");
    int exp[6] = {1, 2, 3, 7, 7, 9}, ns[6] = {1, 2, 3, 9, 10, 12}, allok = 1;
    for (int i = 0; i < 6; i++) if (parse_msm("2<-25% 9<-3", ns[i]) != exp[i]) allok = 0;
    check(allok, "多重组合 2<-25% 9<-3 ⇒ 1,2,3,7,7,9");

    printf("\n断言 %d 通过 / %d 失败\n", g_ok, g_fail);
    (void)OCCUR;
    return g_fail ? 1 : 0;
}
