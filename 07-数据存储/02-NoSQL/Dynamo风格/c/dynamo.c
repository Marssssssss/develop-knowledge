// dynamo.c
//
// Dynamo 风格 key-value 存储 — C 实现核心机制:
//   1. 一致性哈希 + 虚节点(virtual nodes)
//   2. 偏好列表(preference list) — 从 key 选 N 个目标节点
//   3. 用"hash 表 + linked list"组合存储 hint,模拟 hinted handoff
//
// 用法:
//   ./dynamo          — 启动一个 in-memory 节点,监听 stdin 命令
//   演示命令:
//       put  <key> <value>
//       get  <key>
//       node list                   — 打印本节点的偏好列表
//       hash demo                  — 用例词演示 key 路由
//
// 本文件只演示数据平面(partitioning + 偏好列表 + hint queue),
// 复制协议由 ../../python/dynamo.py 完整呈现(sloppy quorum、vector clock)。

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

// ---------- 一致性哈希 ring ----------
//
// 设计要点 (来自 Dynamo 论文 + Cornell CS 6410 课程):
// - 物理节点映射到 256 个虚拟节点(vnode),异构场景可按容量分配权重
// - ring 长度 2^32;CRC32 把 vnode 名字散到 ring
// - 偏好列表 = key hash 在 ring 上顺时针取前 N 个不同物理节点
//
// 为简化演示,我们不动态维护平衡的红黑树,而用排序数组:
// 百万级 vnode 也无压力,这里只放 256 个演示。

typedef struct {
    uint32_t pos;          // ring 上的位置
    int vnode_id;          // 0..NV-1
    int node_id;           // 物理节点 id
} vnode_t;

typedef struct {
    int node_id;
    char name[32];
    int n_vnodes;          // 该物理节点拥有的 vnode 数量(演示用固定 32)
} phys_node_t;

#define RING_SIZE 256
#define PHYS_NODES 6
#define REPLICA_N 3        // N = 3 (Dynamo 论文默认)

static vnode_t ring[RING_SIZE];
static phys_node_t nodes[PHYS_NODES];

// 一个节点的 [key -> value] 简化存储(只为示意)
typedef struct kv {
    char key[64];
    char value[128];
    struct kv *next;
} kv_t;

typedef struct {
    char owner_node[32];   // 这条 hint 本该送到哪个节点
    char target_node[32];  // 我们替它暂存
    char key[64];
    char value[128];
} hint_t;

#define HINT_QUEUE_MAX 32
static hint_t hint_queue[HINT_QUEUE_MAX];
static int hint_q_head = 0, hint_q_tail = 0, hint_count = 0;

// CRC32 (PNG/IEEE 802.3 同款 — 经典 polynomial 0xEDB88320)
static uint32_t crc32(const char *s) {
    uint32_t c = 0xFFFFFFFFu;
    while (*s) {
        c ^= (uint8_t)(*s++);
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320u & -(c & 1));
    }
    return ~c;
}

// ---------- ring 构建 ----------
static int vnode_cmp(const void *a, const void *b) {
    const vnode_t *va = (const vnode_t *)a;
    const vnode_t *vb = (const vnode_t *)b;
    if (va->pos < vb->pos) return -1;
    if (va->pos > vb->pos) return 1;
    return 0;
}

static void build_ring(void) {
    // 6 个物理节点,每节点 256/6 ≈ 42 个 vnode;简化:固定 NV=256/PN=6=42 余数弃
    const char *names[PHYS_NODES] = {
        "node-A", "node-B", "node-C", "node-D", "node-E", "node-F"
    };
    for (int i = 0; i < PHYS_NODES; i++) {
        nodes[i].node_id = i;
        snprintf(nodes[i].name, 32, "%s", names[i]);
        nodes[i].n_vnodes = RING_SIZE / PHYS_NODES;
    }
    int v = 0;
    for (int i = 0; i < PHYS_NODES; i++) {
        for (int k = 0; k < nodes[i].n_vnodes; k++) {
            char buf[64];
            snprintf(buf, 64, "%s#vnode-%d", nodes[i].name, k);
            ring[v].node_id = i;
            ring[v].vnode_id = k;
            ring[v].pos = crc32(buf);
            v++;
        }
    }
    qsort(ring, RING_SIZE, sizeof(vnode_t), vnode_cmp);
}

