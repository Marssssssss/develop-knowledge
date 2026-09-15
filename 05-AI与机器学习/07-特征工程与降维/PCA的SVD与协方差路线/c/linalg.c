#include "linalg.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

Mat mat_new(int n, int p)
{
    Mat m = {NULL, n, p};
    m.a = (double *)calloc((size_t)n * p, sizeof(double));
    if (!m.a) {
        fprintf(stderr, "out of memory\n");
        exit(1);
    }
    return m;
}

void mat_free(Mat m) { free(m.a); }
double *at(Mat m, int r, int c) { return &m.a[(size_t)r * m.p + c]; }
/* 按列去均值(sklearn: PCA centers but does not scale)。 */
Mat mat_center(Mat X)
{
    Mat Y = mat_new(X.n, X.p);
    for (int j = 0; j < X.p; j++) {
        double mu = 0.0;
        for (int r = 0; r < X.n; r++)
            mu += ROW(X, r, j);
        mu /= X.n;
        for (int r = 0; r < X.n; r++)
            ROW(Y, r, j) = ROW(X, r, j) - mu;
    }
    return Y;
}

/* 单边 Jacobi SVD:A = U·diag(s)·Vᵀ(n >= p)。收敛后列范数即奇异值。
 * U/V 由调用方传入并假定已分配((n×p) 与 (p×p));s 长度 p,降序返回。 */
void jacobi_svd(Mat A, double *s, Mat U, Mat V)
{
    int n = A.n, p = A.p;
    Mat B = mat_new(n, p);
    for (int r = 0; r < n; r++)
        for (int c = 0; c < p; c++)
            ROW(B, r, c) = ROW(A, r, c);
    for (int i = 0; i < p; i++)
        for (int j = 0; j < p; j++)
            ROW(V, i, j) = (i == j);

    for (int sweep = 0; sweep < 60; sweep++) {
        double off = 0.0;
        for (int i = 0; i < p - 1; i++) {
            for (int j = i + 1; j < p; j++) {
                double a = 0.0, b = 0.0, g = 0.0;
                for (int r = 0; r < n; r++) {
                    a += ROW(B, r, i) * ROW(B, r, i);
                    b += ROW(B, r, j) * ROW(B, r, j);
                    g += ROW(B, r, i) * ROW(B, r, j);
                }
                if (g == 0.0 || fabs(g) <= 1e-15 * sqrt(a * b))
                    continue;
                off += g * g;
                double theta = 0.5 * atan2(2.0 * g, a - b);
                double c = cos(theta), sn = sin(theta);
                for (int r = 0; r < n; r++) {
                    double bi = ROW(B, r, i), bj = ROW(B, r, j);
                    ROW(B, r, i) = c * bi + sn * bj;
                    ROW(B, r, j) = -sn * bi + c * bj;
                }
                for (int r = 0; r < p; r++) {
                    double vi = ROW(V, r, i), vj = ROW(V, r, j);
                    ROW(V, r, i) = c * vi + sn * vj;
                    ROW(V, r, j) = -sn * vi + c * vj;
                }
            }
        }
        if (off <= 1e-30)
            break;
    }
    for (int i = 0; i < p; i++) {
        double acc = 0.0;
        for (int r = 0; r < n; r++)
            acc += ROW(B, r, i) * ROW(B, r, i);
        s[i] = sqrt(acc);
    }
    /* 按奇异值降序重排,同时填充 U 与 V */
    int *order = (int *)malloc((size_t)p * sizeof(int));
    for (int i = 0; i < p; i++)
        order[i] = i;
    for (int i = 1; i < p; i++)
        for (int k = i; k > 0 && s[order[k]] > s[order[k - 1]]; k--) {
            int t = order[k];
            order[k] = order[k - 1];
            order[k - 1] = t;
        }
    double *ss = (double *)malloc((size_t)p * sizeof(double));
    for (int k = 0; k < p; k++)
        ss[k] = s[order[k]];
    for (int r = 0; r < n; r++)
        for (int k = 0; k < p; k++)
            ROW(U, r, k) = ss[k] > 0.0 ? ROW(B, r, order[k]) / ss[k] : 0.0;
    double *vtmp = (double *)malloc((size_t)p * p * sizeof(double));
    for (int r = 0; r < p; r++) {
        for (int k = 0; k < p; k++)
            vtmp[(size_t)r * p + k] = ROW(V, r, order[k]);
    }
    for (int r = 0; r < p; r++)
        for (int k = 0; k < p; k++)
            ROW(V, r, k) = vtmp[(size_t)r * p + k];
    for (int k = 0; k < p; k++)
        s[k] = ss[k];
    free(ss);
    free(vtmp);
    free(order);
    mat_free(B);
}

