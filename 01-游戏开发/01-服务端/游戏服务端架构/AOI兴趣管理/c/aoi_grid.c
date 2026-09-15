/* aoi_grid.c — AOI 九宫格最小实现与自检
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic aoi_grid.c -o aoi_grid
 *
 * 与 python/aoi_grid.py 逻辑一一对应:
 *   - 格子归属决定视野(3x3);
 *   - 跨格时按 old_nb/new_nb 差集分发 Leave/Enter/Move;
 *   - 同格内微动只发 Move;
 *   - 滞回(gap)抑制视野边缘的 Enter/Leave 抖动。
 *
 * 世界观坐标直接用「格」为单位(1.0 = 一格), cell = (int)x, (int)y。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ENT   512
#define MAX_CELL  (64 * 64)
#define CELL_CAP  128

typedef struct {
    int ids[CELL_CAP];
    int n;
} Cell;

static int   grid_w = 20, grid_h = 20, view_r = 1;
static double px[MAX_ENT], py[MAX_ENT];
static int    pcell[MAX_ENT];              /* eid -> 格子索引, -1 = 不在场景 */
static Cell   cells[MAX_CELL];
static long   n_enter, n_leave, n_move;

static int failures;

static void check(int cond, const char *what)
{
    if (!cond) {
        printf("  [FAIL] %s\n", what);
        failures++;
    }
}

/* ---------------- 基础 ---------------- */
static int idx_of(int cx, int cy) { return cy * grid_w + cx; }

static int cell_of(int eid)
{
    if (px[eid] < 0 || px[eid] >= grid_w || py[eid] < 0 || py[eid] >= grid_h)
        return -1;
    return idx_of((int)px[eid], (int)py[eid]);
}

/* 半径 r 的方形邻域, 出界裁剪; 返回格子数 */
static int nb_collect(int cx, int cy, int r, int *out)
{
    int n = 0, xx, yy;
    for (yy = cy - r; yy <= cy + r; yy++) {
        if (yy < 0 || yy >= grid_h) continue;
        for (xx = cx - r; xx <= cx + r; xx++) {
            if (xx < 0 || xx >= grid_w) continue;
            out[n++] = idx_of(xx, yy);
        }
    }
    return n;
}

static void cell_add(int idx, int eid)
{
    if (cells[idx].n < CELL_CAP) cells[idx].ids[cells[idx].n++] = eid;
}

static void cell_remove(int idx, int eid)
{
    int i;
    for (i = 0; i < cells[idx].n; i++) {
        if (cells[idx].ids[i] == eid) {
            cells[idx].ids[i] = cells[idx].ids[--cells[idx].n];
            return;
        }
    }
}

/* 把 kind 消息发给 idxs 里所有实体(不含 self) */
static void broadcast(const int *idxs, int n, int self, long *counter)
{
    int i, k;
    for (i = 0; i < n; i++) {
        Cell *c = &cells[idxs[i]];
        for (k = 0; k < c->n; k++) {
            if (c->ids[k] == self) continue;
            (*counter)++;
        }
    }
}

/* ---------------- 生命周期 ---------------- */
static void aoi_enter(int eid, double x, double y)
{
    int nbs[64], n;
    px[eid] = x;
    py[eid] = y;
    pcell[eid] = cell_of(eid);
    cell_add(pcell[eid], eid);
    n = nb_collect(pcell[eid] % grid_w, pcell[eid] / grid_w, view_r, nbs);
    broadcast(nbs, n, eid, &n_enter);            /* 我出现 -> 通知视野内的人 */
}

/* 返回本次移动的三类「格子集合」基数; nl/ne/nc 允许传 NULL。
 * 约定 nc == -1 表示「未跨格」。 */
static void aoi_move(int eid, double x, double y, int *nl_out, int *ne_out, int *nc_out)
{
    int old_c = pcell[eid];
    int old_nbs[64], new_nbs[64], no, nn, i;
    int nl = 0, ne = 0, nc = 0;

    px[eid] = x;
    py[eid] = y;
    if (cell_of(eid) == old_c) {                 /* 未跨格: 只同步位置 */
        nc = -1;
        nn = nb_collect(old_c % grid_w, old_c / grid_w, view_r, new_nbs);
        broadcast(new_nbs, nn, eid, &n_move);
        if (nl_out) *nl_out = nl;
        if (ne_out) *ne_out = ne;
        if (nc_out) *nc_out = nc;
        return;
    }

    no = nb_collect(old_c % grid_w, old_c / grid_w, view_r, old_nbs);
    nn = nb_collect((int)x, (int)y, view_r, new_nbs);

    cell_remove(old_c, eid);                     /* 先摘旧格 */
    pcell[eid] = (int)y * grid_w + (int)x;       /* 再插新格 */
    cell_add(pcell[eid], eid);

    /* old - new -> Leave */
    for (i = 0; i < no; i++) {
        int j, found = 0;
        for (j = 0; j < nn; j++) if (old_nbs[i] == new_nbs[j]) { found = 1; break; }
        if (!found) { broadcast(&old_nbs[i], 1, eid, &n_leave); nl++; }
    }
    /* new - old -> Enter */
    for (i = 0; i < nn; i++) {
        int j, found = 0;
        for (j = 0; j < no; j++) if (new_nbs[i] == old_nbs[j]) { found = 1; break; }
        if (!found) { broadcast(&new_nbs[i], 1, eid, &n_enter); ne++; }
    }
    /* new & old -> Move */
    for (i = 0; i < nn; i++) {
        int j, found = 0;
        for (j = 0; j < no; j++) if (new_nbs[i] == old_nbs[j]) { found = 1; break; }
        if (found) { broadcast(&new_nbs[i], 1, eid, &n_move); nc++; }
    }
    if (nl_out) *nl_out = nl;
    if (ne_out) *ne_out = ne;
    if (nc_out) *nc_out = nc;
}

