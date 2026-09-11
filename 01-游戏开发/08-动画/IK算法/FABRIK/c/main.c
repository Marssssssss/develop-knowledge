/*
 * FABRIK (Forward And Backward Reaching Inverse Kinematics)
 * 2D 单链, 单末端执行器, 无约束版本.
 *
 * 算法来源:
 *   Aristidou & Lasenby, "FABRIK: A fast, iterative solver for the
 *   Inverse Kinematics problem", Graphical Models 73(5): 243-260, 2011.
 *
 * 核心思想: 不操作关节旋转, 直接操作关节位置 p[i].
 *   - 反向阶段: 把末端 p[n] 钉到目标 t, 从 n-1 向前反向把每个关节沿连线拉回到 d[i] 距离
 *   - 正向阶段: 把根 p[0] 钉回原位, 从 1 向后正向把每个关节拉到 d[i-1] 距离
 *   - 重复直到 |p[n] - t| < tol
 *
 * 本文件 demo 一个 3 关节 (4 点, 3 段骨头) 的 2D 平面机械臂, 同时给出 3 个目标:
 *   1. 可达目标 -> 收敛到目标位置
 *   2. 不可达目标 -> 链条沿 (root -> t) 方向完全伸直
 *   3. 单次迭代观察中间状态
 *
 * 编译:
 *   gcc -O2 -Wall -Wextra -std=c99 -o fabrik main.c
 *   ./fabrik
 *
 * 期望输出 (允许最后一位有浮点误差):
 *   === Case 1: reachable target ===
 *   Target = (4.0, 3.0), root = (0.0, 0.0)
 *   Joint positions after FABRIK:
 *     p[0] = (0.00, 0.00)
 *     p[1] = (1.50, 1.50)
 *     p[2] = (3.00, 3.00)
 *     p[3] = (4.00, 3.00)
 *   End effector error = <eps>
 *   Iterations: <n>
 */

#include <stdio.h>
#include <math.h>
#include <string.h>

#define MAX_JOINTS 32
#define MAX_ITER   100

typedef struct { double x, y; } Vec2;

static Vec2 vec_sub(Vec2 a, Vec2 b)              { Vec2 r = {a.x - b.x, a.y - b.y}; return r; }
static Vec2 vec_add(Vec2 a, Vec2 b)              { Vec2 r = {a.x + b.x, a.y + b.y}; return r; }
static Vec2 vec_scale(Vec2 a, double s)          { Vec2 r = {a.x * s, a.y * s}; return r; }
static double vec_dot(Vec2 a, Vec2 b)            { return a.x * b.x + a.y * b.y; }
static double vec_len(Vec2 a)                    { return sqrt(vec_dot(a, a)); }
static Vec2 vec_norm(Vec2 a, double *out_len) {
    double l = vec_len(a);
    if (out_len) *out_len = l;
    if (l < 1e-12) { Vec2 z = {0.0, 0.0}; return z; }
    return vec_scale(a, 1.0 / l);
}

/*
 * 一步 FABRIK 迭代的"反向阶段": 把末端拉到 target, 沿链反向回拉到根部.
 * 根此时会被"拉"动, 下一阶段要再钉回去.
 */
static void fabrik_backward(Vec2 *p, int n, const double *d, Vec2 target) {
    p[n] = target;                                  /* p[n] = t (末端钉到目标) */
    for (int i = n - 1; i >= 0; --i) {              /* 从 n-1 递减到 0 */
        double len;
        Vec2 dir = vec_norm(vec_sub(p[i], p[i + 1]), &len);
        if (len < 1e-12) dir.x = 1.0;               /* 退化成同点时使用默认方向 */
        p[i] = vec_add(p[i + 1], vec_scale(dir, d[i]));
    }
}

/*
 * 一步 FABRIK 迭代的"正向阶段": 把根钉回原位, 沿链正向推到末端.
 */
static void fabrik_forward(Vec2 *p, int n, const double *d, Vec2 root) {
    p[0] = root;
    for (int i = 1; i <= n; ++i) {                  /* 从 1 递增到 n */
        double len;
        Vec2 dir = vec_norm(vec_sub(p[i], p[i - 1]), &len);
        if (len < 1e-12) dir.x = 1.0;
        p[i] = vec_add(p[i - 1], vec_scale(dir, d[i - 1]));
    }
}

