/* 硬编码凭据检测（C 版）：裸熵阈值 → 归一化熵 → 规则叠加。
 *
 * C 版没有正则，规则用**子串匹配**实现（strstr），判据与 Python / Go 版一致。
 * 重点同样是那条结论：十六进制串的香农熵上界是 log2(16)=4.0 bit，
 * 所以「阈值 4.5」会系统性漏报十六进制密钥；而且存在**任何阈值都分不开**的
 * 密钥/非密钥对（aws_key 的熵比 git_sha 还低）。
 */
#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <math.h>

#define MIN_LEN 20
#define RAW_TH 4.5
#define NORM_TH 0.90
#define NITEM 10

typedef struct { const char *name; const char *value; int is_secret; } Item;

static const Item CORPUS[NITEM] = {
    {"aws_key",    "AKIAIOSFODNN7EXAMPLE", 1},
    {"api_token",  "3f8a1c7d5e2b4096af17c3de85b0f2146e9a7c31", 1},
    {"jwt_secret", "c3VwZXJzZWNyZXR2YWx1ZTEyMzQ1Njc4OTA=", 1},
    {"db_password","password123", 1},
    {"vendor_sk",  "tok_live_51H8xQ2eZvKYlo2Cabcdefghijklm", 1},
    {"git_sha",    "da39a3ee5e6b4b0d3255bfef95601890afd80709", 0},
    {"request_id", "550e8400-e29b-41d4-a716-446655440000", 0},
    {"png_b64",    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ", 0},
    {"api_url",    "https://api.example.com/v1/users?id=12345", 0},
    {"version",    "1.2.3", 0},
};

/* ---- 香农熵 ---- */

static int alphabet_size(const char *s) {
    int seen[256], i, n = 0;
    memset(seen, 0, sizeof seen);
    for (i = 0; s[i]; i++) {
        unsigned char c = (unsigned char)s[i];
        if (!seen[c]) { seen[c] = 1; n++; }
    }
    return n;
}

static double shannon(const char *s) {
    int cnt[256], i, n = 0;
    double h = 0.0;
    memset(cnt, 0, sizeof cnt);
    for (i = 0; s[i]; i++) { cnt[(unsigned char)s[i]]++; n++; }
    if (!n) return 0.0;
    for (i = 0; i < 256; i++) {
        double p;
        if (!cnt[i]) continue;
        p = (double)cnt[i] / n;
        h -= p * log2(p);
    }
    return h;
}

/* H / log2(|A|)：不同字符集之间才可比 */
static double normalized(const char *s) {
    int a = alphabet_size(s);
    if (a <= 1) return 0.0;
    return shannon(s) / log2((double)a);
}

/* ---- 规则（子串匹配，无正则）---- */

static int contains_ci(const char *hay, const char *needle) {
    int i, j, hl = (int)strlen(hay), nl = (int)strlen(needle);
    for (i = 0; i + nl <= hl; i++) {
        int ok = 1;
        for (j = 0; j < nl; j++)
            if (tolower((unsigned char)hay[i + j]) != tolower((unsigned char)needle[j])) { ok = 0; break; }
        if (ok) return 1;
    }
    return 0;
}

static int name_has_keyword(const char *n) {
    const char *kw[] = {"password", "passwd", "secret", "token",
                        "api_key", "apikey", "credential", "private_key"};
    int i;
    for (i = 0; i < 8; i++) if (contains_ci(n, kw[i])) return 1;
    return 0;
}

static int name_allowlisted(const char *n) {
    const char *wl[] = {"sha", "commit", "digest", "checksum", "uuid",
                        "request_id", "version", "hash"};
    int i;
    for (i = 0; i < 8; i++) if (contains_ci(n, wl[i])) return 1;
    return 0;
}

static int value_rule_hit(const char *v) {
    if (strstr(v, "AKIA") && strlen(v) >= 20) return 1;
    if (strstr(v, "tok_live_")) return 1;
    if (strstr(v, "-----BEGIN")) return 1;
    return 0;
}

/* ---- 三级检测器 ---- */

static int d1(const char *n, const char *v) {
    (void)n;
    return (int)strlen(v) >= MIN_LEN && shannon(v) >= RAW_TH;
}

static int d2(const char *n, const char *v) {
    (void)n;
    return (int)strlen(v) >= MIN_LEN && normalized(v) >= NORM_TH;
}

static int d3(const char *n, const char *v) {
    if (name_allowlisted(n)) return 0;         /* 上下文白名单优先 */
    if (value_rule_hit(v)) return 1;
    if (d2(n, v)) return 1;
    return name_has_keyword(n) && shannon(v) >= 3.0;
}

static void evaluate(int (*det)(const char *, const char *), int *tp, int *fp, int *fn, int *tn) {
    int i;
    *tp = *fp = *fn = *tn = 0;
    for (i = 0; i < NITEM; i++) {
        int got = det(CORPUS[i].name, CORPUS[i].value);
        if (CORPUS[i].is_secret && got)      (*tp)++;
        else if (CORPUS[i].is_secret)        (*fn)++;
        else if (got)                        (*fp)++;
        else                                 (*tn)++;
    }
}

static void sweep(int *max_tp, int *max_tp_zero_fp) {
    double t;
    *max_tp = *max_tp_zero_fp = 0;
    for (t = 3.0; t <= 5.0001; t += 0.01) {
        int tp = 0, fp = 0, i;
        for (i = 0; i < NITEM; i++) {
            if ((int)strlen(CORPUS[i].value) < MIN_LEN) continue;
            if (shannon(CORPUS[i].value) >= t) {
                if (CORPUS[i].is_secret) tp++; else fp++;
            }
        }
        if (tp > *max_tp) *max_tp = tp;
        if (fp == 0 && tp > *max_tp_zero_fp) *max_tp_zero_fp = tp;
    }
}

static int failures = 0;

static void check(const char *label, int cond, const char *detail) {
    if (!cond) { printf("FAIL: %s %s\n", label, detail ? detail : ""); failures++; }
}

static const char *value_of(const char *name) {
    int i;
    for (i = 0; i < NITEM; i++) if (!strcmp(CORPUS[i].name, name)) return CORPUS[i].value;
    return "";
}

int main(void) {
    int tp, fp, fn, tn, max_tp, max_zero;
    char detail[128];

    /* 1) 熵的数学性质 */
    check("空串熵为 0", shannon("") == 0.0, "");
    check("单字符串为 0", shannon("aaaa") == 0.0, "");
    sprintf(detail, "H=%.4f", shannon("0123456789abcdef"));
    check("均匀 16 符号 = 4.0", fabs(shannon("0123456789abcdef") - 4.0) < 1e-9, detail);
    check("偏斜 < 均匀", shannon("aaaaaabc") < shannon("abcdefab"), "");

    /* 2) 十六进制串上界 4.0 → 阈值 4.5 必然漏报 */
    {
        const char *hex40 = value_of("api_token");
        sprintf(detail, "H=%.4f", shannon(hex40));
        check("40 位十六进制 < 4.5", shannon(hex40) < 4.5, detail);
        sprintf(detail, "norm=%.4f", normalized(hex40));
        check("归一化后 > 0.95", normalized(hex40) > 0.95, detail);
    }

    /* 3) 三级检测器 */
    printf("%-22s %-4s %-4s %-4s %-4s\n", "detector", "TP", "FP", "FN", "TN");
    evaluate(d1, &tp, &fp, &fn, &tn);
    printf("%-22s %-4d %-4d %-4d %-4d\n", "D1 raw>=4.5", tp, fp, fn, tn);
    check("D1 = 2/1/3/4", tp == 2 && fp == 1 && fn == 3 && tn == 4, "");
    evaluate(d2, &tp, &fp, &fn, &tn);
    printf("%-22s %-4d %-4d %-4d %-4d\n", "D2 norm>=0.90", tp, fp, fn, tn);
    check("D2 = 4/2/1/3", tp == 4 && fp == 2 && fn == 1 && tn == 3, "");
    evaluate(d3, &tp, &fp, &fn, &tn);
    printf("%-22s %-4d %-4d %-4d %-4d\n", "D3 rule+norm", tp, fp, fn, tn);
    check("D3 = 5/1/0/4", tp == 5 && fp == 1 && fn == 0 && tn == 4, "");

    /* 4) 不可分性：H(aws_key) < H(git_sha) */
    {
        double ha = shannon(value_of("aws_key")), hb = shannon(value_of("git_sha"));
        sprintf(detail, "%.4f vs %.4f", ha, hb);
        check("H(aws_key) < H(git_sha)", ha < hb, detail);
        check("任何裸熵阈值都分不开二者", !(ha >= hb), detail);
    }

    /* 5) 阈值扫描 */
    sweep(&max_tp, &max_zero);
    sprintf(detail, "max_tp=%d zero_fp_tp=%d", max_tp, max_zero);
    check("零误报时召回最多 1 条", max_zero == 1, detail);
    check("最高召回 4 条", max_tp == 4, detail);

    if (failures) { printf("FAILED %d\n", failures); return 1; }
    printf("all checks passed\n");
    return 0;
}
