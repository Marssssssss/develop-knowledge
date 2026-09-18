/* 依赖漏洞判定（C 版）：SemVer 区间求值 → 取区间内最高版本 → 可达性剪枝。
 *
 * 与 Python / Go 版同一套 fixture、同一组判定结果。
 * 为控制篇幅，C 版只演示**单条区间**的求值（不做多约束交集），
 * SBOM 生成见 Python 版 sbom()。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define MAXPRE 4
#define NPKG   5
#define NFN    7
#define MAXC   4

typedef struct {
    int maj, min, pat;
    char pre[MAXPRE][16];
    int npre;
} Ver;

typedef struct { char op[3]; Ver v; } Cons;

/* ---------------------------------------------------------- SemVer 解析 */

/* build metadata（'+' 之后）直接丢弃：规范 §10 规定它不参与优先级 */
static void parse_ver(const char *s, Ver *out) {
    char core[64], pre[64];
    int i, k;
    memset(out, 0, sizeof *out);
    pre[0] = 0;
    core[0] = 0;
    /* 去 build */
    for (i = 0, k = 0; s[i] && s[i] != '+' && s[i] != '-'; i++) core[k++] = s[i];
    core[k] = 0;
    if (s[i] == '-') {
        int j = 0;
        for (i++; s[i] && s[i] != '+'; i++) pre[j++] = s[i];
        pre[j] = 0;
    }
    sscanf(core, "%d.%d.%d", &out->maj, &out->min, &out->pat);
    if (pre[0]) {
        char *tok = strtok(pre, ".");
        while (tok && out->npre < MAXPRE) {
            strncpy(out->pre[out->npre], tok, 15);
            out->npre++;
            tok = strtok(NULL, ".");
        }
    }
}

static int all_digits(const char *s) {
    int i;
    if (!s[0]) return 0;
    for (i = 0; s[i]; i++) if (!isdigit((unsigned char)s[i])) return 0;
    return 1;
}

/* 规范 §11.4：数字按数值、其余按 ASCII 序；数字优先级低于非数字；
   前缀相同时标识符更多者优先级更高；空集（正式版）最高。 */
static int cmp_pre(const Ver *a, const Ver *b) {
    int i;
    if (a->npre == 0 && b->npre == 0) return 0;
    if (a->npre == 0) return 1;
    if (b->npre == 0) return -1;
    for (i = 0; i < a->npre && i < b->npre; i++) {
        const char *x = a->pre[i], *y = b->pre[i];
        int xn = all_digits(x), yn = all_digits(y), c;
        if (xn && yn)       c = (atoi(x) > atoi(y)) - (atoi(x) < atoi(y));
        else if (xn != yn)  c = xn ? -1 : 1;
        else                c = strcmp(x, y) > 0 ? 1 : (strcmp(x, y) < 0 ? -1 : 0);
        if (c) return c;
    }
    return (a->npre > b->npre) - (a->npre < b->npre);
}

static int cmp_ver(Ver a, Ver b) {
    if (a.maj != b.maj) return a.maj < b.maj ? -1 : 1;
    if (a.min != b.min) return a.min < b.min ? -1 : 1;
    if (a.pat != b.pat) return a.pat < b.pat ? -1 : 1;
    return cmp_pre(&a, &b);
}

/* ------------------------------------------------------------ 区间求值 */

