/*
 * kube-scheduler Scheduling Framework 最小实现 (C)。
 *
 * 权威来源(实际读过):
 *   1. https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/
 *   2. https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/scheduler/framework/interface.go
 *
 * 建模要点(与 python / go 版同构):
 *   - 调度周期: PreFilter -> Filter -> PostFilter -> PreScore -> Score -> NormalizeScore -> Reserve -> Permit
 *   - 绑定周期: PreBind -> Bind -> PostBind;Bind 首个接管者短路
 *   - Filter 在 node 内短路;PostFilter 仅在无可行 node 时触发(抢占)
 *   - Reserve 失败 -> 全部已 Reserve 的 plugin 逆序 Unreserve
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NODESCORE_MAX 100
#define MAX_NODES 8
#define MAX_PLUGINS 8
#define MAX_TRACE 256
#define MAX_PODS 8
#define MSG 64

typedef struct { char name[32]; int cpu; int prio; } Pod;

typedef struct {
    char name[32];
    int alloc_cpu;
    Pod pods[MAX_PODS];
    int npods;
} Node;

static int node_used(const Node *n) {
    int s = 0;
    for (int i = 0; i < n->npods; i++) s += n->pods[i].cpu;
    return s;
}

typedef struct Framework Framework;

/* 扩展点回调:返回 "" 表示放行;Filter/PreFilter 返回非空表示原因 */
typedef const char *(*filter_fn)(Framework *, const Pod *, const Node *);
typedef const char *(*postfilter_fn)(Framework *, const Pod *);
typedef int (*score_fn)(Framework *, const Pod *, const Node *);
typedef void (*normalize_fn)(Framework *, int *, int);
typedef int (*reserve_fn)(Framework *, const char *);
typedef void (*unreserve_fn)(Framework *, const char *);
typedef const char *(*permit_fn)(Framework *);
typedef int (*bind_fn)(Framework *, const char *);
typedef int (*prebind_fn)(Framework *, const char *);
typedef void (*postbind_fn)(Framework *, const char *);

typedef struct {
    char name[32];
    int weight;
    filter_fn filter;
    postfilter_fn postfilter;
    score_fn score;
    normalize_fn normalize;
    reserve_fn reserve;
    unreserve_fn unreserve;
    permit_fn permit;
    prebind_fn prebind;
    bind_fn bind;
    postbind_fn postbind;
} Plugin;

struct Framework {
    Plugin plugins[MAX_PLUGINS];
    int nplugins;
    char trace[MAX_TRACE][96];
    int ntrace;
    char unreserve_calls[MAX_PLUGINS][32];
    int nunreserve;
    /* 最近一次 Filter 的拒绝情况(供 PostFilter 使用) */
    char rej_node[MAX_NODES][32];
    char rej_reason[MAX_NODES][MSG];
    int nrej;
    int state_never_called;   /* NeverBind 是否被调用(应为 0) */
    /* 当前调度上下文,供 PostFilter(抢占)回查 node 数据 */
    Node *ctx_nodes;
    int ctx_nnodes;
    const Pod *ctx_pod;
};

static void fw_record(Framework *fw, const char *a, const char *b, const char *c) {
    if (fw->ntrace >= MAX_TRACE) return;
    snprintf(fw->trace[fw->ntrace], 96, "%s:%s:%s", a, b ? b : "-", c ? c : "-");
    fw->ntrace++;
}

static void fw_reset(Framework *fw) {
    fw->ntrace = 0;
    fw->nunreserve = 0;
    fw->nrej = 0;
    fw->state_never_called = 0;
}

static void fw_add(Framework *fw, Plugin p) { fw->plugins[fw->nplugins++] = p; }

static void fw_unreserve_all(Framework *fw, const char *node, int nreserved) {
    for (int i = nreserved - 1; i >= 0; i--) {
        Plugin *p = &fw->plugins[i];
        fw_record(fw, "Unreserve", p->name, NULL);
        snprintf(fw->unreserve_calls[fw->nunreserve++], 32, "%s", p->name);
        if (p->unreserve) p->unreserve(fw, node);
    }
}

typedef struct {
    const char *phase;    /* bound / unschedulable / waiting / nominated */
    char node[32];
    char nominated[32];
    double final_score[MAX_NODES];
    int norm[MAX_PLUGINS][MAX_NODES];
    int nfeasible;
    char feasible[MAX_NODES][32];
} Result;

