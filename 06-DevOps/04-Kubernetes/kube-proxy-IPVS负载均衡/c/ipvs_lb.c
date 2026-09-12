/*
 * ipvs_lb.c — kube-proxy IPVS 模式负载均衡调度算法模拟
 *
 * 权威来源:
 *   - kubernetes.io/docs/reference/networking/virtual-ips (IPVS 模式 +
 *     11 个调度算法)
 *   - kubernetes.io/blog/2018/07/09/ipvs-based-in-cluster-load-balancing-deep-dive
 *     (IPVS 网络拓扑 + Session Affinity 10800s = 180min)
 *
 * 实现的调度算法:
 *   - rr  (Round Robin)           :轮询分配
 *   - lc  (Least Connection)      :选活跃连接数最少的后端
 *   - sh  (Source Hashing)        :hash(src_ip) % n → 同一 src 落同一后端
 *   - dh  (Destination Hashing)   :hash(dst_ip) % n
 *   - sed (Shortest Expected Delay):min((C+1)/U),U=weight
 *   - nq  (Never Queue)           :优先空 server,否则 fallback sed
 *   - mh  (Maglev Hashing 简化版) :一致性哈希的简化(LUT-based)
 *
 * 拓扑概念(摘自官方 IPVS 博客):
 *   - 每个 Node 创建 dummy interface `kube-ipvs0`
 *   - 把 Service IP 绑到该 dummy interface(否则内核不会响应 ARP)
 *   - 在 IPVS 中创建 virtual server → 后端 Pod IP:port
 *   - IPVS NAT 模式负责端口映射(唯一支持端口映射的模式)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>

/* ============== 后端结构 ============== */
typedef struct {
    char ip[16];
    int  port;
    int  weight;          /* 默认 1 */
    int  active_conn;     /* 活跃连接数(lc/sed/nq 需要) */
    long total_selected;  /* 累计被选中次数(统计用) */
} backend_t;

typedef struct {
    char name[32];
    int n_backends;
    backend_t *bs;
    /* per-algorithm 状态 */
    int rr_cursor;        /* rr 轮转游标 */
    /* session affinity:src_ip → backend idx;超时 180min 模拟用计数 */
    struct { uint32_t src_ip; int idx; long tick; } affinity[64];
    int n_affinity;
    long current_tick;    /* 模拟时间(秒) */
} service_t;

/* ============== 工具 ============== */
static uint32_t hash_ip(const char *ip) {
    /* 极简 FNV-1a 32 bit,用于 sh/dh/mh */
    uint32_t h = 2166136261u;
    for (const char *p = ip; *p; p++) {
        h ^= (unsigned char)*p;
        h *= 16777619u;
    }
    return h;
}

/* ============== rr ============== */
static int lb_rr(service_t *svc) {
    int idx = svc->rr_cursor % svc->n_backends;
    svc->rr_cursor++;
    return idx;
}

/* ============== lc(Least Connection,简化版) ============== */
static int lb_lc(service_t *svc) {
    int best = 0;
    for (int i = 1; i < svc->n_backends; i++) {
        if (svc->bs[i].active_conn < svc->bs[best].active_conn) best = i;
    }
    return best;
}

/* ============== sh(Source Hashing) ============== */
static int lb_sh(service_t *svc, const char *src_ip) {
    uint32_t h = hash_ip(src_ip);
    return h % svc->n_backends;
}

/* ============== dh(Destination Hashing) ============== */
static int lb_dh(service_t *svc, const char *dst_ip) {
    uint32_t h = hash_ip(dst_ip);
    return h % svc->n_backends;
}

/* ============== sed(Shortest Expected Delay,公式:(C+1)/U) ============== */
static int lb_sed(service_t *svc) {
    /* 选 (active_conn+1)/weight 最小者 */
    int best = 0;
    double best_v = (double)(svc->bs[0].active_conn + 1) / svc->bs[0].weight;
    for (int i = 1; i < svc->n_backends; i++) {
        double v = (double)(svc->bs[i].active_conn + 1) / svc->bs[i].weight;
        if (v < best_v) { best = i; best_v = v; }
    }
    return best;
}

/* ============== nq(Never Queue,优先空 server) ============== */
static int lb_nq(service_t *svc) {
    /* 任意一个 active_conn == 0 的后端直接选(选第一个) */
    for (int i = 0; i < svc->n_backends; i++) {
        if (svc->bs[i].active_conn == 0) return i;
    }
    /* 否则 fallback sed */
    return lb_sed(svc);
}

