"""模型评估(二):ROC 曲线、AUC 的三种等价算法(Bamber 定理)与多分类 OvR AUC。

权威来源(实际读过):
- scikit-learn 1.9《3.3. Metrics and scoring》
  https://scikit-learn.org/stable/modules/model_evaluation.html
    ROC 坐标:TPR = TP/(TP+FN)、FPR = FP/(FP+TN);AUC = ∫₀¹ TPR(FPR⁻¹(u)) du
- scikit-learn《roc_auc_score》
  https://scikit-learn.org/stable/modules/generated/sklearn.metrics.roc_auc_score.html
    多分类 OvR macro = (1/(c(c−1))) Σj Σ_{k≠j} AUC(j|k)
                         = (1/(c(c−1))) Σj Σ_{k>j} (AUC(j|k) + AUC(k|j))
    多分类 OvR micro:TPR = ΣTPc/Σ(TPc+FNc),FPR = ΣFPc/Σ(FPc+TNc)
- scikit-learn 示例《plot_roc》
  https://scikit-learn.org/stable/auto_examples/model_selection/plot_roc.html
- AUC 的秩和形式(Mann-Whitney U / Wilcoxon)与 Bamber 等价定理:
    AUC = (Σ_{pos} rank_i − n_pos(n_pos+1)/2) / (n_pos·n_neg)
        = P(score_pos > score_neg) + 0.5·P(score_pos = score_neg)     并列取平均秩
  Gini = 2·AUC − 1(Somers' D)

依赖 model_eval.py 里的交叉验证与 P/R/F1;合成数据见 _synth.py。
运行:`python roc_auc.py`
"""

from __future__ import annotations

from _synth import binary_data, multi_data

# ------------------------------- ROC 与 AUC ------------------------------- #


def roc_curve(y, scores):
    """按阈值从高到低扫,返回 (fpr, tpr, thresholds);首元素是坐标原点 (0,0)。

    并列分数必须在同一步一起并入,否则会出现不该有的折线点(等价于没取平均秩)。
    """
    order = sorted(range(len(y)), key=lambda i: -scores[i])
    P, N = sum(y), len(y) - sum(y)
    fpr, tpr, thr = [0.0], [0.0], [float("inf")]
    tp = fp = i = 0
    while i < len(y):
        j, s = i, scores[order[i]]
        while j < len(y) and scores[order[j]] == s:
            tp += y[order[j]]
            fp += 1 - y[order[j]]
            j += 1
        fpr.append(fp / N)
        tpr.append(tp / P)
        thr.append(s)
        i = j
    return fpr, tpr, thr


def auc_trapezoid(fpr, tpr):
    """梯形法数值积分 ∫TPR d(FPR) —— sklearn 对 ROC 点求面积用的就是这个。"""
    return sum((fpr[i + 1] - fpr[i]) * (tpr[i + 1] + tpr[i]) * 0.5
               for i in range(len(fpr) - 1))


def average_ranks(scores):
    """升序秩;**并列取平均秩**(否则秩和统计量会随排序稳定性漂移)。"""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    out = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for t in range(i, j + 1):
            out[order[t]] = (i + j) * 0.5 + 1.0                # 1-based 平均秩
        i = j + 1
    return out


def auc_rank_sum(y, scores):
    """AUC 的秩和形式(即 Mann-Whitney U / Wilcoxon,Bamber 等价定理):

        AUC = (Σ_{pos} rank_i − n_pos(n_pos+1)/2) / (n_pos·n_neg)
            = P(score_pos > score_neg) + 0.5·P(score_pos = score_neg)
    """
    npos = sum(y)
    r = average_ranks(scores)
    return (sum(r[i] for i in range(len(y)) if y[i] == 1) - npos * (npos + 1) / 2.0) \
        / (npos * (len(y) - npos))


def auc_bruteforce(y, scores):
    """按 Bamber 定义直接数有序对(平局计 0.5),用于验证秩和公式。O(n²),仅小样本可用。"""
    w = sum((1.0 if scores[i] > scores[j] else 0.5 if scores[i] == scores[j] else 0.0)
            for i in range(len(y)) if y[i] == 1
            for j in range(len(y)) if y[j] == 0)
    return w / (sum(y) * (len(y) - sum(y)))


# --------------------------- 多分类 AUC(OvR) --------------------------- #


