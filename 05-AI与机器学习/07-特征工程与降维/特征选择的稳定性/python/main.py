#!/usr/bin/env python3
"""特征选择的稳定性:5 组实验(核心构件见 stability_core.py)。

跑法: python main.py
权威依据见同目录 ../README.md「参考资料」。
"""

from stability_core import (
    Rng, make_dataset, f_classif, select_kbest, selection_sets,
    jaccard, dice, kuncheva, mean_pairwise, phi_stability, random_sets,
    selection_probabilities, stable_features, pfer_bound,
)


def experiment_measures():
    """E1:零模型与真实数据上的度量读数。"""
    d, k, m = 60, 5, 60
    null_sets = random_sets(m, k, d, Rng(2026))
    print("[E1] 零模型(均匀随机 k 子集)d=%d k=%d M=%d" % (d, k, m))
    print("     Jaccard  均值 = %.4f   (k/d = %.4f)"
          % (mean_pairwise(null_sets, jaccard), k / d))
    print("     Dice     均值 = %.4f   (k/d = %.4f)"
          % (mean_pairwise(null_sets, dice), k / d))
    print("     Kuncheva 均值 = %+.4f"
          % mean_pairwise(null_sets, lambda a, b: kuncheva(a, b, d)))
    print("     Φ̂            = %+.4f" % phi_stability(null_sets, d))

    X, y = make_dataset(100, 60, 4, 0.9, seed=7)
    real = selection_sets(X, y, k, m, Rng(11))
    print("[E1] 真实数据(60 列里 4 列有信号)")
    print("     Jaccard  均值 = %.4f" % mean_pairwise(real, jaccard))
    print("     Φ̂            = %+.4f" % phi_stability(real, d))
    print("     入选频率 ≥ 0.6 的特征数 = %d"
          % len(stable_features(selection_probabilities(real, d), 0.6)))
    return real


def experiment_k_sensitivity():
    """E2:同一数据上改 k,两个度量各自的走向。"""
    X, y = make_dataset(100, 60, 4, 0.9, seed=7)
    d = 60
    print("[E2] k 变化对两个度量的影响(同一数据、同一选择器)")
    rows = []
    for k in (3, 10, 30):
        sets = selection_sets(X, y, k, 40, Rng(11 + k))
        rows.append((k, mean_pairwise(sets, jaccard), phi_stability(sets, d)))
        print("     k=%2d  Jaccard=%.4f  Φ̂=%+.4f"
              % (k, rows[-1][1], rows[-1][2]))
    return rows


def experiment_theorem5():
    """E3:Theorem 5 —— 常量 k 时「逐对 Kuncheva 平均」与 Φ̂ 完全相等。"""
    d, k, m = 40, 6, 25
    sets = random_sets(m, k, d, Rng(4242))
    ic = mean_pairwise(sets, lambda a, b: kuncheva(a, b, d))
    phi = phi_stability(sets, d)
    print("[E3] 常量 k 下 逐对 Kuncheva 平均 − Φ̂ = %.3e" % (ic - phi))
    return ic, phi


def experiment_error_control(trials=12, m=80):
    """E4:稳定性选择把误选压下去多少,并与 Theorem 1 的上界对照。

    零模型:60 个特征**全部**是噪声,于是留下的任何特征都是误选,V 可直接数出来。
    噪声列在构造上可交换,满足定理前提(E(V) 是期望,所以要报重复次数)。
    """
    d, k, n = 60, 8, 120
    print("[E4] 纯噪声 d=%d k=%d M=%d,重复 %d 次" % (d, k, m, trials))
    rows = []
    for pi_thr in (0.6, 0.75, 0.9):
        total = 0.0
        for t in range(trials):
            X, y = make_dataset(n, d, 0, 0.0, seed=100 + t)
            sets = selection_sets(X, y, k, m, Rng(1000 + t))
            total += len(stable_features(selection_probabilities(sets, d), pi_thr))
        bound = pfer_bound(k, pi_thr, d)
        rows.append((pi_thr, total / trials, bound))
        print("     π_thr=%.2f:单次选择 V=%d  稳定性选择 V=%.2f  上界=%.2f"
              % (pi_thr, k, total / trials, bound))
    return rows


def experiment_stable_but_useless():
    """E5:稳定性高 ≠ 有用。前 4 列互为副本且都带同一个信号。"""
    n, d, k = 120, 14, 2
    y = [i % 2 for i in range(n)]
    rng = Rng(77)
    a = [rng.normal() + (1.5 if y[i] == 1 else 0.0) for i in range(n)]
    X = [[a[i]] * 4 + [rng.normal() for _ in range(d - 4)] for i in range(n)]
    scores = f_classif(X[:60], y[:60])
    print("[E5] 前 4 列互为副本:4 列 F 值全等 = %.12f" % scores[0])
    sets = selection_sets(X, y, k, 40, Rng(5))
    probs = selection_probabilities(sets, d)
    phi = phi_stability(sets, d)
    print("     Φ̂ = %+.6f,每次选出的集合 = %s" % (phi, sorted(sets[0])))
    print("     副本列入选频率 = %.3f,其余列最大入选频率 = %.3f"
          % (max(probs[:4]), max(probs[4:])))
    return phi, probs


def main():
    experiment_measures()
    experiment_k_sensitivity()
    experiment_theorem5()
    experiment_error_control()
    experiment_stable_but_useless()
    print("done")


if __name__ == "__main__":
    main()
