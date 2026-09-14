/* 随机森林 C 参考实现:bootstrap 自助采样 + 逐节点随机特征子集 + OOB + 置换重要性。
 *
 * 权威来源(与 random_forest.py 同一批,实际读过):
 *   L. Breiman (2001) Random Forests
 *     https://www.stat.berkeley.edu/~breiman/randomforest2001.pdf
 *     §3.1 OOB:对每个训练样本 (x,y),只在 bootstrap 集不含 (x,y) 的树上聚合投票,
 *               得到 out-of-bag 分类器;其在训练集上的错误率即 OOB 误差。
 *               每个 bootstrap 集平均留下约 1/3 样本在外,故 OOB 只用约 1/3 的树,
 *               会**略高估**当前误差,但估计本身**无偏**。
 *     §3   随机特征:at a given node, F variables are randomly selected
 *     §10  变量重要性:逐列打乱后看 OOB 误分类比例的上升
 *     定理 2.3  PE* ≤ ρ̄(1−s²)/s²
 *   scikit-learn 1.9《1.11. Ensembles》 https://scikit-learn.org/stable/modules/ensemble.html
 *     分类默认 max_features="sqrt";Extra-Trees 默认 bootstrap=False(故其无 OOB)
 *
 * 编译:gcc -O2 -o random_forest random_forest.c -lm
 * 说明:C 版为固定容量的节点池(不 malloc),树深上限 6、每棵树最多 127 个节点,
 *       恰好够演示"自助采样 + 随机特征子集 + OOB"这三件事;
 *       阈值枚举用一次 qsort + 前缀计数扫描(O(n log n)),与 Python 版同构。
 */

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#define NPT 240
#define NFEAT 4
#define NTREE 120
#define MAXDEPTH 6
#define MINLEAF 2
#define MF 2 /* ceil(sqrt(NFEAT)) = 2 */
#define MAXNODE (NTREE * 128)

typedef struct {
    int feat;
    double thr;
    int left, right; /* -1 表示叶 */
    int label;
} Node;

static double X[NPT][NFEAT];
static int Y[NPT];
static Node nd[MAXNODE];
static int nnode = 0;
static int inbag[NTREE][NPT]; /* 放文件级,避免 main 的栈帧过大 */

/* ---- 线性同余(与 Python/Go 版同序列)---- */
static unsigned long long rs;
static void rseed(unsigned long long s) { rs = s; }
static unsigned long long rnext(void)
{
    rs = rs * 6364136223846793005ULL + 1442695040888963407ULL;
    return rs >> 11;
}
static int rrange(int n) { return (int)(rnext() % (unsigned long long)n); }

/* ---- 数据:y = 1 当 f0²+f1² < 1;f2/f3 为纯噪声 ---- */
static double uni(void) { return (double)rnext() / (double)(1ULL << 53); }

static void make_data(void)
{
    int i;
    rseed(20260914ULL);
    for (i = 0; i < NPT; i++) {
        double f0 = uni() * 4.0 - 2.0, f1 = uni() * 4.0 - 2.0;
        X[i][0] = f0;
        X[i][1] = f1;
        X[i][2] = uni() * 4.0 - 2.0;
        X[i][3] = uni() * 4.0 - 2.0;
        Y[i] = (f0 * f0 + f1 * f1 < 1.0) ? 1 : 0;
    }
}

/* ---- qsort 比较器(需要知道按哪一列排序,用文件级静态变量)---- */
static int g_sortfeat = 0;
static int cmp_idx(const void *a, const void *b)
{
    double xa = X[*(const int *)a][g_sortfeat];
    double xb = X[*(const int *)b][g_sortfeat];
    if (xa < xb) return -1;
    if (xa > xb) return 1;
    return 0;
}

static double gini_of(int *idx, int n)
{
    int i, c0 = 0;
    double p;
    if (n <= 0) return 0.0;
    for (i = 0; i < n; i++) if (Y[idx[i]] == 0) c0++;
    p = (double)c0 / (double)n;
    return 1.0 - p * p - (1.0 - p) * (1.0 - p);
}

