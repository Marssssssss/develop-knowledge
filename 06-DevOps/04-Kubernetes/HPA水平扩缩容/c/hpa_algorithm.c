/*
 * HPA(HorizontalPodAutoscaler)期望副本数算法 (C),逐行对齐 upstream 源码。
 *
 * 权威来源(实际读过):
 *   1. https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
 *   2. https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/controller/podautoscaler/horizontal.go
 *   3. .../replica_calculator.go
 *   4. .../metrics/utilization.go
 *
 * 与 python / go 版同构:usageRatio 依赖整数除法的 utilization;缺失指标缩容按
 * max(100,target)%、扩容按 0%;方向反转则维持;scaleUpLimit=max(2*current,4);
 * 稳定窗口扩收取 min、缩容取 max。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define DEFAULT_TOLERANCE 0.1
#define SCALE_UP_LIMIT_FACTOR 2.0
#define SCALE_UP_LIMIT_MINIMUM 4.0
#define DEFAULT_DOWNSCALE_STAB_SECONDS 300
#define MAX_PODS 8
#define MAX_RECS 8
#define NAMELEN 32

typedef struct { double scale_up, scale_down; } Tolerances;

static int tol_is_within(Tolerances t, double r) {
    return (1.0 - t.scale_down) <= r && r <= (1.0 + t.scale_up);
}

/* metrics/requests: 名字 -> 值;用两个并行数组表达 map */
typedef struct {
    char name[MAX_PODS][NAMELEN];
    long long value[MAX_PODS];
    int n;
} Map;

static void map_set(Map *m, const char *k, long long v) {
    for (int i = 0; i < m->n; i++)
        if (strcmp(m->name[i], k) == 0) { m->value[i] = v; return; }
    snprintf(m->name[m->n], NAMELEN, "%s", k);
    m->value[m->n] = v;
    m->n++;
}

static long long map_get(const Map *m, const char *k, int *ok) {
    for (int i = 0; i < m->n; i++)
        if (strcmp(m->name[i], k) == 0) { if (ok) *ok = 1; return m->value[i]; }
    if (ok) *ok = 0;
    return 0;
}

/* currentUtilization = (metricsTotal*100)/requestsTotal —— 整数除法截断 */
static double get_resource_utilization_ratio(const Map *metrics, const Map *requests,
                                             long long target, long long *out_util) {
    long long mt = 0, rt = 0;
    for (int i = 0; i < metrics->n; i++) {
        int has = 0;
        long long r = map_get(requests, metrics->name[i], &has);
        if (!has) continue;                     /* missing requests == extraneous metrics */
        mt += metrics->value[i];
        rt += r;
    }
    if (rt == 0) { printf("no metrics returned matched known pods\n"); exit(1); }
    long long cur = (mt * 100) / rt;
    if (out_util) *out_util = cur;
    return (double)cur / (double)target;
}

static int ceil_to_int32(double x) { return (int)ceil(x); }

static int calculate_scale_up_limit(int current) {
    double v = SCALE_UP_LIMIT_FACTOR * current;
    if (v < SCALE_UP_LIMIT_MINIMUM) v = SCALE_UP_LIMIT_MINIMUM;
    return (int)v;
}

typedef struct {
    char ready[MAX_PODS][NAMELEN]; int nready;
    char unready[MAX_PODS][NAMELEN]; int nunready;
    char missing[MAX_PODS][NAMELEN]; int nmissing;
} Pods;

typedef struct { int replicas; long long utilization; const char *why; } CalcResult;

static CalcResult get_resource_replicas(int current, long long target, Pods pods,
                                        Map metrics, const Map *requests, Tolerances tol) {
    CalcResult out;
    long long util = 0;
    double usage_ratio = get_resource_utilization_ratio(&metrics, requests, target, &util);
    out.utilization = util;
    int scale_up_with_unready = (pods.nunready > 0) && (usage_ratio > 1.0);

    if (!scale_up_with_unready && pods.nmissing == 0) {
        if (tol_is_within(tol, usage_ratio)) {
            out.replicas = current; out.why = "within tolerance"; return out;
        }
        out.replicas = ceil_to_int32(usage_ratio * pods.nready);
        out.why = "plain";
        return out;
    }

    if (pods.nmissing > 0) {
        if (usage_ratio < 1.0) {
            long long fallback = target > 100 ? target : 100;   /* max(100, target) */
            for (int i = 0; i < pods.nmissing; i++) {
                int ok = 0;
                long long r = map_get(requests, pods.missing[i], &ok);
                map_set(&metrics, pods.missing[i], r * fallback / 100);
            }
        } else if (usage_ratio > 1.0) {
            for (int i = 0; i < pods.nmissing; i++)
                map_set(&metrics, pods.missing[i], 0);
        }
    }
    if (scale_up_with_unready) {
        for (int i = 0; i < pods.nunready; i++)
            map_set(&metrics, pods.unready[i], 0);
    }

    double new_ratio = get_resource_utilization_ratio(&metrics, requests, target, NULL);
    if (tol_is_within(tol, new_ratio)
        || (usage_ratio < 1.0 && new_ratio > 1.0)
        || (usage_ratio > 1.0 && new_ratio < 1.0)) {
        out.replicas = current; out.why = "damped/reversed -> hold"; return out;
    }
    int new_replicas = ceil_to_int32(new_ratio * metrics.n);
    if ((new_ratio < 1.0 && new_replicas > current)
        || (new_ratio > 1.0 && new_replicas < current)) {
        out.replicas = current; out.why = "direction flip by metrics length"; return out;
    }
    out.replicas = new_replicas; out.why = "damped";
    return out;
}