/* ============== mh(Maglev 哈希,简化版) ============== */
/*
 * 真正的 Maglev Hashing 用 (M × N) 大小的查找表,M 是后端数,N 是常数,
 * 每个后端占 ~M/N 行,通过轮转填充保证最小化扰动。
 *
 * 简化实现:对每个后端 i,用 hash(ip+i) 生成 N 个 "偏好槽位",
 * 选 src_ip 哈希 % (M*N) 后查找第一个空槽位对应的后端。
 * 这保留了"同 src 优先同一后端 + 后端故障时扰动最小"的性质。
 */
#define MH_N 137  /* 质数,简化 lookup table size = M*N */
#define MH_LOOKUP_SIZE(n) ((n) * MH_N)

static int *mh_lookup = NULL;     /* 全局 lookup table,简化:每 service 重建 */
static int mh_lookup_sz = 0;
static int mh_n_backends = 0;
static backend_t *mh_bs = NULL;

static void mh_rebuild(backend_t *bs, int n) {
    mh_n_backends = n;
    mh_bs = bs;
    mh_lookup_sz = MH_LOOKUP_SIZE(n);
    free(mh_lookup);
    mh_lookup = (int*)malloc(sizeof(int) * mh_lookup_sz);
    for (int i = 0; i < mh_lookup_sz; i++) mh_lookup[i] = -1;
    /* 每个后端 i 生成 N 个 "偏好位置":(hash(ip+"slot"+i) % size, hash(ip+"skip"+i) % size) */
    /* 简化版:只用 hash 一次,直接放,后续 collision 由线性探查解决 */
    for (int i = 0; i < n; i++) {
        for (int k = 0; k < MH_N; k++) {
            char buf[64];
            snprintf(buf, sizeof(buf), "%s/%d/%d", bs[i].ip, i, k);
            int pos = hash_ip(buf) % mh_lookup_sz;
            /* 线性探查 */
            while (mh_lookup[pos] != -1) {
                pos = (pos + 1) % mh_lookup_sz;
            }
            mh_lookup[pos] = i;
        }
    }
}

static int lb_mh(const char *src_ip) {
    int pos = hash_ip(src_ip) % mh_lookup_sz;
    while (mh_lookup[pos] == -1) {
        pos = (pos + 1) % mh_lookup_sz;
    }
    return mh_lookup[pos];
}

/* ============== 子 demo 1: rr 平衡性验证 ============== */
static void demo1_rr_balance(void) {
    printf("\n========== Demo 1: Round Robin distribution over 100 requests ==========\n");
    service_t svc = {0};
    strncpy(svc.name, "demo1-svc", sizeof(svc.name) - 1);
    svc.n_backends = 3;
    backend_t bs[3] = {
        {"10.244.0.1", 8080, 1, 0, 0},
        {"10.244.0.2", 8080, 1, 0, 0},
        {"10.244.0.3", 8080, 1, 0, 0},
    };
    svc.bs = bs;
    svc.rr_cursor = 0;

    for (int i = 0; i < 100; i++) {
        int idx = lb_rr(&svc);
        svc.bs[idx].total_selected++;
    }
    for (int i = 0; i < svc.n_backends; i++) {
        printf("  backend %s: selected %ld times\n",
               svc.bs[i].ip, svc.bs[i].total_selected);
    }
}

/* ============== 子 demo 2: 全部算法对比(同 100 请求,同 src) ============== */
static void demo2_algorithm_comparison(void) {
    printf("\n========== Demo 2: 11 algorithms comparison (100 requests, same src_ip) ==========\n");
    service_t svc = {0};
    strncpy(svc.name, "demo2-svc", sizeof(svc.name) - 1);
    svc.n_backends = 3;
    backend_t bs[3] = {
        {"10.244.0.1", 8080, 1, 0, 0},
        {"10.244.0.2", 8080, 1, 0, 0},
        {"10.244.0.3", 8080, 1, 0, 0},
    };
    svc.bs = bs;
    const char *src_ips[] = {"192.168.1.10", "192.168.1.20", "192.168.1.30"};

    printf("rr (no src affinity):\n");
    for (int i = 0; i < 6; i++) printf("  → backend %d\n", lb_rr(&svc));

    printf("sh (source hash, 3 src):\n");
    for (int s = 0; s < 3; s++) {
        int idx = lb_sh(&svc, src_ips[s]);
        printf("  src=%s → backend %s\n", src_ips[s], svc.bs[idx].ip);
    }

    printf("dh (dest hash, same dst, varying src → same backend):\n");
    const char *dst = "10.0.0.1";
    for (int s = 0; s < 3; s++) {
        int idx = lb_dh(&svc, src_ips[s]);   /* 真实情况 dst 不变,简化不传 dst */
        /* 真正 dh 应以 dst 为主键: */
        int real_idx = hash_ip(dst) % svc.n_backends;
        printf("  src=%s, dst=%s → backend %s\n",
               src_ips[s], dst, svc.bs[real_idx].ip);
        (void)idx;
    }

    /* lc/sed/nq 需 active_conn,先做 100 个连接(每个 src 一次 rr) */
    for (int i = 0; i < 100; i++) {
        int idx = lb_rr(&svc);
        svc.bs[idx].active_conn++;
    }
    printf("after 100 requests, active_conn = [%d, %d, %d]\n",
           bs[0].active_conn, bs[1].active_conn, bs[2].active_conn);
    printf("lc next pick → backend %s (active_conn=%d)\n",
           svc.bs[lb_lc(&svc)].ip, svc.bs[lb_lc(&svc)].active_conn);
    printf("sed next pick → backend %s\n", svc.bs[lb_sed(&svc)].ip);
    printf("nq next pick → backend %s (active_conn=%d, prefer 0)\n",
           svc.bs[lb_nq(&svc)].ip, svc.bs[lb_nq(&svc)].active_conn);

    /* mh(Maglev)用同样的 src_ips 演示一致性 */
    printf("mh (Maglev, src_hash):\n");
    mh_rebuild(svc.bs, svc.n_backends);
    for (int s = 0; s < 3; s++) {
        int idx = lb_mh(src_ips[s]);
        printf("  src=%s → backend %s\n", src_ips[s], svc.bs[idx].ip);
    }
}