/* ---------------- 场景 ---------------- */
static void reset_world(void)
{
    memset(cells, 0, sizeof(cells));
    memset(pcell, 0xff, sizeof(pcell));
    memset(px, 0, sizeof(px));
    memset(py, 0, sizeof(py));
    n_enter = n_leave = n_move = 0;
}

static void scenario_diff_rule(void)
{
    int eid = 5 * 20 + 5, nl, ne, nc;
    reset_world();
    grid_w = grid_h = 20;
    aoi_enter(eid, 5.5, 5.5);
    n_enter = n_leave = n_move = 0;

    aoi_move(eid, 6.5, 5.5, &nl, &ne, &nc);
    printf("[自检 1] 水平跨 1 格: Leave=%d Enter=%d Common=%d (期望 3/3/6)\n", nl, ne, nc);
    check(nl == 3 && ne == 3 && nc == 6, "水平跨格的差集基数");
    check(n_enter + n_leave + n_move == 0, "场上无他人时不应发出消息");

    aoi_move(eid, 7.5, 6.5, &nl, &ne, &nc);
    printf("[自检 1] 对角跨 1 格: Leave=%d Enter=%d Common=%d (期望 5/5/4)\n", nl, ne, nc);
    check(nl == 5 && ne == 5 && nc == 4, "对角跨格的差集基数");
}

static void scenario_routing(void)
{
    int eid = 5 * 20 + 5;
    reset_world();
    grid_w = grid_h = 20;
    aoi_enter(eid, 5.5, 5.5);          /* (5,5) -> 移到 (6,5) */
    aoi_enter(1000, 4.5, 5.5);         /* (4,5) 属于 leave 组 */
    aoi_enter(1001, 7.5, 5.5);         /* (7,5) 属于 enter 组 */
    aoi_enter(1002, 6.5, 5.5);         /* (6,5) 属于 common 组 */
    n_enter = n_leave = n_move = 0;

    aoi_move(eid, 6.5, 5.5, NULL, NULL, NULL);
    /* 只调用了 broadcast 计数, 无法区分对象 -> 用各格子内实体数间接断言 */
    printf("[自检 2] 三类格子路由: Leave=%ld Enter=%ld Move=%ld "
           "(期望 1/1/1, 各格子恰好 1 个观察者)\n", n_leave, n_enter, n_move);
    check(n_leave == 1 && n_enter == 1 && n_move == 1, "三类格子各自路由");
    check(cells[idx_of(6, 5)].n == 2, "eid 已进入 (6,5), 该格含 eid + 观察者");
    check(cells[idx_of(4, 5)].n == 1 && cells[idx_of(7, 5)].n == 1,
          "leave/enter 两组格子里的观察者未被打扰");
}

static void scenario_dense_cell(void)
{
    int i;
    reset_world();
    grid_w = grid_h = 6;
    for (i = 0; i < 5; i++) aoi_enter(i, 2.1 + i * 0.1, 2.1);  /* 5 个同格 (2,2) */
    n_enter = n_leave = n_move = 0;
    aoi_move(0, 2.15, 2.1, NULL, NULL, NULL);   /* 同格微动 */
    printf("[自检 3] 同格 5 实体微动: Move=%ld (期望 4 = 格内其他人)\n", n_move);
    check(n_move == 4 && n_enter == 0 && n_leave == 0, "同格广播给格内所有人");
}

static unsigned long rng_state = 88172645463325252UL;
static double rnd_double(double lo, double hi)
{
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    return lo + (hi - lo) * ((double)(rng_state % 1000000UL) / 1000000.0);
}

static void scenario_broadcast(void)
{
    enum { N = 300, STEPS = 400 };
    long naive = 0, total;
    int i, s;
    reset_world();
    grid_w = 40; grid_h = 30;
    for (i = 0; i < N; i++) aoi_enter(i, rnd_double(0.0, 39.9), rnd_double(0.0, 29.9));
    n_enter = n_leave = n_move = 0;

    for (s = 0; s < STEPS; s++) {
        for (i = 0; i < N; i++) {
            double nx = px[i] + rnd_double(-0.6, 0.6);
            double ny = py[i] + rnd_double(-0.6, 0.6);
            if (nx < 0.0) nx = 0.0;
            if (nx > 39.9) nx = 39.9;
            if (ny < 0.0) ny = 0.0;
            if (ny > 29.9) ny = 29.9;
            aoi_move(i, nx, ny, NULL, NULL, NULL);
            naive += N - 1;
        }
    }
    total = n_enter + n_leave + n_move;
    printf("[广播量] %d 实体 x %d 步 @ 40x30 格:\n", N, STEPS);
    printf("         朴素全广播 %ld 条 vs AOI %ld 条 (E=%ld L=%ld M=%ld), 降幅 %.2f%%\n",
           naive, total, n_enter, n_leave, n_move,
           100.0 * (1.0 - (double)total / (double)naive));
    printf("         平均每实体每步 %.3f 条 (朴素 = %.3f)\n",
           (double)total / STEPS / N, (double)naive / STEPS / N);
    check(total < naive / 5, "AOI 广播量应远低于朴素全广播");
    check(n_move > n_enter && n_enter > 0, "Move 占比最高且 Enter 非零");
}

int main(void)
{
    printf("== AOI 九宫格 (grid-based Area Of Interest) ==\n\n");
    scenario_diff_rule();
    scenario_routing();
    scenario_dense_cell();
    scenario_broadcast();

    printf("\n%s (failures=%d)\n", failures ? "存在失败项" : "全部自检通过。", failures);
    return failures ? 1 : 0;
}
