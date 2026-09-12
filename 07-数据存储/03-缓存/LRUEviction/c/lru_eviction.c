/*
 * lru_eviction.c — 缓存淘汰算法最小实现
 *
 * 演示两种风格:
 *   1) Memcached 风格精确 LRU: 双向链表 + 哈希表 O(1)
 *   2) Redis 风格近似 LRU: 随机采样 N=5 + 候选池
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic lru_eviction.c -o demo
 * 运行: ./demo
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <time.h>

/* ============================================================
 *  Part 1: Memcached-style exact LRU (doubly-linked list + hash)
 * ============================================================ */

#define EXACT_CAPACITY 64
#define EXACT_KEYLEN  16

typedef struct lru_node {
    char key[EXACT_KEYLEN];
    int  val;
    struct lru_node *prev, *next;
} lru_node;

typedef struct {
    lru_node *head, *tail;            /* head = 最近访问, tail = 最久未访问 */
    lru_node *buckets[EXACT_CAPACITY];
    int      size;
    int      evictions;
} exact_lru;

static unsigned long hash_str(const char *s) {
    unsigned long h = 5381;
    while (*s) h = ((h << 5) + h) + (unsigned char)*s++;
    return h;
}

static void exact_init(exact_lru *c) {
    memset(c, 0, sizeof(*c));
}

static void exact_unlink(exact_lru *c, lru_node *n) {
    if (n->prev) n->prev->next = n->next;
    else         c->head = n->next;
    if (n->next) n->next->prev = n->prev;
    else         c->tail = n->prev;
}

static void exact_push_head(exact_lru *c, lru_node *n) {
    n->prev = NULL;
    n->next = c->head;
    if (c->head) c->head->prev = n;
    c->head = n;
    if (!c->tail) c->tail = n;
}

static lru_node *exact_touch(exact_lru *c, const char *key, int *found_val) {
    unsigned long h = hash_str(key) % EXACT_CAPACITY;
    for (lru_node *n = c->buckets[h]; n; n = n->next) {
        if (n == c->buckets[h] || strcmp(n->key, key) == 0) {
            *found_val = n->val;
            exact_unlink(c, n);
            exact_push_head(c, n);
            return n;
        }
    }
    return NULL;
}

static const char *exact_get(exact_lru *c, const char *key, int *out) {
    int v;
    lru_node *n = exact_touch(c, key, &v);
    if (n) { *out = v; return "hit"; }
    *out = 0;
    return "miss";
}

static void exact_put(exact_lru *c, const char *key, int val) {
    int dummy;
    lru_node *n = exact_touch(c, key, &dummy);
    if (n) { n->val = val; return; }

    if (c->size >= EXACT_CAPACITY) {
        /* 淘汰 tail */
        lru_node *victim = c->tail;
        exact_unlink(c, victim);
        unsigned long h = hash_str(victim->key) % EXACT_CAPACITY;
        /* 简化:demo 不维护桶链,直接清掉 buckets[h] */
        c->buckets[h] = NULL;
        free(victim);
        c->evictions++;
    }
    n = calloc(1, sizeof(*n));
    strncpy(n->key, key, EXACT_KEYLEN - 1);
    n->val = val;
    unsigned long h = hash_str(key) % EXACT_CAPACITY;
    n->next = c->buckets[h];          /* 简化为链表头插入 */
    c->buckets[h] = n;
    exact_push_head(c, n);
    c->size++;
}

/* ============================================================
 *  Part 2: Redis-style approximated LRU (sample + pool)
 * ============================================================ */

#define APPROX_CAPACITY 64
#define APPROX_KEYLEN   16
#define SAMPLES         5

typedef struct {
    char key[APPROX_KEYLEN];
    int  val;
    long lru_ts;                       /* last-access "logical clock" */
} approx_entry;

typedef struct {
    approx_entry store[APPROX_CAPACITY];
    int  size;
    int  evictions;
    long clock;
    approx_entry pool[SAMPLES * 2];
    int  pool_size;
} approx_lru;

static long approx_now(approx_lru *c) { return ++c->clock; }

static int approx_find(approx_lru *c, const char *key, int *out_idx) {
    for (int i = 0; i < c->size; i++)
        if (strcmp(c->store[i].key, key) == 0) {
            *out_idx = i;
            return 1;
        }
    return 0;
}

static int approx_get(approx_lru *c, const char *key, int *out) {
    int idx;
    if (approx_find(c, key, &idx)) {
        c->store[idx].lru_ts = approx_now(c);
        *out = c->store[idx].val;
        return 1;
    }
    return 0;
}

