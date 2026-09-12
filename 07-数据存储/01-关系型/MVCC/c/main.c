/*
 * MVCC 多版本并发控制 — InnoDB 风格最小实现 (纯 C11, 无外部依赖)
 *
 * 核心模型:
 *   - Row { trx_id, roll_ptr → undo log 老版本, value, deleted }
 *   - undo_log 是单链表, 每个 entry 含上一个 entry 的 idx (链头方向)
 *   - ReadView 在 RC 下每次新 SELECT 创建, RR 下首 SELECT 创建后复用
 *   - 可见性规则见 mvcc_is_visible()
 *
 * 参考: MySQL 9.7 Reference Manual 17.3 InnoDB Multi-Versioning
 * https://dev.mysql.com/doc/refman/9.7/en/innodb-multi-versioning.html
 *
 * 编译: gcc -O2 -Wall -Wextra -std=c11 main.c -o main && ./main
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>

#define MAX_ROWS 32
#define MAX_UNDO 256
#define MAX_ACTIVE 64
#define MAX_TX 64

typedef struct {
    int roll_ptr_next; /* undo log 中上一个版本的 idx; -1 = 无 */
    int trx_id;
    int value;
    bool deleted;
} UndoEntry;

typedef struct {
    int value;
    int trx_id;        /* DB_TRX_ID: 最后修改者 */
    int roll_ptr;      /* DB_ROLL_PTR: undo log 中上一个版本; -1 = 基线 */
    bool deleted;
} Row;

typedef struct {
    int low_water;
    int high_water;
    int active_ids[MAX_ACTIVE];
    int active_count;
    int creator_id;
} ReadView;

typedef struct {
    int tx_id;
    bool committed;
    bool aborted;
    ReadView snapshot;
    bool snapshot_set;
    bool is_rr; /* REPEATABLE READ (true) vs READ COMMITTED (false) */
} Transaction;

typedef struct {
    Row rows[MAX_ROWS];
    char row_keys[MAX_ROWS][64];
    int row_count;
    UndoEntry undo_log[MAX_UNDO];
    int undo_count;
    Transaction txs[MAX_TX];
    int tx_count;
    int next_tx_id;
} MVCCDB;

void db_init(MVCCDB *db) {
    memset(db, 0, sizeof(*db));
    db->next_tx_id = 1;
}

int db_row_idx(MVCCDB *db, const char *name) {
    for (int i = 0; i < db->row_count; i++)
        if (strcmp(db->row_keys[i], name) == 0) return i;
    return -1;
}

int db_get_or_create_row(MVCCDB *db, const char *name) {
    int idx = db_row_idx(db, name);
    if (idx >= 0) return idx;
    assert(db->row_count < MAX_ROWS);
    idx = db->row_count++;
    strncpy(db->row_keys[idx], name, 63);
    db->rows[idx].trx_id = 0;
    db->rows[idx].roll_ptr = -1;
    return idx;
}

void tx_acquire_snapshot(MVCCDB *db, Transaction *tx) {
    if (tx->is_rr && tx->snapshot_set) return;
    ReadView *rv = &tx->snapshot;
    rv->creator_id = tx->tx_id;
    /* 收集 active ids */
    rv->active_count = 0;
    int min_active = 0;
    for (int i = 0; i < db->tx_count; i++) {
        Transaction *t = &db->txs[i];
        if (t->committed || t->aborted) continue;
        rv->active_ids[rv->active_count++] = t->tx_id;
        if (min_active == 0 || t->tx_id < min_active) min_active = t->tx_id;
    }
    rv->low_water = (rv->active_count > 0) ? min_active : db->next_tx_id;
    rv->high_water = db->next_tx_id;
    tx->snapshot_set = true;
}

bool tx_visible_rule(Transaction *tx, int trx_id) {
    ReadView *rv = &tx->snapshot;
    if (trx_id == rv->creator_id) return true;
    if (trx_id < rv->low_water) return true;
    if (trx_id >= rv->high_water) return false;
    for (int i = 0; i < rv->active_count; i++)
        if (rv->active_ids[i] == trx_id) return false;
    return true; /* 在 active 列表外 = 已提交 */
}

/* 沿 roll_ptr 链查找第一个可见版本, 返回 value 指针或 NULL */
int *tx_read(MVCCDB *db, Transaction *tx, const char *name) {
    tx_acquire_snapshot(db, tx);
    int idx = db_row_idx(db, name);
    if (idx < 0) return NULL;
    Row *r = &db->rows[idx];
    int cur_trx = r->trx_id, cur_rp = r->roll_ptr;
    while (true) {
        if (tx_visible_rule(tx, cur_trx)) {
            return r->deleted ? NULL : &r->value;
        }
        if (cur_rp == -1) return NULL;
        int saved_trx = cur_trx, saved_rp = cur_rp;
        UndoEntry *e = &db->undo_log[cur_rp];
        /* 检查 e 对应的"那行"是否可见 */
        if (tx_visible_rule(tx, e->trx_id)) {
            return e->deleted ? NULL : &e->value;
        }
        cur_trx = e->trx_id; cur_rp = e->roll_ptr_next;
        (void)saved_trx; (void)saved_rp;
    }
}

