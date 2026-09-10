/*
 * gjk.c — GJK (Gilbert-Johnson-Keerthi) 凸多边形碰撞检测最小实现（2D）
 *
 * 核心原理：两凸形状 A、B 相交 <=> 它们的 Minkowski 差 A-B 包含原点。
 * GJK 不显式构造 A-B，而是通过 support 函数在其边界上取点，
 * 迭代演化一个 simplex（点 -> 线段 -> 三角形），试探能否包围原点。
 *
 * 实现依据：dyn4j "Collision Detection for Convex Shapes" 教程的
 * simplex 演化 + Voronoi 区域判断法（含评论区修正：AB = B - A）。
 *
 * 编译：gcc -O2 -Wall -Wextra gjk.c -o gjk -lm
 */
#include <math.h>
#include <stdio.h>

#define MAX_ITER 64    /* 迭代上限：退化情况下防止死循环 */
#define EPS      1e-12 /* 零向量判定阈值 */

/* ---------- 2D 向量运算 ---------- */

typedef struct {
    double x, y;
} Vec2;

static Vec2 v2(double x, double y) { Vec2 v = {x, y}; return v; }
static Vec2 vec_add(Vec2 a, Vec2 b) { return v2(a.x + b.x, a.y + b.y); }
static Vec2 vec_sub(Vec2 a, Vec2 b) { return v2(a.x - b.x, a.y - b.y); }
static Vec2 vec_neg(Vec2 a) { return v2(-a.x, -a.y); }
static Vec2 vec_mul(Vec2 a, double s) { return v2(a.x * s, a.y * s); }
static double vec_dot(Vec2 a, Vec2 b) { return a.x * b.x + a.y * b.y; }
static double vec_len2(Vec2 a) { return vec_dot(a, a); }

/* 三重积 (a x b) x c = b*(c·a) - a*(c·b)
 * 用途：求"垂直于 b、且指向 c 一侧"的向量（2D 中有唯一直接用途，
 * 3D 中这是唯一通用写法，见 dyn4j 教程 3D 扩展讨论） */
static Vec2 triple_product(Vec2 a, Vec2 b, Vec2 c)
{
    return vec_sub(vec_mul(b, vec_dot(c, a)), vec_mul(a, vec_dot(c, b)));
}

/* ---------- 凸多边形与 support 函数 ---------- */

typedef struct {
    const Vec2 *pts; /* 顶点按顺时针或逆时针排列（凸） */
    int n;
} Poly;

/* support(shape, d)：返回形状在方向 d 上投影最大的顶点。
 * GJK 唯一需要的几何接口，因此对圆/胶囊等曲边形状同样适用。 */
static Vec2 support_poly(Poly s, Vec2 d)
{
    int best = 0;
    double best_proj = vec_dot(s.pts[0], d);
    for (int i = 1; i < s.n; i++) {
        double proj = vec_dot(s.pts[i], d);
        if (proj > best_proj) {
            best_proj = proj;
            best = i;
        }
    }
    return s.pts[best];
}

/* Minkowski 差上的 support 点：S_{A-B}(d) = S_A(d) - S_B(-d)。
 * 这样完全不需要构造 A-B 的全部点（不可行，点数是 |A|*|B|）。 */
static Vec2 mink_support(Poly a, Poly b, Vec2 d)
{
    return vec_sub(support_poly(a, d), support_poly(b, vec_neg(d)));
}

/* 顶点平均质心（凸多边形可用作初始方向的参考点） */
static Vec2 centroid(Poly s)
{
    Vec2 c = v2(0, 0);
    for (int i = 0; i < s.n; i++)
        c = vec_add(c, s.pts[i]);
    return vec_mul(c, 1.0 / s.n);
}

/* ---------- Simplex（单纯形）：最多 3 个点的集合 ---------- */

typedef struct {
    Vec2 pts[3];
    int n; /* 1=点, 2=线段, 3=三角形 */
} Simplex;

static void simplex_push(Simplex *s, Vec2 p)
{
    s->pts[s->n++] = p;
}

/* 线段情形：A 为最后加入点，B 为另一点。
 * 原点只可能在 AB 两侧（加入 A 时已保证 dot(A,d)>0，越过了原点），
 * 新方向取垂直于 AB 且指向原点一侧：d = (AB x AO) x AB */
static int handle_line(Simplex *s, Vec2 *d)
{
    Vec2 a = s->pts[1]; /* 最后加入 */
    Vec2 b = s->pts[0];
    Vec2 ab = vec_sub(b, a); /* 注意：AB = B - A（dyn4j 评论区修正） */
    Vec2 ao = vec_neg(a);    /* AO = O - A = -A */

    *d = triple_product(ab, ao, ab);
    if (vec_len2(*d) < EPS) {
        /* 原点恰好在 AB 线上：视为接触（是否算碰撞由调用者定义，
         * 这里按"包含边界即碰撞"处理） */
        return 1;
    }
    return 0;
}

/* 三角形情形：通过 Voronoi 区域测试判断原点位置。
 * A 最后加入；abPerp 垂直 AB 指向三角形外侧，acPerp 垂直 AC 指向外侧。
 * - abPerp·AO > 0：原点在 AB 外侧区域 -> 丢弃 C，朝 abPerp 继续
 * - acPerp·AO > 0：原点在 AC 外侧区域 -> 丢弃 B，朝 acPerp 继续
 * - 否则：原点在三角形内部（或 BC 边区域）-> 碰撞 */