static Result fw_schedule(Framework *fw, const Pod *pod, Node *nodes, int nnodes) {
    Result out;
    memset(&out, 0, sizeof(out));
    out.phase = "unschedulable";
    fw_reset(fw);
    fw->ctx_nodes = nodes;
    fw->ctx_nnodes = nnodes;
    fw->ctx_pod = pod;
    fw_record(fw, "QueueSort", "-", NULL);

    for (int i = 0; i < fw->nplugins; i++) fw_record(fw, "PreFilter", fw->plugins[i].name, NULL);

    /* Filter:node 内短路 */
    char feasible[MAX_NODES][32];
    int nfeasible = 0;
    for (int n = 0; n < nnodes; n++) {
        const char *rej = NULL;
        for (int i = 0; i < fw->nplugins && !rej; i++) {
            Plugin *p = &fw->plugins[i];
            fw_record(fw, "Filter", p->name, nodes[n].name);
            if (p->filter) {
                const char *r = p->filter(fw, pod, &nodes[n]);
                if (r && r[0]) {
                    rej = r;
                    snprintf(fw->rej_node[fw->nrej], 32, "%s", nodes[n].name);
                    snprintf(fw->rej_reason[fw->nrej], MSG, "%s", r);
                    fw->nrej++;
                }
            }
        }
        if (!rej) snprintf(feasible[nfeasible++], 32, "%s", nodes[n].name);
    }

    /* PostFilter:仅无可行 node */
    if (nfeasible == 0) {
        for (int i = 0; i < fw->nplugins; i++) {
            Plugin *p = &fw->plugins[i];
            fw_record(fw, "PostFilter", p->name, NULL);
            if (p->postfilter) {
                const char *nn = p->postfilter(fw, pod);
                if (nn && nn[0]) {
                    snprintf(out.nominated, 32, "%s", nn);
                    out.phase = "nominated";
                    return out;
                }
            }
        }
        return out;
    }

    /* Score → NormalizeScore(每个 plugin 每轮一次,作用于自己的分数集) */
    int raw[MAX_PLUGINS][MAX_NODES];
    int norm[MAX_PLUGINS][MAX_NODES];
    for (int i = 0; i < fw->nplugins; i++) {
        Plugin *p = &fw->plugins[i];
        for (int n = 0; n < nfeasible; n++) {
            const Node *nd = NULL;
            for (int k = 0; k < nnodes; k++)
                if (strcmp(nodes[k].name, feasible[n]) == 0) nd = &nodes[k];
            raw[i][n] = p->score ? p->score(fw, pod, nd) : 0;
        }
        fw_record(fw, "Score", p->name, NULL);
    }
    for (int i = 0; i < fw->nplugins; i++) {
        Plugin *p = &fw->plugins[i];
        for (int n = 0; n < nfeasible; n++) norm[i][n] = raw[i][n];
        if (p->normalize) p->normalize(fw, norm[i], nfeasible);
        fw_record(fw, "NormalizeScore", p->name, NULL);
    }
    int total_w = 0;
    for (int i = 0; i < fw->nplugins; i++) total_w += fw->plugins[i].weight;
    if (total_w == 0) total_w = 1;
    int best = 0;
    for (int n = 0; n < nfeasible; n++) {
        double s = 0;
        for (int i = 0; i < fw->nplugins; i++)
            s += (double)fw->plugins[i].weight * norm[i][n] / total_w;
        out.final_score[n] = s;
        if (s > out.final_score[best]) best = n;
    }
    out.nfeasible = nfeasible;
    for (int n = 0; n < nfeasible; n++) {
        snprintf(out.feasible[n], 32, "%s", feasible[n]);
        for (int i = 0; i < MAX_PLUGINS && i < fw->nplugins; i++) out.norm[i][n] = norm[i][n];
    }
    snprintf(out.node, 32, "%s", feasible[best]);

    /* Reserve */
    for (int i = 0; i < fw->nplugins; i++) {
        Plugin *p = &fw->plugins[i];
        fw_record(fw, "Reserve", p->name, NULL);
        if (p->reserve && !p->reserve(fw, out.node)) {
            fw_unreserve_all(fw, out.node, i);
            return out;
        }
    }
    /* Permit */
    for (int i = 0; i < fw->nplugins; i++) {
        Plugin *p = &fw->plugins[i];
        fw_record(fw, "Permit", p->name, NULL);
        if (p->permit) {
            const char *d = p->permit(fw);
            if (strcmp(d, "deny") == 0) { fw_unreserve_all(fw, out.node, fw->nplugins); return out; }
            if (strcmp(d, "wait") == 0) { fw_unreserve_all(fw, out.node, fw->nplugins); out.phase = "waiting"; return out; }
        }
    }
    for (int i = 0; i < fw->nplugins; i++) {
        Plugin *p = &fw->plugins[i];
        fw_record(fw, "PreBind", p->name, NULL);
        if (p->prebind && !p->prebind(fw, out.node)) { fw_unreserve_all(fw, out.node, fw->nplugins); return out; }
    }
    for (int i = 0; i < fw->nplugins; i++) {
        Plugin *p = &fw->plugins[i];
        fw_record(fw, "Bind", p->name, NULL);
        if (p->bind && p->bind(fw, out.node)) break;   /* 首个接管者短路 */
    }
    for (int i = 0; i < fw->nplugins; i++) {
        Plugin *p = &fw->plugins[i];
        fw_record(fw, "PostBind", p->name, NULL);
        if (p->postbind) p->postbind(fw, out.node);
    }
    out.phase = "bound";
    return out;
}