void tx_update(MVCCDB *db, Transaction *tx, const char *name, int value) {
    int idx = db_get_or_create_row(db, name);
    Row *r = &db->rows[idx];
    /* 旧版本 push 进 undo log */
    assert(db->undo_count < MAX_UNDO);
    int old_idx = db->undo_count++;
    UndoEntry *e = &db->undo_log[old_idx];
    e->roll_ptr_next = r->roll_ptr;
    e->trx_id = r->trx_id;
    e->value = r->value;
    e->deleted = r->deleted;
    r->roll_ptr = old_idx;
    r->trx_id = tx->tx_id;
    r->value = value;
    r->deleted = false;
}

Transaction *db_begin(MVCCDB *db, bool is_rr) {
    assert(db->tx_count < MAX_TX);
    Transaction *tx = &db->txs[db->tx_count++];
    tx->tx_id = db->next_tx_id++;
    tx->committed = false; tx->aborted = false;
    tx->is_rr = is_rr; tx->snapshot_set = false;
    return tx;
}

void db_commit(Transaction *tx) { tx->committed = true; }
void db_rollback(Transaction *tx) { tx->aborted = true; }

/* --- Demos --- */

static void demo_basic(void) {
    MVCCDB db; db_init(&db);
    int ridx = db_get_or_create_row(&db, "x");
    db.rows[ridx].value = 100; db.rows[ridx].trx_id = 0;
    /* 不开 active tx 让初始 row (trx_id=0) 在 snapshot high_water=1 时被丢 */
    Transaction *t1 = db_begin(&db, false);
    tx_update(&db, t1, "x", 200);
    Transaction *t2 = db_begin(&db, false);
    int *p = tx_read(&db, t2, "x");
    printf("[1] T2 before T1 commit: x=%s\n", p ? "100" : "NULL");
    assert(p && *p == 100);
    db_commit(t1);
    Transaction *t3 = db_begin(&db, false);
    p = tx_read(&db, t3, "x");
    printf("[1] T3 after T1 commit: x=%s (committed 200 visible)\n", p ? "200" : "NULL");
    assert(p && *p == 200);
}

static void demo_rr_reuse(void) {
    MVCCDB db; db_init(&db);
    int ridx = db_get_or_create_row(&db, "balance");
    db.rows[ridx].value = 1000; db.rows[ridx].trx_id = 0;
    Transaction *t1 = db_begin(&db, true); /* RR */
    int *p1 = tx_read(&db, t1, "balance");
    assert(p1 && *p1 == 1000);
    Transaction *t2 = db_begin(&db, false);
    tx_update(&db, t2, "balance", 1500);
    db_commit(t2);
    int *p2 = tx_read(&db, t1, "balance");
    printf("[2] RR 2nd read still sees balance=%s (snapshot reused)\n", p2 ? "1000" : "NULL");
    assert(p2 && *p2 == 1000);
}

static void demo_rc_per_stmt(void) {
    MVCCDB db; db_init(&db);
    int ridx = db_get_or_create_row(&db, "x");
    db.rows[ridx].value = 100; db.rows[ridx].trx_id = 0;
    Transaction *t1 = db_begin(&db, false); /* RC */
    int *p1 = tx_read(&db, t1, "x");
    assert(p1 && *p1 == 100);
    Transaction *t2 = db_begin(&db, false);
    tx_update(&db, t2, "x", 999);
    db_commit(t2);
    int *p2 = tx_read(&db, t1, "x");
    printf("[3] RC 2nd read sees committed t2: x=%s\n", p2 ? "999" : "NULL");
    assert(p2 && *p2 == 999);
}

static void demo_chain(void) {
    MVCCDB db; db_init(&db);
    int ridx = db_get_or_create_row(&db, "k");
    db.rows[ridx].value = 0; db.rows[ridx].trx_id = 0;
    Transaction *t1 = db_begin(&db, false);
    for (int v = 10; v <= 40; v += 10) tx_update(&db, t1, "k", v);
    db_commit(t1);
    int chain = 0, cur = db.rows[ridx].roll_ptr;
    while (cur != -1) { chain++; cur = db.undo_log[cur].roll_ptr_next; }
    printf("[4] Undo chain len = %d (after 4 updates)\n", chain);
    assert(chain == 3);
}

static void demo_write_conflict(void) {
    MVCCDB db; db_init(&db);
    int ridx = db_get_or_create_row(&db, "y");
    db.rows[ridx].value = 50; db.rows[ridx].trx_id = 0;
    Transaction *t1 = db_begin(&db, true);
    Transaction *t2 = db_begin(&db, true);
    tx_update(&db, t1, "y", 60);
    tx_update(&db, t2, "y", 70);
    db_commit(t2);
    db_rollback(t1);
    /* 注意: demo 不回滚实际行, 仅展示 last-writer-wins 简化语义 */
    printf("[5] Final y=%d (last commit wins)\n", db.rows[ridx].value);
    assert(db.rows[ridx].value == 70);
}

int main(void) {
    printf("== MVCC 多版本并发控制 ==\n");
    demo_basic();
    demo_rr_reuse();
    demo_rc_per_stmt();
    demo_chain();
    demo_write_conflict();
    printf("All 5 demos passed.\n");
    return 0;
}
