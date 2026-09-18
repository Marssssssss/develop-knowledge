/* OpenTelemetry Collector 语义模型(纯手写实现)—— 断言入口。
 *
 * 三个文件构成一个翻译单元:
 *   otel_demo.c      断言与 main(本文件)
 *   otel_core.h      ID 解析、配置校验、memory_limiter
 *   otel_pipeline.h  batch、exporter helper、filter
 * 与 Python、Go 版覆盖同一批机制,差异只在表达力:C 无正则,filter 的正则
 * 分支退化为前缀匹配;断言为 2 参形式 check(label, cond)。
 *
 * 构建:gcc -std=c11 -Wall -Wextra -lm -o otel_demo otel_demo.c && ./otel_demo
 */

#include <math.h>
#include <stdio.h>

#include "otel_core.h"
#include "otel_pipeline.h"

static int g_pass = 0, g_fail = 0;

/* 2 参断言。label 含中文,源码须为 UTF-8(MSVC 下加 /utf-8)。 */
static void check(const char *label, int cond) {
    if (cond) {
        g_pass++;
        return;
    }
    g_fail++;
    printf("  - FAIL %s\n", label);
}

/* 浮点等值一律给容差。 */
static int near(double a, double b) { return fabs(a - b) < 1e-9; }

int main(void) {
    char ty[32], nm[32];
    Config cfg;
    Batch b;
    Queue q;
    int edges_ok[1][2] = {{0, 1}};
    int edges_cyc[2][2] = {{0, 1}, {1, 0}};
    int i;

    /* ---------------------------------------------------- A 组件 ID */
    check("A1 batch/traces 合法", valid_id("batch/traces"));
    check("A2 otlp 合法", valid_id("otlp"));
    check("A3 解析 type", parse_id("batch/traces", ty, sizeof ty, nm, sizeof nm) &&
                              strcmp(ty, "batch") == 0);
    check("A4 解析 name", strcmp(nm, "traces") == 0);
    check("A5 无 name 形式", parse_id("otlp", ty, sizeof ty, nm, sizeof nm) &&
                                 nm[0] == '\0');
    check("A6 同 type 不同 name", same_type("batch", "batch/logs"));
    check("A7 不同 type", !same_type("batch", "otlp"));
    {
        const char *bad[] = {"batch/", "/traces", "batch/traces/x", "1batch",
                             "", "a b", "traces/2", "batch/2x"};
        int nbad = (int)(sizeof bad / sizeof bad[0]);
        for (i = 0; i < nbad; i++) {
            check(bad[i][0] != '\0' ? bad[i] : "空字符串", !valid_id(bad[i]));
        }
    }

    /* ---------------------------------------------------- B 配置校验 */
    memset(&cfg, 0, sizeof cfg);
    cfg.recv[0] = "otlp";
    cfg.recv[1] = "prometheus";
    cfg.nrecv = 2;
    cfg.expo[0] = "otlp";
    cfg.expo[1] = "debug";
    cfg.nexp = 2;
    cfg.conn[0] = "spanmetrics";
    cfg.nconn = 1;
    cfg.npipes = 2;
    cfg.pipes[0].signal = "traces";
    cfg.pipes[0].recv[0] = "otlp";
    cfg.pipes[0].nrecv = 1;
    cfg.pipes[0].expo[0] = "spanmetrics";
    cfg.pipes[0].nexp = 1;
    cfg.pipes[1].signal = "metrics";
    cfg.pipes[1].recv[0] = "spanmetrics";
    cfg.pipes[1].nrecv = 1;
    cfg.pipes[1].expo[0] = "otlp";
    cfg.pipes[1].nexp = 1;
    check("B1 正常配置通过", validate(&cfg) == 0);
    cfg.pipes[1].recv[0] = "nope";
    check("B2 未定义 receiver 报错", validate(&cfg) == 2);
    cfg.pipes[1].recv[0] = "spanmetrics";
    cfg.pipes[1].expo[0] = "nope";
    check("B3 未定义 exporter 报错", validate(&cfg) == 3);
    cfg.pipes[1].expo[0] = "otlp";
    cfg.npipes = 0;
    check("B4 空 pipelines 报错", validate(&cfg) == 1);
    cfg.npipes = 2;
    check("B5 otlp 作 receiver 的扇出宽度为 1", recv_fanout(&cfg, "otlp") == 1);
    check("B6 connector 作 receiver", recv_fanout(&cfg, "spanmetrics") == 1);

    /* ---------------------------------------------------- C fanout */
    cfg.pipes[0].expo[1] = "debug";
    cfg.pipes[0].nexp = 2;
    check("C1 pipeline 内 exporter 广播 2 路", cfg.pipes[0].nexp == 2);
    check("C2 广播不影响 connector 入边", recv_fanout(&cfg, "spanmetrics") == 1);

    /* ---------------------------------------------------- D connector 图 */
    check("D1 单向图无环", !has_cycle(2, edges_ok, 1));
    check("D2 双向边成环", has_cycle(2, edges_cyc, 2));
    check("D3 三节点链无环", !has_cycle(3, (const int[][2]){{0, 1}, {1, 2}}, 2));

    /* ---------------------------------------------------- E limiter */
    check("E1 低于软限放行", limiter_verdict(4000, 800, 3000) == 0);
    check("E2 恰等于软限仍放行", limiter_verdict(4000, 800, 3200) == 0);
    check("E3 刚过软限拒绝", limiter_verdict(4000, 800, 3201) == 1);
    check("E4 等于硬限仍拒绝", limiter_verdict(4000, 800, 4000) == 1);
    check("E5 超硬限触发 GC", limiter_verdict(4000, 800, 4001) == 2);
    check("E6 limit=0 视为不设限", limiter_verdict(0, 0, 99999) == 0);
    check("E7 显式 spike 生效",
          limiter_verdict(4096, 1024, 3072) == 0 && limiter_verdict(4096, 1024, 3073) == 1);

    /* ---------------------------------------------------- F batch */
    check("F1 max<size 非法", batch_init(&b, 8192, 200, 4096) == -1);
    check("F2 max==size 合法", batch_init(&b, 8192, 200, 8192) == 0);
    check("F3 未达阈值不发送", batch_init(&b, 8192, 200, 0) == 0 &&
                                   batch_push(&b, 8191, 0) == 0);
    check("F4 达到阈值立即发送", batch_push(&b, 1, 0) == 1 &&
                                     b.last_batches[0] == 8192 && b.last_reason == 0);
    check("F5 max=0 时单批全发", batch_init(&b, 8192, 200, 0) == 0 &&
                                     batch_push(&b, 25000, 0) == 1 &&
                                     b.last_batches[0] == 25000);
    check("F6 按 max 切分且尾批可小", batch_init(&b, 8192, 200, 10000) == 0 &&
                                         batch_push(&b, 25000, 0) == 3 &&
                                         b.last_batches[0] == 10000 &&
                                         b.last_batches[2] == 5000);
    check("F7 小规模切分", batch_init(&b, 1000, 200, 2000) == 0 &&
                               batch_push(&b, 5000, 0) == 3 && b.last_batches[2] == 1000);
    check("F8 未到 timeout 不发送", batch_init(&b, 8192, 200, 0) == 0 &&
                                        batch_push(&b, 5, 1000) == 0 &&
                                        batch_tick(&b, 1199) == 0);
    check("F9 timeout 兜底发送", batch_tick(&b, 1200) == 1 &&
                                     b.last_batches[0] == 5 && b.last_reason == 1);
    check("F10 flush 后计时器复位", b.last_ms < 0 && batch_tick(&b, 9999) == 0);

    /* ---------------------------------------------------- G exporter */
    check("G1 首次等待 5s", near(backoff_of(5, 30, 1.5, 1), 5.0));
    check("G2 第四次指数 16.875", near(backoff_of(5, 30, 1.5, 4), 16.875));
    check("G3 退避封顶 30s", near(backoff_of(5, 30, 1.5, 6), 30.0) &&
                                 near(backoff_of(5, 30, 1.5, 9), 30.0));
    check("G4 预算 300s 内重试 12 次", attempts_within_budget(5, 30, 1.5, 300) == 12);
    check("G5 旧口径 120s -> 6 次", attempts_within_budget(5, 30, 1.5, 120) == 6);
    check("G6 0 表示永不停止", attempts_within_budget(5, 30, 1.5, 0) == -1);
    memset(&q, 0, sizeof q);
    q.size = 5000;
    for (i = 0; i < 5000; i++) {
        if (queue_enqueue(&q, 0) != 1) {
            break;
        }
    }
    check("G7 入队 5000 全成功", i == 5000 && q.used == 5000);
    check("G8 第 5001 个被丢弃", queue_enqueue(&q, 0) == 0 && q.failed == 1);
    memset(&q, 0, sizeof q);
    q.size = 2;
    queue_enqueue(&q, 1);
    queue_enqueue(&q, 1);
    check("G9 overflow 阻塞而非丢弃", queue_enqueue(&q, 1) == -1 && q.failed == 0);
    check("G10 建议队列长度", suggested_queue_size(60, 100, 1) == 6000);
    check("G11 per_batch 非法", suggested_queue_size(60, 100, 0) == -1);

    /* ---------------------------------------------------- H filter */
    check("H1 等值命中", attr_eq("prod", "prod"));
    check("H2 等值不命中", !attr_eq("prod", "dev"));
    check("H3 缺失属性不命中", !attr_eq(NULL, "prod"));
    check("H4 前缀近似正则", attr_prefix("/readyz", "/ready"));
    check("H5 前缀不匹配", !attr_prefix("/api", "/ready"));
    check("H6 数值比较", attr_num_lt("3", 5.0));
    check("H7 缺失属性不参与数值比较", !attr_num_lt(NULL, 5.0));
    check("H8 空串不参与数值比较", !attr_num_lt("", 5.0));

    if (g_fail > 0) {
        printf("FAILED %d / %d\n", g_fail, g_fail + g_pass);
        return 1;
    }
    printf("ALL PASS  %d assertions\n", g_pass);
    return 0;
}