static void range_of(const char *spec, Cons *out, int *n) {
    Ver p;
    *n = 0;
    if (spec[0] == '^' || spec[0] == '~') {
        int caret = (spec[0] == '^');
        parse_ver(spec + 1, &p);
        strcpy(out[*n].op, ">=");
        out[*n].v = p; (*n)++;
        strcpy(out[*n].op, "<");
        out[*n].v = p;
        out[*n].v.npre = 0;
        if (caret) {
            /* §4：0.y.z 是初始开发期 → ^ 在 0.x 上只锁到 minor */
            if (p.maj > 0)      { out[*n].v.maj = p.maj + 1; out[*n].v.min = 0; out[*n].v.pat = 0; }
            else if (p.min > 0) { out[*n].v.maj = 0; out[*n].v.min = p.min + 1; out[*n].v.pat = 0; }
            else                { out[*n].v.maj = 0; out[*n].v.min = 0; out[*n].v.pat = p.pat + 1; }
        } else {
            out[*n].v.maj = p.maj; out[*n].v.min = p.min + 1; out[*n].v.pat = 0;
        }
        (*n)++;
        return;
    }
    {
        /* 形如 ">=0.2.0 <0.2.8" */
        const char *q = spec;
        while (*q && *n < MAXC) {
            while (*q == ' ') q++;
            if (!*q) break;
            if (q[0] == '>' && q[1] == '=') { strcpy(out[*n].op, ">="); parse_ver(q + 2, &out[*n].v); }
            else if (q[0] == '<' && q[1] == '=') { strcpy(out[*n].op, "<="); parse_ver(q + 2, &out[*n].v); }
            else if (q[0] == '>') { strcpy(out[*n].op, ">"); parse_ver(q + 1, &out[*n].v); }
            else if (q[0] == '<') { strcpy(out[*n].op, "<"); parse_ver(q + 1, &out[*n].v); }
            else break;
            (*n)++;
            while (*q && *q != ' ') q++;
        }
    }
}

static int satisfies(Ver ver, const Cons *c, int n) {
    int i;
    for (i = 0; i < n; i++) {
        int k = cmp_ver(ver, c[i].v), ok = 0;
        if (!strcmp(c[i].op, ">=")) ok = k >= 0;
        else if (!strcmp(c[i].op, ">"))  ok = k > 0;
        else if (!strcmp(c[i].op, "<=")) ok = k <= 0;
        else if (!strcmp(c[i].op, "<"))  ok = k < 0;
        else if (!strcmp(c[i].op, "==")) ok = k == 0;
        if (!ok) return 0;
    }
    return 1;
}

/* -------------------------------------------------------------- fixture */

typedef struct { const char *name; const char *avail[4]; int n; } Pkg;

static const Pkg REGISTRY[NPKG] = {
    {"httpkit",  {"2.3.0", "2.4.0", "3.0.0"}, 3},
    {"codec",    {"1.4.2", "1.4.3", "1.5.0"}, 3},
    {"compress", {"0.2.7"}, 1},
    {"logfmt",   {"1.0.1", "1.0.2"}, 2},
    {"orm",      {"3.1.0"}, 1},
};

/* 根约束：与 Python 版 ROOT 一致（传递边在此已展开为单条区间） */
static const char *ROOT_SPEC[NPKG] = {
    "^2.0.0",   /* httpkit  */
    "~1.4.2",   /* codec    （来自 httpkit@2.4.0） */
    "~0.2.0",   /* compress （来自 codec@1.4.3）  */
    "^1.0.0",   /* logfmt   */
    "^3.0.0",   /* orm      */
};

static const char *FN[NFN] = {
    "app.main", "httpkit.Handler", "codec.Decode",
    "compress.Inflate", "orm.Find", "orm.RawQuery", "logfmt.Format"
};
enum { F_MAIN, F_HANDLER, F_DECODE, F_INFLATE, F_FIND, F_RAW, F_FMT };

static const int CALLS[NFN][3] = {
    {F_HANDLER, F_FMT, F_FIND},  /* app.main */
    {F_DECODE, -1},              /* httpkit.Handler */
    {-1},                        /* codec.Decode */
    {-1},                        /* compress.Inflate */
    {F_RAW, -1},                 /* orm.Find */
    {-1},                        /* orm.RawQuery */
    {-1},                        /* logfmt.Format */
};

static const char *CVE[4][4] = {
    {"CVE-COMPRESS", "compress", ">=0.2.0 <0.2.8", "compress.Inflate"},
    {"CVE-CODEC",    "codec",    ">=1.4.0 <1.5.0", "codec.Decode"},
    {"CVE-ORM",      "orm",      ">=3.0.0 <3.1.1", "orm.RawQuery"},
    {"CVE-LOGFMT",   "logfmt",   ">=1.0.0 <1.0.2", "logfmt.Format"},
};

static int fn_index(const char *s) {
    int i;
    for (i = 0; i < NFN; i++) if (!strcmp(FN[i], s)) return i;
    return -1;
}

static void reachable(int *seen) {
    int stack[NFN], top = 0;
    memset(seen, 0, sizeof(int) * NFN);
    stack[top++] = F_MAIN;
    while (top) {
        int f = stack[--top], j;
        if (seen[f]) continue;
        seen[f] = 1;
        for (j = 0; j < 3 && CALLS[f][j] >= 0; j++) stack[top++] = CALLS[f][j];
    }
}

