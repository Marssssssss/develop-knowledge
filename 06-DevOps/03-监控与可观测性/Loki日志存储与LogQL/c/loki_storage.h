/* Loki 限额、chunk/stream 内存模型、WAL 与 schema 校验 —— C 版实现头。
 *
 * 与 loki_core.h 同样是**实现头**（文本级包含），含 static 定义，
 * 由 loki_demo.c 与 query 侧实现头一起并入同一个翻译单元；不要单独编译。
 *
 * 与 Python/Go 版的差异：没有 ring / 一致性哈希 / 成员表，限额那部分只保留
 * 「均摊」这条**能算出确定数值**的规则（N 由调用方显式给定，而不是去推
 * ring 的健康实例数）—— 这恰好是本 demo 要演示的那一条，其余是分布式细节。
 */

#ifndef LOKI_STORAGE_H
#define LOKI_STORAGE_H

#include "loki_core.h"

/* ---- chunk / 存储常量（默认值取自官方配置参考手册） ---- */
#define LOKI_CHUNK_TARGET_SIZE 1572864LL /* 1.5 MiB，**压缩后**的目标大小 */
#define LOKI_CHUNK_BLOCK_SIZE 262144LL   /* 256 KiB，**未压缩**的 block 大小 */
#define LOKI_MAX_CHUNK_AGE_S 7200.0      /* 2h：chunk 存活上限 */
#define LOKI_DEFAULT_ENCODING "gzip"     /* 默认 gzip；最佳实践**推荐** snappy */
#define LOKI_DEFAULT_ROW_SHARDS 16       /* schema v10 起 tsdb 的行分片默认值 */

/* ---- 推送结果 ---- */
#define LOKI_PUSH_APPENDED 0
#define LOKI_PUSH_DUPLICATE 1
#define LOKI_PUSH_OUT_OF_ORDER 2
#define LOKI_PUSH_OVERFLOW 3

/* ---- 刷写原因位掩码（三条件各自独立，可同时成立） ---- */
#define LOKI_FLUSH_IDLE 1
#define LOKI_FLUSH_SIZE 2
#define LOKI_FLUSH_AGE 4

/* ------------------------------------------------------------- 限额 */

typedef struct {
    double ingestion_rate_mb;
    double ingestion_burst_size_mb;
    char ingestion_rate_strategy[16];
    int max_label_names_per_series;
    int max_streams_per_user;
    int max_global_streams_per_user;
    char chunk_idle_period[16];
    long long max_line_size;
    int max_line_size_truncate;
    int max_label_name_length;
    int max_label_value_length;
    int max_entries_limit_per_query;
    int max_query_series;
} loki_limits;

/* 全部取自官方 limits_config 的 `default = ...`。有争议的项在注释里标出。 */
static loki_limits loki_default_limits(void) {
    loki_limits l;
    memset(&l, 0, sizeof(l));
    l.ingestion_rate_mb = 4.0;
    l.ingestion_burst_size_mb = 6.0;
    snprintf(l.ingestion_rate_strategy, sizeof(l.ingestion_rate_strategy), "global");
    l.max_label_names_per_series = 30; /* 最佳实践推荐压到 15 */
    l.max_streams_per_user = 0;        /* 本机实测 0 = 不限（文档另有 10000 的口径） */
    l.max_global_streams_per_user = 5000;
    snprintf(l.chunk_idle_period, sizeof(l.chunk_idle_period), "30m");
    l.max_line_size = 262144; /* 256KB */
    l.max_line_size_truncate = 0;
    l.max_label_name_length = 1024;
    l.max_label_value_length = 2048;
    l.max_entries_limit_per_query = 5000;
    l.max_query_series = 500;
    return l;
}

/* global 策略下 `ingestion_rate_mb` 按 ring 里的**健康实例数**均摊；
 * 实例数取不到（0）时返回 0 并把 *ok 置 0，而不是静默算出一个假值。 */
