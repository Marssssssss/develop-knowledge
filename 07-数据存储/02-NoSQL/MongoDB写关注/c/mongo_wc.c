// mongo_wc.c
//
// MongoDB 副本集 writeConcern 与 readConcern 决策器(C 版核心机制)
//
// 涵盖:
//   1) writeConcern 三要素: w / j / wtimeout 的解读
//   2) 默认 w 计算公式:含 arbiter 的多数派 + 数据承载多数派 取 min
//   3) readConcern 5 级:local/available/majority/linearizable/snapshot
//   4) oplog pull 流程的高度概括(capped collection + 时间戳)
//
// 用法: ./mongo_wc  — 跑 4 个决策表
//
// 复现路径(完整 oplog 拉取 + 因果一致性)见 ../python/mongo_wc.py + ../README

#include <stdio.h>
#include <stdbool.h>
#include <string.h>

// ------------------ writeConcern 决策 ------------------

typedef struct {
    int w_target;      // 数值 w: 直接 N 个副本;0 = 无;负数表示 special
    char w_label[16];  // "majority" / "custom" 等
    bool j;            // 是否要求 journal 落盘
    int wtimeout_ms;   // 0 = 等到天荒地老
    bool has_wtimeout;
} WriteConcern;

static const char *wc_describe(WriteConcern *wc) {
    static char buf[128];
    if (wc->w_target < 0) {
        // -1 = "majority"
        snprintf(buf, 128, "w=%s j=%s wtimeout=%s",
                 wc->w_label, wc->j ? "true" : "false",
                 wc->has_wtimeout ? "yes" : "no");
    } else {
        snprintf(buf, 128, "w=%d j=%s wtimeout=%s",
                 wc->w_target, wc->j ? "true" : "false",
                 wc->has_wtimeout ? "yes" : "no");
    }
    return buf;
}

// 3.0+ 隐式默认 writeConcern 计算
//   majority_of_voting = floor(voting_total/2) + 1
//   data_bearing       = non_arbiter_voting
//   若 (#arbiters > 0) AND (#non_arbiters <= majority_of_voting) → w=1,否则 w=majority
static const char *implicit_default_wc(int voting_total, int n_arbiters) {
    int n_non_arbiters = voting_total - n_arbiters;
    int majority_of_voting = (voting_total / 2) + 1;
    static char ret[32];
    if (n_arbiters > 0 && n_non_arbiters <= majority_of_voting) {
        snprintf(ret, 32, "w=1 (P-S-A trap!)");
    } else {
        snprintf(ret, 32, "w=majority");
    }
    return ret;
}

// writeConcern 隐式 ack 行为
static const char *ack_behavior(bool j_present, bool j) {
    if (j_present && j) return "ack 返回时机:数据落盘(journal)之后";
    if (j_present && !j) return "ack 返回时机:数据进内存之后 (异步 fsync)";
    return "ack 返回时机:默认等于 j=false(内存即可)";
}

// ------------------ readConcern 决策 ------------------

typedef enum {
    RC_LOCAL,
    RC_AVAILABLE,
    RC_MAJORITY,
    RC_LINEARIZABLE,
    RC_SNAPSHOT
} RC;

static const char *rc_name(RC rc) {
    switch (rc) {
        case RC_LOCAL: return "local";
        case RC_AVAILABLE: return "available";
        case RC_MAJORITY: return "majority";
        case RC_LINEARIZABLE: return "linearizable";
        case RC_SNAPSHOT: return "snapshot";
    }
    return "?";
}

static const char *rc_summary(RC rc) {
    switch (rc) {
        case RC_LOCAL: return "当前节点最新值,可能回滚(默认)";
        case RC_AVAILABLE: return "分片 cluster 上最快,可能孤立文档";
        case RC_MAJORITY: return "已多数派提交,不可回滚";
        case RC_LINEARIZABLE: return "强一致读(仅 primary),会等并发写";
        case RC_SNAPSHOT: return "事务内一致性快照;读写均需 w=majority";
    }
    return "?";
}

// ------------------ main ------------------

int main(void) {
    printf("=== MongoDB writeConcern & readConcern 决策器 ===\n\n");

    printf("--- 1. writeConcern 解析 ---\n");
    WriteConcern wcs[] = {
        { 1, "1",  false, 0, false },                // w=1, j 默认=false
        { 1, "1",  true,  0, false },                // w=1, j=true
        { 3, "3",  true,  1000, true },              // w=3, j=true, wtimeout=1s
        { -1, "majority", true, 5000, true },        // w=majority
    };
    for (int i = 0; i < 4; i++) {
        printf("  writeConcern = {");
        printf("\"w\":%s%s, \"j\":%s%s%s} → %s\n",
               wcs[i].w_target < 0 ? "\"" : "",
               wcs[i].w_target < 0 ? wcs[i].w_label : "(num)",
               wcs[i].j ? "true" : "false",
               wcs[i].has_wtimeout ? ", \"wtimeout\":" : "",
               wcs[i].has_wtimeout ? "..." : "",
               wc_describe(&wcs[i]));
    }

    printf("\n--- 2. 隐式默认 writeConcern (公式) ---\n");
    int scenarios[][2] = {
        // {voting_total, n_arbiters}
        {3, 0},   // P-S-S
        {3, 1},   // P-S-A
        {5, 1},   // P-S-S-S-A
        {1, 0},   // 单节点
    };
    const char *desc[] = {"P-S-S", "P-S-A", "P-S-S-S-A", "standalone"};
    for (int i = 0; i < 4; i++) {
        printf("  voting=%d arbiters=%d (%s) → 隐式 %s\n",
               scenarios[i][0], scenarios[i][1], desc[i],
               implicit_default_wc(scenarios[i][0], scenarios[i][1]));
    }

    printf("\n--- 3. j 选项语义 ---\n");
    bool j_present[] = {false, true, true};
    bool j_vals[]    = {false, true, false};
    for (int i = 0; i < 3; i++) {
        printf("  j present=%s j=%s → %s\n",
               j_present[i] ? "yes" : "no",
               j_vals[i] ? "true" : "false",
               ack_behavior(j_present[i], j_vals[i]));
    }

    printf("\n--- 4. readConcern 5 级 ---\n");
    RC rcs[] = {RC_LOCAL, RC_AVAILABLE, RC_MAJORITY, RC_LINEARIZABLE, RC_SNAPSHOT};
    for (int i = 0; i < 5; i++) {
        printf("  %-13s : %s\n", rc_name(rcs[i]), rc_summary(rcs[i]));
    }

    printf("\n--- 5. oplog (capped collection) ---\n");
    printf("  - capped collection in local.oplog.rs (按时间索引)\n");
    printf("  - 每条操作记录: { ts: Timestamp, op: 'i'/'u'/'d'/'c', ns: db.coll, o2: filter, o: doc }\n");
    printf("  - secondaries 通过 tailable cursor 拉取(类似 Kafka)\n");
    printf("  - 主从时间戳同步:secondary 必须达到 commit point 才被允许晋升 primary\n");
    printf("  - 默认 5%% 磁盘空间;超长事务会让 oplog 覆盖老日志 → 触发 resync\n");

    printf("\n--- 6. 强一致 + 读己之写 ---\n");
    printf("  推荐组合:writeConcern={w:'majority', j:true} + readConcern='majority'\n");
    printf("  + 在因果一致会话(causal consistency session)中:驱动设置 afterClusterTime 自动\n");

    return 0;
}
