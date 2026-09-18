/* 缓存一致性模式：写策略竞态枚举 + Facebook lease 的 C 实现。
 *
 * 依据：
 *   - Microsoft Learn《Cache-Aside pattern》：先更新数据存储、再删缓存。
 *   - NSDI'13《Scaling Memcache at Facebook》§3.2.1：lease（64-bit token、
 *     每 key 每 10 秒一个）、stale set / thundering herd、17K/s → 1.3K/s。
 *
 * 构建： gcc -std=c11 -O2 cache_consistency.c -o cc && ./cc
 */
#include <stdio.h>
#include <stdint.h>

static int g_fails = 0;

static void check(const char *label, int cond, const char *detail) {
    if (cond) {
        printf("  ok   %s\n", label);
    } else {
        g_fails++;
        printf("  FAIL %s %s\n", label, detail);
    }
}

/* ---------------------------------------------------------- 竞态枚举 */

typedef enum { W_DEL, W_DBSET, W_ATOMIC_BOTH, R_DBGET, R_CACHESET } Step;

/* 按给定交错序执行。一致 = 缓存无该 key，或缓存值等于库值。 */
static int simulate(const Step *seq, int n) {
    int db = 1;              /* v1 */
    int cache_present = 1, cache = 1, read = 0;
    for (int i = 0; i < n; i++) {
        switch (seq[i]) {
        case W_DEL:         cache_present = 0; break;
        case W_DBSET:       db = 2; break;
        case W_ATOMIC_BOTH: db = 2; cache = 2; cache_present = 1; break;
        case R_DBGET:       read = db; break;
        case R_CACHESET:    cache = read; cache_present = 1; break;
        }
    }
    return !cache_present || cache == db;
}

/* 枚举所有保持各自内部顺序的交错（用位掩码选出写者步骤所在的位置）。 */
static void stale_count(const Step *w, int m, int *total, int *bad) {
    const Step r[2] = { R_DBGET, R_CACHESET };
    const int n = 2, L = m + n;
    *total = 0;
    *bad = 0;
    for (int mask = 0; mask < (1 << L); mask++) {
        int bits = 0;
        for (int i = 0; i < L; i++) if ((mask >> i) & 1) bits++;
        if (bits != m) continue;
        Step seq[8];
        int wi = 0, ri = 0, k = 0;
        for (int i = 0; i < L; i++) {
            seq[k++] = ((mask >> i) & 1) ? w[wi++] : r[ri++];
        }
        (*total)++;
        if (!simulate(seq, L)) (*bad)++;
    }
}

/* ---------------------------------------------------------- Lease */

#define LEASE_TTL 10

typedef struct {
    uint64_t token;
    int epoch;
} Lease;

enum { HIT, MISS, WAIT, STALE };

typedef struct {
    int has, val;                 /* 当前缓存值 */
    int epoch;                    /* 每次 delete 自增，作废全部在途 token */
    int has_last, last_token_at;  /* 上次发 token 的时间（限流用） */
    int dead_has, dead_val;       /* 「最近删除项」结构里的 stale 值 */
    uint64_t seq;                 /* 可复现的 token 发生器 */
    int db_reads;
} Srv;

static uint64_t next_token(Srv *s) {
    s->seq = s->seq * 6364136223846793005ULL + 1442695040888963407ULL;
    return s->seq;
}

/* status: 0=hit 1=miss 2=wait 3=stale */
static int srv_get(Srv *s, int now, int accept_stale, Lease *out) {
    if (s->has) return HIT;
    if (accept_stale && s->dead_has) return STALE;   /* 不消耗 token，不限流 */
    if (s->has_last && now - s->last_token_at < LEASE_TTL) return WAIT;
    s->has_last = 1;
    s->last_token_at = now;
    s->db_reads++;
    if (out) {
        out->token = next_token(s);
        out->epoch = s->epoch;
    }
    return MISS;
}

static int srv_set_lease(Srv *s, int val, const Lease *l) {
    if (l->epoch != s->epoch) return 0;   /* token 已被 delete 作废 */
    s->has = 1;
    s->val = val;
    s->dead_has = 0;
    return 1;
}

