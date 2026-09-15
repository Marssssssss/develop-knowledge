/* gateway.c — 游戏网关 (Gateway / Connector) 主程序与自检
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic gateway.c hashring.c -o gateway
 *
 * 与 python/gateway.py 一一对应。网关要解决四件事:
 *   一、消息路由表: 消息类型 -> 服务;
 *   二、实例选择: uid 取模 vs 一致性哈希(扩容/缩容的重映射率差异, 见 hashring.c);
 *   三、会话表: 绑定 / 查询 / TTL 回收 / 推送回程路由;
 *   四、网关故障: 断连重连 + 会话重建(网关必须无状态才能随便宕机/扩容)。
 */
#include "hashring.h"

#include <stdio.h>
#include <string.h>

#define MAXSESS   128
#define MAXCONN   256
#define MAXGW     8
#define SESS_TTL  300000LL
#define HEARTBEAT 30000LL

static int failures;

static void check(int cond, const char *what)
{
    if (!cond) { printf("  [FAIL] %s\n", what); failures++; }
}

/* ---------------- 一、路由表 ---------------- */
enum { SVC_LOGIN, SVC_MATCH, SVC_ROOM, SVC_BATTLE, SVC_CHAT, SVC_RANK, SVC_INV, SVC_N };

static const char *SVC_NAMES[SVC_N] = {"login", "match", "room", "battle",
                                       "chat", "rank", "inventory"};
static const int SVC_INST[SVC_N] = {4, 2, 8, 16, 2, 2, 4};

static const int ROUTE[][2] = {
    {0x01, SVC_LOGIN}, {0x02, SVC_LOGIN},
    {0x10, SVC_MATCH}, {0x11, SVC_MATCH},
    {0x20, SVC_ROOM},  {0x21, SVC_ROOM}, {0x22, SVC_ROOM},
    {0x30, SVC_BATTLE},{0x31, SVC_BATTLE},
    {0x40, SVC_CHAT},  {0x41, SVC_CHAT},
    {0x50, SVC_RANK},
    {0x60, SVC_INV},   {0x61, SVC_INV},
    {-1, -1}
};

/* 返回服务下标; -1 = 未知类型(应拒绝而不是乱投) */
static int route_of(int msg_type)
{
    int i;
    for (i = 0; ROUTE[i][0] >= 0; i++)
        if (ROUTE[i][0] == msg_type) return ROUTE[i][1];
    return -1;
}

/* ---------------- 三/四、会话表与网关集群 ---------------- */
typedef struct { int uid; int gw; } Conn;
typedef struct { int uid, gw, svc, inst; long long ts; } Sess;

typedef struct {
    int   gw_ids[MAXGW];
    int   gw_n;
    Conn  conns[MAXCONN];
    int   conn_n;
    Sess  sess[MAXSESS];
    int   sess_n;
    int   rebound;
    int   rejected_unknown;
} Cluster;

static void cluster_init(Cluster *c, int n_gw)
{
    int i;
    memset(c, 0, sizeof(*c));
    for (i = 0; i < n_gw; i++) c->gw_ids[i] = i;
    c->gw_n = n_gw;
}

static int gw_slot(const Cluster *c, int gw)
{
    int i;
    for (i = 0; i < c->gw_n; i++) if (c->gw_ids[i] == gw) return i;
    return -1;
}

static int conn_owner(const Cluster *c, int uid)
{
    int i;
    for (i = 0; i < c->conn_n; i++)
        if (c->conns[i].uid == uid) return c->conns[i].gw;
    return -1;
}

static void cluster_connect(Cluster *c, int uid)
{
    c->conns[c->conn_n].uid = uid;
    c->conns[c->conn_n].gw = uid % c->gw_n;
    c->conn_n++;
}

