/*
 * zbuffer.c — Z-Buffer(深度缓冲)与 Z-Fighting 最小软件光栅化演示。
 * 场景与算法同 ../python/zbuffer.py,详见 ../README.md。
 *
 * 编译:gcc -O2 -Wall -Wextra zbuffer.c -lm -o zbuffer
 */

#include <math.h>
#include <stdio.h>

#define W 64
#define H 24
#define NEAR 1.0
#define FAR 400.0
#define FOCAL 40.0

typedef struct { double x, y, z; } Vec3; /* 相机空间,右手系,z 朝前为正 */

typedef struct {
    char color[H][W + 1];
    double depth[H][W];
    int bits; /* 0 = 浮点深度;16/24 = 定点量化位数 */
} Canvas;

static void canvas_init(Canvas *cv, int bits)
{
    int y, x;
    for (y = 0; y < H; y++) {
        for (x = 0; x < W; x++) {
            cv->color[y][x] = '.';
            cv->depth[y][x] = INFINITY; /* clear 到最远(OpenGL 惯例) */
        }
        cv->color[y][W] = '\0';
    }
    cv->bits = bits;
}

/* 把相机空间 z 编码为缓冲值:对 1/z 线性,z=NEAR->0(最近),z=FAR->1(最远) */
static double to_buffer(const Canvas *cv, double z)
{
    double d = (1.0 / z - 1.0 / NEAR) / (1.0 / FAR - 1.0 / NEAR);
    if (cv->bits)
        return floor(d * (double)((1u << cv->bits) - 1) + 0.5); /* 四舍五入 */
    return d;
}

static void put(Canvas *cv, int x, int y, double z, char tag)
{
    double d = to_buffer(cv, z);
    if (d < cv->depth[y][x]) { /* GL_LESS:严格小于才写入 */
        cv->depth[y][x] = d;
        cv->color[y][x] = tag;
    }
}

static void print_border(void)
{
    int i;
    putchar('+');
    for (i = 0; i < W; i++)
        putchar('-');
    printf("+\n");
}

static void show(const Canvas *cv, const char *title)
{
    int y;
    printf("\n%s\n", title);
    print_border();
    for (y = 0; y < H; y++)
        printf("|%s|\n", cv->color[y]);
    print_border();
}

static void project(Vec3 p, double *sx, double *sy)
{
    *sx = W / 2.0 + FOCAL * p.x / p.z; /* 透视除法 */
    *sy = H / 2.0 - FOCAL * p.y / p.z;
}

static void rasterize(Canvas *cv, const Vec3 tri[3], char tag, int depth_test)
{
    double ax, ay, bx, by, cx, cy;
    double inv[3], area;
    int x0, x1, y0, y1, px, py, neg, i;

    project(tri[0], &ax, &ay);
    project(tri[1], &bx, &by);
    project(tri[2], &cx, &cy);

    x0 = (int)fmin(fmin(ax, bx), cx); if (x0 < 0) x0 = 0;
    x1 = (int)fmax(fmax(ax, bx), cx) + 1; if (x1 > W - 1) x1 = W - 1;
    y0 = (int)fmin(fmin(ay, by), cy); if (y0 < 0) y0 = 0;
    y1 = (int)fmax(fmax(ay, by), cy) + 1; if (y1 > H - 1) y1 = H - 1;

    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
    if (fabs(area) < 1e-12)
        return;
    for (i = 0; i < 3; i++)
        inv[i] = 1.0 / tri[i].z;
    neg = area < 0;

    for (py = y0; py <= y1; py++) {
        for (px = x0; px <= x1; px++) {
            /* e0/e1/e2 分别是顶点 0/1/2 的(未归一化)重心权重 */
            double e0 = (cx - bx) * (py - by) - (cy - by) * (px - bx);
            double e1 = (ax - cx) * (py - cy) - (ay - cy) * (px - cx);
            double e2 = (bx - ax) * (py - ay) - (by - ay) * (px - ax);
            if ((e0 < 0) != neg || (e1 < 0) != neg || (e2 < 0) != neg)
                continue; /* 片元中心在三角形外(符号不一致) */
            if (depth_test) {
                /* 透视校正:屏幕空间线性插值 1/z,取倒数还原相机深度 */
                double iz = (e0 * inv[0] + e1 * inv[1] + e2 * inv[2]) / area;
                put(cv, px, py, 1.0 / iz, tag);
            } else {
                cv->color[py][px] = tag; /* 画家算法:直接覆盖 */
            }
        }
    }
}

