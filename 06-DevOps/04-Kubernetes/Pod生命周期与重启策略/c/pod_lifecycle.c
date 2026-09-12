/*
 * pod_lifecycle.c — Pod 生命周期状态机与重启策略模拟
 *
 * 权威来源:
 *   - kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle (Pod phase +
 *     restartPolicy + exponential backoff)
 *   - kubernetes.io/zh-cn/docs/concepts/workloads/pods/pod-lifecycle
 *
 * 实现要点(摘自官方文档):
 *   1. Pod phase 五态: Pending / Running / Succeeded / Failed / Unknown
 *   2. restartPolicy 三值: Always(默认) / OnFailure / Never
 *   3. 退出码 0 → 成功;非 0 → 失败
 *      退出码 | restartPolicy=Always | =OnFailure | =Never
 *      0      | restart           | stay       | stay
 *      非 0   | restart           | restart    | stay
 *   4. 指数退避: 10s, 20s, 40s, 80s, 160s, 300s, 300s, ...
 *      上限 300s (5 min)。容器连续正常运行 10 min 重置退避
 *   5. Sidecar 容器无视 Pod-level restartPolicy,总是使用 container-level Always
 *
 * 本实现用"虚拟时间"(tick)模拟秒级事件,不实际 sleep。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

/* ============== 常量 ============== */
#define MAX_RESTART_HISTORY 32
#define BACKOFF_STEPS {10, 20, 40, 80, 160, 300}  /* 退避序列(秒) */
#define BACKOFF_RESET_SECONDS 600                  /* 10 min 重置 */
#define BACKOFF_CAP_SECONDS 300                    /* 上限 5 min */

/* ============== 枚举 ============== */
typedef enum {
    PHASE_PENDING = 0,
    PHASE_RUNNING,
    PHASE_SUCCEEDED,
    PHASE_FAILED,
    PHASE_UNKNOWN
} pod_phase_t;

typedef enum {
    RP_ALWAYS = 0,
    RP_ONFAILURE,
    RP_NEVER
} restart_policy_t;

typedef enum {
    CTR_WAITING = 0,
    CTR_RUNNING,
    CTR_TERMINATED
} container_state_t;

typedef enum {
    KIND_MAIN = 0,
    KIND_SIDECAR    /* Sidecar 总是 Always,无视 Pod-level */
} container_kind_t;

/* ============== 结构 ============== */
typedef struct {
    int id;
    container_kind_t kind;
    container_state_t state;
    int restart_count;
    int current_backoff;       /* 当前等待重启的秒数,0 表示就绪可启动 */
    int backoff_index;         /* 退避序列索引 */
    int running_since_tick;    /* 进入 Running 的 tick,用于 10min 重置判断 */
    int last_exit_code;        /* 最近退出码,-1 表示未退出 */
} container_t;

typedef struct {
    char name[64];
    pod_phase_t phase;
    restart_policy_t pod_policy;
    container_t containers[8];
    int n_containers;
    int tick;                  /* 虚拟时钟(秒) */
} pod_t;

/* ============== 工具 ============== */
static const char *phase_name(pod_phase_t p) {
    switch (p) {
        case PHASE_PENDING:    return "Pending";
        case PHASE_RUNNING:    return "Running";
        case PHASE_SUCCEEDED:  return "Succeeded";
        case PHASE_FAILED:     return "Failed";
        case PHASE_UNKNOWN:    return "Unknown";
    }
    return "?";
}

static const char *policy_name(restart_policy_t p) {
    switch (p) {
        case RP_ALWAYS:    return "Always";
        case RP_ONFAILURE: return "OnFailure";
        case RP_NEVER:     return "Never";
    }
    return "?";
}

static const char *kind_name(container_kind_t k) {
    return k == KIND_MAIN ? "main" : "sidecar";
}

/* ============== 退避计算 ============== */
/*
 * 第 N 次连续崩溃 → 等待 N 步后索引的秒数。
 * 序列 10, 20, 40, 80, 160, 300 之后封顶 300。
 */
static int backoff_for(int restart_count) {
    static const int steps[] = BACKOFF_STEPS;
    int n = (int)(sizeof(steps) / sizeof(steps[0]));
    int idx = restart_count - 1;
    if (idx < 0) return 0;
    if (idx >= n) idx = n - 1;
    return steps[idx];
}

/* ============== 是否应重启 ============== */
/*
 * 决策表(官方文档表格):
 *   退出码 | Always | OnFailure | Never
 *   0      | 重启   | 不重启    | 不重启
 *   非 0   | 重启   | 重启      | 不重启
 *
 * 注意: Sidecar 容器忽略 Pod-level policy,总是按 container-level Always。
 */
static bool should_restart(container_t *c, restart_policy_t pod_policy, int exit_code) {
    restart_policy_t effective;
    if (c->kind == KIND_SIDECAR) {
        effective = RP_ALWAYS;  /* sidecar 强制 Always */
    } else {
        effective = pod_policy;
    }

    if (effective == RP_ALWAYS)    return true;
    if (effective == RP_ONFAILURE) return exit_code != 0;
    return false;  /* RP_NEVER */
}

