/* 支持向量机 + SMO(线性核)C 参考实现。
 *
 * 权威来源(与 svm_smo.py 同一批,实际读过):
 *   scikit-learn 1.9《1.4. Support Vector Machines》
 *     https://scikit-learn.org/stable/modules/svm.html
 *     对偶 min_α ½αᵀQα − eᵀα  s.t. yᵀα=0, 0≤αi≤C, Qij=yi·yj·K(xi,xj)
 *     决策 f(x)=Σ_{i∈SV} yi·αi·K(xi,x)+b,  线性核时 w=Σ αi·yi·xi
 *   J. Platt (1998) Sequential Minimal Optimization, MSR-TR-98-14
 *     https://www.microsoft.com/en-us/research/wp-content/uploads/1998/04/sequential-minimal-optimization.pdf
 *     解析二变量步 + 误差缓存;η = 2Kij − Kii − Kjj ≤ 0;b1/b2 互补松弛更新
 *   WSS1 最大违反对(Keerthi et al. 2001;Fan/Chen/Lin JMLR 2005)
 *     I_up ={αt<C,yt=+1}∪{αt>0,yt=−1}; I_low={αt>0,yt=+1}∪{αt<C,yt=−1}
 *     最优性:m(α)=max_{I_up}(yt−gt) ≤ M(α)=min_{I_low}(yt−gt)
 *
 * 编译:gcc -O2 -o svm_smo svm_smo.c -lm
 * 说明:线性核下 K(xi,xj)=xi·xj,故 w 可直接由 α 求和得到;核矩阵预先算好以复用。
 */

#include <math.h>
#include <stdio.h>

#define NPT 40
#define NFEAT 2
#define CMAX 1.0
#define TOL 1e-3
#define MAXITER 20000
#define EPSB 1e-9

static double X[NPT][NFEAT];
static int Y[NPT];
static double Kmat[NPT][NPT];
static double alpha[NPT], ay[NPT], g[NPT], fval[NPT], err[NPT];
static double bias = 0.0;

/* 线性同余,与 Python/Go 版同种子同序列 */
static unsigned long long lcg_state;
static double lcg(void)
{
    lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
    return (double)(lcg_state >> 11) / (double)(1ULL << 53);
}

static void gen_blobs(double c1x, double c1y, double c2x, double c2y, double sp, int per)
{
    int i, k;
    lcg_state = 20260914ULL;
    k = 0;
    for (i = 0; i < per; i++) {
        X[k][0] = c1x + (lcg() * 2.0 - 1.0) * sp * 1.7;
        X[k][1] = c1y + (lcg() * 2.0 - 1.0) * sp * 1.7;
        Y[k] = 1;
        k++;
    }
    for (i = 0; i < per; i++) {
        X[k][0] = c2x + (lcg() * 2.0 - 1.0) * sp * 1.7;
        X[k][1] = c2y + (lcg() * 2.0 - 1.0) * sp * 1.7;
        Y[k] = -1;
        k++;
    }
}

static void build_kernel(void)
{
    int i, j, t;
    for (i = 0; i < NPT; i++)
        for (j = 0; j < NPT; j++) {
            double s = 0.0;
            for (t = 0; t < NFEAT; t++) s += X[i][t] * X[j][t];
            Kmat[i][j] = s;
        }
}

/* g_t = Σ_j αj·yj·K(x_t,x_j)(不含 b);gt 与 b 无关,故可直接作为 KKT 违反度量 */
static void compute_g(void)
{
    int t, j;
    for (t = 0; t < NPT; t++) {
        double s = 0.0;
        for (j = 0; j < NPT; j++) s += ay[j] * Kmat[j][t];
        g[t] = s;
    }
}

static void refresh_f(void)
{
    int t;
    for (t = 0; t < NPT; t++) {
        fval[t] = g[t] + bias;
        err[t] = fval[t] - (double)Y[t];
    }
}

/* 返回 1 表示找到违反对,gap 为 m(α)−M(α);gap<=TOL 即已收敛 */
static int violating_pair(double *gap_out, int *i_out, int *j_out)
{
    int t, in = -1, jn = -1;
    double best_up = -1e300, best_low = 1e300;
    for (t = 0; t < NPT; t++) {
        double v = (double)Y[t] - g[t];
        int up = (Y[t] > 0 && alpha[t] < CMAX - EPSB) || (Y[t] < 0 && alpha[t] > EPSB);
        int low = (Y[t] > 0 && alpha[t] > EPSB) || (Y[t] < 0 && alpha[t] < CMAX - EPSB);
        if (up && v > best_up) { best_up = v; in = t; }
        if (low && v < best_low) { best_low = v; jn = t; }
    }
    if (in < 0 || jn < 0) return 0;
    *i_out = in;
    *j_out = jn;
    *gap_out = best_up - best_low;
    return 1;
}

/* 箱约束下 αj 的可行区间:yᵀα=0 是斜线,与 [0,C]² 相交 */
static void bounds(int i, int j, double *L, double *H)
{
    double ai = alpha[i], aj = alpha[j];
    if (Y[i] != Y[j]) {                       /* αi − αj = const */
        *L = (aj - ai > 0.0) ? aj - ai : 0.0;
        *H = (CMAX + aj - ai < CMAX) ? CMAX + aj - ai : CMAX;
    } else {                                  /* αi + αj = const */
        double l = ai + aj - CMAX;
        *L = (l > 0.0) ? l : 0.0;
        *H = (ai + aj < CMAX) ? ai + aj : CMAX;
    }
}

