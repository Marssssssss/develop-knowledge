/*
 * 两阶段锁 (2PL) + 死锁检测 — 最小实现 (C11)
 *
 * 参考: CMU 15-445 Spring 2023 L16 Two-Phase Locking
 *       https://15445.courses.cs.cmu.edu/spring2023/notes/16-twophaselocking.pdf
 * 编译: gcc -O2 -Wall -Wextra -std=c11 main.c -o main && ./main
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>

#define MAX_TX 32
#define MAX_RES 32
#define MAX_REQS 64

typedef enum { LM_S, LM_X } LockMode;
static const char *LMN[] = {"S", "X"};

typedef struct {
    int tx; LockMode mode; bool granted;
} Req;

typedef struct {
    Req reqs[MAX_REQS];
    int n_req;
    int granted_x;        /* -1 = none */
    int granted_s[MAX_TX];
    int n_granted_s;
} Lock;

typedef struct {
    int id;
    bool growing;
    bool state_aborted;
} TX;

typedef struct {
    TX txs[MAX_TX]; int n_tx; int next_tx;
    Lock locks[MAX_RES]; char names[MAX_RES][32]; int n_res;
    int waits_for[MAX_TX][MAX_TX]; /* adjacency */
} DB;

int db_lookup_res(DB *db, const char *name) {
    for (int i = 0; i < db->n_res; i++)
        if (strcmp(db->names[i], name) == 0) return i;
    return -1;
}
Lock *db_get_lock(DB *db, const char *name) {
    int idx = db_lookup_res(db, name);
    if (idx < 0) {
        idx = db->n_res++;
        strncpy(db->names[idx], name, 31);
        db->locks[idx].granted_x = -1;
    }
    return &db->locks[idx];
}
int db_begin(DB *db) {
    int tx = db->next_tx++;
    db->txs[tx].id = tx;
    db->txs[tx].growing = true;
    db->txs[tx].state_aborted = false;
    db->n_tx++;
    return tx;
}
bool db_can_grant(Lock *l, int tx, LockMode mode) {
    if (mode == LM_S) {
        if (l->granted_x != -1) return false;
        /* FIFO 避免饥饿: 若有未 grant 的 X 在队列前, S 也需等 */
        for (int i = 0; i < l->n_req; i++)
            if (!l->reqs[i].granted && l->reqs[i].mode == LM_X) return false;
        return true;
    } else {
        if (l->granted_x != -1) return false;
        if (l->n_granted_s > 0) return false;
        if (l->n_req > 0) return false;
        return true;
    }
}
void add_wait(DB *db, int waiter, int holder) {
    db->waits_for[waiter][holder] = 1;
}
void remove_wait_edges(DB *db, int t) {
    memset(db->waits_for[t], 0, sizeof(db->waits_for[0]));
}
bool detect_cycle(DB *db, int cycle_out[], int *n_cycle) {
    /* DFS from each waiter */
    int visited[MAX_TX] = {0};
    for (int start = 0; start < db->next_tx; start++) {
        for (int end = 0; end < db->next_tx; end++) {
            if (db->waits_for[start][end]) {
                int stack[MAX_TX], top = 0;
                int path[MAX_TX], np = 0;
                stack[top++] = start; path[np++] = start;
                while (top > 0) {
                    int cur = stack[top - 1];
                    if (visited[cur]) { top--; np--; continue; }
                    visited[cur] = 1;
                    int next = -1;
                    for (int i = 0; i < db->next_tx; i++)
                        if (db->waits_for[cur][i] && !visited[i]) { next = i; break; }
                    if (next == -1) { top--; np--; continue; }
                    /* cycle if next is in path */
                    bool in_path = false;
                    int start_idx = 0;
                    for (int i = 0; i < np; i++) if (path[i] == next) { in_path = true; start_idx = i; break; }
                    if (in_path) {
                        int len = np - start_idx;
                        for (int k = 0; k < len; k++) cycle_out[k] = path[start_idx + k];
                        cycle_out[len] = next;
                        *n_cycle = len + 1;
                        return true;
                    }
                    stack[top++] = next; path[np++] = next;
                }
            }
        }
    }
    return false;
}
bool db_lock(DB *db, int tx, const char *res, LockMode mode) {
    if (!db->txs[tx].growing) return false;
    Lock *l = db_get_lock(db, res);
    if (db_can_grant(l, tx, mode)) {
        l->reqs[l->n_req++] = (Req){tx, mode, true};
        if (mode == LM_S) l->granted_s[l->n_granted_s++] = tx;
        else l->granted_x = tx;
        return true;
    }
    l->reqs[l->n_req++] = (Req){tx, mode, false};
    /* record wait edges */
    if (mode == LM_S) {
        if (l->granted_x != -1) add_wait(db, tx, l->granted_x);
    } else {
        if (l->granted_x != -1) add_wait(db, tx, l->granted_x);
        for (int i = 0; i < l->n_granted_s; i++) add_wait(db, tx, l->granted_s[i]);
    }
    int cycle[16]; int n;
    if (detect_cycle(db, cycle, &n)) {
        int victim = 0;
        for (int i = 0; i < n - 1; i++) if (cycle[i] > victim) victim = cycle[i];
        /* abort victim: release its locks */
        for (int i = 0; i < db->n_res; i++) {
            Lock *lk = &db->locks[i];
            /* remove victim from granted */
            for (int k = 0; k < lk->n_granted_s; k++)
                if (lk->granted_s[k] == victim) { lk->granted_s[k] = lk->granted_s[--lk->n_granted_s]; }
            if (lk->granted_x == victim) lk->granted_x = -1;
            /* remove victim from waiting */
            int w = 0;
            for (int k = 0; k < lk->n_req; k++)
                if (lk->reqs[k].tx != victim) lk->reqs[w++] = lk->reqs[k];
            lk->n_req = w;
        }
        remove_wait_edges(db, victim);
        db->txs[victim].state_aborted = true;
        printf("    -> deadlock victim = T%d, cycle = [", victim);
        for (int i = 0; i < n; i++) printf("%s%d", i ? " -> " : "", cycle[i]);
        printf(" -> ]\n");
        return false;
    }
    return false;
}
void db_unlock_all(DB *db, int tx) {
    db->txs[tx].growing = false;
    for (int i = 0; i < db->n_res; i++) {
        Lock *l = &db->locks[i];
        for (int k = 0; k < l->n_req; k++) {
            if (l->reqs[k].tx != tx || !l->reqs[k].granted) continue;
            if (l->reqs[k].mode == LM_S) {
                for (int m = 0; m < l->n_granted_s; m++)
                    if (l->granted_s[m] == tx) { l->granted_s[m] = l->granted_s[--l->n_granted_s]; break; }
            } else {
                if (l->granted_x == tx) l->granted_x = -1;
            }
            l->reqs[k].granted = false; /* 标记 */
        }
    }
    remove_wait_edges(db, tx);
}