/* ============== 推算 Pod phase ============== */
/*
 * 官方规则:
 *   - 至少一个 main 容器未启动或未 Running → Pending
 *   - 所有 main 容器退出 0 → Succeeded
 *   - 至少一个 main 容器非 0 退出且不会被重启 → Failed
 *   - 至少一个 main 容器 Running 或正在启动/重启 → Running
 *
 * 简化:sidecar 不影响 Pod phase(实际 sidecar Running 才能 Ready)
 */
static pod_phase_t compute_phase(pod_t *p) {
    int main_running = 0, main_done_success = 0, main_done_fail = 0;
    for (int i = 0; i < p->n_containers; i++) {
        container_t *c = &p->containers[i];
        if (c->kind != KIND_MAIN) continue;
        if (c->state == CTR_RUNNING) main_running++;
        else if (c->state == CTR_TERMINATED && c->last_exit_code == 0) main_done_success++;
        else if (c->state == CTR_TERMINATED && c->last_exit_code != 0) main_done_fail++;
    }
    if (main_running > 0) return PHASE_RUNNING;
    if (main_done_success == p->n_containers && main_done_fail == 0)
        return PHASE_SUCCEEDED;
    if (main_done_fail > 0) return PHASE_FAILED;
    return PHASE_PENDING;
}

/* ============== 单步推进 ============== */
static void pod_tick(pod_t *p) {
    p->tick++;

    for (int i = 0; i < p->n_containers; i++) {
        container_t *c = &p->containers[i];

        /* 1. 退避等待中 → 倒计时 */
        if (c->state == CTR_TERMINATED && c->current_backoff > 0) {
            c->current_backoff--;
            if (c->current_backoff == 0) {
                c->state = CTR_RUNNING;
                c->running_since_tick = p->tick;
                printf("[t=%ds] %s/%s: backoff elapsed, restart #%d → Running\n",
                       p->tick, p->name, kind_name(c->kind), c->restart_count);
            }
            continue;
        }

        /* 2. 检测 10 min 正常运行重置退避 */
        if (c->state == CTR_RUNNING && c->restart_count > 0 &&
            (p->tick - c->running_since_tick) >= BACKOFF_RESET_SECONDS) {
            c->backoff_index = 0;
            printf("[t=%ds] %s/%s: ran ≥%ds, backoff timer reset\n",
                   p->tick, p->name, kind_name(c->kind), BACKOFF_RESET_SECONDS);
        }
    }
    p->phase = compute_phase(p);
}

/*
 * 容器退出 → 进入 TERMINATED,按策略决定是否触发 backoff 重启。
 * 退出事件由调用方提供 tick 与 exit_code。
 */
static void container_exit(pod_t *p, int cid, int exit_code) {
    container_t *c = &p->containers[cid];
    c->state = CTR_TERMINATED;
    c->last_exit_code = exit_code;

    if (should_restart(c, p->pod_policy, exit_code)) {
        c->restart_count++;
        c->current_backoff = backoff_for(c->restart_count);
        printf("[t=%ds] %s/%s: exit(%d), policy=%s%s → restart #%d in %ds\n",
               p->tick, p->name, kind_name(c->kind), exit_code,
               (c->kind == KIND_SIDECAR) ? "Always(sidecar)" : policy_name(p->pod_policy),
               (c->kind == KIND_SIDECAR) ? "" : "",
               c->restart_count, c->current_backoff);
    } else {
        printf("[t=%ds] %s/%s: exit(%d), policy=%s%s → stay Terminated\n",
               p->tick, p->name, kind_name(c->kind), exit_code,
               (c->kind == KIND_SIDECAR) ? "Always(sidecar)" : policy_name(p->pod_policy),
               (c->kind == KIND_SIDECAR) ? "" : "");
        c->current_backoff = 0;
    }
    p->phase = compute_phase(p);
}

/* ============== 子 demo 1: basic state machine ============== */
static void demo1_basic_state_machine(void) {
    printf("\n========== Demo 1: Basic state machine (Pending → Running → Succeeded) ==========\n");
    pod_t p = {0};
    strncpy(p.name, "demo1-pod", sizeof(p.name) - 1);
    p.pod_policy = RP_ALWAYS;
    p.n_containers = 1;
    p.containers[0] = (container_t){.id = 0, .kind = KIND_MAIN, .state = CTR_WAITING};
    p.phase = PHASE_PENDING;

    printf("Initial: phase=%s, ctr.state=%s\n",
           phase_name(p.phase), p.containers[0].state == CTR_WAITING ? "Waiting" : "?");

    /* 镜像拉取完成 → Running */
    p.containers[0].state = CTR_RUNNING;
    p.containers[0].running_since_tick = p.tick;
    p.tick = 0;
    p.phase = compute_phase(&p);
    printf("After scheduling: phase=%s\n", phase_name(p.phase));

    /* 运行 30s,期间 phase 维持 Running */
    for (int i = 0; i < 30; i++) pod_tick(&p);
    printf("Running for 30s: phase=%s\n", phase_name(p.phase));

    /* 退出 0 → Succeeded */
    container_exit(&p, 0, 0);
    printf("After exit 0: phase=%s, ctr.state=%s\n",
           phase_name(p.phase),
           p.containers[0].state == CTR_TERMINATED ? "Terminated" : "?");

    /* 验证: Always 策略但已 Succeeded,不会重启 */
    pod_tick(&p);
    printf("After 1s tick (Always policy, exit 0): phase=%s, restart_count=%d\n",
           phase_name(p.phase), p.containers[0].restart_count);
}

