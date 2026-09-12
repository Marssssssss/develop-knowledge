/*
 * redis_cluster.c — Redis Cluster 核心机制最小仿真
 *
 * 演示:
 *   1) CRC16/XMODEM 计算 → HASH_SLOT = CRC16(key) % 16384
 *   2) 16384 槽在 N 个 master 间均分
 *   3) Hash Tag "{...}" 把多键映射到同槽
 *   4) Gossip 协议: ping 包 = 通用头部 + N 个 gossip 项
 *   5) Replica 故障转移: currentEpoch + 多数票 + configEpoch
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic redis_cluster.c -o demo
 * 运行: ./demo
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <stdbool.h>

/* ---- CRC16/XMODEM 参考实现 (源自 Redis 集群规范附录 A) ---- */

static const uint16_t crc16tab[256] = {
    0x0000,0x1021,0x2042,0x3063,0x4084,0x50a5,0x60c6,0x70e7,
    0x8108,0x9129,0xa14a,0xb16b,0xc18c,0xd1ad,0xe1ce,0xf1ef,
    0x1231,0x0210,0x3273,0x2252,0x52b5,0x4294,0x72f7,0x62d6,
    0x9339,0x8318,0xb37b,0xa35a,0xd3bd,0xc39c,0xf3ff,0xe3de,
    0x2462,0x3443,0x0420,0x1401,0x64e6,0x74c7,0x44a4,0x5485,
    0xa56a,0xb54b,0x8528,0x9509,0xe5ee,0xf5cf,0xc5ac,0xd58d,
    0x3653,0x2672,0x1611,0x0630,0x76d7,0x66f6,0x5695,0x46b4,
    0xb75b,0xa77a,0x9719,0x8738,0xf7df,0xe7fe,0xd79d,0xc7bc,
    0x48c4,0x58e5,0x6886,0x78a7,0x0840,0x1861,0x2802,0x3823,
    0xc9cc,0xd9ed,0xe98e,0xf9af,0x8948,0x9969,0xa90a,0xb92b,
    0x5af5,0x4ad4,0x7ab7,0x6a96,0x1a71,0x0a50,0x3a33,0x2a12,
    0xdbfd,0xcbdc,0xfbbf,0xeb9e,0x9b79,0x8b58,0xbb3b,0xab1a,
    0x6ca6,0x7c87,0x4ce4,0x5dc5,0x2c22,0x3c03,0x0c60,0x1c41,
    0xedae,0xfd8f,0xcdec,0xddcd,0xad2a,0xbd0b,0x8d68,0x9d49,
    0x7e97,0x6eb6,0x5ed5,0x4ef4,0x3e13,0x2e32,0x1e51,0x0e70,
    0xff9f,0xefbe,0xdfdd,0xcffc,0xbf1b,0xaf3a,0x9f59,0x8f78,
    0x9188,0x81a9,0xb1ca,0xa1eb,0xd10c,0xc12d,0xf14e,0xe16f,
    0x1080,0x00a1,0x30c2,0x20e3,0x5004,0x4025,0x7046,0x6067,
    0x83b9,0x9398,0xa3fb,0xb3da,0xc33d,0xd31c,0xe37f,0xf35e,
    0x02b1,0x1290,0x22f3,0x32d2,0x4235,0x5214,0x6277,0x7256,
    0xb5ea,0xa5cb,0x95a8,0x8589,0xf56e,0xe54f,0xd52c,0xc50d,
    0x34e2,0x24c3,0x14a0,0x0481,0x7466,0x6447,0x5424,0x4405,
    0xa7db,0xb7fa,0x8799,0x97b8,0xe75f,0xf77e,0xc71d,0xd73c,
    0x26d3,0x36f2,0x0691,0x16b0,0x6657,0x7676,0x4615,0x5634,
    0xd94c,0xc96d,0xf90e,0xe92f,0x99c8,0x89e9,0xb98a,0xa9ab,
    0x5844,0x4865,0x7806,0x6827,0x18c0,0x08e1,0x3882,0x28a3,
    0xcb7d,0xdb5c,0xeb3f,0xfb1e,0x8bf9,0x9dd8,0xabbb,0xbb9a,
    0x4a75,0x5a54,0x6a37,0x7a16,0x0af1,0x1ad0,0x2ab3,0x3a92,
    0xfd2e,0xed0f,0xdd6c,0xcd4d,0xbdaa,0xad8b,0x9de8,0x8dc9,
    0x7c26,0x6c07,0x5c64,0x4c45,0x3ca2,0x2c83,0x1ce0,0x0cc1,
    0xef1f,0xff3e,0xcf5d,0xdf7c,0xaf9b,0xbfba,0x8fd9,0x9ff8,
    0x6e17,0x7e36,0x4e55,0x5e74,0x2e93,0x3eb2,0x0ed1,0x1ef0
};

