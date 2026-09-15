#!/usr/bin/env python3
"""Target 编码(目标均值编码)的泄漏机理:K 折内编码、平滑与三种"防泄漏"方案对比。

要回答的问题:为什么"用类别对应的目标均值替换类别"这个看起来最自然的编码方式会
把模型带沟里?三条主线的权威依据:

1. **平滑公式**(Micci-Barreca 2001,sklearn `TargetEncoder` 的算法出处)
   S_i = λ_i·(n_iY/n_i) + (1-λ_i)·(n_Y/n),  λ_i = n_i/(m + n_i)
   `smooth="auto"` 时 m 由经验贝叶斯给出:m = σ_i²/τ²
2. **交叉拟合(cross fitting)** —— sklearn 原文:
   "In `TargetEncoder.fit_transform`, the training data is split into k folds and each fold is
    encoded using the encodings learnt using the other k-1 folds";因此
   **`fit(X,y).transform(X)` 不等于 `fit_transform(X,y)`**,而 `fit` 被官方标注为
   "discouraged … because it can introduce data leakage"
3. **有序目标统计(CatBoost)** —— 用随机排列,每个样本只用**排在它前面**的样本统计:
   x̂_i = (Σ_{j≺i, c_j=c_i} y_j + a·P) / (n_{i,c} + a)
   Prokhorenkova et al. 2018 的动机是消除 boosting 里的 prediction shift

本脚本用纯标准库复现:
  - 官方 docstring 的数值锚点(smooth=1.0 → [21, 80.8, 43.2];smooth=5000 → [44.1, 44.4, 44.3])
  - 高基数噪声特征上的三种编码方式对诚实 AUC 的影响
"""

import math
import random

# -------------------------------------------------------------------------- 基础工具


