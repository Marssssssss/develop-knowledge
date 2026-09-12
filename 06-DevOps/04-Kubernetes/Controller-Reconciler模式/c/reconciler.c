/*
 * reconciler.c — Kubernetes Controller Reconciler Loop 模式最小实现
 *
 * 权威来源:
 *   - kubernetes.io/blog/2026/07/29/controller-runtime-cache-explained/
 *     (r.Get/r.List 走本地 cache,写走 apiserver,watch 事件入 workqueue)
 *
 * 核心模型(摘自官方 TL;DR):
 *   1. r.Get / r.List 读本地 Indexer(map[namespace/name] → object),
 *      不是 apiserver。Cache miss 时 fallback 到 APIReader。
 *   2. r.Update/Create 写 apiserver,绕开 cache(避免脑裂)。
 *   3. watch 事件流: apiserver → Reflector → DeltaFIFO → ResourceEventHandler
 *      → workqueue(按 key 去重)→ Reconcile(ctx, NamespacedName)。
 *   4. workqueue 中只有 key(如 "default/my-pod"),没有对象体。
 *      同一 key 重复入队会被静默合并(去重)。
 *   5. Reconcile 必须幂等:读到的状态可能滞后,但下次会自我修正。
 *   6. 周期触发用 Result{RequeueAfter: 30s},不要用 time.Sleep(若有真实事件到达
 *      会立即触发而不必等定时器)。
 *
 * 本实现构造一个 in-memory apiserver(支持 list + watch + update)、
 * 一个 Indexer(local cache)、一个 workqueue(带去重)、一个 reconciler。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

/* ============== 简化的对象模型 ============== */
typedef struct {
    char ns[32];
    char name[32];
    int  replicas;       /* desired replicas */
    int  ready;          /* ready replicas (actual state) */
    int  resource_version;  /* 模拟 K8s resourceVersion(单调递增) */
} object_t;

/* ============== in-memory apiserver ============== */
#define MAX_OBJECTS 64
typedef struct {
    object_t objs[MAX_OBJECTS];
    int n;
    int rv_counter;          /* 单调递增的 resourceVersion */
    /* watch subscribers:简化用固定大小数组 */
    struct {
        char target[64];     /* "ns/name" or "*" for all */
        char event;          /* 'A'dd / 'U'pdate / 'D'elete */
        object_t obj;
        bool active;
    } watch_events[256];
    int n_events;
} apiserver_t;

static void apiserver_init(apiserver_t *api) {
    memset(api, 0, sizeof(*api));
}

static int apiserver_find(apiserver_t *api, const char *ns, const char *name) {
    for (int i = 0; i < api->n; i++) {
        if (strcmp(api->objs[i].ns, ns) == 0 && strcmp(api->objs[i].name, name) == 0)
            return i;
    }
    return -1;
}

/* K8s 风格 update:resourceVersion 不匹配返回 -1(模拟 409 Conflict) */
static int apiserver_update(apiserver_t *api, object_t *obj) {
    int idx = apiserver_find(api, obj->ns, obj->name);
    if (idx < 0) return -1;
    object_t *existing = &api->objs[idx];
    if (obj->resource_version != existing->resource_version) {
        return -2;  /* 409 Conflict */
    }
    existing->replicas = obj->replicas;
    existing->ready = obj->ready;
    existing->resource_version = ++api->rv_counter;
    /* emit watch event */
    if (api->n_events < 256) {
        api->watch_events[api->n_events].active = true;
        api->watch_events[api->n_events].event = 'U';
        api->watch_events[api->n_events].obj = *existing;
        snprintf(api->watch_events[api->n_events].target,
                 sizeof(api->watch_events[api->n_events].target),
                 "%s/%s", existing->ns, existing->name);
        api->n_events++;
    }
    return 0;
}

