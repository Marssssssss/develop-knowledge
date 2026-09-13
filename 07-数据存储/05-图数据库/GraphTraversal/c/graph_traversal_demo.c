/* 图遍历：BFS / DFS / Dijkstra —— C99 版。
 *
 * 权威来源：
 *   - Wikipedia "Dijkstra's algorithm"
 *     https://en.wikipedia.org/wiki/Dijkstra%27s_algorithm
 *   - Czech Technical University Programming for Engineers L8 Recursion
 *     https://cw.fel.cvut.cz/b252/_media/courses/be5b33pge/lectures/l8_recursion.pdf
 *
 * 邻接表用 (src, dst, weight) 边数组 + 每个 src 的头节点表。
 * Dijkstra 用手写的最小堆（与 Go container/heap 对应）。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

#define MAXV 64
#define MAXE 256

/* ──── 边数组 / 头节点 ──── */
typedef struct {
    int to;
    int w;       /* 加权图使用；无权图 w=1 */
    int next;    /* 同 src 的下一条边在 edges[] 中的下标 */
} Edge;

static Edge edges[MAXE];
static int head[MAXV];
static int ecnt;

static void add_edge(int u, int v, int w) {
    edges[ecnt].to = v;
    edges[ecnt].w = w;
    edges[ecnt].next = head[u];
    head[u] = ecnt;
    ecnt++;
}

/* ──── BFS（无权，按层） ──── */
static void bfs(int start, int V, int *order, int *outlen) {
    static int visited[MAXV];
    static int q[MAXV];
    int fr = 0, bk = 0;
    memset(visited, 0, sizeof(visited));
    visited[start] = 1;
    q[bk++] = start;
    while (fr < bk) {
        int u = q[fr++];
        order[(*outlen)++] = u;
        for (int e = head[u]; e != -1; e = edges[e].next) {
            int v = edges[e].to;
            if (!visited[v]) { visited[v] = 1; q[bk++] = v; }
        }
    }
}

/* ──── DFS（递归） ──── */
static int dfs_visited[MAXV];
static void dfs(int u, int *order, int *outlen) {
    dfs_visited[u] = 1;
    order[(*outlen)++] = u;
    for (int e = head[u]; e != -1; e = edges[e].next) {
        int v = edges[e].to;
        if (!dfs_visited[v]) dfs(v, order, outlen);
    }
}

/* ──── 最小堆（用于 Dijkstra） ──── */
typedef struct {
    int node;
    int dist;
} HNode;

static HNode heap[MAXE];
static int hsz;

static void hswap(int i, int j) {
    HNode t = heap[i]; heap[i] = heap[j]; heap[j] = t;
}
static void hup(int i) {
    while (i > 0) {
        int p = (i - 1) >> 1;
        if (heap[p].dist <= heap[i].dist) break;
        hswap(p, i); i = p;
    }
}
static void hdown(int i) {
    for (;;) {
        int l = 2 * i + 1, r = l + 1, m = i;
        if (l < hsz && heap[l].dist < heap[m].dist) m = l;
        if (r < hsz && heap[r].dist < heap[m].dist) m = r;
        if (m == i) break;
        hswap(i, m); i = m;
    }
}
static void hpush(int node, int dist) {
    heap[hsz].node = node; heap[hsz].dist = dist;
    hup(hsz); hsz++;
}
static HNode hpop(void) {
    HNode top = heap[0];
    hsz--;
    if (hsz > 0) { heap[0] = heap[hsz]; hdown(0); }
    return top;
}

/* ──── Dijkstra：返回 dist 数组（INT_MAX 表示不可达） ──── */
static void dijkstra(int start, int V, int *dist) {
    for (int i = 0; i < V; i++) dist[i] = INT_MAX;
    dist[start] = 0;
    hsz = 0;
    hpush(start, 0);
    while (hsz > 0) {
        HNode it = hpop();
        int u = it.node, d = it.dist;
        if (d > dist[u]) continue;
        for (int e = head[u]; e != -1; e = edges[e].next) {
            int v = edges[e].to, w = edges[e].w;
            int nd = d + w;
            if (nd < dist[v]) { dist[v] = nd; hpush(v, nd); }
        }
    }
}

int main(void) {
    memset(head, -1, sizeof(head));

    /* 7 节点示例（与 Python 版一致）：
     * 1 -> 2(8), 3(1)
     * 2 -> 5(5)
     * 3 -> 2(4), 4(2)
     * 4 -> 2(1), 5(3)
     */
    add_edge(1, 2, 8); add_edge(1, 3, 1);
    add_edge(2, 5, 5);
    add_edge(3, 2, 4); add_edge(3, 4, 2);
    add_edge(4, 2, 1); add_edge(4, 5, 3);

    int order[MAXV], n = 0;
    memset(dfs_visited, 0, sizeof(dfs_visited));

    bfs(1, 6, order, &n);
    printf("BFS from 1 :");
    for (int i = 0; i < n; i++) printf(" %d", order[i]);
    printf("\n");

    n = 0; dfs(1, order, &n);
    printf("DFS from 1 :");
    for (int i = 0; i < n; i++) printf(" %d", order[i]);
    printf("\n");

    int dist[MAXV];
    dijkstra(1, 6, dist);
    printf("Dijkstra(1) :");
    for (int i = 1; i < 6; i++) printf(" %d=%d", i, dist[i]);
    printf("\n");

    printf("复杂度：BFS/DFS 都是 O(V+E) 时间 O(V) 空间；Dijkstra O((V+E) log V)\n");
    return 0;
}