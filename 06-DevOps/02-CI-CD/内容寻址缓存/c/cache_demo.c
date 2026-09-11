/* cache_demo.c — 内容寻址缓存(hashFiles 键 + restore-keys 回退 + LRU/TTL 淘汰)
 *
 * 模拟 actions/cache 的核心语义:
 *   demo 1 精确命中跳过构建  demo 2 lockfile 变更 + restore-keys 部分回退
 *   demo 3 容量 LRU 淘汰 + 一周 TTL
 */
#include <stdio.h>
#include <string.h>

#include "sha256.h"

#define CAPACITY   3        /* GitHub 上限是单仓库 5GB,demo 用条目数近似 */
#define TTL_MIN    10080     /* 一周 = 7*24*60 分钟,一周未访问即淘汰 */
#define MAX_ENTRIES 8

typedef struct {
    char key[96];            /* "npm-<64 hex>" */
    char data[128];          /* 模拟产物(node_modules 清单) */
    int last_access;          /* 逻辑时钟值 */
} Entry;

static Entry entries[MAX_ENTRIES];
static int n_entries;
static int clock_now;         /* 逻辑时钟:每次 lookup/save 前进 1 分钟 */

static int tick(void)
{
    return ++clock_now;
}

/* hashFiles 语义:锁文件内容 SHA-256 => key(内容寻址) */
static void key_for(char out[96], const char *namespace, const char *lock)
{
    char hex[65];
    sha256_hex((const uint8_t *)lock, strlen(lock), hex);
    snprintf(out, 96, "%s-%s", namespace, hex);
}

static int find_entry(const char *key)
{
    for (int i = 0; i < n_entries; i++)
        if (strcmp(entries[i].key, key) == 0)
            return i;
    return -1;
}

static void evict_expired(void)
{
    for (int i = 0; i < n_entries; ) {
        if (clock_now - entries[i].last_access > TTL_MIN) {
            printf("    [evict] TTL(>7d unused): %.24s...\n", entries[i].key);
            memmove(&entries[i], &entries[i + 1],
                    sizeof(Entry) * (size_t)(n_entries - i - 1));
            n_entries--;
        } else {
            i++;
        }
    }
}

static int lru_index(void)
{
    int m = 0;
    for (int i = 1; i < n_entries; i++)
        if (entries[i].last_access < entries[m].last_access)
            m = i;
    return m;
}

/* 官方查找次序:精确命中 -> restore-keys 前缀(最近访问)-> miss
 * kind: 0=miss 1=exact 2=partial */
static int lookup(const char *key, const char *const *restore_keys,
                  int n_rk, char data_out[128])
{
    int i;
    evict_expired();
    if ((i = find_entry(key)) >= 0) {                 /* 1. 精确匹配 */
        entries[i].last_access = tick();
        strcpy(data_out, entries[i].data);
        return 1;
    }
    for (int r = 0; r < n_rk; r++) {                  /* 3. 前缀回退 */
        int best = -1;
        for (int k = 0; k < n_entries; k++)
            if (strncmp(entries[k].key, restore_keys[r],
                        strlen(restore_keys[r])) == 0
                && (best < 0 || entries[k].last_access
                                > entries[best].last_access))
                best = k;                             /* 最近访问 */
        if (best >= 0) {
            entries[best].last_access = tick();
            strcpy(data_out, entries[best].data);
            return 2;
        }
    }
    return 0;                                          /* 4. 全部未命中 */
}

/* job 成功后保存;超过容量按 LRU 淘汰(最近最少访问先走) */
static void save(const char *key, const char *data)
{
    int i;
    evict_expired();
    if ((i = find_entry(key)) >= 0) {                 /* 已存在则更新 */
        entries[i].last_access = tick();
        strncpy(entries[i].data, data, sizeof(entries[i].data) - 1);
        return;
    }
    while (n_entries >= CAPACITY) {
        int m = lru_index();
        printf("    [evict] LRU: %.24s... (data='%.20s...')\n",
               entries[m].key, entries[m].data);
        memmove(&entries[m], &entries[m + 1],
                sizeof(Entry) * (size_t)(n_entries - m - 1));
        n_entries--;
    }
    strcpy(entries[n_entries].key, key);
    strncpy(entries[n_entries].data, data,
            sizeof(entries[n_entries].data) - 1);
    entries[n_entries].last_access = tick();
    n_entries++;
}