static int normalize_desired_replicas(int current, int desired, int hpa_min, int hpa_max,
                                      const char **cond) {
    int maximum_allowed = hpa_max;
    *cond = "TooManyReplicas";
    int lim = calculate_scale_up_limit(current);
    if (hpa_max > lim) { maximum_allowed = lim; *cond = "ScaleUpLimit"; }
    if (desired < hpa_min) { *cond = "TooFewReplicas"; return hpa_min; }
    if (desired > maximum_allowed) return maximum_allowed;
    *cond = "DesiredWithinRange";
    return desired;
}

typedef struct { int value; double timestamp; } Recommendation;
typedef struct { double window; } StabBehavior;

static int stabilize_recommendation(int current, int desired, double now,
                                    const Recommendation *recs, int nrecs,
                                    StabBehavior up, StabBehavior down) {
    int up_rec = desired, down_rec = desired;
    double up_cutoff = now - up.window, down_cutoff = now - down.window;
    for (int i = 0; i < nrecs; i++) {
        if (recs[i].timestamp > up_cutoff && recs[i].value < up_rec) up_rec = recs[i].value;
        if (recs[i].timestamp > down_cutoff && recs[i].value > down_rec) down_rec = recs[i].value;
    }
    int rec = current;
    if (rec < up_rec) rec = up_rec;
    if (rec > down_rec) rec = down_rec;
    return rec;
}

static int g_checks = 0;
static void ck(int cond, const char *msg) {
    if (!cond) { printf("ASSERT FAILED: %s\n", msg); exit(1); }
    g_checks++;
}

