/* PCA 的两条数值路线(C 版):协方差矩阵特征分解 vs 数据矩阵直接 SVD。
 *
 * 对应 sklearn PCA 的两个精确求解器:先算 C = XcᵀXc/(n-1) 再做特征分解的是
 * 'covariance_eigh'(官方原话:effectively doubles the condition number and is
 * therefore less numerically stable),直接对 Xc 做 SVD 的是 'full'。
 *
 * 等价关系 λ_i = σ_i²/(n-1);差别在数值:协方差路线的条件是 SVD 路线的平方,
 * 所以谱跨度大时它会先把小特征值压到 0 以下,开方得到 nan。
 *
 * 算法自己实现:单边 Jacobi SVD + 循环 Jacobi 对称特征分解。
 * 编译:gcc -O2 -Wall -Wextra main.c -lm -o pca
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "linalg.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    /* 实验 1:对齐 sklearn PCA 文档 docstring 的数值锚点 */
    double xs[6][2] = {{-1, -1}, {-2, -1}, {-3, -2}, {1, 1}, {2, 1}, {3, 2}};
    Mat X1 = mat_new(6, 2);
    for (int r = 0; r < 6; r++)
        for (int c = 0; c < 2; c++)
            ROW(X1, r, c) = xs[r][c];
    Mat Xc1 = mat_center(X1);
    double s1[2];
    Mat U1 = mat_new(6, 2), V1 = mat_new(2, 2);
    jacobi_svd(Xc1, s1, U1, V1);
    double lam1[2] = {s1[0] * s1[0] / 5.0, s1[1] * s1[1] / 5.0};
    double tot = lam1[0] + lam1[1];
    printf("== 实验 1:对齐 sklearn PCA 文档的数值锚点 ==\n");
    printf("  奇异值       : [%.5f, %.5f]   期望 [6.30061, 0.54980]\n", s1[0], s1[1]);
    printf("  方差解释比例 : [%.4f, %.4f]   期望 [0.9924, 0.0075]\n", lam1[0] / tot, lam1[1] / tot);
    int ok = fabs(s1[0] - 6.30061) < 1e-5 && fabs(s1[1] - 0.5498) < 1e-5;
    printf("  与官方 docstring 一致:%s\n", ok ? "yes" : "NO");
    if (!ok)
        return 1;

    /* 实验 2:条件数被平方 —— 协方差路线把最小奇异值压成 nan */
    const int n = 200, p = 6;
    double sigma[6] = {1.0, 1e-2, 1e-4, 1e-8, 1e-10, 1e-12};
    Mat X2 = make_matrix_with_spectrum(n, p, sigma, 7u);
    Mat Xc2 = mat_center(X2);
    double s2[6];
    Mat U2 = mat_new(n, p), V2 = mat_new(p, p);
    jacobi_svd(Xc2, s2, U2, V2);
    double *C = (double *)calloc((size_t)p * p, sizeof(double));
    for (int i = 0; i < p; i++)
        for (int j = 0; j < p; j++) {
            double acc = 0.0;
            for (int r = 0; r < n; r++)
                acc += ROW(Xc2, r, i) * ROW(Xc2, r, j);
            C[(size_t)i * p + j] = acc / (n - 1);
        }
    double *w = (double *)malloc((size_t)p * sizeof(double));
    double *Q = (double *)malloc((size_t)p * p * sizeof(double));
    jacobi_eigh(C, p, w, Q);
    double kappa = sigma[0] / sigma[p - 1];
    printf("\n== 实验 2:条件数被平方(covariance_eigh 的数值代价)==\n");
    printf("  kappa(Xc)=%.0e -> kappa(C)=k^2=%.0e,而 1/eps ~= %.0e\n", kappa, kappa * kappa, 1 / 2.22e-16);
    printf("  i   sigma_真值  sigma_SVD      相对误差    sigma_协方差    相对误差\n");
    double max_svd = 0.0, e3_svd = 0.0, e3_cov = 0.0;
    int nan_count = 0;
    for (int i = 0; i < p; i++) {
        double es = fabs(s2[i] - sigma[i]) / sigma[i];
        if (es > max_svd)
            max_svd = es;
        if (i == 3)
            e3_svd = es;
        if (w[i] <= 0.0) {
            nan_count++;
            printf("  %d  %.0e     %.6e   %.2e      %-10s  %s\n", i, sigma[i], s2[i], es, "nan",
                   "<- lambda<0,开方后连数都不是");
            continue;
        }
        double sc = sqrt(w[i] * (n - 1));
        double ec = fabs(sc - sigma[i]) / sigma[i];
        if (i == 3)
            e3_cov = ec;
        printf("  %d  %.0e     %.6e   %.2e      %.6e   %.2e\n", i, sigma[i], s2[i], es, sc, ec);
    }
    printf("  SVD 路线最大相对误差 %.2e(上限 eps*kappa=%.1e);协方差路线在 sigma=1e-8 上已 %.2e\n",
           max_svd, 2.22e-16 * kappa, e3_cov);
    int pass = max_svd < 1e-5 && e3_cov > 1e-3 && e3_cov / e3_svd > 1e6 && nan_count >= 1;
    printf("  结论校验:%s\n", pass ? "pass" : "FAIL");

    mat_free(X1); mat_free(Xc1); mat_free(U1); mat_free(V1);
    mat_free(X2); mat_free(Xc2); mat_free(U2); mat_free(V2);
    free(C); free(w); free(Q);
    return pass ? 0 : 1;
}