/* ------------------------------------------------ 插件实现 */
static const char *f_nrf(Framework *fw, const Pod *p, const Node *n) {
    (void)fw;
    return (n->alloc_cpu - node_used(n) < p->cpu) ? "Insufficient cpu" : "";
}

static const char *f_taint(Framework *fw, const Pod *p, const Node *n) {
    (void)fw; (void)p;
    return (strncmp(n->name, "taint-", 6) == 0) ? "untolerated taint" : "";
}

static int s_least(Framework *fw, const Pod *p, const Node *n) {
    (void)fw; (void)p;
    if (!n) return 0;
    return (n->alloc_cpu - node_used(n)) * 100 / n->alloc_cpu;
}

static void norm_max(Framework *fw, int *v, int n) {
    (void)fw;
    int highest = 0;
    for (int i = 0; i < n; i++) if (v[i] > highest) highest = v[i];
    if (highest == 0) highest = 1;
    for (int i = 0; i < n; i++) v[i] = v[i] * NODESCORE_MAX / highest;
}

static int bind_take(Framework *fw, const char *node) { (void)fw; (void)node; return 1; }
static int r_ok(Framework *fw, const char *node) { (void)fw; (void)node; return 1; }
static int r_fail(Framework *fw, const char *node) { (void)fw; (void)node; return 0; }
static void u_noop(Framework *fw, const char *node) { (void)fw; (void)node; }
static const char *p_deny(Framework *fw) { (void)fw; return "deny"; }
static const char *p_wait(Framework *fw) { (void)fw; return "wait"; }

static int bind_take(Framework *fw, const char *node) { (void)fw; (void)node; return 1; }

static int bind_never(Framework *fw, const char *node) {
    (void)node;
    fw->state_never_called = 1;      /* 若被调用即为错误 */
    return 1;
}

/* 抢占:遍历被拒 node,挑一个"驱逐低优先级 Pod 后装得下"的 node 提名 */
static char g_nominated[32];

static const char *post_preempt(Framework *fw, const Pod *pod) {
    g_nominated[0] = '\0';
    for (int n = 0; n < fw->ctx_nnodes; n++) {
        Node *nd = &fw->ctx_nodes[n];
        int freed = 0;
        for (int k = 0; k < nd->npods; k++)
            if (nd->pods[k].prio < pod->prio) freed += nd->pods[k].cpu;
        if (nd->alloc_cpu - (node_used(nd) - freed) >= pod->cpu) {
            snprintf(g_nominated, 32, "%s", nd->name);
            return g_nominated;
        }
    }
    return "";
}

/* ------------------------------------------------ 自检 */
static int g_checks = 0;

static void ck(int cond, const char *msg) {
    if (!cond) { printf("ASSERT FAILED: %s\n", msg); exit(1); }
    g_checks++;
}

static int trace_has(Framework *fw, const char *prefix) {
    for (int i = 0; i < fw->ntrace; i++)
        if (strncmp(fw->trace[i], prefix, strlen(prefix)) == 0) return 1;
    return 0;
}

