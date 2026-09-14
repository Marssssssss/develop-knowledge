// Buffer Pool — InnoDB-style midpoint insertion LRU + clock sweep
// + WAL-before-data + fuzzy checkpoint.
//
// Page-table hash + frame array + ordered LRU list + dirty page flush
// list.  Mirror of buffer_pool.py.
//
// Refs:
//   - MySQL 8.0 manual §17.5.1 Buffer Pool LRU algorithm:
//     https://dev.mysql.com/doc/refman/8.0/en/innodb-buffer-pool.html
//   - 庖丁解 InnoDB 之 Buffer Pool (catkang 2023)

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

#define POOL_SIZE 16
#define PAGE_TABLE_CAP 64

typedef struct {
    long lsn;
    int  page_id;
} flush_entry_t;

typedef struct {
    int  page_id;       // -1 if free
    int  pin_count;
    int  dirty;
    int  usage;
    bool is_old;
    long page_lsn;
} frame_t;

typedef struct {
    frame_t      frames[POOL_SIZE];
    int          lru[POOL_SIZE];     // MRU → LRU
    int          free_idx[POOL_SIZE];
    int          n_free;
    flush_entry_t flush_list[POOL_SIZE];
    int          n_flush;
    int          hits, misses, evictions, flushes;
    int          size, old_pct;
} pool_t;

// page_id → frame_index (parallel arrays)
static int pt_pid[PAGE_TABLE_CAP];
static int pt_fi [PAGE_TABLE_CAP];
static int pt_n  = 0;

static long wal_lsn = 0;
static long wal_flush(void) { return (wal_lsn += 10); }

static int  mru_index(pool_t * p) { return p->size * (100 - p->old_pct) / 100; }

static void lru_remove(pool_t * p, int fi) {
    for (int i = 0; i < p->size; i++) if (p->lru[i] == fi) {
        for (int j = i; j < p->size - 1; j++) p->lru[j] = p->lru[j + 1];
        p->lru[p->size - 1] = -1;
        return;
    }
}

static void lru_insert_new(pool_t * p, int fi) {
    for (int i = p->size - 1; i > 0; i--) p->lru[i] = p->lru[i - 1];
    p->lru[0] = fi;
    p->frames[fi].is_old = false;
}

static void lru_insert_old(pool_t * p, int fi) {
    int mid = mru_index(p);
    for (int i = p->size - 1; i > mid; i--) p->lru[i] = p->lru[i - 1];
    p->lru[mid] = fi;
    p->frames[fi].is_old = true;
}

static int pt_get_fi(int pid) {
    for (int i = 0; i < pt_n; i++) if (pt_pid[i] == pid) return pt_fi[i];
    return -1;
}

static void pt_put(int pid, int fi) {
    pt_pid[pt_n] = pid;
    pt_fi [pt_n] = fi;
    pt_n++;
}

static void pt_del(int pid) {
    for (int i = 0; i < pt_n; i++) if (pt_pid[i] == pid) {
        for (int j = i; j < pt_n - 1; j++) {
            pt_pid[j] = pt_pid[j + 1];
            pt_fi [j] = pt_fi [j + 1];
        }
        pt_n--; return;
    }
}

static int acquire_frame(pool_t * p) {
    if (p->n_free > 0) return p->free_idx[--p->n_free];
    // clock sweep over old sublist (tail-first)
    int start = mru_index(p);
    for (int i = p->size - 1; i >= start; i--) {
        int fi = p->lru[i];
        if (fi < 0) continue;
        frame_t * f = &p->frames[fi];
        if (f->pin_count == 0) {
            if (f->usage > 1) { f->usage--; continue; }
            return fi;
        }
    }
    // fallback: full scan over entire LRU
    for (int i = p->size - 1; i >= 0; i--) {
        int fi = p->lru[i];
        if (fi < 0) continue;
        if (p->frames[fi].pin_count == 0) return fi;
    }
    return -1;
}