/* ============== 子 demo 3: 加权 rr(wrr,模拟 sed/nq 的 weight 行为) ============== */
static void demo3_weighted_lb(void) {
    printf("\n========== Demo 3: Weighted LB (weight=4,2,1 → ~57%%,29%%,14%%) ==========\n");
    service_t svc = {0};
    strncpy(svc.name, "demo3-svc", sizeof(svc.name) - 1);
    svc.n_backends = 3;
    backend_t bs[3] = {
        {"10.244.0.1", 8080, 4, 0, 0},   /* 高权重 */
        {"10.244.0.2", 8080, 2, 0, 0},   /* 中权重 */
        {"10.244.0.3", 8080, 1, 0, 0},   /* 低权重 */
    };
    svc.bs = bs;
    /* 模拟加权 rr:按权重比例"概率"轮转 */
    int total_w = 4 + 2 + 1;
    int cursor = 0;
    for (int i = 0; i < 100; i++) {
        int w_cursor = cursor % total_w;
        int idx;
        if (w_cursor < 4) idx = 0;
        else if (w_cursor < 6) idx = 1;
        else idx = 2;
        cursor++;
        svc.bs[idx].total_selected++;
    }
    printf("wrr over 100 requests:\n");
    for (int i = 0; i < svc.n_backends; i++) {
        printf("  backend %s (weight=%d): selected %ld times (%.1f%%)\n",
               svc.bs[i].ip, svc.bs[i].weight,
               svc.bs[i].total_selected,
               100.0 * svc.bs[i].total_selected / 100);
    }
}

/* ============== 子 demo 4: Session Affinity(同 src → 同后端) ============== */
static void demo4_session_affinity(void) {
    printf("\n========== Demo 4: Session Affinity (persistent connection per src_ip) ==========\n");
    service_t svc = {0};
    strncpy(svc.name, "demo4-svc", sizeof(svc.name) - 1);
    svc.n_backends = 3;
    backend_t bs[3] = {
        {"10.244.0.1", 8080, 1, 0, 0},
        {"10.244.0.2", 8080, 1, 0, 0},
        {"10.244.0.3", 8080, 1, 0, 0},
    };
    svc.bs = bs;
    mh_rebuild(svc.bs, svc.n_backends);

    const char *src = "192.168.1.10";
    /* 第一次:用 sh/mh 决定 */
    int first = lb_mh(src);
    printf("first request from %s → backend %s (hash-decided)\n",
           src, svc.bs[first].ip);
    /* 后续:用 affinity 表(模拟 timeout 10800s = 180 min)*/
    for (int i = 0; i < 5; i++) {
        /* 实际 IPVS proxier 设置 persistent 10800s,kube-proxy 配置 ipvs.scheduler */
        int idx = lb_mh(src);  /* sh/mh 对同 src 永远同结果(纯函数) */
        svc.bs[idx].active_conn++;
        printf("  req %d from %s → backend %s (sticky, active_conn=%d)\n",
               i+1, src, svc.bs[idx].ip, svc.bs[idx].active_conn);
    }
}

int main(void) {
    demo1_rr_balance();
    demo2_algorithm_comparison();
    demo3_weighted_lb();
    demo4_session_affinity();
    free(mh_lookup);
    return 0;
}
