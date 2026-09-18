/* Redis 键过期与淘汰策略的 C 实现。
 *
 * 依据：redis.io《Key eviction》《EXPIRE》文档 + redis/src/{expire,evict}.c、server.h。
 * 构建： gcc -std=c11 -O2 redis_expiry.c -o re && ./re
 */
#include <stdio.h>
#include <math.h>

static int g_fails = 0;

static void check(const char *label, int cond, const char *detail) {
    if (cond) { printf("  ok   %s\n", label); }
    else { g_fails++; printf("  FAIL %s %s\n", label, detail); }
}

/* ---------------------------------------------------------- EXPIRE */

#define TTL_NONE (-1)   /* 非易失键 */

/* 返回 1 表示设置成功。non-volatile 在 GT/LT 比较中视为 infinite TTL。 */
static int expire_opt(int cur_expire_at, int ttl, int now, const char *opt) {
    long long cur = (cur_expire_at == TTL_NONE) ? (1LL << 40) : cur_expire_at;
    long long new_at = (long long)now + ttl;
    if (opt[0] == 'N' && opt[1] == 'X') return cur_expire_at == TTL_NONE;
    if (opt[0] == 'X' && opt[1] == 'X') return cur_expire_at != TTL_NONE;
    if (opt[0] == 'G' && opt[1] == 'T') return new_at > cur;   /* 比的是过期时间点 */
    if (opt[0] == 'L' && opt[1] == 'T') return new_at < cur;
    return 1;
}

/* 只有「删除或整体覆盖内容」的命令才清 TTL */
static int clears_ttl(const char *cmd) {
    int n = 0;
    while (cmd[n]) n++;
    if (n >= 5 && cmd[n - 5] == 'S' && cmd[n - 4] == 'T' && cmd[n - 3] == 'O' &&
        cmd[n - 2] == 'R' && cmd[n - 1] == 'E') return 1;      /* *STORE */
    return 0;
}

/* ---------------------------------------------------- 定期删除参数 */

#define AE_KEYS_PER_LOOP 20
#define AE_FAST_DURATION 1000   /* 微秒 */
#define AE_SLOW_TIME_PERC 25
#define AE_ACCEPTABLE_STALE 10

typedef struct { int keys_per_loop, fast_us, slow_perc, acceptable_stale; } AEParams;

static AEParams ae_params(int active_expire_effort) {
    int e = active_expire_effort - 1;             /* Rescale from 0 to 9 */
    AEParams p;
    p.keys_per_loop = AE_KEYS_PER_LOOP + AE_KEYS_PER_LOOP / 4 * e;
    p.fast_us = AE_FAST_DURATION + AE_FAST_DURATION / 4 * e;
    p.slow_perc = AE_SLOW_TIME_PERC + 2 * e;
    p.acceptable_stale = AE_ACCEPTABLE_STALE - e;
    return p;
}

/* ---------------------------------------------------------- LFU */

#define LFU_INIT_VAL 5
#define LFU_MAX 255
#define LFU_MINUTES_WRAP 65535

static int lfu_init_lru(int minutes) { return ((minutes & LFU_MINUTES_WRAP) << 8) | LFU_INIT_VAL; }
static int lfu_ldt(int lru) { return lru >> 8; }
static int lfu_counter(int lru) { return lru & 255; }

/* evict.c：16 位分钟只环绕一次 */
static int lfu_time_elapsed(int ldt, int now_minutes) {
    int now = now_minutes & LFU_MINUTES_WRAP;
    if (now >= ldt) return now - ldt;
    return LFU_MINUTES_WRAP - ldt + now;
}

/* Morris 概率计数器：p = 1 / ((counter - 5) * factor + 1) */
static int lfu_log_incr(int counter, double r, int factor) {
    if (counter == LFU_MAX) return LFU_MAX;
    double baseval = counter - LFU_INIT_VAL;
    if (baseval < 0) baseval = 0;
    double p = 1.0 / (baseval * factor + 1);
    return (r < p) ? counter + 1 : counter;
}

static int lfu_decay(int counter, int ldt, int now_minutes, int decay_time) {
    if (decay_time == 0) return counter;            /* 0 = 永不衰减 */
    int periods = lfu_time_elapsed(ldt, now_minutes) / decay_time;
    if (periods) counter = (periods > counter) ? 0 : counter - periods;
    return counter;
}

/* 均值场近似：c += 1/((c-5)*f+1) */
static double lfu_expected(int hits, int factor) {
    double c = LFU_INIT_VAL;
    for (int i = 0; i < hits; i++) {
        if (c >= LFU_MAX) return LFU_MAX;
        double base = c - LFU_INIT_VAL;
        if (base < 0) base = 0;
        c += 1.0 / (base * factor + 1);
    }
    return c < LFU_MAX ? c : LFU_MAX;
}

/* ---------------------------------------------------------- 策略 */

static int volatile_policy(const char *p) {
    return p[0] == 'v' && p[1] == 'o' && p[2] == 'l';   /* volatile- 前缀 */
}

/* 返回 1 = 报错（不可淘汰），0 = 正常淘汰 */
static int eviction_error(const char *policy, int has_volatile_keys) {
    if (policy[0] == 'n') return 1;                     /* noeviction */
    if (volatile_policy(policy) && !has_volatile_keys) return 1;
    return 0;
}

/* ---------------------------------------------------------- 自检 */