static int handle_triangle(Simplex *s, Vec2 *d)
{
    Vec2 a = s->pts[2]; /* 最后加入 */
    Vec2 b = s->pts[1];
    Vec2 c = s->pts[0];
    Vec2 ab = vec_sub(b, a);
    Vec2 ac = vec_sub(c, a);
    Vec2 ao = vec_neg(a);

    Vec2 ab_perp = triple_product(ac, ab, ab); /* 垂直 AB，背离 C */
    Vec2 ac_perp = triple_product(ab, ac, ac); /* 垂直 AC，背离 B */

    if (vec_dot(ab_perp, ao) > 0) {
        s->pts[0] = s->pts[1]; /* 丢弃 C，保留 A、B */
        s->pts[1] = s->pts[2];
        s->n = 2;
        *d = ab_perp;
        return 0;
    }
    if (vec_dot(ac_perp, ao) > 0) {
        s->pts[1] = s->pts[2]; /* 丢弃 B，保留 A、C */
        s->n = 2;
        *d = ac_perp;
        return 0;
    }
    return 1; /* 原点在三角形内 -> Minkowski 差包含原点 -> 相交 */
}

static int handle_simplex(Simplex *s, Vec2 *d)
{
    return (s->n == 2) ? handle_line(s, d) : handle_triangle(s, d);
}

/* ---------- GJK 主循环 ---------- */

/* 返回 1=相交，0=分离；*iters 返回迭代次数（观察收敛速度用） */
static int gjk_intersect(Poly a, Poly b, int *iters)
{
    Simplex s;
    Vec2 d, p;

    /* 初始方向任意；取中心连线利于尽早退出（dyn4j 建议） */
    d = vec_sub(centroid(b), centroid(a));
    if (vec_len2(d) < EPS)
        d = v2(1, 0);

    p = mink_support(a, b, d);
    s.n = 0;
    simplex_push(&s, p);
    d = vec_neg(d);

    for (*iters = 1; *iters < MAX_ITER; (*iters)++) {
        Vec2 new_pt = mink_support(a, b, d);
        simplex_push(&s, new_pt);

        /* 关键终止条件 1：沿 d 方向的最远点都没有越过原点，
         * 说明整个 Minkowski 差在垂直 d 的平面一侧 -> 不含原点 -> 分离 */
        if (vec_dot(new_pt, d) <= 0)
            return 0;

        /* 终止条件 2：simplex 包含原点 -> 相交；否则演化 simplex */
        if (handle_simplex(&s, &d))
            return 1;
    }
    return 0; /* 达到迭代上限（浮点退化），保守返回分离 */
}

/* ---------- 测试 ---------- */

static void run_case(const char *name, Poly a, Poly b, int expect)
{
    int iters = 0;
    int hit = gjk_intersect(a, b, &iters);
    printf("%-28s -> %s (iters=%d)  expected=%s  [%s]\n",
           name, hit ? "COLLIDE" : "SEPARATE", iters,
           expect ? "COLLIDE" : "SEPARATE",
           hit == expect ? "PASS" : "FAIL");
}

int main(void)
{
    /* 单位正方形 A：[0,2]x[0,2] */
    Vec2 sq_a[] = { {0, 0}, {2, 0}, {2, 2}, {0, 2} };
    /* 正方形 B1：与 A 重叠（[1,3]x[1,3]） */
    Vec2 sq_b1[] = { {1, 1}, {3, 1}, {3, 3}, {1, 3} };
    /* 正方形 B2：远离 A（[5,7]x[5,7]） */
    Vec2 sq_b2[] = { {5, 5}, {7, 5}, {7, 7}, {5, 7} };
    /* 正方形 B3：与 A 有 1 单位间隙（[3,5]x[0,2]） */
    Vec2 sq_b3[] = { {3, 0}, {5, 0}, {5, 2}, {3, 2} };
    /* 正方形 B4：薄重叠（x 方向只侵入 0.001） */
    Vec2 sq_b4[] = { {1.999, 1.9}, {3.999, 1.9}, {3.999, 3.9}, {1.999, 3.9} };
    /* 五边形 P：中心 (1,1) 半径 2 */
    Vec2 pent[] = {
        {3, 1}, {1.618, 2.618}, {-0.618, 1.618}, {-0.618, 0.382}, {1.618, -0.618}
    };
    /* 三角形 T：与五边形重叠 */
    Vec2 tri[] = { {2, 2}, {4, 2}, {3, 4} };

    Poly a = { sq_a, 4 };

    run_case("square vs square overlap", a, (Poly){ sq_b1, 4 }, 1);
    run_case("square vs square far", a, (Poly){ sq_b2, 4 }, 0);
    run_case("square vs square gap=1", a, (Poly){ sq_b3, 4 }, 0);
    run_case("square vs square thin", a, (Poly){ sq_b4, 4 }, 1);
    run_case("pentagon vs triangle", (Poly){ pent, 5 }, (Poly){ tri, 3 }, 1);

    return 0;
}
