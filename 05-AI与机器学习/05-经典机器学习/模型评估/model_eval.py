"""模型评估(一):交叉验证折的下标 + 混淆矩阵 + 精确率/召回率/F_β 的多口径平均。

权威来源(实际读过):
- scikit-learn 1.9《3.3. Metrics and scoring》
  https://scikit-learn.org/stable/modules/model_evaluation.html
    混淆矩阵:"matrix entry i, j is the number of observations actually in group i,
      but predicted to be in group j"(**行 = 真实类**,列 = 预测类)
    accuracy  = (1/n) Σ 1(ŷi = yi)
    precision = tp/(tp+fp)                  recall = tp/(tp+fn)
    F_β       = (1+β²)·tp / ((1+β²)·tp + fp + β²·fn)
    P(A,B) = |A∩B|/|B|、R(A,B) = |A∩B|/|A|;micro / macro / weighted / samples 四种口径
- scikit-learn《3.1. Cross-validation》
  https://scikit-learn.org/stable/modules/cross_validation.html
    KFold 的测试折 i 取 [n·i/k, n·(i+1)/k);docstring 例 X=["a","b","c","d"]、n_splits=2
      → 测试 [2 3] 再 [0 1];**KFold 默认 shuffle=False**(有序数据上各折就不是同分布)
    StratifiedKFold 保持各折类别比例;LOO / ShuffleSplit / GroupKFold / TimeSeriesSplit 语义

ROC / AUC 与多分类 OvR AUC 见同目录 roc_auc.py;合成数据见 _synth.py。
运行:`python model_eval.py`
"""

from __future__ import annotations

from _synth import binary_data, imbalanced_data, lcg, mean_sd

# ---------------------------- ① 交叉验证折的下标 ---------------------------- #


