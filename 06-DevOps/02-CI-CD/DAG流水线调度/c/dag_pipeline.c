/* dag_pipeline.c — DAG 流水线调度(Kahn 拓扑排序 + 关键路径 + 失败传播)
 *
 * 模拟 GitLab CI needs / GitHub Actions needs 的 DAG 调度核心:
 *   demo 1 菱形依赖分层  demo 2 环检测  demo 3 barrier vs DAG  demo 4 失败传播
 */
#include <stdio.h>
#include <string.h>

#define MAX_JOBS 16
#define MAX_NEEDS 8

typedef struct {
    const char *name;
    const char *stage;
    int dur;                     /* 模拟时长(分钟) */
    int needs[MAX_NEEDS];        /* 依赖的 job 下标 */
    int n_needs;
} Job;

/* 状态机:待定 -> 成功/失败;依赖失败者 -> 跳过 */
enum { PENDING, SUCCESS, FAILED, SKIPPED };

static Job jobs[MAX_JOBS];
static int n_jobs;

static int find_job(const char *name)
{
    for (int i = 0; i < n_jobs; i++)
        if (strcmp(jobs[i].name, name) == 0)
            return i;
    return -1;
}

static void add_job(const char *name, const char *stage, int dur,
                    const char *const needs[], int n_needs)
{
    Job *j = &jobs[n_jobs];
    j->name = name;
    j->stage = stage;
    j->dur = dur;
    j->n_needs = n_needs;
    for (int i = 0; i < n_needs; i++) {
        int idx = find_job(needs[i]);
        if (idx < 0) {
            fprintf(stderr, "job '%s' depends on unknown job '%s'\n",
                    name, needs[i]);
            return;
        }
        j->needs[i] = idx;
    }
    n_jobs++;
}

/* Kahn 分层拓扑排序:layers[i] 存第 i 层 job 下标;返回层数,-1 表示有环 */
static int topo_layers(int layers[][MAX_JOBS], int layer_len[])
{
    int in_deg[MAX_JOBS] = {0};
    int succ[MAX_JOBS][MAX_JOBS];
    int queue[MAX_JOBS], q_len = 0;

    memset(succ, 0, sizeof(succ));
    for (int j = 0; j < n_jobs; j++) {
        for (int k = 0; k < jobs[j].n_needs; k++) {
            succ[jobs[j].needs[k]][j] = 1;   /* 边 need -> j */
            in_deg[j]++;
        }
    }
    for (int j = 0; j < n_jobs; j++)
        if (in_deg[j] == 0)
            queue[q_len++] = j;              /* 第 0 层入队 */

    int n_layers = 0, done = 0;
    while (q_len > 0) {
        /* 保证可复现:层内按下标升序(注册顺序即拓扑输入顺序) */
        for (int a = 1; a < q_len; a++)
            for (int b = 0; b < a; b++)
                if (queue[a] < queue[b]) {
                    int t = queue[a]; queue[a] = queue[b]; queue[b] = t;
                }
        layer_len[n_layers] = q_len;
        memcpy(layers[n_layers], queue, sizeof(int) * (size_t)q_len);
        done += q_len;
        n_layers++;

        int nxt[MAX_JOBS], n_len = 0;
        for (int q = 0; q < q_len; q++) {    /* 这一批 job "完成" */
            int u = queue[q];
            for (int v = 0; v < n_jobs; v++)
                if (succ[u][v] && --in_deg[v] == 0)
                    nxt[n_len++] = v;        /* 删出边 = 入度减 1 */
        }
        memcpy(queue, nxt, sizeof(int) * (size_t)n_len);
        q_len = n_len;
    }
    return done == n_jobs ? n_layers : -1;   /* 出队数 < 节点数 => 有环 */
}

/* 关键路径递推:ef[j] = dur[j] + max(ef[need]) */
static void earliest_finish(int ef[])
{
    int layers[MAX_JOBS][MAX_JOBS], layer_len[MAX_JOBS];
    int n_layers = topo_layers(layers, layer_len);
    if (n_layers < 0)
        return;
    for (int l = 0; l < n_layers; l++)
        for (int q = 0; q < layer_len[l]; q++) {
            int j = layers[l][q], best = 0;
            for (int k = 0; k < jobs[j].n_needs; k++) {
                int p = jobs[j].needs[k];
                if (ef[p] > best)
                    best = ef[p];
            }
            ef[j] = jobs[j].dur + best;
        }
}

/* stage barrier:每个 stage 取最大时长再求和 */
static int stage_barrier_time(void)
{
    int total = 0, seen[MAX_JOBS] = {0};
    for (int s = 0; s < n_jobs; s++) {
        if (seen[s])
            continue;
        int max_dur = 0;
        for (int j = 0; j < n_jobs; j++)
            if (strcmp(jobs[j].stage, jobs[s].stage) == 0) {
                seen[j] = 1;
                if (jobs[j].dur > max_dur)
                    max_dur = jobs[j].dur;
            }
        total += max_dur;
    }
    return total;
}