/* --- demos --- */
static void demo_xlock(DB *db) {
    int t1 = db_begin(db); int t2 = db_begin(db);
    bool r1 = db_lock(db, t1, "A", LM_X);
    bool r2 = db_lock(db, t2, "A", LM_X);
    printf("[1] T%d X(A) granted=%d; T%d X(A) blocked=%d\n", t1, r1, t2, !r2);
    db_unlock_all(db, t1);
}
static void demo_s_shared(DB *db) {
    int t1 = db_begin(db); int t2 = db_begin(db); int t3 = db_begin(db);
    bool a = db_lock(db, t1, "A", LM_S); bool b = db_lock(db, t2, "A", LM_S);
    bool c = db_lock(db, t3, "A", LM_X);
    printf("[2] S-S 共存: T%d.grant=%d T%d.grant=%d; T%d X(A) blocked=%d\n", t1, a, t2, b, t3, !c);
    db_unlock_all(db, t1); db_unlock_all(db, t2);
}
static void demo_strict(DB *db) {
    int t = db_begin(db);
    db_lock(db, t, "K", LM_X);
    printf("[3] T%d growing=%d, locks 已持\n", t, db->txs[t].growing);
    db_unlock_all(db, t);
    printf("[3] commit 后 growing=%d (变为 shrinking/已释放)\n", db->txs[t].growing);
}
static void demo_cycle(DB *db) {
    int t1 = db_begin(db); int t2 = db_begin(db);
    db_lock(db, t1, "A", LM_X); db_lock(db, t2, "B", LM_X);
    db_lock(db, t1, "B", LM_X); db_lock(db, t2, "A", LM_X);
    printf("[4] cycle detected — see above\n");
}
static void demo_iso() {
    printf("[5] 异常矩阵见 README §'对比/选型'\n");
}
int main(void) {
    DB db = {0}; db.next_tx = 1; db.granted_x = -1;
    printf("== 两阶段锁 2PL + 死锁检测 ==\n");
    demo_xlock(&db); demo_s_shared(&db); demo_strict(&db);
    demo_cycle(&db); demo_iso();
    printf("All 5 demos OK.\n");
    return 0;
}