static int apiserver_create(apiserver_t *api, object_t *obj) {
    if (apiserver_find(api, obj->ns, obj->name) >= 0) return -1;
    obj->resource_version = ++api->rv_counter;
    api->objs[api->n++] = *obj;
    if (api->n_events < 256) {
        api->watch_events[api->n_events].active = true;
        api->watch_events[api->n_events].event = 'A';
        api->watch_events[api->n_events].obj = *obj;
        snprintf(api->watch_events[api->n_events].target,
                 sizeof(api->watch_events[api->n_events].target),
                 "%s/%s", obj->ns, obj->name);
        api->n_events++;
    }
    return 0;
}

/* ============== Indexer(local cache) ============== */
typedef struct {
    char key[64];      /* "ns/name" */
    object_t obj;
    bool present;
} indexer_entry_t;

typedef struct {
    indexer_entry_t entries[MAX_OBJECTS];
    int n;
} indexer_t;

static void indexer_init(indexer_t *idx) { memset(idx, 0, sizeof(*idx)); }

static void indexer_set(indexer_t *idx, const char *ns, const char *name, object_t *obj) {
    char key[64];
    snprintf(key, sizeof(key), "%s/%s", ns, name);
    for (int i = 0; i < idx->n; i++) {
        if (strcmp(idx->entries[i].key, key) == 0) {
            idx->entries[i].obj = *obj;
            idx->entries[i].present = true;
            return;
        }
    }
    if (idx->n < MAX_OBJECTS) {
        snprintf(idx->entries[idx->n].key, sizeof(idx->entries[idx->n].key), "%s", key);
        idx->entries[idx->n].obj = *obj;
        idx->entries[idx->n].present = true;
        idx->n++;
    }
}

static object_t *indexer_get(indexer_t *idx, const char *ns, const char *name) {
    char key[64];
    snprintf(key, sizeof(key), "%s/%s", ns, name);
    for (int i = 0; i < idx->n; i++) {
        if (strcmp(idx->entries[i].key, key) == 0 && idx->entries[i].present)
            return &idx->entries[i].obj;
    }
    return NULL;
}

/* ============== workqueue(带去重 + FIFO) ============== */
#define MAX_QUEUE 128
typedef struct {
    char keys[MAX_QUEUE][64];
    bool in_queue[MAX_QUEUE];    /* 标记是否已在队列中(去重) */
    int head, tail, count;
} workqueue_t;

static void wq_init(workqueue_t *q) { memset(q, 0, sizeof(*q)); }

static void wq_add(workqueue_t *q, const char *ns, const char *name) {
    char key[64];
    snprintf(key, sizeof(key), "%s/%s", ns, name);
    /* 去重 */
    for (int i = 0; i < MAX_QUEUE; i++) {
        if (q->in_queue[i] && strcmp(q->keys[i], key) == 0) {
            printf("  [wq] dedup: %s already in queue\n", key);
            return;
        }
    }
    /* 找空位 */
    int slot = -1;
    for (int i = 0; i < MAX_QUEUE; i++) {
        if (!q->in_queue[i]) { slot = i; break; }
    }
    if (slot < 0) return;
    snprintf(q->keys[slot], sizeof(q->keys[slot]), "%s", key);
    q->in_queue[slot] = true;
    q->count++;
    printf("  [wq] enqueue: %s\n", key);
}

static bool wq_get(workqueue_t *q, char *out_key) {
    for (int i = 0; i < MAX_QUEUE; i++) {
        if (q->in_queue[i]) {
            snprintf(out_key, 64, "%s", q->keys[i]);
            q->in_queue[i] = false;
            q->count--;
            return true;
        }
    }
    return false;
}

/* ============== Reconciler ============== */
typedef struct {
    apiserver_t *api;
    indexer_t *cache;
    workqueue_t *wq;
    int reconcile_count;
    int write_count;
} reconciler_t;