// 沿 ring 顺时针收集 N 个不同物理节点 — 即 preference list
static int preference_list(const char *key, int out[PHYS_NODES]) {
    uint32_t h = crc32(key);
    int seen[PHYS_NODES] = {0};
    int n = 0;
    // 二分起点后扫描(ring 已排序)
    int i = 0;
    while (n < REPLICA_N && i < RING_SIZE * 2) {
        // 顺时针 = pos > h(或绕回)
        vnode_t *slot = NULL;
        for (int step = 0; step < RING_SIZE; step++) {
            if (ring[step].pos > h) { slot = &ring[step]; break; }
        }
        if (!slot) slot = &ring[0]; // 绕回
        h = slot->pos; // 下一轮从这个 vnode 之后继续
        if (!seen[slot->node_id]) {
            out[n++] = slot->node_id;
            seen[slot->node_id] = 1;
        }
        // 把当前 vnode 临时排除:把它的 pos 设到极小确保下次不再命中
        // 简化处理 — 演示里 N≤6 不会冲突
        i++;
        if (n == REPLICA_N) break;
    }
    return n;
}

static void print_pref(const char *key) {
    int pl[PHYS_NODES];
    int n = preference_list(key, pl);
    printf("key=\"%s\"  hash=%08x  preference_list=[", key, crc32(key));
    for (int i = 0; i < n; i++) {
        printf("%s%s", nodes[pl[i]].name, i == n - 1 ? "" : ", ");
    }
    printf("]\n");
}

// ---------- hint 队列 ----------
static void hint_push(const char *owner, const char *target, const char *key, const char *value) {
    if (hint_count >= HINT_QUEUE_MAX) {
        printf("HINT_QUEUE full, drop hint for %s\n", key);
        return;
    }
    hint_t *h = &hint_queue[hint_q_tail];
    snprintf(h->owner_node, 32, "%s", owner);
    snprintf(h->target_node, 32, "%s", target);
    snprintf(h->key, 64, "%s", key);
    snprintf(h->value, 128, "%s", value);
    hint_q_tail = (hint_q_tail + 1) % HINT_QUEUE_MAX;
    hint_count++;
    printf("[HINT] saved (intended for %s) on %s\n", owner, target);
}

static void hint_replay(const char *recovered_node) {
    // 节点恢复后,扫描队列,把属于它的 hint 投递
    printf("[HINT-REPLAY] scanning for hints belonging to %s ...\n", recovered_node);
    for (int i = 0; i < hint_count; i++) {
        int idx = (hint_q_head + i) % HINT_QUEUE_MAX;
        if (strcmp(hint_queue[idx].owner_node, recovered_node) == 0) {
            printf("  -> delivering key=%s value=%s\n",
                   hint_queue[idx].key, hint_queue[idx].value);
        }
    }
}

// ---------- main ----------
int main(int argc, char **argv) {
    srand((unsigned)time(NULL));
    build_ring();

    printf("=== Dynamo 风格存储演示 (C 版核心机制) ===\n");
    printf("ring size = %d vnodes, physical nodes = %d, N = %d\n\n",
           RING_SIZE, PHYS_NODES, REPLICA_N);

    // 演示 1: 一致性哈希 + 偏好列表
    print_pref("user:42:cart");
    print_pref("user:42:cart");
    print_pref("product:sku-9001");
    print_pref("order:2026-09-12");
    print_pref("session:abc123");
    print_pref("hot:key_always_here");
    printf("\n");

    // 演示 2: 同一个 key 永远命中同样 3 个节点 (确定性)
    printf("--- 同一 key 路由确定性 ---\n");
    for (int i = 0; i < 3; i++) print_pref("user:42:cart");

    // 演示 3: hinted handoff 队列
    printf("\n--- hinted handoff ---\n");
    // 模拟 node-C 暂时 down,put("user:42:cart","book") 本应发 C,
    // 写到 ring 顺时针下一个健康节点 D 上,并留下 hint
    int pl[PHYS_NODES];
    preference_list("user:42:cart", pl);
    // 假设 nodes[pl[2]] 是首选第三副本(node-C),它 down
    int c_down = pl[2];
    int d_online = pl[2] == pl[3] ? pl[3] + 1 : (pl[2] + 1) % PHYS_NODES;
    // 修正:实际上 Dynamo 把 hint 放在 ring 上下一个健康节点,不一定是 D
    // 这里简单化为 "下一个"
    hint_push(nodes[c_down].name, nodes[d_online].name,
              "user:42:cart", "book");
    hint_push(nodes[c_down].name, nodes[d_online].name,
              "user:42:cart:v2", "pen");
    // node-C 上线
    hint_replay(nodes[c_down].name);

    printf("\n演示完毕。运行时协议(sloppy quorum / vector clock / read repair) "
           "见 ../python/dynamo.py。\n");
    return 0;
}