static uint16_t crc16(const char *buf, int len) {
    uint16_t crc = 0;
    for (int i = 0; i < len; i++)
        crc = (crc << 8) ^ crc16tab[((crc >> 8) ^ (uint8_t)buf[i]) & 0xFF];
    return crc;
}

/* ---- 哈希槽:支持 {tag} ---- */

static int hash_slot(const char *key, int keylen) {
    int s = -1, e = -1;
    for (int i = 0; i < keylen; i++) {
        if (key[i] == '{') { s = i; break; }
    }
    if (s == -1) return crc16(key, keylen) & 16383;

    for (int i = s + 1; i < keylen; i++) {
        if (key[i] == '}') { e = i; break; }
    }
    if (e == -1 || e == s + 1) return crc16(key, keylen) & 16383;
    return crc16(key + s + 1, e - s - 1) & 16383;
}

/* ---- 16384 槽分配 ---- */

static int slot_owner[16384];

static void assign_slots(const char *masters[], int n) {
    int per = 16384 / n, rem = 16384 % n;
    int cursor = 0;
    for (int i = 0; i < n; i++) {
        int span = per + (i < rem ? 1 : 0);
        for (int s = cursor; s < cursor + span; s++)
            slot_owner[s] = i;
        cursor += span;
        printf("Master '%s' serves slots [%d, %d)\n",
               masters[i], cursor - span, cursor);
    }
}

/* ---- Gossip 包(简化) ---- */

#define MAX_GOSSIP 16

typedef struct {
    char     node_id[41];   /* 40 字节 hex + \0 */
    char     ip[16];
    uint16_t port;
    uint8_t  flags;         /* bit0: replica */
} gossip_node;

typedef struct {
    char      from[41];
    uint64_t  current_epoch;
    uint64_t  config_epoch;
    uint8_t   flags;
    gossip_node_t gossips[MAX_GOSSIP];
    uint8_t   gossip_count;
} ping_packet;

static void gossip_tick(const char *self_id, const char *known_nodes[],
                        int n_known, ping_packet *out) {
    strncpy(out->from, self_id, 40);
    out->current_epoch = 1;
    out->config_epoch = 1;
    out->flags = 0;
    out->gossip_count = 0;
    /* 取 N = max(1, n/10) 个 gossip 项 */
    int ng = n_known / 10; if (ng < 1) ng = 1;
    if (ng > MAX_GOSSIP) ng = MAX_GOSSIP;
    for (int i = 0; i < ng; i++) {
        gossip_node_t *g = &out->gossips[out->gossip_count++];
        snprintf(g->node_id, 41, "%s", known_nodes[i]);
        snprintf(g->ip, 16, "10.0.0.%d", i + 1);
        g->port = 7000 + i;
        g->flags = 0;
    }
}

/* ---- 故障转移投票(demo 简化:master 总是同意首个请求) ---- */

typedef struct {
    char      master_id[41];
    uint64_t  config_epoch;
} replica_t;

static bool try_failover(replica_t *r, const char *masters[], int n_masters) {
    r->current_epoch++;       /* 实际应通过集群总线广播;此处演示 */
    int votes = 0;
    for (int i = 0; i < n_masters; i++) votes++;
    if (votes > n_masters / 2) {
        r->config_epoch += 1;
        return true;
    }
    return false;
}

/* ---- demo 入口 ---- */

int main(void) {
    printf("=== Redis Cluster Demo ===\n\n");

    /* 1) 分配 16384 槽给 3 master */
    const char *masters[] = {"master:A", "master:B", "master:C"};
    assign_slots(masters, 3);

    /* 2) 演示几个 key 的 slot 计算 */
    const char *keys[] = {
        "user:1001",
        "{user1000}.following",
        "{user1000}.followers",
        "foo",
        "bar"
    };
    printf("\n[Key → slot mapping]\n");
    for (int i = 0; i < 5; i++) {
        int slot = hash_slot(keys[i], (int)strlen(keys[i]));
        printf("  '%-25s' → slot %d (owner: master:%s)\n",
               keys[i], slot, masters[slot_owner[slot]]);
    }

    /* 3) Gossip ping 包 */
    printf("\n[Gossip ping packet]\n");
    const char *known[] = {"node-B-id", "node-C-id", "node-D-id"};
    ping_packet p;
    gossip_tick("node-A-id", known, 3, &p);
    printf("  from       = %s\n", p.from);
    printf("  epoch      = %lu\n", (unsigned long)p.current_epoch);
    printf("  gossips    = %u items\n", p.gossip_count);
    for (int i = 0; i < p.gossip_count; i++) {
        printf("    [%d] %s @ %s:%u\n",
               i, p.gossips[i].node_id, p.gossips[i].ip, p.gossips[i].port);
    }

    /* 4) Replica 故障转移 */
    printf("\n[Replica failover]\n");
    replica_t r = { .master_id = "master:A", .config_epoch = 0 };
    if (try_failover(&r, masters, 3)) {
        printf("  replica of %s won election, new configEpoch = %lu\n",
               r.master_id, (unsigned long)r.config_epoch);
    }
    return 0;
}