static void precision_table(void)
{
    const double zs[] = { 10, 50, 100, 200, 300, 400 };
    size_t i;
    printf("\n深度分辨率表(相邻两个可表示深度的世界空间距离 dz,越小越好):\n");
    printf("  依据 Khronos OpenGL Wiki: dz ~= z^2*(FAR-NEAR)/(FAR*NEAR*(2^bits-1))\n");
    printf("  NEAR=%.0f, FAR=%.0f\n", NEAR, FAR);
    printf("  %6s %12s %12s\n", "z", "16-bit dz", "24-bit dz");
    for (i = 0; i < sizeof zs / sizeof zs[0]; i++) {
        double z = zs[i];
        double base = z * z * (FAR - NEAR) / (FAR * NEAR);
        printf("  %6.0f %12.4f %12.6f\n", z, base / 65535.0, base / 16777215.0);
    }
    printf("  经验法则:log2(FAR/NEAR) = %.1f bit 精度损失(OpenGL 蓝皮书)\n",
           log2(FAR / NEAR));
}

/* 场景 A:互相贯穿的三角形对,平均深度相同(画家算法的噩梦)
 * T1 平面 z = 3.2 + 0.5*y,T2 镜像 z = 3.2 - 0.5*y,在 y=0 相交 */
static const Vec3 SCENE_A[2][3] = {
    { { -1.8, -0.7, 2.85 }, { 1.8, -0.7, 2.85 }, { 0.0, 0.7, 3.55 } },
    { { -1.8, 0.7, 2.85 }, { 1.8, 0.7, 2.85 }, { 0.0, -0.7, 3.55 } },
};

int main(void)
{
    /* 场景 A:画家算法(平均深度相同,后画的 B 整体覆盖,贯穿处错误) */
    Canvas painter, zbuf;
    canvas_init(&painter, 0);
    rasterize(&painter, SCENE_A[0], 'A', 0);
    rasterize(&painter, SCENE_A[1], 'B', 0);
    show(&painter, "场景 A 画家算法(无深度测试,后画的 B 整体覆盖,贯穿处错误):");

    canvas_init(&zbuf, 0);
    rasterize(&zbuf, SCENE_A[0], 'A', 1);
    rasterize(&zbuf, SCENE_A[1], 'B', 1);
    show(&zbuf, "场景 A Z-Buffer 浮点深度(逐像素测试,相交线正确):");

    /* 场景 B:近平行三角形对,D 恒比 C 近 0.3 个世界单位 */
    {
        Vec3 slant_c[3] = {
            { -100.0, -40.0, 100.0 }, { 100.0, -40.0, 300.0 },
            { 0.0, 40.0, 200.0 }
        };
        Vec3 slant_d[3];
        struct { int bits; const char *name; } modes[] = {
            { 0, "场景 B Z-Buffer 浮点深度(D 恒近 0.3,正确地全胜):" },
            { 16, "场景 B Z-Buffer 16-bit(远处量化步长 > 0.3,Z-Fighting 条带):" },
            { 24, "场景 B Z-Buffer 24-bit(步长足够小,D 仍全胜):" },
        };
        size_t m;
        int i;
        for (i = 0; i < 3; i++) {
            slant_d[i] = slant_c[i];
            slant_d[i].z += 0.3;
        }
        for (m = 0; m < sizeof modes / sizeof modes[0]; m++) {
            Canvas cv;
            canvas_init(&cv, modes[m].bits);
            rasterize(&cv, slant_c, 'C', 1);
            rasterize(&cv, slant_d, 'D', 1);
            show(&cv, modes[m].name);
        }
    }

    precision_table();
    return 0;
}