static void evict(pool_t * p, int fi) {
    frame_t * f = &p->frames[fi];
    if (f->dirty) {
        long w = wal_flush();
        if (f->page_lsn > w) {
            fprintf(stderr, "WAL-before-data violated\n");
            return;
        }
        f->dirty = 0;
        p->flushes++;
    }
    int pid = f->page_id;
    pt_del(pid);
    lru_remove(p, fi);
    p->free_idx[p->n_free++] = fi;
    f->page_id = -1;
    f->pin_count = f->dirty = f->usage = 0;
    f->page_lsn = 0;
    p->evictions++;
}

static int fix_page(pool_t * p, int pid) {
    int fi = pt_get_fi(pid);
    if (fi >= 0) {
        frame_t * f = &p->frames[fi];
        f->pin_count++;
        if (f->is_old) { lru_remove(p, fi); lru_insert_new(p, fi); }
        p->hits++;
        return fi;
    }
    p->misses++;
    fi = acquire_frame(p);
    if (fi < 0) return -1;
    frame_t * f = &p->frames[fi];
    if (f->page_id >= 0) evict(p, fi);
    f->page_id  = pid;
    f->pin_count = 1;
    f->usage    = 1;
    f->dirty    = 0;
    pt_put(pid, fi);
    lru_insert_old(p, fi);
    return fi;
}

static void unfix(pool_t * p, int fi, int dirty) {
    frame_t * f = &p->frames[fi];
    f->pin_count--;
    if (dirty) {
        f->dirty    = 1;
        f->page_lsn = wal_flush();
        p->flush_list[p->n_flush].lsn     = f->page_lsn;
        p->flush_list[p->n_flush].page_id = f->page_id;
        p->n_flush++;
    }
}

static void print_lru(pool_t * p) {
    printf("LRU MRU→LRU: ");
    for (int i = 0; i < p->size; i++) {
        int fi = p->lru[i];
        if (fi < 0) continue;
        if (p->frames[fi].page_id >= 0)
            printf("%d%s ", p->frames[fi].page_id,
                   p->frames[fi].is_old ? "o" : "n");
    }
    printf("\n");
}

int main(void) {
    pool_t pool;
    memset(&pool, 0, sizeof(pool));
    pool.size    = POOL_SIZE;
    pool.old_pct = 37;
    for (int i = 0; i < POOL_SIZE; i++) {
        pool.frames[i].page_id = -1;
        pool.lru[i] = -1;
        pool.free_idx[i] = POOL_SIZE - 1 - i;
    }
    pool.n_free = POOL_SIZE;

    // 1) sequential scan beyond pool size → forces evictions
    for (int pid = 1; pid <= POOL_SIZE + 5; pid++) {
        int fi = fix_page(&pool, pid);
        unfix(&pool, fi, 0);
    }
    printf("after scan:       hits=%d misses=%d evictions=%d\n",
           pool.hits, pool.misses, pool.evictions);

    // 2) hot loop on pages 1..5 → should all hit and stay in new sublist
    for (int round = 0; round < 5; round++)
        for (int pid = 1; pid <= 5; pid++) {
            int fi = fix_page(&pool, pid);
            unfix(&pool, fi, 0);
        }
    printf("after hot loop:   hits=%d misses=%d evictions=%d dirty=%d\n",
           pool.hits, pool.misses, pool.evictions, pool.n_flush);

    // 3) write two dirty pages
    int fi2 = fix_page(&pool, 2);
    unfix(&pool, fi2, 1);
    int fi4 = fix_page(&pool, 4);
    unfix(&pool, fi4, 1);

    print_lru(&pool);

    // 4) big scan resistance check
    for (int pid = 100; pid < 130; pid++) {
        int fi = fix_page(&pool, pid);
        unfix(&pool, fi, 0);
    }
    int hot_resident = 0;
    for (int pid = 1; pid <= 5; pid++)
        if (pt_get_fi(pid) >= 0) hot_resident++;
    printf("after big scan:   hot_1..5_resident=%d/5  evictions=%d\n",
           hot_resident, pool.evictions);

    return 0;
}