def kfold_indices(n, k):
    """测试折 i 取 [n·i/k, n·(i+1)/k) —— 与 sklearn `_BaseKFold` 的整数切分一致,不重不漏。"""
    return [([t for t in range(n) if t not in set(range(n * i // k, n * (i + 1) // k))],
             list(range(n * i // k, n * (i + 1) // k))) for i in range(k)]


def stratified_kfold_indices(y, k, seed=0):
    """分层 K 折:每个类别**内部**各自均分到 k 折 ⇒ 各折类别比例≈整体比例。

    对极不平衡数据至关重要 —— 普通 K 折可能让某折完全没有少数类样本,
    该折 recall = 0/(0+0) 无定义,折间方差被虚假放大。
    """
    r, folds, by_cls = lcg(seed), [[] for _ in range(k)], {}
    for i, c in enumerate(y):
        by_cls.setdefault(c, []).append(i)
    for c in sorted(by_cls):
        idx = by_cls[c]
        for i in range(len(idx) - 1, 0, -1):                   # Fisher-Yates
            j = int(r() * (i + 1))
            idx[i], idx[j] = idx[j], idx[i]
        for t, v in enumerate(idx):
            folds[t % k].append(v)
    return [([t for t in range(len(y)) if t not in set(folds[i])], sorted(folds[i]))
            for i in range(k)]


# ---------------------------- ② 混淆矩阵与 P/R/F ---------------------------- #


def confusion_matrix(yt, yp, labels):
    """M[i][j] = 真实类 i、预测类 j 的样本数(**行 = 真实**),与 sklearn 一致。"""
    pos = {c: k for k, c in enumerate(labels)}
    M = [[0] * len(labels) for _ in labels]
    for a, b in zip(yt, yp):
        if a in pos and b in pos:
            M[pos[a]][pos[b]] += 1
    return M


def accuracy(yt, yp):
    """(1/n)Σ1(ŷi=yi)。类别极不平衡时极具误导性:全猜多数类也能很高。"""
    return sum(a == b for a, b in zip(yt, yp)) / len(yt)


def prf(yt, yp, labels, average="macro", beta=1.0):
    """精确率/召回率/F_β;average ∈ {'macro','weighted','micro',None}。

        precision = tp/(tp+fp)      recall = tp/(tp+fn)
        F_β = (1+β²)·tp / ((1+β²)·tp + fp + β²·fn)     β<1 偏精确率、β>1 偏召回率

    macro = 单类指标**等权**平均(小类不被淹没);weighted = 按 support 加权;
    micro = 先把各类 tp/fp/fn 汇总再算 ⇒ 被大类主导。类不平衡时三者会分叉。
    """
    M = confusion_matrix(yt, yp, labels)
    K = len(labels)
    tp = [M[i][i] for i in range(K)]
    fp = [sum(M[i][j] for i in range(K) if i != j) for j in range(K)]
    fn = [sum(M[i][j] for j in range(K) if j != i) for i in range(K)]
    sup = [tp[i] + fn[i] for i in range(K)]
    b2 = beta * beta

    def m(t, f, n):
        d = (1.0 + b2) * t + f + b2 * n
        return (t / (t + f) if t + f else 0.0, t / (t + n) if t + n else 0.0,
                (1.0 + b2) * t / d if d else 0.0)

    per = [m(tp[i], fp[i], fn[i]) for i in range(K)]
    if average is None:
        return [v[0] for v in per], [v[1] for v in per], [v[2] for v in per]
    if average == "micro":
        return m(sum(tp), sum(fp), sum(fn))
    w = [1.0] * K if average == "macro" else sup
    tw = sum(w) or 1.0
    return tuple(sum(w[i] * per[i][j] for i in range(K)) / tw for j in range(3))


# ---------------------------------- 演示 ---------------------------------- #


def main() -> None:
    print("=" * 76)
    print("模型评估(一):交叉验证折 + 混淆矩阵 + P/R/F1 多口径")
    print("=" * 76)

    print("\n① KFold 下标生成(对照 sklearn docstring:X=['a','b','c','d'], n_splits=2)")
    for tr, te in kfold_indices(4, 2):
        print(f"   训练 {tr}  测试 {te}")
    print(f"   —— 测试折区间 = [n·i/k, n·(i+1)/k),不重不漏;LOO(n=4) = "
          f"{[[i] for i in range(4)]}")

    X, y, sc = binary_data(400, 5)
    Xtr, ytr, sctr, yte, scte = X[:300], y[:300], sc[:300], y[300:], sc[300:]
    yp = [1 if v >= 0.5 else 0 for v in scte]
    M = confusion_matrix(yte, yp, [0, 1])
    print(f"\n② 二分类(n=400,正例率 {sum(y) / len(y):.3f});阈值 0.5 的混淆矩阵"
          "(行=真实类,**不对称** ⇒ 两个错分方向的业务代价不同)")
    print(f"   {'':<7}{'预测0':>7}{'预测1':>7}")
    for i, row in enumerate(M):
        print(f"   真实{i}  {row[0]:>7}{row[1]:>7}")
    print(f"   准确率 = {accuracy(yte, yp):.4f}")
    print(f"   {'口径':<11}{'precision':>11}{'recall':>9}{'F1':>9}")
    for avg in ("macro", "weighted", "micro", None):
        p, rr, f1 = prf(yte, yp, [0, 1], average=avg)
        if avg is None:
            print(f"   per-class    类0={p[0]:.4f}/{rr[0]:.4f}/{f1[0]:.4f}"
                  f"  类1={p[1]:.4f}/{rr[1]:.4f}/{f1[1]:.4f}")
        else:
            print(f"   {avg:<11}{p:>11.4f}{rr:>9.4f}{f1:>9.4f}")
    print("   —— macro 对各类等权、类不平衡时被小类拉低;micro/weighted 被大类主导")

    Xi, yi = imbalanced_data(600, 17)
    print(f"\n③ 极不平衡 + 已按 x0 排序的数据(正例率 {sum(yi) / len(yi):.4f})上的 5 折")
    print("   sklearn KFold 默认 shuffle=False → 直接切连续块,有序数据上各折不同分布")
    print(f"   {'方案':<15}{'各折正例数':<24}{'折间比例极差':>10}")
    for tag, folds in (("普通 KFold", kfold_indices(len(yi), 5)),
                       ("分层 Stratified", stratified_kfold_indices(yi, 5, seed=3))):
        cnt = [sum(yi[t] for t in te) for _, te in folds]
        rate = [c / len(te) for c, (_, te) in zip(cnt, folds)]
        print(f"   {tag:<13}{str(cnt):<26}{max(rate) - min(rate):>10.4f}")
    print("   —— 普通 K 折首折正例为 0:该折 recall = 0/(0+0) 无定义,折间方差被虚假放大")

    fold_acc, fold_auc = [], []
    for _, te in stratified_kfold_indices(ytr, 5, seed=9):
        fold_acc.append(accuracy([ytr[i] for i in te],
                                 [1 if sctr[i] >= 0.5 else 0 for i in te]))
        # 折内 AUC 用秩和公式(见 roc_auc.py);这里内联一段极简实现避免循环 import
        pos = [sctr[i] for i in te if ytr[i] == 1]
        neg = [sctr[i] for i in te if ytr[i] == 0]
        win = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg)
        fold_auc.append(win / (len(pos) * len(neg)))
    ma, sa = mean_sd(fold_acc)
    mr, sr = mean_sd(fold_auc)
    print("\n④ 5 折交叉验证(二分类)的均值 ± 标准差 —— 单点划分没有误差棒")
    print(f"   accuracy = {ma:.4f} ± {sa:.4f}   (各折 {[round(v, 4) for v in fold_acc]})")
    print(f"   AUC      = {mr:.4f} ± {sr:.4f}   (各折 {[round(v, 4) for v in fold_auc]})")
    print("   —— 折间标准差才是模型稳定性的证据;选模型时应看均值,报结论时须带方差")
    print("-" * 76)


if __name__ == "__main__":
    main()
