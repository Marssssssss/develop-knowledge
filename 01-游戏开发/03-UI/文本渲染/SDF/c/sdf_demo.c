/*
 * sdf_demo.c — 有符号距离场(SDF)字形渲染原理演示
 *
 * 流程：
 *   1. 以 "7" 字形闭合多边形作为矢量轮廓（单位坐标 [0,16]，y 向下）
 *   2. 在 64x64 网格上采样精确有符号距离，构建 SDF 纹理
 *      （归一化约定：内部 > 0.5，外部 < 0.5，0.5 恰好落在轮廓上）
 *   3. 以 8x 放大(512x512)用三种采样方式渲染对比：
 *      a) nearest.ppm        最近邻 + alpha test  -> 阶梯锯齿
 *      b) bilinear_mask.ppm  二值 coverage 双线性 -> 边缘线性模糊
 *      c) bilinear_sdf.ppm   SDF 双线性 + smoothstep -> 平滑边缘
 *   4. effects.ppm           基于 SDF 的阴影 + 描边特效
 *      (Valve 论文的 specialized effects)
 *   5. sdf_texture.ppm       64x64 距离场灰度可视化
 *
 * 输出为二进制 PPM(P6)，可用 GIMP / IrfanView / magick 查看。
 * 编译: gcc -O2 -Wall -Wextra -o sdf_demo sdf_demo.c -lm
 */

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#define DOMAIN 16.0  /* 形状定义域边长（单位坐标系） */
#define TEX_N 64     /* SDF / coverage 纹理分辨率 */
#define OUT_N 512    /* 输出图像分辨率（8x 纹理放大） */
#define RANGE 4.0    /* 距离归一化截断半径(spread)，单位坐标 */
#define AA_W 0.01    /* smoothstep 半宽（值域单位），约 2 个输出像素 */
#define GLYPH_V 7    /* 轮廓顶点数 */

/* "7" 字形闭合多边形轮廓（屏幕坐标，y 向下） */
static const double gx[GLYPH_V] = {2.0, 14.0, 14.0, 9.0, 5.5, 10.5, 2.0};
static const double gy[GLYPH_V] = {2.0, 2.0, 4.5, 14.0, 14.0, 4.5, 4.5};

static double clamp01(double v) {
    return v < 0.0 ? 0.0 : (v > 1.0 ? 1.0 : v);
}

/* smoothstep：GLSL 同名内建函数的标量等价实现 */
static double smoothstep(double a, double b, double x) {
    double t = clamp01((x - a) / (b - a));
    return t * t * (3.0 - 2.0 * t);
}

/* 射线法点在多边形内判定：向 +x 发射线，统计与边的穿越次数（奇数为内） */
static int point_in_glyph(double px, double py) {
    int inside = 0;
    for (int i = 0, j = GLYPH_V - 1; i < GLYPH_V; j = i++) {
        if ((gy[i] > py) != (gy[j] > py)) {
            double x_at = gx[i] + (py - gy[i]) * (gx[j] - gx[i]) / (gy[j] - gy[i]);
            if (px < x_at)
                inside = !inside;
        }
    }
    return inside;
}

/* 点到线段 (ax,ay)-(bx,by) 的最短欧氏距离（投影 clamp 到 [0,1]） */
static double dist_point_segment(double px, double py,
                                 double ax, double ay, double bx, double by) {
    double dx = bx - ax, dy = by - ay;
    double len2 = dx * dx + dy * dy;
    double t = 0.0;
    if (len2 > 0.0)
        t = clamp01(((px - ax) * dx + (py - ay) * dy) / len2);
    double cx = ax + t * dx, cy = ay + t * dy;
    return hypot(px - cx, py - cy);
}

/* 精确有符号距离：对所有轮廓边取最短；内部取负（内负外正约定） */
static double signed_distance(double px, double py) {
    double best = HUGE_VAL;
    for (int i = 0, j = GLYPH_V - 1; i < GLYPH_V; j = i++) {
        double d = dist_point_segment(px, py, gx[j], gy[j], gx[i], gy[i]);
        if (d < best)
            best = d;
    }
    return point_in_glyph(px, py) ? -best : best;
}