static double distributor_rate_mb(double rate_mb, int instances, const char *strategy, int *ok) {
    if (instances <= 0) {
        if (ok != NULL) {
            *ok = 0;
        }
        return 0.0;
    }
    if (ok != NULL) {
        *ok = 1;
    }
    if (strcmp(strategy, "global") == 0) {
        return rate_mb / (double)instances;
    }
    return rate_mb;
}

/* burst **不均摊**：官方原文即如此，于是它与实例数无关。
 * 扩容后 rate 阈值下降而 burst 不变，正是「滚动重启后 429 与降载同时出现」的成因。 */
static double distributor_burst_mb(double burst_mb, int instances, const char *strategy) {
    (void)instances;
    (void)strategy;
    return burst_mb;
}

/* local 策略不做均摊 → 集群实际总额被放大 N 倍（这是坑，不是特性）。 */
static double cluster_effective_rate_mb(double rate_mb, int instances, const char *strategy) {
    if (strcmp(strategy, "global") == 0) {
        return rate_mb;
    }
    return rate_mb * (double)instances;
}

static const char *check_line(const loki_limits *lim, long long size) {
    if (size <= lim->max_line_size) {
        return "ok";
    }
    return lim->max_line_size_truncate ? "truncated" : "rejected";
}

/* 注意判据是 `>` 而不是 `>=`：恰好等于限额是**通过**的。
 * demo 里 30 与 31 两条断言就是在钉这个边界（写反了会漏掉一整档）。 */
static const char *check_labels(const loki_limits *lim, const loki_entry *e) {
    for (int i = 0; i < e->nlabels; i++) {
        if ((int)strlen(e->labels[i].name) > lim->max_label_name_length) {
            return "label_name_too_long";
        }
        if ((int)strlen(e->labels[i].value) > lim->max_label_value_length) {
            return "label_value_too_long";
        }
    }
    if (e->nlabels > lim->max_label_names_per_series) {
        return "too_many_labels";
    }
    return "ok";
}

/* ------------------------------------------------- chunk 与 stream */

/* 这里复用的是 **stream** 而不是「chunk 对象」：一个 stream 在内存里只有
 * 一份活跃 chunk，所以 entry_count 归零就等于「该 chunk 已刷写」。
 * compression_ratio 是**说明性**参考值（业界常见 3~6 倍），不是实测数据，
 * 仅用于演示「target_size 按压缩后计算」这一点。 */
#define LOKI_STREAM_MAX 16

typedef struct {
    char tenant[64];
    long long target_size;
    double idle_s;
    double max_age_s;
    double compression_ratio;
    int entry_count;
    int ignored_duplicates;
    int rejected_out_of_order;
    int rejected_overflow;
    int flushed_chunks;
    long long chunk_start_ns;
    long long bytes_uncompressed;
    long long ts[LOKI_STREAM_MAX];
    char lines[LOKI_STREAM_MAX][LOKI_LINE_MAX];
} loki_stream;

static void stream_init(loki_stream *st, const char *tenant, long long target_size,
                        double idle_s, double max_age_s, double compression_ratio) {
    memset(st, 0, sizeof(*st));
    snprintf(st->tenant, sizeof(st->tenant), "%s", tenant);
    st->target_size = target_size;
    st->idle_s = idle_s;
    st->max_age_s = max_age_s;
    st->compression_ratio = compression_ratio;
}

/* 三条时序规则，注意第 2、3 条与直觉相反：
 *   1. ts 回退 → 拒行（LOKI_PUSH_OUT_OF_ORDER），并单独计数；
 *   2. ts 与内容**都**相同 → 静默忽略，不算错误（网络重试的正常结果）；
 *   3. ts 相同但内容不同 → **接受**。这不是 bug，同一纳秒的多行日志是合法的。 */
