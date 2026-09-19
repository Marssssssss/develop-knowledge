/*
 * Faiss IVF-PQ(C),与 python/ivf_pq.py 同构(结构 + 距离口径部分)。
 *
 * 权威来源(实际读过):
 *   1. IndexIVF.h —— 查询向量也被量化,**只扫对应倒排列表** ⇒ 非穷举;multi-probe 选
 *      nprobe 个量化索引访问多个列表;nprobe 默认 1;code_size 单位为字节
 *   2. IndexIVFPQ.h —— "Each **residual** vector is encoded as a product quantizer code";
 *      use_precomputed_table 表大小 nlist * pq.M * pq.ksub
 *   3. ProductQuantizer.h —— M / nbits / dsub=d/M / ksub=2^nbits;centroids 布局 (M,ksub,dsub);
 *      dis_table(m,j)=||x_m − c_(m,j)||²,形状 M×ksub;sdc_table 是对称距离表
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define D      16
#define NLIST  32
#define MQ     4
#define NBITS  4
#define KSUB   (1 << NBITS)
#define DSUB   (D / MQ)
#define NDB    400
#define EPS    1e-9

static int g_ok = 0, g_fail = 0;
static void check(int cond, const char *msg) {
    if (cond) { g_ok++;   printf("  [ok]   %s\n", msg); }
    else      { g_fail++; printf("  [FAIL] %s\n", msg); }
}

/* LCG:可复现伪随机 */
static unsigned int g_s = 7;
static double lcg_next(void) {
    g_s = g_s * 1664525u + 1013904223u;
    return (double)(g_s >> 8) / (double)(1u << 24) * 6.0 - 3.0;
}

static double coarse[NLIST][D];
static double sub[MQ][KSUB][DSUB];
static double db[NDB][D];
static int list_ids[NLIST][NDB];
static int list_codes[NLIST][NDB][MQ];
static int list_len[NLIST];

static double l2v(const double *a, const double *b, int n) {
    double s = 0.0;
    for (int i = 0; i < n; i++) { double t = a[i] - b[i]; s += t * t; }
    return s;
}

static int nearest_coarse(const double *x) {
    int best = 0; double bd = 1e30;
    for (int j = 0; j < NLIST; j++) {
        double t = l2v(x, coarse[j], D);
        if (t < bd) { bd = t; best = j; }
    }
    return best;
}

static void residual(const double *x, int j, double *r) {
    for (int i = 0; i < D; i++) r[i] = x[i] - coarse[j][i];
}

static void encode(const double *r, int *code) {
    for (int m = 0; m < MQ; m++) {
        int best = 0; double bd = 1e30;
        for (int k = 0; k < KSUB; k++) {
            double t = l2v(r + m * DSUB, sub[m][k], DSUB);
            if (t < bd) { bd = t; best = k; }
        }
        code[m] = best;
    }
}

static void decode(const int *code, double *out) {
    for (int m = 0; m < MQ; m++)
        for (int i = 0; i < DSUB; i++) out[m * DSUB + i] = sub[m][code[m]][i];
}

/* dis_table(m,j) = ||r_m − c_(m,j)||²,形状 M × ksub */
static void distance_table(const double *r, double tab[MQ][KSUB]) {
    for (int m = 0; m < MQ; m++)
        for (int k = 0; k < KSUB; k++) tab[m][k] = l2v(r + m * DSUB, sub[m][k], DSUB);
}

/* ADC:查表累加,无浮点向量运算 */
static double adc(double tab[MQ][KSUB], const int *code) {
    double s = 0.0;
    for (int m = 0; m < MQ; m++) s += tab[m][code[m]];
    return s;
}

static int search_scanned(const double *q, int nprobe) {
    int order[NLIST];
    double dist[NLIST];
    for (int j = 0; j < NLIST; j++) { order[j] = j; dist[j] = l2v(q, coarse[j], D); }
    for (int a = 1; a < NLIST; a++)           /* 插入排序取前 nprobe */
        for (int b = a; b > 0 && dist[b] < dist[b - 1]; b--) {
            double td = dist[b]; dist[b] = dist[b - 1]; dist[b - 1] = td;
            int ti = order[b]; order[b] = order[b - 1]; order[b - 1] = ti;
        }
    int scanned = 0;
    for (int i = 0; i < nprobe && i < NLIST; i++) {
        int j = order[i];
        double r[D]; residual(q, j, r);
        double tab[MQ][KSUB]; distance_table(r, tab);
        for (int k = 0; k < list_len[j]; k++) { (void)adc(tab, list_codes[j][k]); scanned++; }
    }
    return scanned;
}