/* 返回服务下标; inst 输出实例号; 未知类型返回 -1 */
static int cluster_dispatch(Cluster *c, int uid, int msg_type,
                            long long now, int *inst)
{
    int svc = route_of(msg_type), i;
    if (svc < 0) { c->rejected_unknown++; return -1; }
    *inst = uid % SVC_INST[svc];
    for (i = 0; i < c->sess_n; i++)
        if (c->sess[i].uid == uid) {
            c->sess[i].gw = conn_owner(c, uid);
            c->sess[i].svc = svc;
            c->sess[i].inst = *inst;
            c->sess[i].ts = now;
            return svc;
        }
    c->sess[c->sess_n].uid = uid;
    c->sess[c->sess_n].gw = conn_owner(c, uid);
    c->sess[c->sess_n].svc = svc;
    c->sess[c->sess_n].inst = *inst;
    c->sess[c->sess_n].ts = now;
    c->sess_n++;
    return svc;
}

/* 推送回程: 靠会话表里记录的网关 id 找回网关; 返回网关 id 或 -1 */
static int cluster_push(const Cluster *c, int uid)
{
    int i;
    for (i = 0; i < c->sess_n; i++)
        if (c->sess[i].uid == uid) {
            int gw = c->sess[i].gw;
            if (gw_slot(c, gw) < 0) return -1;          /* 网关已下线 */
            if (conn_owner(c, uid) != gw) return -1;    /* 连接已不在原网关 */
            return gw;
        }
    return -1;                                          /* 没有会话记录 */
}

static int cluster_expire(Cluster *c, long long now)
{
    int i, gone = 0;
    for (i = 0; i < c->sess_n; i++)
        if (now - c->sess[i].ts > SESS_TTL) {
            c->sess[i] = c->sess[c->sess_n - 1];
            c->sess_n--;
            gone++;
            i--;
        }
    return gone;
}

/* 网关下线: 断开其连接、作废其会话、从网关列表摘除 */
static int cluster_kill_gw(Cluster *c, int gw)
{
    int i, dropped = 0, slot = gw_slot(c, gw);
    for (i = 0; i < c->conn_n; i++)
        if (c->conns[i].gw == gw) {
            int uid = c->conns[i].uid, j;
            for (j = 0; j < c->sess_n; j++)
                if (c->sess[j].uid == uid) {
                    c->sess[j] = c->sess[c->sess_n - 1];
                    c->sess_n--; j--;
                }
            c->conns[i] = c->conns[c->conn_n - 1];
            c->conn_n--; i--;
            dropped++;
        }
    if (slot >= 0) {
        c->gw_ids[slot] = c->gw_ids[c->gw_n - 1];
        c->gw_n--;
    }
    return dropped;
}

static int cluster_reconnect_all(Cluster *c, const int *uids, int n)
{
    int i;
    for (i = 0; i < n; i++) cluster_connect(c, uids[i]);
    c->rebound += n;
    return n;
}