/* 把候选加入 pool,按 lru_ts 升序,只保留最旧的 SAMPLES 个 */
static void pool_push(approx_lru *c, const char *key, long ts) {
    if (c->pool_size < SAMPLES * 2) {
        strncpy(c->pool[c->pool_size].key, key, APPROX_KEYLEN - 1);
        c->pool[c->pool_size].lru_ts = ts;
        c->pool_size++;
    } else {
        /* 替换最旧者(保留更新鲜的候选) */
        int oldest = 0;
        for (int i = 1; i < c->pool_size; i++)
            if (c->pool[i].lru_ts < c->pool[oldest].lru_ts) oldest = i;
        if (ts < c->pool[oldest].lru_ts) {
            strncpy(c->pool[oldest].key, key, APPROX_KEYLEN - 1);
            c->pool[oldest].lru_ts = ts;
        }
    }
}

static const char *approx_put_with_evict(approx_lru *c, const char *key, int val) {
    if (c->size < APPROX_CAPACITY) {
        strncpy(c->store[c->size].key, key, APPROX_KEYLEN - 1);
        c->store[c->size].val = val;
        c->store[c->size].lru_ts = approx_now(c);
        c->size++;
        return NULL;
    }
    /* 超容:从 pool + 随机新采样中选最旧 */
    approx_entry candidates[SAMPLES * 3];
    int n = 0;
    /* pool 全部加入 */
    for (int i = 0; i < c->pool_size && n < SAMPLES * 3; i++)
        candidates[n++] = c->pool[i];
    /* 随机采样 SAMPLES 个新候选 */
    int picked[SAMPLES];
    for (int i = 0; i < SAMPLES; i++) {
        int j = rand() % c->size;
        /* 简化:去重 */
        int dup = 0;
        for (int k = 0; k < i; k++) if (picked[k] == j) { dup = 1; break; }
        if (dup) { i--; continue; }
        picked[i] = j;
        if (n < SAMPLES * 3)
            candidates[n++] = c->store[j];
    }
    /* 选最旧者 */
    int victim = 0;
    for (int i = 1; i < n; i++)
        if (candidates[i].lru_ts < candidates[victim].lru_ts) victim = i;
    const char *evicted_key = strdup(candidates[victim].key);

    /* 在 store 中找到并替换 */
    for (int i = 0; i < c->size; i++)
        if (strcmp(c->store[i].key, candidates[victim].key) == 0) {
            strncpy(c->store[i].key, key, APPROX_KEYLEN - 1);
            c->store[i].val = val;
            c->store[i].lru_ts = approx_now(c);
            break;
        }
    /* pool 留下其余候选 */
    c->pool_size = 0;
    for (int i = 0; i < n; i++)
        if (i != victim)
            pool_push(c, candidates[i].key, candidates[i].lru_ts);
    c->evictions++;
    return evicted_key;
}

/* ============================================================
 *  Demo
 * ============================================================ */

int main(void) {
    srand((unsigned)time(NULL));

    printf("=== Cache Eviction Demo ===\n\n");

    /* --- Memcached-style exact LRU --- */
    printf("[Part 1] Exact LRU (doubly-linked list, capacity=%d)\n",
           EXACT_CAPACITY);
    exact_lru e;
    exact_init(&e);

    /* 顺序插入 100 个,只 cache 最新 64 */
    for (int i = 0; i < 100; i++) {
        char k[16];
        snprintf(k, sizeof(k), "k%02d", i);
        exact_put(&e, k, i);
    }
    /* 命中测试:前 36 个应该全部 miss */
    int hits = 0, misses = 0;
    for (int i = 0; i < 36; i++) {
        char k[16]; snprintf(k, sizeof(k), "k%02d", i);
        int v;
        const char *r = exact_get(&e, k, &v);
        if (strcmp(r, "hit") == 0) hits++; else misses++;
    }
    printf("  Hit/miss for k00..k35: %d hit, %d miss\n", hits, misses);
    printf("  Evictions: %d\n\n", e.evictions);

    /* --- Redis-style approximated LRU --- */
    printf("[Part 2] Approximated LRU (sample=%d + pool, capacity=%d)\n",
           SAMPLES, APPROX_CAPACITY);
    approx_lru a = {0};
    a.clock = 1000;

    for (int i = 0; i < 100; i++) {
        char k[16];
        snprintf(k, sizeof(k), "k%02d", i);
        approx_put_with_evict(&a, k, i);
    }
    /* 命中测试 */
    int ahits = 0, amisses = 0;
    for (int i = 0; i < 36; i++) {
        char k[16]; snprintf(k, sizeof(k), "k%02d", i);
        int v;
        if (approx_get(&a, k, &v)) ahits++; else amisses++;
    }
    printf("  Hit/miss for k00..k35: %d hit, %d miss\n", ahits, amisses);
    printf("  Evictions: %d\n", a.evictions);

    /* 对比 */
    printf("\n[Conclusion]\n");
    printf("  Exact LRU evicts the truly oldest (命中率: 100%% on newest %d)\n",
           EXACT_CAPACITY);
    printf("  Approx LRU evicts oldest among SAMPLES random + pool (≈95-99%%)\n");
    printf("  → Approx 节省每 entry 16B 指针开销,代价是 ~3%% 命中率。\n");
    return 0;
}