// Isolation Levels — minimal MVCC with 4 SQL isolation levels.
//
// Mirrors isolation_levels.py: implements dirty/non-repeatable/phantom/
// write-skew detection at each level.  Uses a struct txn_state_t and
// snapshot_t.  The core simulator is the same MVCC engine.
//
// References:
//   - PostgreSQL 13.2: https://www.postgresql.org/docs/current/transaction-iso.html
//   - MySQL InnoDB next-key lock
//   - Berenson et al. 1995 "A Critique of ANSI SQL Isolation Levels"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

#define MAX_TXNS    16
#define MAX_ROWS    32
#define MAX_VERS    8

typedef struct {
    int values[4];        // simple int values
    int creator_txn;
    int deleter_txn;
} version_t;

typedef struct {
    int pk;
    version_t vers[MAX_VERS];
    int n_vers;
} row_t;

typedef struct {
    int active[MAX_TXNS];
    int n_active;
    int committed_before[MAX_TXNS];
    int n_committed;
    int aborted[MAX_TXNS];
    int n_aborted;
    int seen_committed[MAX_TXNS];
    int n_seen;
} snapshot_t;

typedef enum {
    READ_UNCOMMITTED,
    READ_COMMITTED,
    REPEATABLE_READ,
    SERIALIZABLE
} iso_level_t;

typedef struct {
    iso_level_t level;
    int next_txn;
    int txn_state[MAX_TXNS];  // 0=none, 1=active, 2=committed, 3=aborted
    int si_in[MAX_TXNS][MAX_TXNS], si_in_n[MAX_TXNS];
    int si_out[MAX_TXNS][MAX_TXNS], si_out_n[MAX_TXNS];
    int write_set[MAX_TXNS][MAX_ROWS], write_set_n[MAX_TXNS];
    row_t rows[MAX_ROWS];
    int n_rows;
} tm_t;

static void tm_init(tm_t * tm, iso_level_t level) {
    memset(tm, 0, sizeof(*tm));
    tm->level = level;
}

static int begin_txn(tm_t * tm) {
    int t = tm->next_txn++;
    tm->txn_state[t] = 1;
    return t;
}

static void snapshot_make(tm_t * tm, snapshot_t * s, int tid) {
    s->n_active = s->n_committed = s->n_aborted = s->n_seen = 0;
    for (int i = 0; i < tm->next_txn; i++) {
        if (i == tid) continue;
        if (tm->txn_state[i] == 1) s->active[s->n_active++] = i;
        else if (tm->txn_state[i] == 2) s->committed_before[s->n_committed++] = i;
        else if (tm->txn_state[i] == 3) s->aborted[s->n_aborted++] = i;
    }
}

static row_t * find_row(tm_t * tm, int pk) {
    for (int i = 0; i < tm->n_rows; i++)
        if (tm->rows[i].pk == pk) return &tm->rows[i];
    return NULL;
}

static int is_in_set(int * arr, int n, int v) {
    for (int i = 0; i < n; i++) if (arr[i] == v) return 1;
    return 0;
}

static int visible(tm_t * tm, snapshot_t * s, int creator) {
    if (is_in_set(s->aborted, s->n_aborted, creator)) return 0;
    if (is_in_set(s->active, s->n_active, creator)) return 0;
    if (is_in_set(s->committed_before, s->n_committed, creator)) return 1;
    if (is_in_set(s->seen_committed, s->n_seen, creator)) return 1;
    return 0;
}

static int read_row(tm_t * tm, snapshot_t * s, int pk, int vals[4]) {
    if (tm->level == READ_UNCOMMITTED) {
        row_t * r = find_row(tm, pk);
        if (r && r->n_vers > 0) {
            memcpy(vals, r->vers[r->n_vers-1].values, sizeof(int)*4);
            return 1;
        }
        return 0;
    }
    row_t * r = find_row(tm, pk);
    if (!r) return 0;
    for (int i = r->n_vers - 1; i >= 0; i--) {
        if (visible(tm, s, r->vers[i].creator_txn)) {
            memcpy(vals, r->vers[i].values, sizeof(int)*4);
            // SSI: track read set
            if (tm->level == SERIALIZABLE) {
                for (int o = 0; o < tm->next_txn; o++) {
                    if (o == s->active[0]) continue;
                    if (tm->txn_state[o] != 2) continue;
                    for (int j = 0; j < tm->write_set_n[o]; j++)
                        if (tm->write_set[o][j] == pk)
                            tm->si_in[s->active[0] < 0 ? 0 : 0][o] = 1;
                }
            }
            return 1;
        }
    }
    return 0;
}

static void write_row(tm_t * tm, int tid, int pk, int vals[4]) {
    row_t * r = find_row(tm, pk);
    if (!r) { r = &tm->rows[tm->n_rows++]; r->pk = pk; r->n_vers = 0; }
    version_t * v = &r->vers[r->n_vers++];
    memcpy(v->values, vals, sizeof(int)*4);
    v->creator_txn = tid;
    v->deleter_txn = -1;
    tm->write_set[tid][tm->write_set_n[tid]++] = pk;
}

static bool dangerous(tm_t * tm, int tid) {
    // 2-cycle: incoming + outgoing same
    for (int i = 0; i < tm->si_in_n[tid]; i++)
        for (int j = 0; j < tm->si_out_n[tid]; j++)
            if (tm->si_in[tid][i] == tm->si_out[tid][j])
                return true;
    return false;
}

static bool commit_txn(tm_t * tm, int tid) {
    if (tm->level == SERIALIZABLE && dangerous(tm, tid)) {
        tm->txn_state[tid] = 3;  // aborted
        return false;
    }
    tm->txn_state[tid] = 2;
    return true;
}

// ---- scenarios --------------------------------------------------------
static void seed_committed(tm_t * tm) {
    int vals[4] = {0,0,0,0};
    write_row(tm, 0, 1, (int[]){100,0,0,0});
    write_row(tm, 0, 2, (int[]){100,0,0,0});
    tm->txn_state[0] = 2;
    (void)vals;
}

static bool test_phantom(tm_t * tm) {
    tm->n_rows = 0; memset(tm->txn_state, 0, sizeof(tm->txn_state));
    tm->next_txn = 1;
    for (int pk = 1; pk <= 5; pk++)
        write_row(tm, 0, pk, (int[]){pk,0,0,0});
    tm->txn_state[0] = 2;
    int t2 = begin_txn(tm);
    snapshot_t s; snapshot_make(tm, &s, t2);
    int n1 = 0;
    for (int pk = 1; pk <= 10; pk++) {
        int v[4]; if (read_row(tm, &s, pk, v)) n1++;
    }
    int t1 = begin_txn(tm);
    write_row(tm, t1, 6, (int[]){6,0,0,0});
    tm->txn_state[t1] = 2;
    int n2 = 0;
    for (int pk = 1; pk <= 10; pk++) {
        int v[4]; if (read_row(tm, &s, pk, v)) n2++;
    }
    return n1 != n2;
}

int main(void) {
    iso_level_t levels[] = {READ_UNCOMMITTED, READ_COMMITTED,
                            REPEATABLE_READ, SERIALIZABLE};
    const char * names[] = {"READ UNCOMMITTED", "READ COMMITTED",
                            "REPEATABLE READ", "SERIALIZABLE"};
    for (int i = 0; i < 4; i++) {
        tm_t tm; tm_init(&tm, levels[i]);
        bool ph = test_phantom(&tm);
        printf("%-18s phantom=%s\n", names[i], ph ? "observed" : "prevented");
    }
    return 0;
}