/* 步骤 1：构建 SDF 纹理与二值 coverage 纹理 */
static void build_textures(double sdf[TEX_N][TEX_N], double mask[TEX_N][TEX_N]) {
    for (int j = 0; j < TEX_N; j++) {
        for (int i = 0; i < TEX_N; i++) {
            double px = (i + 0.5) / TEX_N * DOMAIN;
            double py = (j + 0.5) / TEX_N * DOMAIN;
            double d = signed_distance(px, py);
            sdf[j][i] = clamp01(0.5 - d / (2.0 * RANGE)); /* 0.5 即轮廓 */
            mask[j][i] = d < 0.0 ? 1.0 : 0.0;             /* 传统位图字体 */
        }
    }
}

/* 双线性采样（等价 GPU 的 GL_LINEAR）；fx/fy 为连续纹素坐标 */
static double bilinear(const double tex[TEX_N][TEX_N], double fx, double fy) {
    double x0 = floor(fx), y0 = floor(fy);
    double tx = fx - x0, ty = fy - y0;
    int ix0 = (int)x0, iy0 = (int)y0;
    int ix1 = ix0 + 1, iy1 = iy0 + 1;
    if (ix0 < 0) ix0 = 0; if (ix0 > TEX_N - 1) ix0 = TEX_N - 1;
    if (iy0 < 0) iy0 = 0; if (iy0 > TEX_N - 1) iy0 = TEX_N - 1;
    if (ix1 < 0) ix1 = 0; if (ix1 > TEX_N - 1) ix1 = TEX_N - 1;
    if (iy1 < 0) iy1 = 0; if (iy1 > TEX_N - 1) iy1 = TEX_N - 1;
    double v00 = tex[iy0][ix0], v10 = tex[iy0][ix1];
    double v01 = tex[iy1][ix0], v11 = tex[iy1][ix1];
    return (v00 * (1.0 - tx) + v10 * tx) * (1.0 - ty) +
           (v01 * (1.0 - tx) + v11 * tx) * ty;
}

/* 写出二进制 PPM(P6) 文件；rgb 为 w*h*3 字节 */
static void write_ppm(const char *path, const unsigned char *rgb, int w, int h) {
    FILE *f = fopen(path, "wb");
    if (!f) {
        perror(path);
        exit(EXIT_FAILURE);
    }
    fprintf(f, "P6\n%d %d\n255\n", w, h);
    if (fwrite(rgb, 3, (size_t)w * (size_t)h, f) != (size_t)w * (size_t)h) {
        fprintf(stderr, "write %s failed\n", path);
        exit(EXIT_FAILURE);
    }
    fclose(f);
}

/* 输出像素映射：屏幕像素 -> 连续纹素坐标 */
static void to_texel(double x, double y, double *fx, double *fy) {
    *fx = (x + 0.5) / OUT_N * TEX_N - 0.5;
    *fy = (y + 0.5) / OUT_N * TEX_N - 0.5;
}