/* 模拟 npm install:产物内容取决于锁文件列出的依赖 */
static void fake_install(char out[128], const char *lock)
{
    if (strstr(lock, "lodash") != NULL)
        snprintf(out, 128, "node_modules[express@4.18.0, lodash@4.17.21]");
    else
        snprintf(out, 128, "node_modules[express@4.18.0]");
}

static void reset(void)
{
    n_entries = 0;
    clock_now = 0;
}

static const char *kind_str(int k)
{
    return k == 1 ? "EXACT" : k == 2 ? "PARTIAL" : "MISS";
}

int main(void)
{
    const char *lock_v1 =
        "{\"packages\":{\"node_modules/express\":{\"version\":\"4.18.0\"}}}";
    const char *lock_v2 =
        "{\"packages\":{\"node_modules/express\":{\"version\":\"4.18.0\"},"
        "\"node_modules/lodash\":{\"version\":\"4.17.21\"}}}";
    const char *rk[1] = { "npm-" };
    char k1[96], k2[96], k3[96], k4[96], data[128];

    printf("== demo 1: 精确命中 -> 跳过构建 ==\n");
    reset();
    key_for(k1, "npm", lock_v1);
    printf("  lockfile v1 -> key %.28s...\n", k1);
    printf("  run #1: %s -> run npm install (120 s)\n",
           kind_str(lookup(k1, rk, 1, data)));
    fake_install(data, lock_v1);
    save(k1, data);
    printf("         saved '%s'\n", data);
    printf("  run #2: %s -> skip install, restore '%s' (5 s)\n",
           kind_str(lookup(k1, rk, 1, data)), data);

    printf("\n== demo 2: lockfile 变更 + restore-keys 部分回退 ==\n");
    reset();
    key_for(k1, "npm", lock_v1);
    key_for(k2, "npm", lock_v2);
    fake_install(data, lock_v1);
    save(k1, data);
    printf("  cache has v1 entry; v2 lockfile -> new key %.24s...\n", k2);
    printf("  run: %s -> restore old '%s' as base, npm install (60 s,"
           " 增量)\n", kind_str(lookup(k2, rk, 1, data)), data);
    fake_install(data, lock_v2);
    save(k2, data);
    printf("       saved new '%s'\n", data);

    printf("\n== demo 3: 容量 LRU 淘汰 + 一周 TTL ==\n");
    reset();
    key_for(k1, "npm", "lock#0");
    key_for(k2, "npm", "lock#1");
    key_for(k3, "npm", "lock#2");
    key_for(k4, "npm", "lock#3");
    save(k1, "deps-v0");
    save(k2, "deps-v1");
    save(k3, "deps-v2");
    printf("  saved 3 entries, then touch v0\n");
    lookup(k1, NULL, 0, data);                /* 访问 v0 => v1 成为 LRU */
    printf("  save v3 (capacity=%d):\n", CAPACITY);
    save(k4, "deps-v3");                      /* 淘汰 v1(最久未访问) */
    printf("  lookup v1: %s (已被 LRU 淘汰)\n",
           kind_str(lookup(k2, NULL, 0, data)));
    printf("  lookup v0: %s (刚访问过,保留)\n",
           kind_str(lookup(k1, NULL, 0, data)));
    clock_now += TTL_MIN + 1;                  /* 逻辑时钟推进 8 天 */
    printf("  8 天后 lookup v3: %s (一周未访问被 TTL 淘汰)\n",
           kind_str(lookup(k4, NULL, 0, data)));
    return 0;
}