def ovr_auc_macro(y, S, labels):
    """(1/(c(c−1))) Σj Σ_{k>j} (AUC(j|k) + AUC(k|j))。

    AUC(j|k) 指**只看类 j 与类 k 的样本**、用第 j 列得分算出的二分类 AUC。
    两列得分不同 ⇒ AUC(j|k) + AUC(k|j) ≠ 1,不能互相代替。
    """
    K = len(labels)
    pos = {c: k for k, c in enumerate(labels)}
    tot = 0.0
    for j in range(K):
        for k in range(j + 1, K):
            for a in (j, k):
                yb, sb = [], []
                for i, yi in enumerate(y):
                    if pos.get(yi) in (j, k):
                        yb.append(1 if pos[yi] == j else 0)
                        sb.append(S[i][a])
                if a == k:                                     # 用第 k 列时须翻转标签
                    yb = [1 - v for v in yb]
                tot += auc_rank_sum(yb, sb)
    return tot / (K * (K - 1))


def ovr_auc_micro(y, S, labels):
    """把 (样本 × 类别) 展平成单个二分类问题后算 AUC。

    此时 TPR = TP/(TP+FN) = ΣTPc/Σ(TPc+FNc),与 sklearn 的 micro OvR 定义一致。
    """
    pos = {c: k for k, c in enumerate(labels)}
    fy, fs = [], []
    for i, yi in enumerate(y):
        for j in range(len(labels)):
            fy.append(1 if pos.get(yi) == j else 0)
            fs.append(S[i][j])
    return auc_rank_sum(fy, fs)


# ---------------------------------- 演示 ---------------------------------- #


def main() -> None:
    print("=" * 76)
    print("模型评估(二):ROC 曲线 + AUC 的三种算法(Bamber 定理)+ 多分类 OvR AUC")
    print("=" * 76)

    yte, scte = [v[300:] for v in binary_data(400, 5)[1:]]
    fpr, tpr, _ = roc_curve(yte, scte)
    a_trap, a_rank = auc_trapezoid(fpr, tpr), auc_rank_sum(yte, scte)
    print(f"\n① 二分类 ROC(n=100,正例率 {sum(yte) / len(yte):.3f})")
    print(f"   曲线 {len(fpr)} 个点(含原点);前 6 点 = "
          + "  ".join(f"({fpr[i]:.3f},{tpr[i]:.3f})" for i in range(6)))
    print(f"   AUC 梯形积分 ∫TPR d(FPR)  = {a_trap:.10f}")
    print(f"   AUC 秩和(Mann-Whitney U)  = {a_rank:.10f}")
    print(f"   AUC 数有序对(Bamber 定义) = {auc_bruteforce(yte, scte):.10f}")
    print(f"   Gini = 2·AUC − 1 = {2 * a_trap - 1:.6f}   (Somers' D = 基尼系数)")
    print(f"   最大 |梯形 − 秩和| = {abs(a_trap - a_rank):.2e} "
          "⇒ 面积与秩和是同一统计量的两种算法")
    print("   —— 该恒等只在**并列取平均秩**时成立;随手打破平局,秩和就会偏离面积")
    print("   —— AUC 的另一个名字是 c 统计量:P(随机正例得分 > 随机负例得分)")

    _, ym, Sm = multi_data(450, 23)
    per = [auc_rank_sum([1 if v == j else 0 for v in ym], [S[j] for S in Sm]) for j in range(3)]
    print("\n② 三分类 OvR AUC(每类各得一列得分 S[:,j])")
    print(f"   各类 vs 其余(one-vs-rest)AUC = [{', '.join(f'{v:.4f}' for v in per)}]")
    print(f"   macro(成对平均) = {ovr_auc_macro(ym, Sm, [0, 1, 2]):.6f}")
    print(f"   micro(展平聚合)= {ovr_auc_micro(ym, Sm, [0, 1, 2]):.6f}")
    print("   —— macro 把 c(c−1)/2 个成对问题等权平均、对**小类**更敏感;")
    print("      micro 用 ΣTPc/Σ(TPc+FNc) 汇总,被**大类**主导")
    print(f"   K=2 退化检验:仅类 0 vs 其余的二分类 AUC = "
          f"{auc_rank_sum([1 if v == 0 else 0 for v in ym], [S[0] for S in Sm]):.6f}"
          f"(= per[0] = {per[0]:.6f})")
    print("   —— 只有 2 类时 macro 公式只剩一对,自然退化回普通二分类 AUC")
    print("-" * 76)


if __name__ == "__main__":
    main()