int main(void) {
    static double sdf[TEX_N][TEX_N], mask[TEX_N][TEX_N];
    unsigned char *out = malloc((size_t)OUT_N * OUT_N * 3);
    if (!out) {
        fprintf(stderr, "out of memory\n");
        return EXIT_FAILURE;
    }

    build_textures(sdf, mask);
    printf("SDF texture %dx%d built (range +/-%.1f units)\n", TEX_N, TEX_N, RANGE);

    /* 5a. 距离场可视化（放大到输出尺寸，直接最近邻即可） */
    for (int y = 0; y < OUT_N; y++) {
        for (int x = 0; x < OUT_N; x++) {
            int ix = (int)((x + 0.5) / OUT_N * TEX_N);
            int iy = (int)((y + 0.5) / OUT_N * TEX_N);
            if (ix > TEX_N - 1) ix = TEX_N - 1;
            if (iy > TEX_N - 1) iy = TEX_N - 1;
            double v = sdf[iy][ix];
            unsigned char g = (unsigned char)(v * 255.0 + 0.5);
            unsigned char *p = out + ((size_t)y * OUT_N + x) * 3;
            p[0] = g; p[1] = g; p[2] = g;
        }
    }
    write_ppm("sdf_texture.ppm", out, OUT_N, OUT_N);

    /* 5b. 最近邻 + alpha test：阶梯锯齿 */
    for (int y = 0; y < OUT_N; y++) {
        for (int x = 0; x < OUT_N; x++) {
            double fx, fy, v;
            to_texel(x, y, &fx, &fy);
            int ix = (int)lround(fx), iy = (int)lround(fy);
            if (ix < 0) ix = 0; if (ix > TEX_N - 1) ix = TEX_N - 1;
            if (iy < 0) iy = 0; if (iy > TEX_N - 1) iy = TEX_N - 1;
            v = sdf[iy][ix];
            unsigned char g = (unsigned char)(smoothstep(0.5 - AA_W, 0.5 + AA_W, v) * 255.0 + 0.5);
            unsigned char *p = out + ((size_t)y * OUT_N + x) * 3;
            p[0] = g; p[1] = g; p[2] = g;
        }
    }
    write_ppm("nearest.ppm", out, OUT_N, OUT_N);

    /* 5c. 二值 coverage 双线性：边缘线性模糊（灰色渐变带） */
    for (int y = 0; y < OUT_N; y++) {
        for (int x = 0; x < OUT_N; x++) {
            double fx, fy, cov;
            to_texel(x, y, &fx, &fy);
            cov = bilinear(mask, fx, fy); /* 直接输出覆盖率 -> 可见的模糊 */
            unsigned char g = (unsigned char)(cov * 255.0 + 0.5);
            unsigned char *p = out + ((size_t)y * OUT_N + x) * 3;
            p[0] = g; p[1] = g; p[2] = g;
        }
    }
    write_ppm("bilinear_mask.ppm", out, OUT_N, OUT_N);

    /* 5d. SDF 双线性 + smoothstep：平滑边缘（本 demo 的主角） */
    for (int y = 0; y < OUT_N; y++) {
        for (int x = 0; x < OUT_N; x++) {
            double fx, fy, v;
            to_texel(x, y, &fx, &fy);
            v = bilinear(sdf, fx, fy);
            double a = smoothstep(0.5 - AA_W, 0.5 + AA_W, v);
            unsigned char g = (unsigned char)(a * 255.0 + 0.5);
            unsigned char *p = out + ((size_t)y * OUT_N + x) * 3;
            p[0] = g; p[1] = g; p[2] = g;
        }
    }
    write_ppm("bilinear_sdf.ppm", out, OUT_N, OUT_N);

    /* 5e. 特效：阴影（偏移采样）+ 描边（阈值带），均来自同一张 SDF */
    for (int y = 0; y < OUT_N; y++) {
        for (int x = 0; x < OUT_N; x++) {
            double fx, fy, v, sv;
            to_texel(x, y, &fx, &fy);
            v = bilinear(sdf, fx, fy);
            /* 阴影：向右下偏移 3 个纹素再采样阈值化 */
            sv = bilinear(sdf, fx + 3.0, fy + 3.0);
            double shadow = smoothstep(0.5 - AA_W, 0.5 + AA_W, sv);
            double glyph = smoothstep(0.5 - AA_W, 0.5 + AA_W, v);
            int outline = (v > 0.5 - 0.08) && (v < 0.5 + 0.02); /* 外描边带 */
            unsigned char *p = out + ((size_t)y * OUT_N + x) * 3;
            /* 合成顺序：深蓝背景 -> 灰色阴影 -> 黄色描边 -> 白色字形 */
            double r = 24.0, g = 28.0, b = 56.0;
            r += (96.0 - r) * shadow; g += (102.0 - g) * shadow; b += (120.0 - b) * shadow;
            if (outline) { r = 232.0; g = 180.0; b = 48.0; }
            if (glyph) { r = 245.0; g = 245.0; b = 245.0; }
            p[0] = (unsigned char)(r + 0.5);
            p[1] = (unsigned char)(g + 0.5);
            p[2] = (unsigned char)(b + 0.5);
        }
    }
    write_ppm("effects.ppm", out, OUT_N, OUT_N);

    free(out);
    printf("wrote: sdf_texture.ppm nearest.ppm bilinear_mask.ppm "
           "bilinear_sdf.ppm effects.ppm (%dx%d)\n", OUT_N, OUT_N);
    return 0;
}