static int failures = 0;

static void check(const char *label, int cond) {
    if (!cond) { printf("FAIL: %s\n", label); failures++; }
}

int main(void) {
    Ver a, b;
    Cons cs[MAXC];
    int n, i, j;
    const char *chain[8] = {
        "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
        "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"};

    /* 1) 规范 §11 的优先级链 */
    for (i = 0; i + 1 < 8; i++) {
        parse_ver(chain[i], &a);
        parse_ver(chain[i + 1], &b);
        check("优先级链递增", cmp_ver(a, b) < 0);
    }
    /* build metadata 不参与比较 */
    parse_ver("1.0.0+build1", &a);
    parse_ver("1.0.0+build2", &b);
    check("build metadata 被忽略", cmp_ver(a, b) == 0);
    /* ^ 在 0.x 上只锁 minor */
    range_of("^0.2.3", cs, &n);
    parse_ver("0.3.0", &a);
    check("^0.2.3 拒绝 0.3.0", !satisfies(a, cs, n));
    parse_ver("0.2.9", &a);
    check("^0.2.3 允许 0.2.9", satisfies(a, cs, n));

    /* 2) 逐个包取区间内最高版本 */
    {
        const char *want[NPKG] = {"2.4.0", "1.4.3", "0.2.7", "1.0.2", "3.1.0"};
        printf("%-10s %-8s %s\n", "pkg", "spec", "resolved");
        for (i = 0; i < NPKG; i++) {
            char best[32];
            int have = 0;
            best[0] = 0;
            range_of(ROOT_SPEC[i], cs, &n);
            for (j = 0; j < REGISTRY[i].n; j++) {
                parse_ver(REGISTRY[i].avail[j], &a);
                if (!satisfies(a, cs, n)) continue;
                if (!have) { strcpy(best, REGISTRY[i].avail[j]); have = 1; }
                else { parse_ver(best, &b); if (cmp_ver(a, b) > 0) strcpy(best, REGISTRY[i].avail[j]); }
            }
            printf("%-10s %-8s %s\n", REGISTRY[i].name, ROOT_SPEC[i], best);
            check(REGISTRY[i].name, !strcmp(best, want[i]));
        }
    }

    /* 3) 可达性剪枝 */
    {
        int seen[NFN], by_ver = 0, reach_cnt = 0;
        reachable(seen);
        check("compress.Inflate 不可达", !seen[F_INFLATE]);
        check("codec.Decode 可达", seen[F_DECODE]);
        printf("\n%-14s %-9s %-11s %s\n", "CVE", "pkg", "by_version", "reachable");
        for (i = 0; i < 4; i++) {
            /* 用第 2 步解析出的版本：这里重跑一次求值以保持独立 */
            int hit, rc, k = -1;
            for (j = 0; j < NPKG; j++) if (!strcmp(REGISTRY[j].name, CVE[i][1])) k = j;
            range_of(ROOT_SPEC[k], cs, &n);
            {
                char best[32]; int have = 0; best[0] = 0;
                for (j = 0; j < REGISTRY[k].n; j++) {
                    parse_ver(REGISTRY[k].avail[j], &a);
                    if (!satisfies(a, cs, n)) continue;
                    if (!have) { strcpy(best, REGISTRY[k].avail[j]); have = 1; }
                    else { parse_ver(best, &b); if (cmp_ver(a, b) > 0) strcpy(best, REGISTRY[k].avail[j]); }
                }
                range_of(CVE[i][2], cs, &n);
                parse_ver(best, &a);
                hit = satisfies(a, cs, n);
                rc = hit && seen[fn_index(CVE[i][3])];
            }
            if (hit) by_ver++;
            if (rc) reach_cnt++;
            printf("%-14s %-9s %-11s %s\n", CVE[i][0], CVE[i][1], hit ? "true" : "false", rc ? "true" : "false");
        }
        check("版本命中 3 条", by_ver == 3);
        check("可达告警 2 条", reach_cnt == 2);
    }

    if (failures) { printf("FAILED %d\n", failures); return 1; }
    printf("all checks passed\n");
    return 0;
}