static int take_step(int i, int j)
{
    double Ei = err[i], Ej = err[j], L, H, eta, ajnew, aiold, ajold, b1, b2;
    if (i == j) return 0;
    aiold = alpha[i];
    ajold = alpha[j];
    bounds(i, j, &L, &H);
    if (H - L < 1e-12) return 0;
    /* W 沿对角线的二阶导 η = ∂²W/∂αj² = 2Kij − Kii − Kjj ≤ 0 */
    eta = 2.0 * Kmat[i][j] - Kmat[i][i] - Kmat[j][j];
    if (eta >= -1e-12) return 0;
    /* W' = yj(Ei−Ej) ⇒ 牛顿步 αj ← αj − yj(Ei−Ej)/η,投影回 [L,H] */
    ajnew = ajold - (double)Y[j] * (Ei - Ej) / eta;
    if (ajnew > H) ajnew = H;
    if (ajnew < L) ajnew = L;
    if (fabs(ajnew - ajold) < 1e-5) return 0;
    alpha[j] = ajnew;
    alpha[i] = aiold + (double)(Y[i] * Y[j]) * (ajold - ajnew);
    b1 = bias - Ei - (double)Y[i] * (alpha[i] - aiold) * Kmat[i][i]
                  - (double)Y[j] * (alpha[j] - ajold) * Kmat[i][j];
    b2 = bias - Ej - (double)Y[i] * (alpha[i] - aiold) * Kmat[i][j]
                  - (double)Y[j] * (alpha[j] - ajold) * Kmat[j][j];
    if (alpha[i] > 0.0 && alpha[i] < CMAX)      bias = b1;
    else if (alpha[j] > 0.0 && alpha[j] < CMAX) bias = b2;
    else                                        bias = 0.5 * (b1 + b2);
    ay[i] = alpha[i] * (double)Y[i];
    ay[j] = alpha[j] * (double)Y[j];
    return 1;
}

/* L = max_{I_up}(y−g) ≤ b ≤ min_{I_low}(y−g) = U,取中点消除累积漂移 */
static void refine_b(void)
{
    int t, has_lo = 0, has_hi = 0;
    double b_lo = -1e300, b_hi = 1e300;
    for (t = 0; t < NPT; t++) {
        double v = (double)Y[t] - g[t];
        int in_up  = (Y[t] > 0 && alpha[t] < CMAX - EPSB) || (Y[t] < 0 && alpha[t] > EPSB);
        int in_low = (Y[t] > 0 && alpha[t] > EPSB) || (Y[t] < 0 && alpha[t] < CMAX - EPSB);
        if (in_up && (!has_lo || v > b_lo)) { b_lo = v; has_lo = 1; }
        if (in_low && (!has_hi || v < b_hi)) { b_hi = v; has_hi = 1; }
    }
    if (has_lo && has_hi)      bias = 0.5 * (b_lo + b_hi);
    else if (has_lo)           bias = b_lo;
    else if (has_hi)           bias = b_hi;
}

static int kkt_violations(void)
{
    int t, bad = 0;
    for (t = 0; t < NPT; t++) {
        double yf = Y[t] * fval[t];
        int ok;
        if (alpha[t] <= 1e-6)          ok = yf >= 1.0 - 5 * TOL;
        else if (alpha[t] >= CMAX - 1e-6) ok = yf <= 1.0 + 5 * TOL;
        else                            ok = fabs(yf - 1.0) <= 5 * TOL;
        if (!ok) bad++;
    }
    return bad;
}

int main(void)
{
    int it, nsv = 0, i;
    double w[NFEAT], nw, margin;

    gen_blobs(2.5, 2.5, -2.5, -2.5, 0.5, NPT / 2);
    build_kernel();
    for (it = 0; it < MAXITER; it++) {
        double gap;
        int i2, j2;
        compute_g();
        if (!violating_pair(&gap, &i2, &j2)) break;
        if (gap <= TOL) break;
        refresh_f();
        if (!take_step(i2, j2)) break;
    }
    compute_g();
    refine_b();
    refresh_f();

    printf("======================================================================\n");
    printf("SVM + SMO(线性核,C 实现)  N=%d  C=%.2f\n", NPT, CMAX);
    printf("======================================================================\n");
    for (i = 0; i < NFEAT; i++) {
        double s = 0.0;
        int t;
        for (t = 0; t < NPT; t++) s += ay[t] * X[t][i];
        w[i] = s;
    }
    nw = sqrt(w[0] * w[0] + w[1] * w[1]);
    margin = (nw > 1e-12) ? 2.0 / nw : 0.0;
    for (i = 0; i < NPT; i++) if (alpha[i] > 1e-6) nsv++;

    printf("收敛迭代 = %d 轮\n", it);
    printf("w = [%+.4f, %+.4f]   b = %+.4f   margin = 2/|w| = %.4f\n",
           w[0], w[1], bias, margin);
    printf("支持向量 %d/%d 个\n", nsv, NPT);
    printf("KKT 违反数 = %d\n", kkt_violations());

    {
        int correct = 0;
        for (i = 0; i < NPT; i++) {
            double s = bias;
            int t;
            for (t = 0; t < NPT; t++)
                if (alpha[t] > 0.0) s += ay[t] * Kmat[t][i];
            if ((s >= 0.0 ? 1 : -1) == Y[i]) correct++;
        }
        printf("训练准确率 = %.4f\n", (double)correct / (double)NPT);
    }
    printf("---- 关键性质:y·f 在支持向量上恰为 1,在其余点 ≥1 --------------\n");
    for (i = 0; i < NPT; i++)
        if (alpha[i] > 1e-6)
            printf("  sv#%-3d alpha=%.6f  y= %+d  y·f=%.6f\n",
                   i, alpha[i], Y[i], Y[i] * fval[i]);
    return 0;
}
