// cassandra_cl.c
//
// Cassandra 风格一致性级别 — C 版核心机制:
//   1. 一致性级别 ONE / QUORUM / ALL 决策:判定 W 个 ack 是否达成
//   2. RF=3 cluster + 一致性 (CL, ack_count)→成功 / 失败 / 否
//   3. Sloppy quorum 简化(节点 down 时计数仍按目标数)
//   4. ANY 级别的"hint-only"路径(目标全 down 但写了 hint 也算成功)
//
// 用法:
//   ./cassandra_cl          — 跑一系列场景演示
//
// 详解(含 LWT / Read Repair / Anti-Entropy Repair)见 ../python/cassandra_cl.py

#include <stdio.h>
#include <stdbool.h>
#include <string.h>

#define RF 3
#define N_NODES 6

typedef enum {
    CL_ANY = 0,    // 写:任何 1 节点(可全 hint);读:undefined
    CL_ONE,        // 1 个 replica
    CL_TWO,
    CL_THREE,
    CL_QUORUM,     // ceil(RF/2) + 1 = 2 for RF=3
    CL_LOCAL_QUORUM,
    CL_EACH_QUORUM,
    CL_ALL         // 全部 RF 个
} ConsistencyLevel;

static const char *cl_name(ConsistencyLevel cl) {
    switch (cl) {
        case CL_ANY: return "ANY";
        case CL_ONE: return "ONE";
        case CL_TWO: return "TWO";
        case CL_THREE: return "THREE";
        case CL_QUORUM: return "QUORUM";
        case CL_LOCAL_QUORUM: return "LOCAL_QUORUM";
        case CL_EACH_QUORUM: return "EACH_QUORUM";
        case CL_ALL: return "ALL";
    }
    return "?";
}

// 期望 ack 数(对 RF=3 的固定 cluster;LOCAL/EACH_QUORUM 需多 DC 信息,这里降级演示)
static int required_acks(ConsistencyLevel cl) {
    switch (cl) {
        case CL_ANY:           return 1;
        case CL_ONE:           return 1;
        case CL_TWO:           return 2;
        case CL_THREE:         return 3;
        case CL_QUORUM:        return (RF / 2) + 1;   // 2 for RF=3
        case CL_LOCAL_QUORUM:  return (RF / 2) + 1;
        case CL_EACH_QUORUM:   return (RF / 2) + 1;
        case CL_ALL:           return RF;
    }
    return 0;
}

// 判定写入是否成功。
//   alive_count: preference list 上还活着的副本数
//   hint_accepted: 是否允许把 hint 视为成功(用于 CL_ANY)
static const char *write_result(ConsistencyLevel cl, int alive_count, bool hint_accepted) {
    int need = required_acks(cl);
    if (cl == CL_ANY) {
        // ANY:即使全部 down 但写了 hint 也算成功
        if (alive_count > 0) return "OK";
        if (hint_accepted)  return "OK (hint-only)";
        return "FAIL (no live replica & no hint)";
    }
    if (cl == CL_ALL) {
        if (alive_count >= RF) return "OK";
        if (alive_count < RF) return "FAIL (unavailable, can't satisfy ALL)";
    }
    // ONE..QUORUM
    if (alive_count >= need) return "OK";
    return "FAIL (insufficient acks)";
}

// strong consistency 规则:R + W > RF
static bool is_strongly_consistent(int write_acks, int read_acks, int rf) {
    return write_acks + read_acks > rf;
}

int main(void) {
    printf("=== Cassandra 一致性级别演示 (C 版核心机制,RF=%d) ===\n\n", RF);

    ConsistencyLevel levels[] = {
        CL_ANY, CL_ONE, CL_TWO, CL_THREE,
        CL_QUORUM, CL_LOCAL_QUORUM, CL_EACH_QUORUM, CL_ALL
    };
    int n_levels = sizeof(levels) / sizeof(levels[0]);

    // 场景 A:3 副本全健康
    printf("--- 场景 A: 3 副本全 alive ---\n");
    for (int i = 0; i < n_levels; i++) {
        ConsistencyLevel cl = levels[i];
        if (cl == CL_LOCAL_QUORUM || cl == CL_EACH_QUORUM) continue; // 需 DC 信息
        int need = required_acks(cl);
        printf("  CL=%-13s need_acks=%d  alive=3  →  %s\n",
               cl_name(cl), need, write_result(cl, 3, false));
    }

    // 场景 B:只有 2 副本 alive(1 个 down)
    printf("\n--- 场景 B: 2 副本 alive,1 副本 down ---\n");
    for (int i = 0; i < n_levels; i++) {
        ConsistencyLevel cl = levels[i];
        if (cl == CL_LOCAL_QUORUM || cl == CL_EACH_QUORUM) continue;
        int need = required_acks(cl);
        printf("  CL=%-13s need_acks=%d  alive=2  →  %s\n",
               cl_name(cl), need, write_result(cl, 2, true));
    }

    // 场景 C:全部 down
    printf("\n--- 场景 C: 0 副本 alive (3 down) ---\n");
    for (int i = 0; i < n_levels; i++) {
        ConsistencyLevel cl = levels[i];
        if (cl == CL_LOCAL_QUORUM || cl == CL_EACH_QUORUM) continue;
        int need = required_acks(cl);
        printf("  CL=%-13s need_acks=%d  alive=0  →  %s\n",
               cl_name(cl), need, write_result(cl, 0, true));
    }

    // 强一致性公式
    printf("\n--- 强一致性公式 R + W > RF ---\n");
    int combos[][3] = {
        // {write_CL_acks, read_CL_acks, rf}
        {2, 2, 3},   // QUORUM write + QUORUM read on RF=3
        {3, 1, 3},   // ALL write + ONE read
        {1, 1, 3},   // ONE + ONE (eventual)
        {2, 1, 3},   // QUORUM write + ONE read
    };
    const char *names[] = {"QUORUM/QUORUM", "ALL/ONE", "ONE/ONE", "QUORUM/ONE"};
    for (int i = 0; i < 4; i++) {
        bool strong = is_strongly_consistent(combos[i][0], combos[i][1], combos[i][2]);
        printf("  %-14s : W+R=%d, RF=%d  →  %s\n",
               names[i], combos[i][0] + combos[i][1], combos[i][2],
               strong ? "STRONG" : "eventual");
    }

    // 配置文件参数(实际工程调优)
    printf("\n--- 关键参数(来源 cassandra.yaml) ---\n");
    printf("  hinted_handoff_enabled       : true (默认;窗口 3h)\n");
    printf("  max_hint_window_in_ms        : 10800000 (=3h)\n");
    printf("  read_repair_chance           : 0.1 (10%% 读触发 blocking repair)\n");
    printf("  gc_grace_seconds             : 864000 (10 天,用于 tombstone purge)\n");

    printf("\n工程推荐: 多 DC 部署默认 LOCAL_QUORUM 而不是 QUORUM(避免跨 DC RTT)\n");
    return 0;
}