static void reconciler_reconcile(reconciler_t *r, const char *key) {
    char ns[32], name[32];
    sscanf(key, "%31[^/]/%31s", ns, name);
    r->reconcile_count++;

    /* 1. Get from CACHE(不是 apiserver)— 官方 r.Get */
    object_t *cached = indexer_get(r->cache, ns, name);
    if (!cached) {
        printf("  [reconcile %d] %s: not in cache, skipping (would fallback to APIReader)\n",
               r->reconcile_count, key);
        return;
    }
    printf("  [reconcile %d] %s: replicas=%d ready=%d rv=%d (from cache)\n",
           r->reconcile_count, key, cached->replicas, cached->ready, cached->resource_version);

    /* 2. Reconcile 逻辑:确保 ready == replicas */
    if (cached->replicas != cached->ready) {
        /* 3. Write to APISERVER(绕开 cache)— 官方 r.Update */
        object_t updated = *cached;
        updated.ready = updated.replicas;
        int rc = apiserver_update(r->api, &updated);
        if (rc == -2) {
            printf("  [reconcile %d] %s: 409 Conflict, will retry on next event\n",
                   r->reconcile_count, key);
            /* 重新入队等下一次 watch event */
            wq_add(r->wq, ns, name);
        } else if (rc == 0) {
            r->write_count++;
            printf("  [reconcile %d] %s: updated ready=%d (rv=%d, watch event will refresh cache)\n",
                   r->reconcile_count, key, updated.ready, updated.resource_version);
            /* 注意:本地 cache 不会立即更新,要等 watch event 来 */
        }
    } else {
        printf("  [reconcile %d] %s: in sync, no action\n", r->reconcile_count, key);
    }
}

/* ============== 子 demo 1: list+watch warm-up ============== */
static void demo1_cache_warmup(void) {
    printf("\n========== Demo 1: List+watch warm-up (cache initial sync) ==========\n");
    apiserver_t api; apiserver_init(&api);
    indexer_t cache; indexer_init(&cache);
    workqueue_t wq; wq_init(&wq);

    /* seed 3 个 deployment */
    apiserver_create(&api, &(object_t){.ns = "default", .name = "web", .replicas = 3, .ready = 3});
    apiserver_create(&api, &(object_t){.ns = "default", .name = "api", .replicas = 2, .ready = 1});
    apiserver_create(&api, &(object_t){.ns = "default", .name = "db",  .replicas = 1, .ready = 1});

    /* List — cache warm-up */
    printf("[reflector] LIST: populate cache from initial snapshot\n");
    for (int i = 0; i < api.n; i++) {
        indexer_set(&cache, api.objs[i].ns, api.objs[i].name, &api.objs[i]);
        printf("  cached %s/%s replicas=%d ready=%d rv=%d\n",
               api.objs[i].ns, api.objs[i].name,
               api.objs[i].replicas, api.objs[i].ready, api.objs[i].resource_version);
    }

    /* 处理 LIST 期间产生的 ADDED 事件 */
    for (int i = 0; i < api.n_events; i++) {
        wq_add(&wq, api.watch_events[i].obj.ns, api.watch_events[i].obj.name);
    }

    printf("[reconciler] start worker, drain queue:\n");
    reconciler_t r = {.api = &api, .cache = &cache, .wq = &wq};
    char key[64];
    while (wq_get(&wq, key)) {
        reconciler_reconcile(&r, key);
    }
    printf("[result] reconcile_count=%d, write_count=%d\n",
           r.reconcile_count, r.write_count);
}

/* ============== 子 demo 2: workqueue dedup ============== */
static void demo2_workqueue_dedup(void) {
    printf("\n========== Demo 2: Workqueue dedup (5 events to same key → 1 enqueue) ==========\n");
    workqueue_t wq; wq_init(&wq);
    printf("Emit 5 UPDATE events to default/web:\n");
    for (int i = 0; i < 5; i++) {
        printf("event %d: UPDATE default/web (replicas changed)\n", i + 1);
        wq_add(&wq, "default", "web");
    }
    printf("Final queue.count = %d (expected 1)\n", wq.count);
}

