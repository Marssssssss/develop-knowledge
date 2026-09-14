// Prepared Statement Cache + Connection Pool (extended-query-aware).
//
// Implements:
// - Statement cache with custom vs generic plan switch (after N executions)
// - Pooler with per-backend prepared statement mapping (PgBouncer 1.21+
//   max_prepared_statements style)
// - LRU eviction per backend
//
// Refs:
//   - PostgreSQL protocol-flow.html §55.2.3 Extended Query
//     https://www.postgresql.org/docs/current/protocol-flow.html
//   - PgBouncer max_prepared_statements:
//     https://github.com/topicusonderwijs/pgbouncer-ps-patch

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

#define MAX_CACHE  64
#define MAX_BACK   8

typedef struct {
    char sql[128];
    int  custom_count;
    double custom_costs[16];
    double generic_cost;
    int  n_custom_costs;
} stmt_t;

typedef struct {
    char sql_hash[33];
    char stmt_name[32];
} prep_entry_t;

typedef struct {
    int  id;
    prep_entry_t prepared[64];
    int  n_prepared;
} backend_t;

typedef struct {
    stmt_t cache[MAX_CACHE];
    char cache_sql[MAX_CACHE][128];
    int  n_cache;
    // ordered by recency: index 0 = MRU
    int  mru_order[MAX_CACHE];

    backend_t backends[MAX_BACK];
    int  n_backends;
    int  max_per_backend;
    int  evictions;
} pool_t;

static unsigned long hash_str(const char * s) {
    unsigned long h = 5381;
    for (; *s; s++) h = ((h << 5) + h) + (unsigned char)*s;
    return h;
}

static void hash_to_str(unsigned long h, char * out) {
    snprintf(out, 33, "%016lx", h);
}

static double custom_cost(const char * sql, int param) {
    // if first param small → index scan, cheaper
    return 10.0 + 5.0 * (param < 100 ? 0.1 : 1.0);
}

static double generic_cost(const char * sql) {
    (void)sql;
    return 15.0;
}

static stmt_t * cache_get_or_create(pool_t * p, const char * sql) {
    unsigned long h = hash_str(sql);
    char key[33];
    hash_to_str(h, key);
    for (int i = 0; i < p->n_cache; i++) {
        if (strcmp(p->cache[i].sql, sql) == 0) {
            // touch MRU
            for (int j = 0; j < p->n_cache; j++) {
                if (p->mru_order[j] == i) {
                    for (int k = j; k > 0; k--)
                        p->mru_order[k] = p->mru_order[k-1];
                    p->mru_order[0] = i;
                    break;
                }
            }
            return &p->cache[i];
        }
    }
    if (p->n_cache >= MAX_CACHE) {
        // evict LRU
        int evict_idx = p->mru_order[p->n_cache - 1];
        for (int j = p->n_cache - 1; j > 0; j--)
            p->mru_order[j] = p->mru_order[j-1];
        strncpy(p->cache[evict_idx].sql, sql, 127);
        p->cache[evict_idx].custom_count = 0;
        p->cache[evict_idx].n_custom_costs = 0;
        p->cache[evict_idx].generic_cost = generic_cost(sql);
        p->mru_order[0] = evict_idx;
        return &p->cache[evict_idx];
    }
    int idx = p->n_cache++;
    strncpy(p->cache[idx].sql, sql, 127);
    p->cache[idx].custom_count = 0;
    p->cache[idx].n_custom_costs = 0;
    p->cache[idx].generic_cost = generic_cost(sql);
    for (int j = p->n_cache - 1; j > 0; j--)
        p->mru_order[j] = p->mru_order[j-1];
    p->mru_order[0] = idx;
    return &p->cache[idx];
}

// Generic vs custom plan switch (after 5, if generic cheaper → use generic)
static double plan_for(stmt_t * s, int param, bool * used_generic) {
    s->custom_count++;
    if (s->n_custom_costs < 16) {
        s->custom_costs[s->n_custom_costs++] = custom_cost(s->sql, param);
    }
    if (s->custom_count >= 5) {
        double avg = 0;
        for (int i = 0; i < s->n_custom_costs; i++)
            avg += s->custom_costs[i];
        avg /= s->n_custom_costs;
        if (avg > s->generic_cost) {
            *used_generic = true;
            return s->generic_cost;
        }
    }
    *used_generic = false;
    return custom_cost(s->sql, param);
}

static int backend_find(backend_t * b, const char * sql) {
    char key[33];
    hash_to_str(hash_str(sql), key);
    for (int i = 0; i < b->n_prepared; i++)
        if (strcmp(b->prepared[i].sql_hash, key) == 0) return i;
    return -1;
}

static void backend_prepare(backend_t * b, const char * sql, const char * name) {
    int existing = backend_find(b, sql);
    if (existing >= 0) return;
    if (b->n_prepared >= 64) {
        // evict first entry (simplified LRU)
        for (int i = 0; i < b->n_prepared - 1; i++)
            b->prepared[i] = b->prepared[i+1];
        b->n_prepared--;
    }
    hash_to_str(hash_str(sql), b->prepared[b->n_prepared].sql_hash);
    snprintf(b->prepared[b->n_prepared].stmt_name, 32, "S_%s", name);
    b->n_prepared++;
}

static double execute(pool_t * p, int client_id, const char * sql,
                      int param) {
    stmt_t * s = cache_get_or_create(p, sql);
    int bi = client_id % p->n_backends;
    backend_t * b = &p->backends[bi];
    // PgBouncer protocol-aware: re-prepare on backend if not present
    if (backend_find(b, sql) < 0) {
        backend_prepare(b, sql, s->sql);
    }
    bool used_generic = false;
    return plan_for(s, param, &used_generic);
}

int main(void) {
    pool_t p = {0};
    p.n_backends = 3;
    p.max_per_backend = 64;

    const char * sql = "SELECT * FROM users WHERE country = $1";

    printf("=== Test 1: 6 executions of same SQL, different params ===\n");
    int params[] = {42, 99, 7, 1000, 50, 88};
    for (int i = 0; i < 6; i++) {
        double c = execute(&p, /*client_id=*/1, sql, params[i]);
        printf("  exec %d  param=%d  cost=%.2f\n", i+1, params[i], c);
    }

    printf("\n=== Test 2: 3 clients, 3 backends ===\n");
    for (int cid = 10; cid <= 30; cid += 10) {
        double c = execute(&p, cid, sql, 42);
        int bi = cid % p.n_backends;
        printf("  client %d → backend %d, cost=%.2f, "
               "backend prepared=%d\n",
               cid, bi, c, p.backends[bi].n_prepared);
    }

    printf("\n=== Test 3: cache size + backend distribution ===\n");
    printf("  cache size: %d  backends: %d  evictions: %d\n",
           p.n_cache, p.n_backends, p.evictions);
    for (int i = 0; i < p.n_backends; i++)
        printf("    backend %d: %d prepared statements\n",
               i, p.backends[i].n_prepared);

    return 0;
}