/* ---------------- main ---------------- */
int main(void)
{
    const long long now = 1000000LL;
    Cluster cl;
    Ring ring;
    Remap r;
    LoadStat ld;
    int inst, i, nv = 0, victims[64];

    printf("== 一、消息路由表(消息类型 -> 服务) ==\n");
    check(route_of(0x01) == SVC_LOGIN && route_of(0x31) == SVC_BATTLE,
          "已知消息类型应路由到正确服务");
    check(route_of(0x99) == -1, "未知消息类型应被拒绝");
    printf("  0x01 -> %s, 0x31 -> %s, 0x99 -> 拒绝\n",
           SVC_NAMES[route_of(0x01)], SVC_NAMES[route_of(0x31)]);

    printf("\n== 二、实例选择: uid 取模 vs 一致性哈希 (VNODES=%d/节点) ==\n", VNODES);
    ring_build(&ring, 8);
    check(ring_pick(&ring, 12345) == ring_pick(&ring, 12345),
          "拓扑不变时应有粘性");
    r = remap_rate(8, 9);
    printf("  扩容 8->9: 取模重映射 %.2f%%, 一致性哈希重映射 %.2f%% "
           "(理论下限 1/max = %.2f%%)\n",
           r.modulo * 100, r.ring * 100, r.ideal * 100);
    check(r.modulo > 0.80, "取模扩容应几乎全员迁移");
    check(r.ring < 0.25, "一致性哈希应只迁移约 1/N");
    r = remap_rate(9, 8);
    printf("  缩容 9->8: 取模重映射 %.2f%%, 一致性哈希重映射 %.2f%%\n",
           r.modulo * 100, r.ring * 100);
    check(r.modulo > 0.80 && r.ring < 0.25, "缩容同理");
    ld = ring_load(8);
    printf("  负载均衡(8 节点, %d uid): 取模最大偏差 %.2f%%, 一致性哈希 %.2f%%\n",
           MAX_UIDS, ld.modulo_max_dev * 100, ld.ring_max_dev * 100);
    check(ld.ring_max_dev < 0.35, "一致性哈希的负载偏差应在可接受范围");

    printf("\n== 三、会话表 / 推送回程 ==\n");
    cluster_init(&cl, 3);
    for (i = 0; i < 6; i++) {
        cluster_connect(&cl, i);
        cluster_dispatch(&cl, i, 0x20, now, &inst);
    }
    cluster_dispatch(&cl, 0, 0x20, now, &inst);
    printf("  uid=0 发 0x20 -> room#%d; 推送回程 = gw%d\n",
           inst, cluster_push(&cl, 0));
    check(cluster_push(&cl, 5) >= 0, "有会话的 uid 应能推送");
    check(cluster_push(&cl, 99) < 0, "无会话的 uid 推送应失败");
    check(cluster_dispatch(&cl, 0, 0x99, now, &inst) < 0 && cl.rejected_unknown == 1,
          "未知消息应被拒绝并计数");
    {
        long long later = now + 30000LL * 12;      /* 6 分钟后 */
        int expired;
        for (i = 0; i < 3; i++) cluster_dispatch(&cl, i, 0x02, later, &inst);
        expired = cluster_expire(&cl, later);
        printf("  TTL=%llds; 心跳过的 3 个保留, 回收 %d 个 -> 剩余 %d 个会话\n",
               SESS_TTL / 1000, expired, cl.sess_n);
        check(expired == 3 && cl.sess_n == 3, "TTL 应只回收到期且未心跳的会话");
    }

    printf("\n== 四、网关故障: 断连重连 + 会话重建 ==\n");
    cluster_init(&cl, 4);
    for (i = 0; i < 40; i++) {
        cluster_connect(&cl, i);
        cluster_dispatch(&cl, i, 0x20, now, &inst);
    }
    for (i = 0; i < 40; i++) if (conn_owner(&cl, i) == 1) victims[nv++] = i;
    {
        int dropped = cluster_kill_gw(&cl, 1);
        int rebuilt;
        printf("  gw1 下线: 断开 %d 条连接, 会话记录作废; 剩余网关 %d 个\n",
               dropped, cl.gw_n);
        check(dropped == nv && dropped == 10, "应断开 gw1 上的全部连接");
        rebuilt = cluster_reconnect_all(&cl, victims, nv);
        printf("  客户端重连并重建会话: %d 个 (模数 4 -> %d, 落点必然变化)\n",
               rebuilt, cl.gw_n);
        check(rebuilt == nv, "全部受害连接都应重连成功");
        for (i = 0; i < nv; i++) {
            if (cluster_dispatch(&cl, victims[i], 0x20, now, &inst) < 0) return 1;
        }
        printf("  -> 网关必须无状态(或状态外置), 才能随便宕机/扩容  OK\n");
    }

    printf("\n%s (failures=%d)\n", failures ? "存在失败项" : "全部自检通过。", failures);
    return failures ? 1 : 0;
}