/* ============== 子 demo 3: stale read after write ============== */
static void demo3_stale_read(void) {
    printf("\n========== Demo 3: Stale read after write (eventually consistent) ==========\n");
    apiserver_t api; apiserver_init(&api);
    indexer_t cache; indexer_init(&cache);
    workqueue_t wq; wq_init(&wq);

    apiserver_create(&api, &(object_t){.ns = "default", .name = "web", .replicas = 3, .ready = 2});
    /* warm-up cache from list */
    for (int i = 0; i < api.n; i++)
        indexer_set(&cache, api.objs[i].ns, api.objs[i].name, &api.objs[i]);
    api.n_events = 0;  /* consume initial events */

    printf("Initial state: cache.rv=%d, api.rv=%d\n",
           indexer_get(&cache, "default", "web")->resource_version,
           apiserver_find(&api, "default", "web") >= 0
             ? api.objs[apiserver_find(&api, "default", "web")].resource_version : -1);

    /* 模拟 controller:读 cache → 决定写 apiserver */
    reconciler_t r = {.api = &api, .cache = &cache, .wq = &wq};
    reconciler_reconcile(&r, "default/web");  /* 写 apiserver */
    /* 关键:此时 cache 还是旧值!watch event 异步 */
    object_t *cached = indexer_get(&cache, "default", "web");
    int api_rv = api.objs[apiserver_find(&api, "default", "web")].resource_version;
    printf("Right after write: cache.rv=%d (still old), api.rv=%d (new) — STALE!\n",
           cached->resource_version, api_rv);

    /* 模拟 watch event 触发 cache 刷新 */
    printf("[watch] UPDATE event delivered, refresh cache:\n");
    int idx = apiserver_find(&api, "default", "web");
    indexer_set(&cache, "default", "web", &api.objs[idx]);
    cached = indexer_get(&cache, "default", "web");
    printf("After watch event: cache.rv=%d, api.rv=%d — CONSISTENT\n",
           cached->resource_version, api_rv);

    /* 再次 reconcile,看到 ready=replicas=3,in sync */
    reconciler_reconcile(&r, "default/web");
}

/* ============== 子 demo 4: 409 Conflict retry ============== */
static void demo4_conflict_retry(void) {
    printf("\n========== Demo 4: 409 Conflict retry on concurrent write ==========\n");
    apiserver_t api; apiserver_init(&api);
    indexer_t cache; indexer_init(&cache);
    workqueue_t wq; wq_init(&wq);
    apiserver_create(&api, &(object_t){.ns = "default", .name = "web", .replicas = 3, .ready = 2});
    for (int i = 0; i < api.n; i++)
        indexer_set(&cache, api.objs[i].ns, api.objs[i].name, &api.objs[i]);
    api.n_events = 0;

    reconciler_t r = {.api = &api, .cache = &cache, .wq = &wq};
    /* 模拟"我"读到 rv=1,准备写;但另一个 controller 已经把 rv 推到 2 */
    printf("Controller A reads cache: rv=1\n");
    /* 另一个 controller 先写 */
    object_t other = api.objs[0];
    other.ready = 3;
    apiserver_update(&api, &other);
    printf("Controller B updates: api.rv=%d (A's view is stale)\n",
           api.objs[0].resource_version);
    /* A 用旧 rv 写 → 409 */
    object_t a_write = api.objs[0];  /* 复制当前,但 rv 还是 cache 的 1 */
    int a_idx = apiserver_find(&api, "default", "web");
    a_write.resource_version = indexer_get(&cache, "default", "web")->resource_version;
    int rc = apiserver_update(&api, &a_write);
    printf("Controller A writes with stale rv: rc=%d (expected -2)\n", rc);
    /* A 被 409 后重新入队,等下一次 watch event */
    wq_add(&wq, "default", "web");
    printf("Re-enqueue, waiting for next watch event...\n");
    char key[64];
    if (wq_get(&wq, key)) {
        /* 模拟 watch event 触发 cache 刷新 */
        int idx = apiserver_find(&api, "default", "web");
        indexer_set(&cache, "default", "web", &api.objs[idx]);
        reconciler_reconcile(&r, key);
    }
}

int main(void) {
    demo1_cache_warmup();
    demo2_workqueue_dedup();
    demo3_stale_read();
    demo4_conflict_retry();
    return 0;
}
