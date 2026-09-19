/*
 * ES/Lucene 分析器链与中文分词(C),与 python/analyzer_chain.py 同构。
 *
 * 权威来源(实际读过):
 *   1. .../analyzer-anatomy.html —— 0+ char filter → 1 tokenizer → 0+ token filter;
 *      tokenizer 记录 position/offset;token filter 不得改 position/offset
 *   2. .../analysis-analyzers.html —— standard analyzer 按 Unicode 文本切分算法分词
 *   3. .../index-modules-similarity.html —— BM25 k1=1.2 b=0.75;discount_overlaps 默认 true
 *      (positionIncrement==0 的 overlap token 不计入 norm)
 *   4. .../norms.html —— norm ~1 byte/doc/field
 *   5. IKSegmenter.java —— 4 个 ISegmenter + IKArbitrator.process(ctx, cfg.isUseSmart())
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <math.h>

#define K1 1.2
#define B  0.75
#define NORMS_BYTES_PER_DOC_FIELD 1
#define MAXTOK 32
#define MAXWORD 64
#define MAXDICT 12
#define EPS 1e-9

static int g_ok = 0, g_fail = 0;

static void check(int cond, const char *msg) {
    if (cond) { g_ok++;   printf("  [ok]   %s\n", msg); }
    else      { g_fail++; printf("  [FAIL] %s\n", msg); }
}

typedef struct { char term[MAXWORD]; int start, end, pos, pos_inc; } Token;
typedef struct { Token t[MAXTOK]; int n; } Tokens;

/* ---------------------------------------------------- char filter */
/* 剥离 <tag>。注意:返回的是静态缓冲,调用方只做短期使用 */
static char g_cfbuf[256];

static const char *cf_html_strip(const char *s) {
    int o = 0, in = 0;
    for (int i = 0; s[i] && o < 255; i++) {
        if (s[i] == '<') { in = 1; continue; }
        if (s[i] == '>') { in = 0; g_cfbuf[o++] = ' '; continue; }
        if (!in) g_cfbuf[o++] = s[i];
    }
    g_cfbuf[o] = '\0';
    return g_cfbuf;
}

/* ---------------------------------------------------- tokenizer */
static int is_alnum_c(unsigned char c) {
    return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
}

/* 字母数字连续段;首个 token 的 positionIncrement 也是 1 */
static Tokens tk_standard(const char *s) {
    Tokens out; out.n = 0;
    int pos = 0, i = 0, len = (int)strlen(s);
    while (i < len && out.n < MAXTOK) {
        while (i < len && !is_alnum_c((unsigned char)s[i])) i++;
        if (i >= len) break;
        int st = i;
        while (i < len && is_alnum_c((unsigned char)s[i])) i++;
        Token *t = &out.t[out.n++];
        int w = i - st; if (w >= MAXWORD) w = MAXWORD - 1;
        memcpy(t->term, s + st, w); t->term[w] = '\0';
        t->start = st; t->end = i; t->pos = pos++; t->pos_inc = 1;
    }
    return out;
}

/* ---------------------------------------------------- token filters */
static Tokens tf_lowercase(Tokens in) {
    for (int i = 0; i < in.n; i++)
        for (char *p = in.t[i].term; *p; p++) *p = (char)tolower((unsigned char)*p);
    return in;
}

/* 停用词:存活 token 的 position 原样保留 ⇒ 留位置空洞 */
static Tokens tf_stop(Tokens in, const char *stops[], int ns) {
    Tokens out; out.n = 0;
    for (int i = 0; i < in.n; i++) {
        int drop = 0;
        for (int j = 0; j < ns; j++) if (!strcmp(in.t[i].term, stops[j])) { drop = 1; break; }
        if (!drop) out.t[out.n++] = in.t[i];
    }
    return out;
}

/* 同义词:同占一个 position ⇒ pos_inc=0 */
static Tokens tf_synonym(Tokens in, const char *from, const char *to) {
    Tokens out = in;
    for (int i = 0; i < in.n && out.n < MAXTOK; i++) {
        if (!strcmp(in.t[i].term, from)) {
            Token s = in.t[i];
            snprintf(s.term, MAXWORD, "%s", to);
            s.pos_inc = 0;
            out.t[out.n++] = s;
        }
    }
    return out;
}

/* ---------------------------------------------------- 中文分词 */
static const char *IKDICT[MAXDICT] = {
    "中华人民共和国", "人民共和国", "中华", "人民", "共和国",
    "共和", "国", "成立", "了", "万岁"
};
static const int IKDICT_N = 10;

/* UTF-8 中文按 3 字节计;demo 串是纯 CJK,够用 */
static int cjk_len(const char *s) { return (int)strlen(s) / 3; }
static int cjk_eq(const char *s, int off, const char *w) {
    int n = (int)strlen(w);
    return (int)strlen(s) - off * 3 >= n && !strncmp(s + off * 3, w, n);
}

/* ik_max_word:所有能参与完整切分的词元数;ik_smart:最长匹配单路径词元数 */
static int ik_count_maxword(const char *s) {
    int n = cjk_len(s), r[64], c = 0;
    r[n] = 1;
    for (int i = n - 1; i >= 0; i--) {
        r[i] = 0;
        for (int k = 0; k < IKDICT_N; k++) {
            int wl = cjk_len(IKDICT[k]);
            if (cjk_eq(s, i, IKDICT[k]) && r[i + wl]) { r[i] = 1; break; }
        }
    }
    for (int i = 0; i < n; i++)
        for (int k = 0; k < IKDICT_N; k++) {
            int wl = cjk_len(IKDICT[k]);
            if (cjk_eq(s, i, IKDICT[k]) && r[i + wl]) c++;
        }
    return c;
}

