// Cost-Based Optimizer — minimal C version: statistics + cost model +
// DP join enumeration for ≤ 8 tables + GEQO-style random search above.
//
// Mirrors cbo.py.
//
// Refs:
//   - PostgreSQL planner-stats.html + geqo.html
//   - Selinger et al. 1979 "Access Path Selection in a Relational DBMS"
//     (System R optimizer; DP enumeration)

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>

#define MAX_REL  16
#define MAX_JOIN 32

// costs
#define SEQ_PAGE_COST   1.0
#define RANDOM_PAGE_COST 4.0
#define CPU_TUPLE_COST  0.01
#define CPU_INDEX_COST  0.005
#define CPU_OP_COST     0.0025
#define GEQO_THRESHOLD  8

typedef struct {
    char name[32];
    int  n_rows, n_pages;
    int  has_idx_on_id;     // simple "index on join col" flag
} tstat_t;

// cost -> parent path
typedef struct {
    double cost;
    int    parent_set;      // bitmask of relations in parent
} cell_t;

static double cost_seq(tstat_t * t) {
    return SEQ_PAGE_COST * t->n_pages + CPU_TUPLE_COST * t->n_rows;
}

static double cost_idx(tstat_t * t, double sel) {
    double idx_pages  = 0.1 * t->n_rows;
    double n_fetched  = t->n_rows * sel;
    return RANDOM_PAGE_COST * idx_pages + RANDOM_PAGE_COST * n_fetched
           + CPU_INDEX_COST * t->n_rows + CPU_TUPLE_COST * n_fetched;
}

static double total_cost = 0.0;
static int    best_order[MAX_REL];
static int    best_set;
static tstat_t tables[MAX_REL];
static int    n_rels = 0;

// DP over bitmask sets
static cell_t memo[1 << MAX_REL];

static double dp_cost(int set) {
    if (memo[set].cost > 0.0 && set != 0) return memo[set].cost;
    if (set == 0) return 0.0;
    if ((set & (set - 1)) == 0) {
        // single relation: pick cheapest access
        int idx = __builtin_ctz(set);
        double s = cost_seq(&tables[idx]);
        double i = tables[idx].has_idx_on_id
                 ? cost_idx(&tables[idx], 0.1) : 1e18;
        memo[set].cost = s < i ? s : i;
        memo[set].parent_set = 0;
        return memo[set].cost;
    }
    double best = 1e18;
    int best_parent = 0;
    for (int last = 0; last < n_rels; last++) {
        if (!(set & (1 << last))) continue;
        int rest = set & ~(1 << last);
        double rest_cost = dp_cost(rest);
        double last_cost = cost_seq(&tables[last]);
        double cand = rest_cost + last_cost;
        if (cand < best) { best = cand; best_parent = rest; }
    }
    memo[set].cost = best;
    memo[set].parent_set = best_parent;
    return best;
}

// GEQO: random permutation search
static double fitness(int * order, int n) {
    double c = 0.0;
    for (int i = 0; i < n; i++) c += cost_seq(&tables[order[i]]);
    return 1.0 / (1.0 + c);
}

static void shuffle(int * arr, int n) {
    for (int i = n - 1; i > 0; i--) {
        int j = rand() % (i + 1);
        int t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
}

static void geqo(int n, int gens, int pool) {
    int pop[64][MAX_REL];
    double fit[64];
    for (int i = 0; i < pool; i++) {
        for (int j = 0; j < n; j++) pop[i][j] = j;
        shuffle(pop[i], n);
        fit[i] = fitness(pop[i], n);
    }
    for (int g = 0; g < gens; g++) {
        // rank
        for (int i = 0; i < pool; i++)
            for (int j = i + 1; j < pool; j++)
                if (fit[j] > fit[i]) {
                    double t = fit[i]; fit[i] = fit[j]; fit[j] = t;
                    int tmp[MAX_REL];
                    memcpy(tmp, pop[i], sizeof(tmp));
                    memcpy(pop[i], pop[j], sizeof(tmp));
                    memcpy(pop[j], tmp, sizeof(tmp));
                }
        // crossover: refill bottom half
        for (int i = pool / 2; i < pool; i++) {
            int a = rand() % (pool / 2);
            int b = rand() % (pool / 2);
            int cut = 1 + rand() % (n - 1);
            int seen[MAX_REL] = {0};
            for (int k = 0; k < cut; k++) {
                pop[i][k] = pop[a][k];
                seen[pop[i][k]] = 1;
            }
            int pos = cut;
            for (int k = 0; k < n && pos < n; k++) {
                int v = pop[b][k];
                if (!seen[v]) { pop[i][pos++] = v; seen[v] = 1; }
            }
            fit[i] = fitness(pop[i], n);
        }
    }
    int best_idx = 0;
    for (int i = 1; i < pool; i++)
        if (fit[i] > fit[best_idx]) best_idx = i;
    for (int i = 0; i < n; i++) printf("t%d ", pop[best_idx][i]);
    printf("(cost=%.2f)\n", 1.0 / fit[best_idx] - 1.0);
}

int main(void) {
    srand((unsigned)time(NULL));

    // 3-table demo: users + orders + items
    n_rels = 3;
    strcpy(tables[0].name, "users");  tables[0].n_rows = 100000;  tables[0].n_pages = 2500;   tables[0].has_idx_on_id = 1;
    strcpy(tables[1].name, "orders"); tables[1].n_rows = 1000000; tables[1].n_pages = 25000;  tables[1].has_idx_on_id = 1;
    strcpy(tables[2].name, "items");  tables[2].n_rows = 5000000; tables[2].n_pages = 125000; tables[2].has_idx_on_id = 1;

    double c = dp_cost((1 << n_rels) - 1);
    printf("DP best plan cost = %.2f\n", c);

    // 10-table GEQO
    n_rels = 10;
    for (int i = 0; i < n_rels; i++) {
        snprintf(tables[i].name, 32, "t%d", i);
        tables[i].n_rows = 10000 * (i + 1);
        tables[i].n_pages = 250 * (i + 1);
        tables[i].has_idx_on_id = 1;
    }
    memset(memo, 0, sizeof(memo));
    printf("GEQO 10-table order: ");
    geqo(n_rels, 60, 30);

    return 0;
}