/* ============== 子 demo 2: 三策略对比 ============== */
static void demo2_policy_comparison(void) {
    printf("\n========== Demo 2: restartPolicy comparison (exit code 0 vs 1) ==========\n");
    const restart_policy_t policies[] = {RP_ALWAYS, RP_ONFAILURE, RP_NEVER};
    const char *pname[] = {"Always", "OnFailure", "Never"};
    const int exit_codes[] = {0, 1};

    for (int pi = 0; pi < 3; pi++) {
        for (int ei = 0; ei < 2; ei++) {
            pod_t p = {0};
            snprintf(p.name, sizeof(p.name), "demo2-%s", pname[pi]);
            p.pod_policy = policies[pi];
            p.n_containers = 1;
            p.containers[0] = (container_t){.id = 0, .kind = KIND_MAIN, .state = CTR_RUNNING};
            p.containers[0].running_since_tick = p.tick;
            p.phase = PHASE_RUNNING;

            container_exit(&p, 0, exit_codes[ei]);
            printf("policy=%s, exit=%d → restart=%s, restart_count=%d, backoff=%ds\n",
                   pname[pi], exit_codes[ei],
                   p.containers[0].restart_count > 0 ? "yes" : "no",
                   p.containers[0].restart_count,
                   p.containers[0].current_backoff);
        }
    }
}

/* ============== 子 demo 3: 指数退避序列 ============== */
static void demo3_exponential_backoff(void) {
    printf("\n========== Demo 3: Exponential backoff schedule ==========\n");
    printf("Sequence: ");
    for (int i = 1; i <= 8; i++) {
        printf("%d%s", backoff_for(i), (i < 8) ? ", " : "\n");
    }
    printf("(per docs: 10s, 20s, 40s, 80s, 160s, 300s, then capped at 300s)\n");

    /* 模拟容器连续崩溃 7 次,等待退避累计秒数 */
    pod_t p = {0};
    strncpy(p.name, "demo3-pod", sizeof(p.name) - 1);
    p.pod_policy = RP_ALWAYS;
    p.n_containers = 1;
    p.containers[0] = (container_t){.id = 0, .kind = KIND_MAIN, .state = CTR_RUNNING};
    p.phase = PHASE_RUNNING;

    int total_wait = 0;
    for (int i = 1; i <= 7; i++) {
        /* 模拟崩 → 重启 → 立即再崩 */
        container_exit(&p, 0, 1);  /* 退避 current_backoff 秒 */
        total_wait += p.containers[0].current_backoff;
        printf("crash #%d: wait %ds (cum %ds)\n",
               i, p.containers[0].current_backoff, total_wait);
        /* 快进到退避结束 */
        for (int t = 0; t < p.containers[0].current_backoff; t++) pod_tick(&p);
    }
}

/* ============== 子 demo 4: sidecar 独立性 ============== */
static void demo4_sidecar_independence(void) {
    printf("\n========== Demo 4: Sidecar containers always restart (independent of Pod policy) ==========\n");
    /* Pod-level Never,但 sidecar 强制 Always */
    pod_t p = {0};
    strncpy(p.name, "demo4-pod", sizeof(p.name) - 1);
    p.pod_policy = RP_NEVER;          /* Pod-level Never */
    p.n_containers = 2;
    p.containers[0] = (container_t){.id = 0, .kind = KIND_MAIN, .state = CTR_RUNNING};
    p.containers[1] = (container_t){.id = 1, .kind = KIND_SIDECAR, .state = CTR_RUNNING};

    /* main 退出 0 */
    container_exit(&p, 0, 0);
    /* sidecar 退出 0(sidecar 仍应按 Always 重启) */
    container_exit(&p, 1, 0);

    printf("After both exit 0:\n");
    printf("  main    : restart_count=%d, backoff=%ds → %s\n",
           p.containers[0].restart_count, p.containers[0].current_backoff,
           p.containers[0].restart_count > 0 ? "restarting" : "Terminated");
    printf("  sidecar : restart_count=%d, backoff=%ds → %s\n",
           p.containers[1].restart_count, p.containers[1].current_backoff,
           p.containers[1].restart_count > 0 ? "restarting" : "Terminated");
}

/* ============== main ============== */
int main(void) {
    demo1_basic_state_machine();
    demo2_policy_comparison();
    demo3_exponential_backoff();
    demo4_sidecar_independence();
    return 0;
}