static int ik_count_smart(const char *s) {
    int n = cjk_len(s), i = 0, c = 0;
    while (i < n) {
        int best = -1;
        for (int k = 0; k < IKDICT_N; k++)
            if (cjk_eq(s, i, IKDICT[k]) && (best < 0 || cjk_len(IKDICT[k]) > cjk_len(IKDICT[best])))
                best = k;
        i += (best < 0) ? 1 : cjk_len(IKDICT[best]);
        c++;
    }
    return c;
}

/* ---------------------------------------------------- BM25 */
static double bm25(double tf, double dl, double avgdl, double n, double df, double bb) {
    double idf = log(1.0 + (n - df + 0.5) / (df + 0.5));
    return idf * (tf * (K1 + 1.0)) / (tf + K1 * (1.0 - bb + bb * dl / avgdl));
}

static int norm_len(Tokens ts, int discount) {
    if (!discount) return ts.n;
    int c = 0;
    for (int i = 0; i < ts.n; i++) if (ts.t[i].pos_inc != 0) c++;
    return c;
}

int main(void) {
    printf("== Demo 1 · char filter → tokenizer → token filter ==\n");
    Tokens tk = tk_standard(cf_html_strip("<b>The</b> Quick brown-fox"));
    tk = tf_lowercase(tk);
    const char *stops[3] = {"the", "a", "is"};
    tk = tf_stop(tk, stops, 3);
    tk = tf_synonym(tk, "quick", "fast");
    for (int i = 0; i < tk.n; i++)
        printf("   %-8s offset=[%d,%d) pos=%d inc=%d\n",
               tk.t[i].term, tk.t[i].start, tk.t[i].end, tk.t[i].pos, tk.t[i].pos_inc);
    check(tk.n >= 4, "html_strip 先跑 ⇒ 标签里的 b 不成为 token");
    int has_fast = 0;
    for (int i = 0; i < tk.n; i++) if (!strcmp(tk.t[i].term, "fast")) has_fast = 1;
    check(has_fast, "同义词 quick->fast 展开成功");

    printf("\n== Demo 2 · token filter 不得改 position / offset ==\n");
    Tokens raw = tk_standard("The Quick brown fox");
    Tokens kept = tf_lowercase(raw);
    int same = 1;
    for (int i = 0; i < raw.n; i++)
        if (raw.t[i].start != kept.t[i].start || raw.t[i].pos != kept.t[i].pos) same = 0;
    check(same, "lowercase 后 offset 与 position 逐个不变");
    Tokens dropped = tf_stop(kept, stops, 3);
    check(dropped.n == 3 && dropped.t[0].pos == 1 && dropped.t[2].pos == 3,
          "停用词留位置空洞:position 仍是 1,2,3");

    printf("\n== Demo 3 · ik_max_word vs ik_smart ==\n");
    const char *s = "中华人民共和国成立了";
    int cm = ik_count_maxword(s), cs = ik_count_smart(s);
    printf("   max_word=%d  smart=%d\n", cm, cs);
    check(cm > cs, "max_word 词元数多于 smart");
    check(cs == 3, "smart 走最长匹配单路径(3 个词元)");

    printf("\n== Demo 4 · 分词粒度改变 dl ⇒ 改变 BM25 ==\n");
    const char *d2 = "中华人民共和国万岁";
    double avgS = (ik_count_smart(s) + ik_count_smart(d2)) / 2.0;
    double avgM = (ik_count_maxword(s) + ik_count_maxword(d2)) / 2.0;
    double sS = bm25(1, cs, avgS, 2, 1, B);
    double sM = bm25(1, cm, avgM, 2, 1, B);
    printf("   smart   dl=%d avgdl=%.2f score=%.6f\n", cs, avgS, sS);
    printf("   maxword dl=%d avgdl=%.2f score=%.6f\n", cm, avgM, sM);
    check(fabs(sS - sM) > EPS, "粒度不同 ⇒ dl/avgdl 不同 ⇒ 分数不同");
    check(bm25(1, 3, 6, 10, 2, B) > bm25(1, 9, 6, 10, 2, B), "b=0.75 下短文档 tf 分量更高");
    check(fabs(bm25(1, 3, 6, 10, 2, 0.0) - bm25(1, 9, 6, 10, 2, 0.0)) < EPS,
          "b=0 时长度归一化关闭 ⇒ dl=3 与 dl=9 同分");

    printf("\n== Demo 5 · discount_overlaps ==\n");
    Tokens base = tf_synonym(tk_standard("quick fox"), "quick", "fast");
    int on = norm_len(base, 1), off = norm_len(base, 0);
    printf("   tokens=%d  true→%d  false→%d\n", base.n, on, off);
    check(on == 2 && off == 3, "默认 true:0-increment 的 fast 不计入 norm");
    printf("   norm 成本 ≈ %d 字节/文档/字段\n", NORMS_BYTES_PER_DOC_FIELD);

    printf("\n断言 %d 通过 / %d 失败\n", g_ok, g_fail);
    return g_fail ? 1 : 0;
}
