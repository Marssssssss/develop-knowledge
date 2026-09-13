// Terraform 资源依赖图与拓扑排序 (C 版)
//
// 来源:
// - Terraform Internals: Dependency Graph
//   (docs.hashicorp.com/terraform/internals/v1.15.x/graph)
// - 关键句:"graph walking is done in parallel: a node is walked as soon
//   as all of its dependencies are walked. By default, up to 10 nodes
//   in the graph will be processed concurrently"
// - "Validate the graph has no cycles and has a single root"
//
// 实现:
// - 邻接表 + 入度数组
// - Kahn 拓扑排序 + 分层 (每层 = 并行批次)
// - 环检测(Kahn 出队数 < 节点数 = 有环)
// - parallelism 上限模拟 `terraform apply -parallelism=N`

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_NODES 64
#define NAME_LEN  32

typedef struct {
    char names[MAX_NODES][NAME_LEN];
    int  n;
    /* edges[i][k] = 第 i 个节点的目标节点下标列表, edges_cnt[i] = 出边数 */
    int  edges[MAX_NODES][MAX_NODES];
    int  edges_cnt[MAX_NODES];
    /* in_degree[i] = 入边数 */
    int  in_degree[MAX_NODES];
} Graph;

static int idx(Graph *g, const char *name) {
    for (int i = 0; i < g->n; i++) {
        if (strcmp(g->names[i], name) == 0) return i;
    }
    return -1;
}

static int add_node(Graph *g, const char *name) {
    int i = idx(g, name);
    if (i >= 0) return i;
    if (g->n >= MAX_NODES) { fprintf(stderr, "node overflow\n"); exit(1); }
    strncpy(g->names[g->n], name, NAME_LEN - 1);
    g->names[g->n][NAME_LEN - 1] = 0;
    return g->n++;
}

static void add_edge(Graph *g, const char *frm, const char *to) {
    int u = add_node(g, frm);
    int v = add_node(g, to);
    /* 防重复加边 */
    for (int k = 0; k < g->edges_cnt[u]; k++) {
        if (g->edges[u][k] == v) return;
    }
    g->edges[u][g->edges_cnt[u]++] = v;
}

static int cmp_str(const void *a, const void *b) {
    return strcmp(*(const char **)a, *(const char **)b);
}

/* 计算入度(全图) */
static void calc_in_degree(Graph *g) {
    for (int i = 0; i < g->n; i++) g->in_degree[i] = 0;
    for (int u = 0; u < g->n; u++) {
        for (int k = 0; k < g->edges_cnt[u]; k++) {
            int v = g->edges[u][k];
            g->in_degree[v]++;
        }
    }
}

/* Kahn + 分层;返回层数;has_cycle 通过 out 参数返回 1/0 */
static int topo_layers(Graph *g, int layers[][MAX_NODES], int *has_cycle) {
    calc_in_degree(g);
    int visited = 0, n_layers = 0;

    while (1) {
        /* 当前层 = 所有入度为 0 的节点 */
        int layer_sz = 0;
        for (int i = 0; i < g->n; i++) {
            if (g->in_degree[i] == 0) {
                layers[n_layers][layer_sz++] = i;
            }
        }
        if (layer_sz == 0) break;
        /* 同层按名字排序,确定性 */
        const char *names[MAX_NODES];
        for (int i = 0; i < layer_sz; i++) names[i] = g->names[layers[n_layers][i]];
        qsort(names, layer_sz, sizeof(char *), cmp_str);
        int sorted[MAX_NODES];
        for (int i = 0; i < layer_sz; i++) sorted[i] = idx(g, names[i]);
        memcpy(layers[n_layers], sorted, sizeof(int) * layer_sz);

        /* 出队本层节点 */
        for (int k = 0; k < layer_sz; k++) {
            int u = layers[n_layers][k];
            g->in_degree[u] = -1; /* 标记已访问 */
            visited++;
            for (int e = 0; e < g->edges_cnt[u]; e++) {
                int v = g->edges[u][e];
                if (g->in_degree[v] > 0) g->in_degree[v]--;
            }
        }
        /* 记录该层大小:复用 layers[n_layers][0..layer_sz-1] 已知,下面用 n_layers_sz 数组不便,
           改用单独的 layer_sz 数组: */
        n_layers++;
        /* 占位 */
    }
    *has_cycle = (visited < g->n) ? 1 : 0;
    return n_layers;
}