static int stream_push(loki_stream *st, long long ts_ns, const char *line) {
    if (st->entry_count >= LOKI_STREAM_MAX) {
        st->rejected_overflow++;
        return LOKI_PUSH_OVERFLOW;
    }
    if (st->entry_count > 0) {
        int last = st->entry_count - 1;
        if (ts_ns == st->ts[last] && strcmp(st->lines[last], line) == 0) {
            st->ignored_duplicates++;
            return LOKI_PUSH_DUPLICATE;
        }
        if (ts_ns < st->ts[last]) {
            st->rejected_out_of_order++;
            return LOKI_PUSH_OUT_OF_ORDER;
        }
    }
    st->ts[st->entry_count] = ts_ns;
    snprintf(st->lines[st->entry_count], LOKI_LINE_MAX, "%s", line);
    st->bytes_uncompressed += (long long)strlen(st->lines[st->entry_count]);
    st->entry_count++;
    if (st->entry_count == 1) {
        st->chunk_start_ns = ts_ns;
    }
    return LOKI_PUSH_APPENDED;
}

/* 空闲自**最后一次写入**起算（不是从 chunk 开始），大小按**压缩后**算。 */
static int stream_flush_reasons(const loki_stream *st, long long now_ns) {
    if (st->entry_count == 0) {
        return 0;
    }
    int reasons = 0;
    double idle_ns = (double)(now_ns - st->ts[st->entry_count - 1]);
    if (idle_ns >= st->idle_s * 1e9) {
        reasons |= LOKI_FLUSH_IDLE;
    }
    double compressed = (double)st->bytes_uncompressed / st->compression_ratio;
    if (compressed >= (double)st->target_size) {
        reasons |= LOKI_FLUSH_SIZE;
    }
    if ((double)(now_ns - st->chunk_start_ns) >= st->max_age_s * 1e9) {
        reasons |= LOKI_FLUSH_AGE;
    }
    return reasons;
}

static int stream_flush(loki_stream *st) {
    int n = st->entry_count;
    if (n > 0) {
        st->flushed_chunks++;
    }
    st->entry_count = 0;
    st->bytes_uncompressed = 0;
    st->chunk_start_ns = 0;
    return n;
}

/* 写副本的仲裁数：floor(rf/2)+1。rf=1 时是 1（单副本不用商量），
 * rf=2 时是 2 —— 两副本必须都成功，所以 rf=2 的可用性反而**低于** rf=1。 */
static int loki_quorum(int replication_factor) {
    if (replication_factor <= 0) {
        return -1;
    }
    return replication_factor / 2 + 1;
}

/* --------------------------------------------------------------- WAL */
/* WAL 与崩溃恢复契约见同目录的 loki_wal.h（单独成文件压行数）。 */

/* ------------------------------------------------------------- schema */

static int schema_version_number(const char *schema) {
    if (schema == NULL || schema[0] != 'v') {
        return -1;
    }
    return atoi(schema + 1);
}

/* 返回 NULL 表示可以启动，否则返回官方式的 CONFIG ERROR 文案。
 * structured metadata 与原生 OTLP 摄入要求 tsdb + v13+，否则 Loki
 * **直接拒绝启动** —— 这是少数「配置写错进程起不来」的项，别当警告看。 */
static const char *schema_check(const char *store, const char *schema, int structured_metadata) {
    if (strcmp(store, "boltdb-shipper") == 0) {
        return "CONFIG ERROR: boltdb-shipper is deprecated, use tsdb; removed in 4.0";
    }
    if (structured_metadata) {
        if (strcmp(store, "tsdb") != 0) {
            return "CONFIG ERROR: tsdb index type is required for structured metadata";
        }
        if (schema_version_number(schema) < 13) {
            return "CONFIG ERROR: schema v13 is required for structured metadata";
        }
    }
    return NULL;
}

/* tsdb 的 index.period 只接受 24h（写别的值会被拒）；非 tsdb 无此约束。 */
static int validate_index_period(const char *store, const char *period) {
    if (strcmp(store, "tsdb") != 0) {
        return 1;
    }
    return strcmp(period, "24h") == 0;
}

#endif /* LOKI_STORAGE_H */