/* 返回节点 id;idx 为该节点样本索引(可能含重复,因 bootstrap 有放回) */
static int build(int *idx, int n, int depth)
{
    int id = nnode++;
    int c0 = 0, i, k, f;
    int subset[NFEAT], nsub = 0;
    double parent, best_gain = 1e-12;
    int bestfeat = -1;
    double bestthr = 0.0;
    int buf[NPT];

    for (i = 0; i < n; i++) if (Y[idx[i]] == 0) c0++;
    nd[id].left = nd[id].right = -1;
    nd[id].label = (c0 * 2 >= n) ? 0 : 1; /* 参数更新前的多数类 */
    if (c0 == 0 || c0 == n || depth >= MAXDEPTH || n < 2 * MINLEAF) return id;

    /* 逐节点随机特征子集(不放回抽 MF 个)—— Breiman §3 */
    for (f = 0; f < NFEAT; f++) subset[nsub++] = f;
    for (i = NFEAT - 1; i > 0; i--) {
        int s = rrange(i + 1), t = subset[i];
        subset[i] = subset[s];
        subset[s] = t;
    }
    nsub = MF;

    parent = gini_of(idx, n);
    for (k = 0; k < nsub; k++) {
        int left0 = 0;
        int *sorted = buf;
        f = subset[k];
        for (i = 0; i < n; i++) sorted[i] = idx[i];
        g_sortfeat = f;
        qsort(sorted, (size_t)n, sizeof(int), cmp_idx);
        for (i = 0; i < n - 1; i++) {
            double gl, gr, gain;
            int nl, nr;
            if (Y[sorted[i]] == 0) left0++;
            if (X[sorted[i]][f] == X[sorted[i + 1]][f]) continue; /* 同值不可切 */
            nl = i + 1;
            if (nl < MINLEAF || n - nl < MINLEAF) continue;
            nr = n - nl;
            gl = 1.0 - ((double)left0 / nl) * ((double)left0 / nl)
                     - ((double)(nl - left0) / nl) * ((double)(nl - left0) / nl);
            gr = 1.0 - ((double)(c0 - left0) / nr) * ((double)(c0 - left0) / nr)
                     - ((double)(nr - (c0 - left0)) / nr) * ((double)(nr - (c0 - left0)) / nr);
            gain = parent - ((double)nl / n) * gl - ((double)nr / n) * gr;
            if (gain > best_gain) {
                best_gain = gain;
                bestfeat = f;
                bestthr = 0.5 * (X[sorted[i]][f] + X[sorted[i + 1]][f]);
            }
        }
    }
    if (bestfeat < 0) return id;

    {
        int nl = 0;
        for (i = 0; i < n; i++)
            if (X[idx[i]][bestfeat] <= bestthr) buf[nl++] = idx[i]; /* 原地压实 */
        for (i = 0, k = nl; i < n; i++)
            if (X[idx[i]][bestfeat] > bestthr) buf[k++] = idx[i];
        if (nl == 0 || nl == n) return id;
        nd[id].feat = bestfeat;
        nd[id].thr = bestthr;
        nd[id].left = build(buf, nl, depth + 1);
        nd[id].right = build(buf + nl, n - nl, depth + 1);
    }
    return id;
}

static int predict_node(int id, const double *x)
{
    while (nd[id].left >= 0)
        id = (x[nd[id].feat] <= nd[id].thr) ? nd[id].left : nd[id].right;
    return nd[id].label;
}