/* 循环 Jacobi 对称特征分解:返回降序特征值 w,Q 的列是特征向量。 */
void jacobi_eigh(double *S, int p, double *w, double *Q)
{
    double *A = (double *)malloc((size_t)p * p * sizeof(double));
    for (int i = 0; i < p * p; i++)
        A[i] = S[i];
    for (int i = 0; i < p * p; i++)
        Q[i] = 0.0;
    for (int i = 0; i < p; i++)
        Q[(size_t)i * p + i] = 1.0;

    for (int sweep = 0; sweep < 100; sweep++) {
        double off = 0.0;
        for (int i = 0; i < p; i++)
            for (int j = i + 1; j < p; j++)
                off += A[(size_t)i * p + j] * A[(size_t)i * p + j];
        if (off <= 1e-30)
            break;
        for (int i = 0; i < p - 1; i++) {
            for (int j = i + 1; j < p; j++) {
                double aij = A[(size_t)i * p + j];
                if (aij == 0.0)
                    continue;
                double theta = 0.5 * atan2(2.0 * aij, A[(size_t)i * p + i] - A[(size_t)j * p + j]);
                double c = cos(theta), sn = sin(theta);
                for (int k = 0; k < p; k++) { /* A ← A·Q */
                    double aki = A[(size_t)k * p + i], akj = A[(size_t)k * p + j];
                    A[(size_t)k * p + i] = c * aki + sn * akj;
                    A[(size_t)k * p + j] = -sn * aki + c * akj;
                }
                for (int k = 0; k < p; k++) { /* A ← Qᵀ·A */
                    double aik = A[(size_t)i * p + k], ajk = A[(size_t)j * p + k];
                    A[(size_t)i * p + k] = c * aik + sn * ajk;
                    A[(size_t)j * p + k] = -sn * aik + c * ajk;
                }
                for (int k = 0; k < p; k++) { /* Q ← Q·Q */
                    double qki = Q[(size_t)k * p + i], qkj = Q[(size_t)k * p + j];
                    Q[(size_t)k * p + i] = c * qki + sn * qkj;
                    Q[(size_t)k * p + j] = -sn * qki + c * qkj;
                }
            }
        }
    }
    int *order = (int *)malloc((size_t)p * sizeof(int));
    for (int i = 0; i < p; i++)
        order[i] = i;
    for (int d0 = 0; d0 < p; d0++)
        w[d0] = A[(size_t)d0 * p + d0];
    for (int i = 1; i < p; i++)
        for (int k = i; k > 0 && w[order[k]] > w[order[k - 1]]; k--) {
            int t = order[k];
            order[k] = order[k - 1];
            order[k - 1] = t;
        }
    double *W = (double *)malloc((size_t)p * sizeof(double));
    double *Qt = (double *)malloc((size_t)p * p * sizeof(double));
    for (int k = 0; k < p; k++) {
        W[k] = A[(size_t)order[k] * p + order[k]];
        for (int r = 0; r < p; r++)
            Qt[(size_t)r * p + k] = Q[(size_t)r * p + order[k]];
    }
    for (int k = 0; k < p; k++)
        w[k] = W[k];
    for (int i = 0; i < p * p; i++)
        Q[i] = Qt[i];
    free(W);
    free(Qt);
    free(order);
    free(A);
}

/* 造奇异值恰为 sigma 的矩阵 X = U·diag(sigma)(各列零均值)。
 * 均值为零很关键:X 会先被 center(),若列本身有均值,去均值相当于减掉一个
 * 秩 1 分量,会把设定的谱改掉,实验就测不到目标了(U 的列与常向量 1/√n 正交)。 */
Mat make_matrix_with_spectrum(int n, int p, const double *sigma, unsigned seed)
{
    Mat U = mat_new(n, p), X = mat_new(n, p);
    srand(seed);
    for (int j = 0; j < p; j++) {
        double mu = 0.0;
        for (int r = 0; r < n; r++) {
            ROW(U, r, j) = 2.0 * rand() / RAND_MAX - 1.0;
            mu += ROW(U, r, j);
        }
        mu /= n;
        for (int r = 0; r < n; r++)
            ROW(U, r, j) -= mu;
        for (int round = 0; round < 2; round++) { /* 两轮改进 Gram-Schmidt */
            for (int k = 0; k < j; k++) {
                double d = 0.0;
                for (int r = 0; r < n; r++)
                    d += ROW(U, r, j) * ROW(U, r, k);
                for (int r = 0; r < n; r++)
                    ROW(U, r, j) -= d * ROW(U, r, k);
            }
        }
        double nrm = 0.0;
        for (int r = 0; r < n; r++)
            nrm += ROW(U, r, j) * ROW(U, r, j);
        nrm = sqrt(nrm);
        for (int r = 0; r < n; r++)
            ROW(U, r, j) /= nrm;
    }
    for (int r = 0; r < n; r++) /* 右奇异向量基取单位阵 ⇒ 谱完全由 sigma 决定 */
        for (int j = 0; j < p; j++)
            ROW(X, r, j) = ROW(U, r, j) * sigma[j];
    mat_free(U);
    return X;
}
