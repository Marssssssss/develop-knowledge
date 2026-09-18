/* OTel Collector 语义模型 —— batch processor、exporter helper、filter 条件。
 *
 * 同样以「实现头」方式被 otel_demo.c 文本级包含(见 otel_core.h 顶部说明)。
 */

#ifndef OTEL_PIPELINE_H
#define OTEL_PIPELINE_H

#include <math.h>
#include <stdlib.h>
#include <string.h>

/* ---------------------------------------------------------------- batch */

#define MAXBATCH 8

typedef struct {
    int send_size, timeout_ms, max_size;
    int buf, last_ms;
    int last_batches[MAXBATCH];
    int n_batches, last_reason; /* reason: 0=size 1=timeout */
} Batch;

/* 返回 -1 = 配置非法(max_size 非 0 时必须 >= send_size)。 */
static int batch_init(Batch *b, int size, int timeout_ms, int max_size) {
    if (max_size != 0 && max_size < size) {
        return -1;
    }
    memset(b, 0, sizeof *b);
    b->send_size = size;
    b->timeout_ms = timeout_ms;
    b->max_size = max_size;
    b->last_ms = -1;
    return 0;
}

/* 排空缓冲并按 max_size 切分;尾批可以小于 send_size。 */
static int batch_flush(Batch *b, int reason) {
    int k = 0;
    while (b->buf > 0) {
        int n = b->buf;
        if (b->max_size > 0 && b->max_size < n) {
            n = b->max_size;
        }
        if (k < MAXBATCH) {
            b->last_batches[k] = n;
        }
        k++;
        b->buf -= n;
    }
    b->last_ms = -1;
    b->n_batches = k;
    b->last_reason = reason;
    return k;
}

/* 一次投递 n 条后检查阈值(真实 Collector 的输入单元是 request)。 */
static int batch_push(Batch *b, int n, int now) {
    b->buf += n;
    if (b->last_ms < 0 && n > 0) {
        b->last_ms = now;
    }
    if (b->buf >= b->send_size) {
        return batch_flush(b, 0);
    }
    return 0;
}

static int batch_tick(Batch *b, int now) {
    if (b->buf > 0 && b->last_ms >= 0 && now - b->last_ms >= b->timeout_ms) {
        return batch_flush(b, 1);
    }
    return 0;
}

/* ---------------------------------------------------------------- exporter */

/* 官方默认:initial=5s max=30s multiplier=1.5 max_elapsed_time=300s。
 * max_elapsed_time<=0 表示「永不停止」,返回 -1。 */
static double backoff_of(double initial, double max_interval, double mult, int attempt) {
    double v = initial * pow(mult, (double)(attempt - 1));
    return v > max_interval ? max_interval : v;
}

static int attempts_within_budget(double initial, double max_interval, double mult,
                                  double max_elapsed) {
    double total = 0.0;
    int k = 0;
    if (max_elapsed <= 0) {
        return -1;
    }
    for (;;) {
        double next = total + backoff_of(initial, max_interval, mult, k + 1);
        if (next > max_elapsed) {
            return k;
        }
        total = next;
        k++;
    }
}

typedef struct {
    int size, used, failed;
} Queue;

/* 1=入队 0=丢弃(队列满且不阻塞) -1=调用方阻塞等待 */
static int queue_enqueue(Queue *q, int block_on_overflow) {
    if (q->used >= q->size) {
        if (block_on_overflow) {
            return -1;
        }
        q->failed++;
        return 0;
    }
    q->used++;
    return 1;
}

/* 官方估算公式:缓冲秒数 × RPS ÷ 每批请求数;per_batch<=0 返回 -1。 */
static int suggested_queue_size(double buffer_s, double rps, double per_batch) {
    if (per_batch <= 0) {
        return -1;
    }
    return (int)(buffer_s * rps / per_batch);
}

/* ---------------------------------------------------------------- filter */

static int attr_eq(const char *a, const char *b) {
    return a != NULL && b != NULL && strcmp(a, b) == 0;
}

/* C 版无正则,用前缀匹配近似 =~ 的整串匹配语义(见 README 差异表)。 */
static int attr_prefix(const char *a, const char *p) {
    return a != NULL && p != NULL && strncmp(a, p, strlen(p)) == 0;
}

/* 缺失属性(空指针)一律不命中。 */
static int attr_num_lt(const char *a, double n) {
    return a != NULL && *a != '\0' && atof(a) < n;
}

#endif /* OTEL_PIPELINE_H */
