#ifndef SCHEDULER_FRAMEWORK_IMPL_H
#define SCHEDULER_FRAMEWORK_IMPL_H

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


#endif