int main(void) {
    Tolerances tol = { DEFAULT_TOLERANCE, DEFAULT_TOLERANCE };
    ck(tol_is_within(tol, 1.0), "1.0 在容差内");
    ck(tol_is_within(tol, 0.90) && tol_is_within(tol, 1.10), "容差端点含");
    ck(!tol_is_within(tol, 0.899) && !tol_is_within(tol, 1.101), "越界应拒绝");

    /* 整数除法截断 */
    Map m1; memset(&m1, 0, sizeof(m1)); map_set(&m1, "a", 100);
    Map r1; memset(&r1, 0, sizeof(r1)); map_set(&r1, "a", 300);
    long long util = 0;
    double ratio = get_resource_utilization_ratio(&m1, &r1, 50, &util);
    ck(util == 33, "utilization 应为 33(整数截断)");
    ck(fabs(ratio - 33.0 / 50.0) < 1e-12, "ratio 应为 0.66");

    /* 基础公式 */
    Map reqs; memset(&reqs, 0, sizeof(reqs));
    map_set(&reqs, "p1", 100); map_set(&reqs, "p2", 100); map_set(&reqs, "p3", 100);
    Map met; memset(&met, 0, sizeof(met));
    map_set(&met, "p1", 200); map_set(&met, "p2", 200); map_set(&met, "p3", 200);
    Pods p3; memset(&p3, 0, sizeof(p3));
    strcpy(p3.ready[0], "p1"); strcpy(p3.ready[1], "p2"); strcpy(p3.ready[2], "p3"); p3.nready = 3;
    CalcResult cr = get_resource_replicas(3, 100, p3, met, &reqs, tol);
    ck(cr.utilization == 200, "utilization 200");
    ck(cr.replicas == 6 && strcmp(cr.why, "plain") == 0, "3*200/100=6 且走 plain");

    /* 容差内不动 / 越界才动 */
    Map met105; memset(&met105, 0, sizeof(met105));
    map_set(&met105, "p1", 105); map_set(&met105, "p2", 105); map_set(&met105, "p3", 105);
    cr = get_resource_replicas(3, 100, p3, met105, &reqs, tol);
    ck(cr.replicas == 3 && strcmp(cr.why, "within tolerance") == 0, "1.05 应维持");
    Map met111; memset(&met111, 0, sizeof(met111));
    map_set(&met111, "p1", 111); map_set(&met111, "p2", 111); map_set(&met111, "p3", 111);
    cr = get_resource_replicas(3, 100, p3, met111, &reqs, tol);
    ck(cr.replicas == 4, "ceil(1.11*3)=4");

    /* scaleUpLimit */
    ck(calculate_scale_up_limit(1) == 4, "current=1 → 4");
    ck(calculate_scale_up_limit(3) == 6, "current=3 → 6");
    const char *cond = NULL;
    ck(normalize_desired_replicas(1, 10, 1, 100, &cond) == 4 && strcmp(cond, "ScaleUpLimit") == 0,
       "单次扩容上限 4");
    ck(normalize_desired_replicas(5, 1, 2, 100, &cond) == 2 && strcmp(cond, "TooFewReplicas") == 0,
       "低于 min 取 min");
    ck(normalize_desired_replicas(20, 40, 1, 30, &cond) == 30 && strcmp(cond, "TooManyReplicas") == 0,
       "hpaMax=30 < scaleUpLimit=40 → 30");

    /* 缺失指标 + 缩容:补 max(100,target)% 后落回容差 → 维持 4 */
    Map reqs4; memset(&reqs4, 0, sizeof(reqs4));
    map_set(&reqs4, "p1", 100); map_set(&reqs4, "p2", 100);
    map_set(&reqs4, "p3", 100); map_set(&reqs4, "p4", 100);
    Map met40; memset(&met40, 0, sizeof(met40));
    map_set(&met40, "p1", 40); map_set(&met40, "p2", 40); map_set(&met40, "p3", 40);
    Pods pm; memset(&pm, 0, sizeof(pm));
    strcpy(pm.ready[0], "p1"); strcpy(pm.ready[1], "p2"); strcpy(pm.ready[2], "p3"); pm.nready = 3;
    strcpy(pm.missing[0], "p4"); pm.nmissing = 1;
    cr = get_resource_replicas(4, 50, pm, met40, &reqs4, tol);
    ck(cr.utilization == 40, "原始 utilization 40");
    ck(cr.replicas == 4 && strcmp(cr.why, "damped/reversed -> hold") == 0, "阻尼后维持 4");
    /* 对照组:无缺失 → ceil(0.8*3)=3 */
    Pods p3b = p3;
    cr = get_resource_replicas(4, 50, p3b, met40, &reqs, tol);
    ck(cr.replicas == 3, "无缺失时应缩到 3");

    /* 未 Ready + 扩容(requests 不均) */
    Map mu; memset(&mu, 0, sizeof(mu)); map_set(&mu, "p1", 260);
    Map ru; memset(&ru, 0, sizeof(ru)); map_set(&ru, "p1", 100); map_set(&ru, "p2", 300);
    Pods pu; memset(&pu, 0, sizeof(pu));
    strcpy(pu.ready[0], "p1"); pu.nready = 1;
    strcpy(pu.unready[0], "p2"); pu.nunready = 1;
    cr = get_resource_replicas(2, 50, pu, mu, &ru, tol);
    ck(cr.replicas == 3 && strcmp(cr.why, "damped") == 0, "阻尼后 3");
    ck(ceil_to_int32(5.2 * 1) == 6, "忽略阻尼的对照值 6");

    /* 方向反转 → 维持 */
    Map mr; memset(&mr, 0, sizeof(mr)); map_set(&mr, "p1", 100);
    cr = get_resource_replicas(2, 50, pu, mr, &ru, tol);
    ck(cr.replicas == 2 && strcmp(cr.why, "damped/reversed -> hold") == 0, "方向反转维持 2");

    /* 稳定窗口 */
    double now = 1000.0;
    StabBehavior down = { DEFAULT_DOWNSCALE_STAB_SECONDS };
    StabBehavior up0 = { 0 }, up60 = { 60 };
    Recommendation recs[2] = { {2, now - 60}, {1, now - 10} };
    ck(stabilize_recommendation(5, 1, now, recs, 2, up0, down) == 2, "缩容取窗口内最大 2");
    Recommendation oldr[1] = { {2, now - 400} };
    ck(stabilize_recommendation(5, 1, now, oldr, 1, up0, down) == 1, "窗口外不参与");
    Recommendation upr[1] = { {2, now - 10} };
    ck(stabilize_recommendation(1, 4, now, upr, 1, up60, down) == 2, "扩容取窗口内最小 2");
    ck(stabilize_recommendation(1, 4, now, upr, 1, up0, down) == 4, "扩容窗口 0 直取 desired");

    printf("hpa_algorithm(c): %d assertions passed\n", g_checks);
    return 0;
}