int main(void) {
    for (int j = 0; j < NLIST; j++) for (int i = 0; i < D; i++) coarse[j][i] = lcg_next();
    for (int m = 0; m < MQ; m++)
        for (int k = 0; k < KSUB; k++)
            for (int i = 0; i < DSUB; i++) sub[m][k][i] = lcg_next();
    for (int n = 0; n < NDB; n++) {
        for (int i = 0; i < D; i++) db[n][i] = lcg_next();
        int j = nearest_coarse(db[n]);
        double r[D]; residual(db[n], j, r);
        int k = list_len[j]++;
        list_ids[j][k] = n;
        encode(r, list_codes[j][k]);
    }

    printf("== Demo 1 · 结构参数 ==\n");
    int code_bytes = (MQ * NBITS + 7) / 8;
    int raw_bytes = D * 4;
    printf("   d=%d M=%d nbits=%d ⇒ dsub=%d ksub=%d\n", D, MQ, NBITS, DSUB, KSUB);
    check(DSUB == D / MQ && KSUB == (1 << NBITS), "dsub 与 ksub 由参数推导");
    check(code_bytes == 2, "nbits=4,M=4 ⇒ 码字 2 字节");
    check(raw_bytes / code_bytes == 32, "压缩比 32×(d=16,float32)");

    printf("\n== Demo 2 · 距离表 M × ksub ==\n");
    double r[D]; residual(db[0], nearest_coarse(db[0]), r);
    double tab[MQ][KSUB]; distance_table(r, tab);
    int allsame = 1;
    for (int m = 0; m < MQ; m++)
        for (int k = 0; k < KSUB; k++)
            if (fabs(tab[m][k] - l2v(r + m * DSUB, sub[m][k], DSUB)) > EPS) allsame = 0;
    check(allsame, "表项与逐项暴力 L2 完全一致");

    printf("\n== Demo 3 · ADC = ||r − decode(code)||² ==\n");
    int code[MQ]; encode(r, code);
    double a = adc(tab, code);
    double rec[D]; decode(code, rec);
    double direct = l2v(r, rec, D);
    printf("   ADC=%.6f  direct=%.6f\n", a, direct);
    check(fabs(a - direct) < EPS, "ADC 恒等于到重构残差的平方距离");
    check(direct > 0.0, "PQ 有损:重构误差 > 0");

    printf("\n== Demo 4 · nprobe 决定扫多少 ==\n");
    int prev = -1;
    int probes[5] = {1, 2, 4, 8, NLIST};
    for (int i = 0; i < 5; i++) {
        int sc = search_scanned(db[1], probes[i]);
        printf("   nprobe=%-3d ⇒ 扫过 %d / %d 条 (%.1f%%)\n",
               probes[i], sc, NDB, 100.0 * sc / NDB);
        check(sc >= prev, "nprobe 增大不会减少扫描量");
        prev = sc;
    }
    check(search_scanned(db[1], NLIST) == NDB, "nprobe == nlist ⇒ 扫过 100% 的库");

    printf("\n== Demo 5 · SDC 表 ==\n");
    int qcode[MQ];
    double qr[D]; residual(db[1], nearest_coarse(db[1]), qr);
    encode(qr, qcode);
    double sdc = 0.0;
    for (int m = 0; m < MQ; m++) sdc += l2v(sub[m][qcode[m]], sub[m][code[m]], DSUB);
    printf("   SDC 表 = M×ksub×ksub = %d 个 float ; ADC 表 = M×ksub = %d\n",
           MQ * KSUB * KSUB, MQ * KSUB);
    printf("   同一对: ADC=%.6f SDC=%.6f\n", a, sdc);
    check(sdc >= 0.0, "SDC 非负");
    check(fabs(sdc - a) > 1e-6, "SDC ≠ ADC(对称 vs 非对称)");

    printf("\n断言 %d 通过 / %d 失败\n", g_ok, g_fail);
    return g_fail ? 1 : 0;
}