static int srv_set_plain(Srv *s, int val) {   /* 无仲裁 —— 会产生 stale set */
    s->has = 1;
    s->val = val;
    s->dead_has = 0;
    return 1;
}

static void srv_delete(Srv *s) {
    if (s->has) { s->dead_has = 1; s->dead_val = s->val; s->has = 0; }
    s->epoch++;
}

/* ---------------------------------------------------------- 自检 */

int main(void) {
    int total, bad;
    const Step del_then_db[] = { W_DEL, W_DBSET };
    const Step db_then_del[] = { W_DBSET, W_DEL };
    const Step dbl_del[] = { W_DBSET, W_DEL, W_DEL };
    const Step wthrough[] = { W_ATOMIC_BOTH };

    printf("[1] 写顺序决定不一致窗口\n");
    stale_count(del_then_db, 2, &total, &bad);
    check("先删缓存再更新库 4/6", total == 6 && bad == 4, "");
    stale_count(db_then_del, 2, &total, &bad);
    check("先更新库再删缓存 1/6", total == 6 && bad == 1, "");
    stale_count(dbl_del, 3, &total, &bad);
    check("延迟双删 1/10", total == 10 && bad == 1, "");
    stale_count(wthrough, 1, &total, &bad);
    check("写穿透(单原子步) 1/3", total == 3 && bad == 1, "");

    printf("[2] 那条坏交错：删 → 读旧值 → 回填 → 库才更新\n");
    const Step badseq[] = { W_DEL, R_DBGET, R_CACHESET, W_DBSET };
    check("留下不一致", simulate(badseq, 4) == 0, "");
    const Step goodseq[] = { W_DEL, W_DBSET, R_DBGET, R_CACHESET };
    check("写完整后读则一致", simulate(goodseq, 4) == 1, "");

    printf("[3] Lease：token 与 delete 作废\n");
    Srv s;
    s = (Srv){0}; s.seq = 0x9E3779B97F4A7C15ULL;
    Lease tok;
    check("冷 key -> miss", srv_get(&s, 1000, 0, &tok) == MISS, "");
    check("回写成功", srv_set_lease(&s, 1, &tok) == 1, "");
    check("读命中", srv_get(&s, 1001, 0, NULL) == HIT, "");
    srv_delete(&s);
    check("删除后带旧 token 的回写被拒", srv_set_lease(&s, 99, &tok) == 0, "");
    check("无 lease 的回写被接受(stale set)", srv_set_plain(&s, 99) == 1, "");
    check("缓存已被污染", s.val == 99, "");

    printf("[4] Stale value 与 10 秒限流\n");
    Srv s2;
    s2 = (Srv){0}; s2.seq = 1;
    Lease t2;
    srv_get(&s2, 2000, 0, &t2);
    srv_set_lease(&s2, 1, &t2);
    srv_delete(&s2);
    check("accept_stale 时返回 stale", srv_get(&s2, 2001, 1, NULL) == STALE, "");
    check("不限流，可连续读 stale", srv_get(&s2, 2002, 1, NULL) == STALE, "");
    check("不接受 stale 则被限流为 wait", srv_get(&s2, 2002, 0, NULL) == WAIT, "");
    Srv s3;
    s3 = (Srv){0}; s3.seq = 7;
    srv_get(&s3, 3000, 0, NULL);
    check("窗口内 wait", srv_get(&s3, 3009, 0, NULL) == WAIT, "");
    check("窗口外可再发 token", srv_get(&s3, 3010, 0, NULL) == MISS, "");

    printf("[5] Thundering herd\n");
    Srv h;
    h = (Srv){0}; h.seq = 3;
    Lease ht;
    int first = srv_get(&h, 1000, 0, &ht);   /* 只有第一个拿到 token */
    int waits = 0;
    for (int i = 1; i < 100; i++) if (srv_get(&h, 1000, 0, NULL) == WAIT) waits++;
    check("首个请求 miss", first == MISS, "");
    check("99 个收到稍等", waits == 99, "");
    check("有 lease 时只打库 1 次", h.db_reads == 1, "");

    printf("\n");
    if (g_fails) { printf("FAILED %d\n", g_fails); return 1; }
    printf("ALL PASS\n");
    return 0;
}