/* 按拓扑序执行;fail_mask[i]=1 的 job 失败,传递依赖者被跳过 */
static void simulate_run(const int fail_mask[])
{
    int layers[MAX_JOBS][MAX_JOBS], layer_len[MAX_JOBS];
    int status[MAX_JOBS];
    memset(status, PENDING, sizeof(status));
    int n_layers = topo_layers(layers, layer_len);
    if (n_layers < 0)
        return;
    for (int l = 0; l < n_layers; l++)
        for (int q = 0; q < layer_len[l]; q++) {
            int j = layers[l][q], bad = -1;
            for (int k = 0; k < jobs[j].n_needs; k++) {
                int p = jobs[j].needs[k];
                if (status[p] == FAILED || status[p] == SKIPPED)
                    bad = p;
            }
            if (bad >= 0) {
                status[j] = SKIPPED;
                printf("  [skip]  %-10s SKIPPED (need '%s')\n",
                       jobs[j].name, jobs[bad].name);
            } else if (fail_mask[j]) {
                status[j] = FAILED;
                printf("  [fail]  %-10s FAILED  (exit code 1)\n",
                       jobs[j].name);
            } else {
                status[j] = SUCCESS;
                printf("  [ok]    %-10s SUCCESS (%d min)\n",
                       jobs[j].name, jobs[j].dur);
            }
        }
}

static void reset_jobs(void) { n_jobs = 0; }

int main(void)
{
    /* demo 1: 菱形依赖 */
    printf("== demo 1: 菱形依赖(DAG) ==\n");
    reset_jobs();
    add_job("build", "build", 3, NULL, 0);
    add_job("test_unit", "test", 2, (const char *[]){"build"}, 1);
    add_job("test_intg", "test", 4, (const char *[]){"build"}, 1);
    add_job("test_perf", "test", 5, (const char *[]){"build"}, 1);
    add_job("deploy", "deploy", 2,
            (const char *[]){"test_unit", "test_intg", "test_perf"}, 3);
    {
        int layers[MAX_JOBS][MAX_JOBS], layer_len[MAX_JOBS], ef[MAX_JOBS];
        int n_layers = topo_layers(layers, layer_len);
        for (int l = 0; l < n_layers; l++) {
            printf("  layer %d: [", l);
            for (int q = 0; q < layer_len[l]; q++)
                printf("%s%s", q ? ", " : "",
                       jobs[layers[l][q]].name);
            printf("]\n");
        }
        earliest_finish(ef);
        int best = 0;
        for (int j = 1; j < n_jobs; j++)
            if (ef[j] > ef[best])
                best = j;
        printf("  critical path end: %s (wall-clock lower bound = %d min)\n",
               jobs[best].name, ef[best]);
    }

    /* demo 2: needs 成环 */
    printf("\n== demo 2: needs 成环(创建即失败) ==\n");
    reset_jobs();
    add_job("build", "build", 1, (const char *[]){"deploy"}, 1);
    add_job("test", "test", 1, (const char *[]){"build"}, 1);
    add_job("deploy", "deploy", 1, (const char *[]){"test"}, 1);
    {
        int layers[MAX_JOBS][MAX_JOBS], layer_len[MAX_JOBS];
        if (topo_layers(layers, layer_len) < 0) {
            printf("  pipeline creation failed: cycle detected among"
                   " jobs: build, deploy, test\n");
        }
    }

    /* demo 3: stage barrier vs DAG(GitLab 博客算例) */
    printf("\n== demo 3: stage barrier vs DAG ==\n");
    reset_jobs();
    add_job("build_a", "build", 1, NULL, 0);
    add_job("build_b", "build", 5, NULL, 0);
    add_job("test_c", "test", 2, (const char *[]){"build_a"}, 1);
    add_job("deploy", "deploy", 1, (const char *[]){"test_c"}, 1);
    {
        int ef[MAX_JOBS] = {0};
        int barrier = stage_barrier_time();
        earliest_finish(ef);
        int dag = ef[0];
        for (int j = 1; j < n_jobs; j++)
            if (ef[j] > dag)
                dag = ef[j];
        printf("  stage barrier: %d min  (build 5 + test 2 + deploy 1)\n",
               barrier);
        printf("  DAG(needs):    %d min   (critical path"
               " build_a -> test_c -> deploy)\n", dag);
        printf("  节省: %d min — test_c 无需等 build_b\n", barrier - dag);
    }

    /* demo 4: 失败传播 */
    printf("\n== demo 4: 失败传播(test_a 失败 -> deploy_a 跳过) ==\n");
    reset_jobs();
    add_job("build", "build", 1, NULL, 0);
    add_job("test_a", "test", 2, (const char *[]){"build"}, 1);
    add_job("test_b", "test", 2, (const char *[]){"build"}, 1);
    add_job("deploy_a", "deploy", 1, (const char *[]){"test_a"}, 1);
    add_job("deploy_b", "deploy", 1, (const char *[]){"test_b"}, 1);
    {
        int fail_mask[MAX_JOBS] = {0};
        fail_mask[find_job("test_a")] = 1;
        simulate_run(fail_mask);
    }
    return 0;
}
