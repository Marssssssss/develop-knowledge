/* 线性代数接口:单边 Jacobi SVD 与循环 Jacobi 对称特征分解。
 *
 * 自己实现的原因:本 demo 要观察的正是"同一份数据走两条数值路线会差多少",
 * 所以两条路线必须共享同一套基础算法,不能借助会夹带自己误差控制的库。
 * 数据结构 Mat 为行主序 n×p 稠密矩阵。
 */
#ifndef LINALG_H
#define LINALG_H

#include <stddef.h>

typedef struct {
    double *a; /* row-major,n 行 p 列 */
    int n, p;
} Mat;

Mat mat_new(int n, int p);
void mat_free(Mat m);
double *at(Mat m, int r, int c);
#define ROW(m, r, c) at(m, r, c)[0]

Mat mat_center(Mat X);                                       /* 按列去均值 */
void jacobi_svd(Mat A, double *s, Mat U, Mat V);             /* A = U·diag(s)·Vᵀ */
void jacobi_eigh(double *S, int p, double *w, double *Q);    /* 对称阵 → 特征值降序 + 特征向量 */
Mat make_matrix_with_spectrum(int n, int p, const double *sigma, unsigned seed);

#endif /* LINALG_H */