/* 简化:把层大小直接保存在 layers_sz 数组 */
static void topo_with_sizes(Graph *g, int layers[][MAX_NODES], int *layer_sz_arr) {
    calc_in_degree(g);
    int n_layers = 0, visited = 0;
    while (1) {
        int sz = 0;
        for (int i = 0; i < g->n; i++) {
            if (g->in_degree[i] == 0) layers[n_layers][sz++] = i;
        }
        if (sz == 0) break;
        /* sort */
        const char *names[MAX_NODES];
        for (int i = 0; i < sz; i++) names[i] = g->names[layers[n_layers][i]];
        qsort(names, sz, sizeof(char *), cmp_str);
        for (int i = 0; i < sz; i++) layers[n_layers][i] = idx(g, names[i]);

        layer_sz_arr[n_layers] = sz;
        for (int k = 0; k < sz; k++) {
            int u = layers[n_layers][k];
            g->in_degree[u] = -1;
            visited++;
            for (int e = 0; e < g->edges_cnt[u]; e++) {
                int v = g->edges[u][e];
                if (g->in_degree[v] > 0) g->in_degree[v]--;
            }
        }
        n_layers++;
    }
    if (visited < g->n) {
        fprintf(stderr, "[cycle detected] visited %d of %d\n", visited, g->n);
        layer_sz_arr[0] = 0; /* 标记失败 */
    }
}

/* parallelism:把每层按 max_par 切片 */
static void schedule_parallel(int layers[][MAX_NODES], int *layer_sz, int n_layers, int max_par) {
    printf("  schedule (-parallelism=%d):\n", max_par);
    int batch = 0;
    for (int i = 0; i < n_layers; i++) {
        int sz = layer_sz[i];
        for (int off = 0; off < sz; off += max_par) {
            printf("    batch %2d: ", ++batch);
            int end = off + max_par < sz ? off + max_par : sz;
            for (int j = off; j < end; j++) {
                printf("%s%s", layers[i][j], j + 1 < end ? ", " : "");
            }
            printf("\n");
        }
    }
}

// -----------------------------------------------------------------------------
// Demo
// -----------------------------------------------------------------------------

static void demo_diamond(Graph *g) {
    add_edge(g, "aws_vpc.main", "aws_subnet.public");
    add_edge(g, "aws_vpc.main", "aws_security_group.web");
    add_edge(g, "aws_subnet.public", "aws_instance.web");
    add_edge(g, "aws_security_group.web", "aws_instance.web");
}

static void demo_explicit(Graph *g) {
    /* IAM role policy 必须先 attached 再 instance assume */
    add_edge(g, "aws_iam_role.app", "aws_iam_instance_profile.app");
    add_edge(g, "aws_iam_role.app", "aws_iam_role_policy_attachment.s3_access");
    add_edge(g, "aws_iam_role_policy_attachment.s3_access", "aws_instance.app");
    add_edge(g, "aws_iam_instance_profile.app", "aws_instance.app");
}

static void demo_cycle(Graph *g) {
    add_edge(g, "A", "B");
    add_edge(g, "B", "C");
    add_edge(g, "C", "A");
}

static void demo_parallel(Graph *g) {
    char buf[32];
    for (int i = 0; i < 11; i++) {
        snprintf(buf, sizeof(buf), "res_%d", i);
        add_edge(g, buf, "aggregator");
    }
}

int main(void) {
    Graph g = {0};
    int layers[MAX_NODES][MAX_NODES];
    int layer_sz[MAX_NODES];

    printf("=== Terraform 资源依赖图与拓扑排序 demo (C 版) ===\n\n");

    printf("--- Demo 1: 菱形 (VPC + Subnet+SG 并行 → Instance) ---\n");
    demo_diamond(&g);
    topo_with_sizes(&g, layers, layer_sz);
    int n_layers = 0;
    while (layer_sz[n_layers] > 0) n_layers++;
    for (int i = 0; i < n_layers; i++) {
        printf("  Layer %d (并行): ", i + 1);
        for (int j = 0; j < layer_sz[i]; j++) {
            printf("%s%s", g.names[layers[i][j]], j + 1 < layer_sz[i] ? ", " : "");
        }
        printf("\n");
    }
    printf("\n");

    printf("--- Demo 2: depends_on 显式依赖 (IAM role → policy → instance) ---\n");
    memset(&g, 0, sizeof g);
    demo_explicit(&g);
    topo_with_sizes(&g, layers, layer_sz);
    n_layers = 0;
    while (layer_sz[n_layers] > 0) n_layers++;
    for (int i = 0; i < n_layers; i++) {
        printf("  Layer %d: ", i + 1);
        for (int j = 0; j < layer_sz[i]; j++) {
            printf("%s%s", g.names[layers[i][j]], j + 1 < layer_sz[i] ? ", " : "");
        }
        printf("\n");
    }
    printf("\n");

    printf("--- Demo 3: 环检测 (A → B → C → A) ---\n");
    memset(&g, 0, sizeof g);
    demo_cycle(&g);
    calc_in_degree(&g);
    topo_with_sizes(&g, layers, layer_sz);
    if (layer_sz[0] == 0) printf("  ✓ 正确识别环,终止\n");
    printf("\n");

    printf("--- Demo 4: parallelism=10 跨批 (11 个无依赖 + 1 聚合) ---\n");
    memset(&g, 0, sizeof g);
    demo_parallel(&g);
    topo_with_sizes(&g, layers, layer_sz);
    n_layers = 0;
    while (layer_sz[n_layers] > 0) n_layers++;
    schedule_parallel(layers, layer_sz, n_layers, 10);
    printf("\n");

    return 0;
}
