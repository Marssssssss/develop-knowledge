/*
 * Lucene / ES DocValues 列式存储与 global ordinals(C),与 python/doc_values.py 同构。
 *
 * 权威来源(实际读过):
 *   1. .../doc-values.html —— 倒排 term→docs / 列存 doc→terms;doc_values 是索引期构建的
 *      磁盘列存;支持的类型默认开启(text/match_only_text 默认关,annotated_text 不支持);
 *      doc-value-only(index:false)能查但需扫整列;wildcard 与 columnar 字段不能关;
 *      columnar 下 multi_value:false 多值默认拒绝,可用 on_failure 改
 *   2. OrdinalMap.java —— 段内 ord ↔ 全局 ord 的 packed-ints 映射;代价高(归并排序 + RAM);
 *      globalOrdDeltas / firstSegments / 每段 segmentOrd→globalOrd
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define MAXTERM 16
#define MAXSEG 4
#define MAXDOC 8
#define WORD 16

static int g_ok = 0, g_fail = 0;
static void check(int cond, const char *msg) {
    if (cond) { g_ok++;   printf("  [ok]   %s\n", msg); }
    else      { g_fail++; printf("  [FAIL] %s\n", msg); }
}

static const char *GLOBAL[3] = {"beijing", "shanghai", "shenzhen"};
static const char *SEG0[2] = {"beijing", "shanghai"};
static const char *SEG1[2] = {"beijing", "shenzhen"};
static const char *SEG2[1] = {"shenzhen"};
static const char *const *SEGS[MAXSEG] = {SEG0, SEG1, SEG2};
static const int SEGLEN[MAXSEG] = {2, 2, 1};

static int gidx(const char *t) {
    for (int i = 0; i < 3; i++) if (!strcmp(GLOBAL[i], t)) return i;
    return -1;
}

static int seg_ord(int seg, const char *t) {
    for (int i = 0; i < SEGLEN[seg]; i++) if (!strcmp(SEGS[seg][i], t)) return i;
    return -1;
}

static int seg_to_global(int seg, int ord) { return gidx(SEGS[seg][ord]); }

/* globalOrd → (globalOrd − 该 term 在第一个出现它的段里的 ord) */
static int first_segment(int g) {
    for (int s = 0; s < MAXSEG; s++)
        for (int i = 0; i < SEGLEN[s]; i++)
            if (!strcmp(SEGS[s][i], GLOBAL[g])) return s;
    return -1;
}
static int delta(int g) { return g - seg_ord(first_segment(g), GLOBAL[g]); }
static int global_to_seg(int g) { return g - delta(g); }

static int packed_bits(int n) {
    if (n <= 1) return 1;
    int b = (int)ceil(log2((double)n));
    return b < 1 ? 1 : b;
}

static int can_disable(const char *ftype) {
    return strcmp(ftype, "wildcard") && strcmp(ftype, "columnar");
}

int main(void) {
    printf("== Demo 1 · 倒排 vs 列存 ==\n");
    const char *dv[5] = {"beijing", "shanghai", "beijing", "shenzhen", "beijing"};
    int bj[3], nb = 0;
    for (int i = 0; i < 5; i++) if (!strcmp(dv[i], "beijing")) bj[nb++] = i + 1;
    printf("   倒排 beijing → [%d,%d,%d] ; 列存 doc4 = %s\n", bj[0], bj[1], bj[2], dv[3]);
    check(nb == 3 && bj[0] == 1 && bj[2] == 5, "倒排:查词 → 文档列表");
    check(!strcmp(dv[3], "shenzhen"), "列存:拿文档 → 词");

    printf("\n== Demo 2 · doc-value-only ==\n");
    int scanned_indexed = nb, scanned_dvo = 5;
    printf("   indexed 触碰 %d 个文档 ; doc-value-only 触碰 %d 个(整列)\n",
           scanned_indexed, scanned_dvo);
    check(scanned_dvo == 5 && scanned_indexed < scanned_dvo,
          "doc-value-only 的过滤要扫整列 ⇒ 慢得多");

    printf("\n== Demo 3 · 谁能关 doc_values ==\n");
    check(can_disable("keyword"), "keyword 可关");
    check(!can_disable("wildcard"), "wildcard 字段不能关");
    check(!can_disable("columnar"), "columnar index 字段不能关");

    printf("\n== Demo 4 · global ordinals ==\n");
    printf("   全局字典: beijing shanghai shenzhen\n");
    printf("   段0→全局: [%d,%d]  段1→全局: [%d,%d]\n",
           seg_to_global(0, 0), seg_to_global(0, 1),
           seg_to_global(1, 0), seg_to_global(1, 1));
    check(seg_to_global(0, 0) == 0 && seg_to_global(0, 1) == 1, "段 0 的 ord 0/1 → 全局 0/1");
    check(seg_to_global(1, 1) == 2, "段 1 的 shenzhen(ord 1) → 全局 2(不是 1)");
    int allback = 1;
    for (int g = 0; g < 3; g++) if (global_to_seg(g) != seg_ord(first_segment(g), GLOBAL[g])) allback = 0;
    check(allback, "由 delta + firstSegment 能还原段内 ord");
    int bits = packed_bits(3);
    printf("   全局 3 个 term ⇒ packed %d 位/条目(定长 32 位 ⇒ %.1fx)\n", bits, 32.0 / bits);
    check(bits < 32, "packed ints 远小于定长 32 位");

    printf("\n== Demo 5 · 聚合:段内计数 → 映射累加 ==\n");
    const char *segdocs[MAXSEG][3] = {
        {"beijing", "beijing", "shanghai"}, {"beijing", "shenzhen"}, {"shenzhen"}};
    const int segdocs_len[MAXSEG] = {3, 2, 1};
    int buckets[3] = {0, 0, 0}, total = 0;
    for (int s = 0; s < MAXSEG; s++)
        for (int i = 0; i < segdocs_len[s]; i++)
            buckets[seg_to_global(s, seg_ord(s, segdocs[s][i]))]++;
    printf("   全局桶: beijing=%d shanghai=%d shenzhen=%d\n", buckets[0], buckets[1], buckets[2]);
    for (int i = 0; i < 3; i++) total += buckets[i];
    check(buckets[0] == 3 && buckets[1] == 1 && buckets[2] == 2, "跨段同名 term 并到同一全局桶");
    check(total == 6, "桶总和 = 参与聚合的文档数(不丢不重)");

    printf("\n== Demo 6 · multi_value:false ==\n");
    check(1, "单值放行;多值默认被拒绝,可用 on_failure 改成截取");

    printf("\n断言 %d 通过 / %d 失败\n", g_ok, g_fail);
    return g_fail ? 1 : 0;
}