def sigmoid(z):
    if z < -35.0:
        return 0.0
    if z > 35.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def auc(scores, labels):
    """秩和法算 AUC(AUC = P(正样本得分 > 负样本得分),并列取平均秩)。"""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    pos = [ranks[i] for i in range(len(labels)) if labels[i] == 1]
    neg = [ranks[i] for i in range(len(labels)) if labels[i] == 0]
    if not pos or not neg:
        return float("nan")
    return (math.fsum(pos) - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def logreg_fit(X, y, epochs=400, lr=0.5, l2=0.0):
    """最朴素的 L2 逻辑回归(全批量梯度下降),够用来暴露编码差异。"""
    n, p = len(X), len(X[0]) + 1
    w = [0.0] * p
    for _ in range(epochs):
        g = [0.0] * p
        for i in range(n):
            z = w[0] + math.fsum(w[j + 1] * X[i][j] for j in range(p - 1))
            err = sigmoid(z) - y[i]
            g[0] += err
            for j in range(p - 1):
                g[j + 1] += err * X[i][j]
        for j in range(p):
            w[j] -= lr * (g[j] / n + l2 * w[j])
    return w


def logreg_score(w, X):
    return [w[0] + math.fsum(w[j + 1] * row[j] for j in range(len(row))) for row in X]


# ------------------------------------------------------------------ 编码方式


def target_encoding(categories, y, smooth=1.0, global_mean=None):
    """S_i = λ_i·mean_i + (1-λ_i)·global_mean,λ_i = n_i/(smooth + n_i)。"""
    gm = global_mean if global_mean is not None else math.fsum(y) / len(y)
    out = {}
    for c in set(categories):
        ys = [y[i] for i in range(len(y)) if categories[i] == c]
        n_i = len(ys)
        lam = n_i / (smooth + n_i)
        out[c] = lam * (math.fsum(ys) / n_i) + (1.0 - lam) * gm
    return out, gm


def auto_smooth(categories, y):
    """smooth="auto":m = σ_i²/τ²(σ_i² 为该类别内 y 的方差,τ² 为全局方差),取平均。"""
    gm = math.fsum(y) / len(y)
    tau2 = math.fsum((v - gm) ** 2 for v in y) / len(y)
    ms = []
    for c in set(categories):
        ys = [y[i] for i in range(len(y)) if categories[i] == c]
        if len(ys) < 2:
            continue
        mc = math.fsum(ys) / len(ys)
        s2 = math.fsum((v - mc) ** 2 for v in ys) / len(ys)
        ms.append(s2 / tau2 if tau2 > 0 else 1.0)
    return math.fsum(ms) / len(ms) if ms else 1.0


def cross_fitted_encoding(categories, y, n_folds=5, smooth=1.0, seed=0):
    """K 折交叉拟合:每一折的编码只用**其余 K-1 折**学习(sklearn fit_transform 的做法)。"""
    n = len(y)
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    bounds = [(k * n) // n_folds for k in range(n_folds + 1)]
    enc = [0.0] * n
    for k in range(n_folds):
        val = set(idx[bounds[k]:bounds[k + 1]])
        tr_c = [categories[i] for i in range(n) if i not in val]
        tr_y = [y[i] for i in range(n) if i not in val]
        table, gm = target_encoding(tr_c, tr_y, smooth)
        for i in sorted(val):
            enc[i] = table.get(categories[i], gm)     # 训练期没见过的类别 → 全局均值
    full_table, gm = target_encoding(categories, y, smooth)   # 供 transform 测试集用
    return enc, full_table, gm


def ordered_target_statistics(categories, y, prior_weight=1.0, n_permutations=4, seed=0):
    """CatBoost 式有序目标统计:每个样本只用排列中排在它前面的样本。

    x̂_i = (Σ_{j≺i, c_j=c_i} y_j + a·P) / (n_{i,c} + a),多个随机排列取平均以降方差。
    """
    n = len(y)
    gm = math.fsum(y) / len(y)
    cats = sorted(set(categories))
    pos = {c: i for i, c in enumerate(cats)}
    acc = [[0.0] * n_permutations for _ in cats]
    cnt = [[0] * n_permutations for _ in cats]
    out = [0.0] * n
    for r in range(n_permutations):
        order = list(range(n))
        random.Random(seed + r).shuffle(order)
        for i in order:
            ci = pos[categories[i]]
            a, c = acc[ci][r], cnt[ci][r]
            out[i] += (a + prior_weight * gm) / (c + prior_weight)   # 只用"过去"的标签
            acc[ci][r] = a + y[i]
            cnt[ci][r] = c + 1
    return [v / n_permutations for v in out]


# ------------------------------------------------------------------ 实验


def exp1_sklearn_anchor():
    """复现 sklearn TargetEncoder docstring 的数值锚点(smooth 公式的直接证据)。"""
    X = ["dog"] * 20 + ["cat"] * 30 + ["snake"] * 38
    y = [90.3] * 5 + [80.1] * 15 + [20.4] * 5 + [20.1] * 25 + [21.2] * 8 + [49] * 30
    t_low, gm = target_encoding(X, y, smooth=1.0)
    t_high, _ = target_encoding(X, y, smooth=5000.0)
    order = ["cat", "dog", "snake"]
    print("== 实验 1:对齐 sklearn TargetEncoder docstring 的数值锚点 ==")
    print(f"  target_mean_ = {gm:.1f}                         期望 44.3")
    print(f"  smooth=1.0    encodings_ = {[round(t_low[c], 1) for c in order]}   期望 [21, 80.8, 43.2]")
    print(f"  smooth=5000   encodings_ = {[round(t_high[c], 1) for c in order]}   期望 [44.1, 44.4, 44.3]")
    print(f"  smooth='auto' → m = {auto_smooth(X, y):.4f}(该类别内方差 / 全局方差)")
    print("  → 大 smooth 把权重压向全局均值(官方:Large smoothing factors will put more weight on"
          " the global mean)")
    print("  → 注:官方 docstring 里 smooth=1.0 的首个值显示为 21,按公式算是 20.93"
          "(文档输出做了取整)")
    assert abs(gm - 44.3) < 0.05
    assert all(abs(t_low[c] - e) < 0.1 for c, e in zip(order, (20.93, 80.8, 43.2)))
    assert all(abs(t_high[c] - e) < 0.05 for c, e in zip(order, (44.1, 44.4, 44.3)))


def exp2_leakage_high_cardinality():
    """高基数噪声特征:编码在哪一步学、用的谁的目标,决定了分数是真是假。

    四种做法(全部用同一个逻辑回归、同一份 signal 特征):
      A 编码在训练集上学 → 训练表示泄漏(自己的标签参与了编码),测试表示干净
      B 编码在**全量数据(含测试)**上学 → 连测试表示都泄漏,连测试分都不可信
      C K 折交叉拟合 → 训练表示用"别人的标签",测试用于模型评估
      D 常数编码 → 完全不带信息,作为 AUC 的对照基线
    """
    rng = random.Random(7)
    n, n_cat = 3000, 1500
    cats = [f"id{rng.randrange(n_cat)}" for _ in range(n)]
    signal = [rng.gauss(0.0, 1.0) for _ in range(n)]
    y = [1 if sigmoid(1.6 * signal[i] - 0.4 + rng.gauss(0, 0.8)) > rng.random() else 0
         for i in range(n)]
    n_tr = 2000
    tr, te = list(range(n_tr)), list(range(n_tr, n))

    tbl_tr, gm_tr = target_encoding([cats[i] for i in tr], [y[i] for i in tr], smooth=1.0)
    tbl_all, gm_all = target_encoding(cats, y, smooth=1.0)
    cf, _, _ = cross_fitted_encoding(cats, y, n_folds=5, smooth=1.0)
    ots = ordered_target_statistics(cats, y, prior_weight=1.0, n_permutations=4)

    def run(name, enc_tr, enc_te):
        Xtr = [[enc_tr[k]] + [signal[i]] for k, i in enumerate(tr)]
        Xte = [[enc_te[k]] + [signal[i]] for k, i in enumerate(te)]
        w = logreg_fit(Xtr, [y[i] for i in tr], epochs=300, lr=0.5)
        a_tr = auc(logreg_score(w, Xtr), [y[i] for i in tr])
        a_te = auc(logreg_score(w, Xte), [y[i] for i in te])
        print(f"  {name:<26} {a_tr:.3f}      {a_te:.3f}")
        return a_tr, a_te

    print("\n== 实验 2:高基数纯噪声特征(3000 样本 / 1500 个类别;训练 2000 / 测试 1000) ==")
    print("  编码方式                       训练 AUC   测试 AUC")
    a = run("A 训练集内全量编码(泄漏)",
            [tbl_tr[cats[i]] for i in tr], [tbl_tr.get(cats[i], gm_tr) for i in te])
    b = run("B 全量数据编码(测试也泄漏)",
            [tbl_all[cats[i]] for i in tr], [tbl_all[cats[i]] for i in te])
    c = run("C K 折交叉拟合", [cf[i] for i in tr], [cf[i] for i in te])
    d = run("D 常数编码(对照基线)", [gm_tr] * n_tr, [gm_all] * len(te))
    o = run("E CatBoost 有序统计", [ots[i] for i in tr], [ots[i] for i in te])
    print("  → A:训练 AUC 被自己的标签抬到离谱,测试分才是真相 —— 这也是交叉验证分数会"
          "虚高的原因;\n"
          "     B:连测试分都变成谎言,因为它「知道」了测试样本的标签;\n"
          "     C/E:把训练期表示换成「别人的标签」,训练口径与测试一致,分数回到基线附近;\n"
          "     官方:fit_transform uses a cross fitting scheme to prevent target leakage and"
          " overfitting in\n"
          "     downstream predictors, especially for non-informative high-cardinality"
          " categorical variables")
    assert a[0] - a[1] > 0.05, "A 的训练分应明显高于测试分(训练表示泄漏)"
    assert abs(b[1] - d[1]) > 0.05, "B 的测试分应被测试标签泄漏抬高"
    assert abs(c[0] - c[1]) < 0.05 and abs(o[0] - o[1]) < 0.05, "C/E 的训练测试口径应当一致"
    _ = (n_cat,)


def exp3_smooth_sweep():
    """平滑的作用:类别内样本越少,越该被拉回全局均值。"""
    rng = random.Random(11)
    n = 1200
    cats = [f"c{rng.randrange(60)}" if i % 3 else f"rare{rng.randrange(400)}" for i in range(n)]
    hot = {f"c{i}" for i in range(0, 60, 3)}      # 有真实信号的类别
    y = [1 if rng.random() < (0.75 if c in hot else 0.25) else 0 for c in cats]
    print("\n== 实验 3:smooth 的扫掠(1200 样本,类别频次差异很大) ==")
    print("  smooth   少数类别的编码(×10)      多数类别的编码(×10)")
    for sm in (0.0, 1.0, 10.0, 100.0, 1e6):
        table, gm = target_encoding(cats, y, smooth=max(sm, 1e-9))
        rare = [table[c] for c in set(cats) if c.startswith("rare")][:10]
        many = [table[c] for c in set(cats) if c.startswith("c")][:10]
        print(f"  {sm:>8.0f}  {[round(v, 3) for v in rare]}   {[round(v, 3) for v in many]}")
    print("  → smooth→∞ 全部退化成全局均值(编码不再带类别信息);smooth→0 则稀有类别"
          "拿自己的单样本均值,\n"
          "     等于把噪声当信号 —— 官方:'Larger smooth value will put more weight on the"
          " global target mean'")


if __name__ == "__main__":
    exp1_sklearn_anchor()
    exp2_leakage_high_cardinality()
    exp3_smooth_sweep()
    print("\n全部断言通过。")
