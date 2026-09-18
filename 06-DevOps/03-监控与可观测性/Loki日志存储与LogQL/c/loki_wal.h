/* Loki WAL（写前日志）与崩溃恢复契约 —— C 版实现头（与 core/storage/query
 * 同属一个翻译单元，由 loki_demo.c 文本级包含；不要单独编译）。
 *
 * 单独成文件是为了压行数，也正好对上 Python/Go 版的 loki_ingester.py /
 * loki_wal.go —— WAL 是 ingester 的伴生结构，不与 chunk 模型混在一起。
 *
 * 演示三条契约：
 *   1. 未刷写的 entry 一定在 WAL 里（pending），崩溃后能整体重放；
 *   2. checkpoint 只折叠**已落盘**的部分（replayed_from 前移），不删数据；
 *   3. 优雅关闭走另一条路：chunk 已落存储 → WAL 可以整体截断。
 */

#ifndef LOKI_WAL_H
#define LOKI_WAL_H

#include "loki_core.h"

#define LOKI_WAL_MAX 64

typedef struct {
    char tenant[64];
    long long ts_ns;
    char line[LOKI_LINE_MAX];
    int flushed;
} loki_wal_record;

typedef struct {
    loki_wal_record records[LOKI_WAL_MAX];
    int count;
    int replayed_from; /* 重放起点：它之前的记录已被 checkpoint 折叠掉 */
    double checkpoint_s;
    long long next_checkpoint_ns;
} loki_wal;

static void wal_init(loki_wal *w, double checkpoint_s) {
    memset(w, 0, sizeof(*w));
    w->checkpoint_s = checkpoint_s;
}

static void wal_append(loki_wal *w, const char *tenant, long long ts_ns, const char *line) {
    if (w->count >= LOKI_WAL_MAX) {
        return; /* demo 不触发；真实实现会阻塞而不是丢弃 */
    }
    loki_wal_record *r = &w->records[w->count];
    snprintf(r->tenant, sizeof(r->tenant), "%s", tenant);
    r->ts_ns = ts_ns;
    snprintf(r->line, sizeof(r->line), "%s", line);
    r->flushed = 0;
    w->count++;
    if (w->count == 1) {
        w->next_checkpoint_ns = ts_ns + (long long)(w->checkpoint_s * 1e9);
    }
}

/* 待重放 = checkpoint 之后仍未确认刷写的记录数（这才是崩溃恢复要重放的量）。 */
static int wal_pending_count(const loki_wal *w) {
    int n = 0;
    for (int i = w->replayed_from; i < w->count; i++) {
        if (!w->records[i].flushed) {
            n++;
        }
    }
    return n;
}

static int wal_checkpoint(loki_wal *w, long long now_ns) {
    if (now_ns < w->next_checkpoint_ns) {
        return 0;
    }
    w->replayed_from = w->count;
    w->next_checkpoint_ns = now_ns + (long long)(w->checkpoint_s * 1e9);
    return 1;
}

/* 优雅关闭：chunk 已落存储，WAL 可整体截断（与崩溃恢复走的是不同路径）。 */
static void wal_mark_flushed(loki_wal *w) {
    for (int i = w->replayed_from; i < w->count; i++) {
        w->records[i].flushed = 1;
    }
    w->replayed_from = w->count;
}

#endif /* LOKI_WAL_H */