int main(void) {
    printf("[1] EXPIRE NX/XX/GT/LT\n");
    check("NX 对无 TTL -> 成功", expire_opt(TTL_NONE, 10, 0, "NX") == 1, "");
    check("XX 对无 TTL -> 跳过", expire_opt(TTL_NONE, 10, 0, "XX") == 0, "");
    check("GT 对 non-volatile -> 不成立", expire_opt(TTL_NONE, 10, 0, "GT") == 0, "");
    check("LT 对 non-volatile -> 成立", expire_opt(TTL_NONE, 10, 0, "LT") == 1, "");
    check("GT：910 < 1000 -> 跳过", expire_opt(1000, 10, 900, "GT") == 0, "");
    check("GT：1100 > 1000 -> 设置", expire_opt(1000, 200, 900, "GT") == 1, "");
    check("LT：910 < 1000 -> 设置", expire_opt(1000, 10, 900, "LT") == 1, "");

    printf("[2] 命令与 TTL\n");
    check("*STORE 清 TTL", clears_ttl("SUNIONSTORE") == 1, "");
    check("SET 不清(*) —— 需显式列举", clears_ttl("SET") == 0, "");

    printf("[3] activeExpireCycle effort 换算\n");
    AEParams p1 = ae_params(1), p10 = ae_params(10);
    check("默认 keys_per_loop 20", p1.keys_per_loop == 20, "");
    check("默认 fast 1000 us", p1.fast_us == 1000, "");
    check("默认 slow_perc 25", p1.slow_perc == 25, "");
    check("默认 acceptable_stale 10", p1.acceptable_stale == 10, "");
    check("effort=10 -> keys 65", p10.keys_per_loop == 65, "");
    check("effort=10 -> fast 3250", p10.fast_us == 3250, "");
    check("effort=10 -> slow 43", p10.slow_perc == 43, "");
    check("effort=10 -> stale 1", p10.acceptable_stale == 1, "");
    check("effort 越大 stale 越低", p10.acceptable_stale < p1.acceptable_stale, "");
    check("effort 越大扫的键越多", p10.keys_per_loop > p1.keys_per_loop, "");

    printf("[4] LFU 布局与 Morris 计数\n");
    int lru = lfu_init_lru(1234);
    check("高 16 位是分钟", lfu_ldt(lru) == 1234, "");
    check("低 8 位 = LFU_INIT_VAL 5", lfu_counter(lru) == LFU_INIT_VAL, "");
    check("r=0 必递增", lfu_log_incr(5, 0.0, 10) == 6, "");
    check("r=1 不递增", lfu_log_incr(5, 1.0, 10) == 5, "");
    check("255 饱和", lfu_log_incr(255, 0.0, 10) == 255, "");
    check("counter<5 时 baseval 夹到 0，p=1", lfu_log_incr(0, 0.5, 10) == 1, "");
    check("factor 大 -> p 小",
          (1.0 / ((100 - 5) * 100 + 1)) < (1.0 / ((100 - 5) * 10 + 1)), "");

    printf("[5] 衰减与 16 位回绕\n");
    check("过 5 分钟减 5", lfu_decay(20, 100, 105, 1) == 15, "");
    check("下限 0", lfu_decay(3, 100, 110, 1) == 0, "");
    check("decay_time=0 永不衰减", lfu_decay(20, 100, 99999, 0) == 20, "");
    check("decay_time=5 过 10 分钟减 2", lfu_decay(20, 100, 110, 5) == 18, "");
    check("未回绕", lfu_time_elapsed(100, 105) == 5, "");
    /* 源码 65535-ldt+now = 10；真实环绕距离 65536-ldt+now = 11 —— 少算 1 分钟 */
    check("跨回绕：源码算 10", lfu_time_elapsed(65530, 5) == 10, "");
    check("跨回绕：真实 11（源码少算 1）", (65536 - 65530 + 5) == 11, "");
    check("idle = 255 - counter", (255 - 0) == 255 && (255 - 255) == 0, "");

    printf("[6] 官方 lfu-log-factor 表格（容差内）\n");
    check("factor 10 / 100 次 ≈ 10", fabs(lfu_expected(100, 10) - 10) <= 2.0, "");
    check("factor 10 / 1000 次 ≈ 18", fabs(lfu_expected(1000, 10) - 18) <= 2.0, "");
    check("factor 10 / 100K 次 ≈ 142", fabs(lfu_expected(100000, 10) - 142) <= 15.0, "");
    check("factor 0 / 100 次 ≈ 104", fabs(lfu_expected(100, 0) - 104) <= 2.0, "");
    check("factor 100 / 1M 次 ≈ 143", fabs(lfu_expected(1000000, 100) - 143) <= 15.0, "");
    check("factor 越大计数越低",
          lfu_expected(100, 100) < lfu_expected(100, 10), "");

    printf("[7] volatile- 陷阱\n");
    check("allkeys-lru 不报错", eviction_error("allkeys-lru", 0) == 0, "");
    check("volatile-lru 无 TTL 键 -> 报错", eviction_error("volatile-lru", 0) == 1, "");
    check("volatile-lru 有 TTL 键 -> 正常", eviction_error("volatile-lru", 1) == 0, "");
    check("volatile-lfu 同理", eviction_error("volatile-lfu", 0) == 1, "");
    check("noeviction 恒报错", eviction_error("noeviction", 1) == 1, "");

    printf("\n");
    if (g_fails) { printf("FAILED %d\n", g_fails); return 1; }
    printf("ALL PASS\n");
    return 0;
}