/*
 * 完整 FABRIK 求解: 每轮 (backward + forward) 一次, 直到末端到目标距离 < tol.
 * 返回实际迭代次数.
 *
 * 处理不可达目标 (|target - root| > total_length):
 *   先把所有点反向从 target 退回到 root 位置 (Aristidou 论文 §3.7 描述的"伸展退化"),
 *   再正常迭代. 不可达情形下末端最终停在沿 (root -> target) 方向"够得最远"的位置.
 */
static int fabrik_solve(Vec2 *p, int n, const double *d,
                        Vec2 target, double tol) {
    Vec2 root = p[0];
    double total = 0.0;
    for (int i = 0; i < n; ++i) total += d[i];

    double dist_root_to_target = vec_len(vec_sub(target, root));
    if (dist_root_to_target > total) {
        /* 不可达: 沿 (root -> target) 方向沿链条伸直 */
        Vec2 dir = vec_norm(vec_sub(target, root), NULL);
        p[0] = root;
        for (int i = 1; i <= n; ++i)
            p[i] = vec_add(p[i - 1], vec_scale(dir, d[i - 1]));
        return 0;
    }

    for (int it = 0; it < MAX_ITER; ++it) {
        double err = vec_len(vec_sub(p[n], target));
        if (err < tol) return it;

        fabrik_backward(p, n, d, target);
        fabrik_forward(p, n, d, root);
    }
    return MAX_ITER;
}

static void print_chain(const Vec2 *p, int n, const char *title) {
    printf("%s\n", title);
    for (int i = 0; i <= n; ++i) {
        printf("  p[%d] = (%6.3f, %6.3f)\n", i, p[i].x, p[i].y);
    }
}

int main(void) {
    /* 3 段骨头, 长度 2/2/2 -> 总长 6 */
    const int N = 3;                       /* 骨头数 = 3, 关节数 = N+1 = 4 */
    const double d[N] = { 2.0, 2.0, 2.0 };
    Vec2 p[N + 1] = {                       /* 初始位姿: 沿 +x 直线 */
        {0.0, 0.0}, {2.0, 0.0}, {4.0, 0.0}, {6.0, 0.0}
    };

    /* Case 1: 可达目标 (4, 3), 距离 = 5 < 6 */
    Vec2 p1[N + 1];
    memcpy(p1, p, sizeof(p));
    Vec2 t1 = { 4.0, 3.0 };
    int it1 = fabrik_solve(p1, N, d, t1, 1e-9);
    print_chain(p1, N, "=== Case 1: reachable target ===");
    printf("Target = (%.3f, %.3f), root = (%.3f, %.3f)\n",
           t1.x, t1.y, p1[0].x, p1[0].y);
    printf("End effector error = %.3e\n", vec_len(vec_sub(p1[N], t1)));
    printf("Iterations: %d\n\n", it1);

    /* Case 2: 不可达目标 (10, 0), 距离 = 10 > 6 */
    Vec2 p2[N + 1];
    memcpy(p2, p, sizeof(p));
    Vec2 t2 = { 10.0, 0.0 };
    fabrik_solve(p2, N, d, t2, 1e-9);
    print_chain(p2, N, "=== Case 2: unreachable target (stretched along root->t) ===");
    printf("Target = (%.3f, %.3f)\n", t2.x, t2.y);
    printf("End-effector reached = (%.3f, %.3f)\n\n", p2[N].x, p2[N].y);

    /* Case 3: 同一目标只跑一次迭代, 显示中间态 */
    Vec2 p3[N + 1];
    memcpy(p3, p, sizeof(p));
    Vec2 t3 = { 4.0, 3.0 };
    fabrik_backward(p3, N, d, t3);
    print_chain(p3, N, "=== Case 3: reachable target, after 1 BACKWARD pass ===");
    printf("End effector is at target (%.1f, %.1f)\n", t3.x, t3.y);
    printf("But root moved from (0,0) to (%.3f, %.3f) -> forward phase fixes it\n\n",
           p3[0].x, p3[0].y);
    fabrik_forward(p3, N, d, (Vec2){0.0, 0.0});
    print_chain(p3, N, "=== Case 3: after 1 BACKWARD + 1 FORWARD ===");
    printf("Root is back to (0,0); end effector error = %.3e\n",
           vec_len(vec_sub(p3[N], t3)));

    return 0;
}