int main(void) {
    Framework fw;
    memset(&fw, 0, sizeof(fw));

    Node nodes[2];
    memset(nodes, 0, sizeof(nodes));
    snprintf(nodes[0].name, 32, "n1"); nodes[0].alloc_cpu = 8;
    snprintf(nodes[1].name, 32, "n2"); nodes[1].alloc_cpu = 4;
    snprintf(nodes[1].pods[0].name, 32, "a"); nodes[1].pods[0].cpu = 3; nodes[1].npods = 1;

    Plugin nrf; memset(&nrf, 0, sizeof(nrf));
    snprintf(nrf.name, 32, "NodeResourcesFit"); nrf.weight = 1; nrf.filter = f_nrf;
    Plugin taint; memset(&taint, 0, sizeof(taint));
    snprintf(taint.name, 32, "NodeUnschedulable"); taint.weight = 1; taint.filter = f_taint;
    fw_add(&fw, nrf); fw_add(&fw, taint);

    Pod pod; memset(&pod, 0, sizeof(pod));
    snprintf(pod.name, 32, "p"); pod.cpu = 2;

    Result r = fw_schedule(&fw, &pod, nodes, 2);
    ck(strcmp(r.phase, "bound") == 0, "应绑定成功");
    ck(trace_has(&fw, "QueueSort"), "缺 QueueSort");
    ck(trace_has(&fw, "PreFilter"), "缺 PreFilter");
    ck(trace_has(&fw, "Filter"), "缺 Filter");
    ck(trace_has(&fw, "Score"), "缺 Score");
    ck(trace_has(&fw, "NormalizeScore"), "缺 NormalizeScore");
    ck(trace_has(&fw, "Reserve"), "缺 Reserve");
    ck(trace_has(&fw, "Permit"), "缺 Permit");
    ck(trace_has(&fw, "PreBind"), "缺 PreBind");
    ck(trace_has(&fw, "Bind"), "缺 Bind");
    ck(trace_has(&fw, "PostBind"), "缺 PostBind");
    ck(!trace_has(&fw, "PostFilter"), "有可行 node 时不应调 PostFilter");

    /* Filter node 内短路:n2 只剩 1 CPU */
    int n2_filter = 0, n2_first_is_nrf = 0;
    for (int i = 0; i < fw.ntrace; i++) {
        if (strncmp(fw.trace[i], "Filter:", 7) == 0 && strstr(fw.trace[i], ":n2")) {
            n2_filter++;
            if (n2_filter == 1 && strstr(fw.trace[i], "NodeResourcesFit")) n2_first_is_nrf = 1;
        }
    }
    ck(n2_filter == 1, "n2 应在第一个 Filter 处短路");
    ck(n2_first_is_nrf, "n2 首个 Filter 应是 NodeResourcesFit");

    /* NormalizeScore */
    Plugin least; memset(&least, 0, sizeof(least));
    snprintf(least.name, 32, "LeastAllocated"); least.weight = 1;
    least.score = s_least; least.normalize = norm_max;
    Framework fw2; memset(&fw2, 0, sizeof(fw2));
    fw_add(&fw2, nrf); fw_add(&fw2, least);
    Pod pod1; memset(&pod1, 0, sizeof(pod1)); snprintf(pod1.name, 32, "p"); pod1.cpu = 1;
    Result r2 = fw_schedule(&fw2, &pod1, nodes, 2);
    ck(r2.norm[1][0] == NODESCORE_MAX, "归一化后最高分应为 100");
    ck(r2.nfeasible == 2, "1 CPU 时两个 node 都可行");
    ck(r2.norm[1][1] == 25, "n2 归一化后应为 25");
    ck(r2.final_score[0] > r2.final_score[1], "n1 分应高于 n2");
    ck(strcmp(r2.node, "n1") == 0, "应选 n1");

    /* Reserve 失败逆序回滚 */
    Plugin r1, r2p;
    memset(&r1, 0, sizeof(r1)); snprintf(r1.name, 32, "R1"); r1.weight = 1;
    r1.reserve = r_ok; r1.unreserve = u_noop;
    memset(&r2p, 0, sizeof(r2p)); snprintf(r2p.name, 32, "R2"); r2p.weight = 1;
    r2p.reserve = r_fail; r2p.unreserve = u_noop;
    Framework fw7; memset(&fw7, 0, sizeof(fw7));
    fw_add(&fw7, r1); fw_add(&fw7, r2p);
    Node n1; memset(&n1, 0, sizeof(n1)); snprintf(n1.name, 32, "n1"); n1.alloc_cpu = 4;
    Result r7 = fw_schedule(&fw7, &pod1, &n1, 1);
    ck(strcmp(r7.phase, "unschedulable") == 0, "Reserve 失败不应绑定");
    ck(fw7.nunreserve == 1 && strcmp(fw7.unreserve_calls[0], "R1") == 0, "只回滚已成功的 R1");

    /* Permit deny → 逆序 Unreserve 全部 */
    Plugin deny; memset(&deny, 0, sizeof(deny));
    snprintf(deny.name, 32, "DenyAll"); deny.weight = 1; deny.permit = p_deny;
    Framework fw8; memset(&fw8, 0, sizeof(fw8));
    fw_add(&fw8, r1); fw_add(&fw8, deny);
    Result r8 = fw_schedule(&fw8, &pod1, &n1, 1);
    ck(strcmp(r8.phase, "unschedulable") == 0, "Permit deny 不应绑定");
    ck(fw8.nunreserve == 2, "deny 应回滚全部 Reserve plugin");
    ck(strcmp(fw8.unreserve_calls[0], "DenyAll") == 0, "逆序:先 DenyAll");
    ck(strcmp(fw8.unreserve_calls[1], "R1") == 0, "逆序:后 R1");

    /* Permit wait */
    Plugin waitp; memset(&waitp, 0, sizeof(waitp));
    snprintf(waitp.name, 32, "WaitForever"); waitp.weight = 1; waitp.permit = p_wait;
    Framework fw9; memset(&fw9, 0, sizeof(fw9));
    fw_add(&fw9, r1); fw_add(&fw9, waitp);
    Result r9 = fw_schedule(&fw9, &pod1, &n1, 1);
    ck(strcmp(r9.phase, "waiting") == 0, "wait 应停在 waiting");

    /* Bind 短路 */
    Plugin vb, nb;
    memset(&vb, 0, sizeof(vb)); snprintf(vb.name, 32, "VolumeBinder"); vb.weight = 1; vb.bind = bind_take;
    memset(&nb, 0, sizeof(nb)); snprintf(nb.name, 32, "NeverBind"); nb.weight = 1; nb.bind = bind_never;
    Framework fw10; memset(&fw10, 0, sizeof(fw10));
    fw_add(&fw10, vb); fw_add(&fw10, nb);
    Result r10 = fw_schedule(&fw10, &pod1, &n1, 1);
    ck(strcmp(r10.phase, "bound") == 0, "应绑定成功");
    ck(fw10.state_never_called == 0, "首个接管的 Bind plugin 之后应全部跳过");

    /* PostFilter 仅在无可行 node 时触发 */
    Plugin pf; memset(&pf, 0, sizeof(pf));
    snprintf(pf.name, 32, "DefaultPreemption"); pf.weight = 1; pf.postfilter = post_preempt;
    Framework fw3; memset(&fw3, 0, sizeof(fw3));
    fw_add(&fw3, nrf); fw_add(&fw3, taint); fw_add(&fw3, pf);
    Node tn[2]; memset(tn, 0, sizeof(tn));
    snprintf(tn[0].name, 32, "taint-1"); tn[0].alloc_cpu = 4;
    snprintf(tn[0].pods[0].name, 32, "lo"); tn[0].pods[0].cpu = 4; tn[0].npods = 1;
    snprintf(tn[1].name, 32, "taint-2"); tn[1].alloc_cpu = 4;
    snprintf(tn[1].pods[0].name, 32, "lo2"); tn[1].pods[0].cpu = 4; tn[1].npods = 1;
    Pod hi; memset(&hi, 0, sizeof(hi)); snprintf(hi.name, 32, "hi"); hi.cpu = 2; hi.prio = 100;
    Result r3 = fw_schedule(&fw3, &hi, tn, 2);
    ck(trace_has(&fw3, "PostFilter"), "无可行 node 必须调 PostFilter");
    ck(strcmp(r3.node, "") == 0, "PostFilter 只提名不绑定");
    ck(strcmp(r3.nominated, "taint-1") == 0, "高优先级 Pod 应提名 taint-1");

    /* 低优先级 Pod 抢占不到:victim 优先级不低于自己 */
    Pod lo3; memset(&lo3, 0, sizeof(lo3));
    snprintf(lo3.name, 32, "lo3"); lo3.cpu = 2; lo3.prio = 0;
    Result r5 = fw_schedule(&fw3, &lo3, tn, 2);
    ck(strcmp(r5.nominated, "") == 0, "优先级不高于 victim 不应抢占");
    ck(strcmp(r5.node, "") == 0, "抢占失败不绑定");

    printf("scheduler_framework(c): %d assertions passed\n", g_checks);
    return 0;
}