int main(void)
{
    int root[NTREE];
    int t, i, f;
    double oob_correct = 0.0, mean_oob_frac = 0.0;
    int oob_total = 0;

    make_data();
    printf("======================================================================\n");
    printf("随机森林 C 实现  N=%d  特征=%d  树=%d  深=%d  mtry=%d\n",
           NPT, NFEAT, NTREE, MAXDEPTH, MF);
    printf("======================================================================\n");

    for (t = 0; t < NTREE; t++) {
        int idx[NPT];
        rseed(0x5EED0000ULL + (unsigned long long)t);
        for (i = 0; i < NPT; i++) idx[i] = rrange(NPT); /* 有放回 bootstrap */
        for (i = 0; i < NPT; i++) inbag[t][i] = 0;
        for (i = 0; i < NPT; i++) inbag[t][idx[i]] = 1;
        root[t] = build(idx, NPT, 0);
    }
    printf("\n节点池用量 = %d / %d(每棵树最多 2^(depth+1)-1 = 127 个节点)\n",
           nnode, MAXNODE);

    /* 每个 bootstrap 集平均留多少样本在袋外(理论 1−1/e ≈ 0.632 被抽中) */
    for (t = 0; t < NTREE; t++) {
        int out = 0;
        for (i = 0; i < NPT; i++) if (!inbag[t][i]) out++;
        mean_oob_frac += (double)out / (double)NPT;
    }
    mean_oob_frac /= (double)NTREE;

    /* ---- OOB 投票(Breiman §3.1):只统计"没见过该样本"的树 ---- */
    for (i = 0; i < NPT; i++) {
        int v0 = 0, v1 = 0;
        for (t = 0; t < NTREE; t++) {
            int p;
            if (inbag[t][i]) continue; /* 该树见过 i,不能参与 i 的 OOB 投票 */
            p = predict_node(root[t], X[i]);
            if (p == 0) v0++; else v1++;
        }
        if (v0 + v1 == 0) continue;
        oob_total++;
        if ((v1 > v0 ? 1 : 0) == Y[i]) oob_correct++;
    }
    printf("\n每棵树平均留下的袋外样本比例 = %.4f(理论 1−(1−1/N)^N → 1−1/e ≈ 0.632,\n",
           mean_oob_frac);
    printf("  即每个 bootstrap 集平均把约 36.8%% 样本留在袋外;但由于树数多,\n");
    printf("  每个样本几乎总能找到至少一棵'没见过它'的树)\n");
    printf("样本被至少一棵树留作袋外的比例 = %d/%d = %.4f\n\n",
           oob_total, NPT, (double)oob_total / (double)NPT);
    printf("OOB 准确率 = %.4f   OOB 误差 = %.4f\n",
           oob_correct / oob_total, 1.0 - oob_correct / oob_total);

    /* ---- 训练集(全体树)准确率,与 OOB 对照 ---- */
    {
        int ok = 0;
        for (i = 0; i < NPT; i++) {
            int v0 = 0, v1 = 0;
            for (t = 0; t < NTREE; t++) {
                int p = predict_node(root[t], X[i]);
                if (p == 0) v0++; else v1++;
            }
            if ((v1 > v0 ? 1 : 0) == Y[i]) ok++;
        }
        printf("全体树在训练集上的准确率 = %.4f(远高于 OOB:森林把训练集背下来了)\n",
               (double)ok / (double)NPT);
    }

    /* ---- §10 置换重要性:打乱第 f 列后 OOB 误分类增加量 ---- */
    printf("\n变量重要性(§10 置换重要性,第 f 列打乱后 OOB 误分类比例的上升):\n");
    {
        int base_bad = 0;
        for (i = 0; i < NPT; i++) {
            int v0 = 0, v1 = 0;
            for (t = 0; t < NTREE; t++) {
                if (inbag[t][i]) continue;
                if (predict_node(root[t], X[i]) == 0) v0++; else v1++;
            }
            if (v0 + v1 && (v1 > v0 ? 1 : 0) != Y[i]) base_bad++;
        }
        for (f = 0; f < NFEAT; f++) {
            int perm[NPT], bad = 0;
            double x[NPT][NFEAT];
            for (i = 0; i < NPT; i++) perm[i] = i;
            rseed(0xBEEF0000ULL + (unsigned long long)f);
            for (i = NPT - 1; i > 0; i--) { /* Fisher-Yates */
                int s = rrange(i + 1), tmp = perm[i];
                perm[i] = perm[s];
                perm[s] = tmp;
            }
            for (i = 0; i < NPT; i++)
                for (t = 0; t < NFEAT; t++) x[i][t] = X[i][t];
            for (i = 0; i < NPT; i++) x[i][f] = X[perm[i]][f]; /* 第 f 列整体打乱 */
            for (i = 0; i < NPT; i++) {
                int v0 = 0, v1 = 0;
                for (t = 0; t < NTREE; t++) {
                    if (inbag[t][i]) continue;
                    if (predict_node(root[t], x[i]) == 0) v0++; else v1++;
                }
                if (v0 + v1 && (v1 > v0 ? 1 : 0) != Y[i]) bad++;
            }
            printf("   f%d  置换重要性 = %+.4f  (打乱后误分类 %d vs 基准 %d)\n",
                   f, (double)(bad - base_bad) / (double)oob_total, bad, base_bad);
        }
    }
    printf("---- f0/f1 应显著为正(它们是真实边界),f2/f3 应≈0 -------------------\n");
    return 0;
}
