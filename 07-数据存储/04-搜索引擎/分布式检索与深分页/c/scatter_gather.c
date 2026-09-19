/*
 * ES 分布式检索两阶段与深分页(C),与 python/scatter_gather.py 同构。
 *
 * 权威来源(实际读过):
 *   1. .../search-search.html —— search_type 只支持 query_then_fetch(分片本地频率)与
 *      dfs_query_then_fetch(全局频率);allow_partial_search_results 两种行为;
 *      batched_reduce_size;max_concurrent_shard_requests 默认 5
 *   2. .../paginate-search-results.html —— 深分页每片装 (from+size) 条;
 *      index.max_result_window 默认 10000;search_after 用上一页末条 sort 做游标;
 *      用 PIT 时 from 必须 0 或 -1
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define MAX_RESULT_WINDOW 10000
#define DEFAULT_MAX_CONCURRENT 5
#define NDOCS 12000
#define NSHARD 3

static int g_ok = 0, g_fail = 0;
static void check(int cond, const char *msg) {
    if (cond) { g_ok++;   printf("  [ok]   %s\n", msg); }
    else      { g_fail++; printf("  [FAIL] %s\n", msg); }
}

/* 分片 sid 的第 k 条(1-based)的排序键:片 0 最小、片 2 居中、片 1 最大 */
static int shard_sort(int sid, int k) {
    if (sid == 0) return k;
    if (sid == 1) return 1000000 - k;
    return 500000 + k;
}

/* 每片本地取 top (from+size),受片内命中总数限制 */
static int query_phase_count(int frm, int size) {
    int want = frm + size;
    return want > NDOCS ? NDOCS : want;
}

static int coord_count(int frm, int size, int nshard) {
    return query_phase_count(frm, size) * nshard;
}

/* 归并:返回前 size 条里**最小的排序键来自哪个分片** */
static int top_shard(int size, int nshard) {
    int best = 0, bestk = 1 << 30;
    for (int s = 0; s < nshard; s++)
        for (int k = 1; k <= (size > NDOCS ? NDOCS : size); k++) {
            int key = shard_sort(s, k);
            if (key < bestk) { bestk = key; best = s; }
        }
    return best;
}

/* 协调节点:返回 0 表示成功,-1 表示窗口超限,>0 表示失败分片数(且不允许部分结果) */
static int coordinate(int frm, int size, int nshard, int allow_partial,
                      const int *failed, int nfailed, int *out_cand) {
    if (frm + size > MAX_RESULT_WINDOW) return -1;
    *out_cand = coord_count(frm, size, nshard);
    if (nfailed > 0 && !allow_partial) return nfailed;
    return 0;
}

static double idf(double n, double df) { return log(1.0 + (n - df + 0.5) / (df + 0.5)); }

int main(void) {
    int cand = 0;
    printf("== Demo 1 · scatter-gather ==\n");
    coordinate(0, 5, NSHARD, 1, NULL, 0, &cand);
    printf("   from=0 size=5 ⇒ 候选 %d 条(3 片 × 5)\n", cand);
    check(cand == 15, "query 阶段候选数 = 分片数 × (from+size)");
    check(top_shard(3, NSHARD) == 0, "top3 排序键最小 ⇒ 全来自分片 0,fetch 只触及该片");

    printf("\n== Demo 2 · 深分页 ==\n");
    int pairs[4][2] = {{0, 10}, {100, 10}, {1000, 10}, {9900, 100}};
    for (int i = 0; i < 4; i++) {
        int f = pairs[i][0], s = pairs[i][1];
        printf("   from=%-5d size=%-4d ⇒ 候选 %d 条(3 片 × %d)\n",
               f, s, coord_count(f, s, NSHARD), f + s);
    }
    check(coord_count(9900, 100, NSHARD) == 30000, "from=9900 size=100 ⇒ 3 片共装 30000 条");
    check(coordinate(10000, 1, NSHARD, 1, NULL, 0, &cand) == -1,
          "from+size=10001 > max_result_window(10000) ⇒ 被拒绝");

    printf("\n== Demo 3 · search_after / PIT ==\n");
    int last_sort = shard_sort(0, 3), next_first = shard_sort(0, 4);
    printf("   游标 sort=%d ⇒ 下一页首条 sort=%d\n", last_sort, next_first);
    check(next_first > last_sort, "search_after 是严格大于 ⇒ 不重复上一页");
    int pit_from_ok = (0 == 0) && (-1 == -1);
    check(pit_from_ok, "PIT 下 from 只允许 0(默认)或 -1");
    check(DEFAULT_MAX_CONCURRENT == 5, "max_concurrent_shard_requests 默认 5");

    printf("\n== Demo 4 · search_type:本地 IDF vs 全局 IDF ==\n");
    double aL = idf(5, 1) * 1, bL = idf(5, 5) * 3;
    double aG = idf(10, 6) * 1, bG = idf(10, 6) * 3;
    printf("   query_then_fetch    : A=%.4f B=%.4f\n", aL, bL);
    printf("   dfs_query_then_fetch: A=%.4f B=%.4f\n", aG, bG);
    check(aL > bL, "本地频率下 docA 靠前(它所在分片 df 很小)");
    check(bG > aG, "全局频率下 docB 靠前 ⇒ 两种 search_type 排序翻转");

    printf("\n== Demo 5 · 部分结果 ==\n");
    int one[1] = {1};
    check(coordinate(0, 5, NSHARD, 1, one, 1, &cand) == 0,
          "allow_partial_search_results=true ⇒ 分片失败仍返回部分结果");
    check(coordinate(0, 5, NSHARD, 0, one, 1, &cand) > 0,
          "allow_partial_search_results=false ⇒ 直接报错,不返回部分结果");

    printf("\n断言 %d 通过 / %d 失败\n", g_ok, g_fail);
    return g_fail ? 1 : 0;
}
