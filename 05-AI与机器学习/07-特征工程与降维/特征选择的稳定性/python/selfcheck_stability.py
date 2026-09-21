#!/usr/bin/env python3
"""特征选择稳定性模型自检:判据来自 sklearn 源码与 JMLR/arXiv 两篇原文。

期望值凡标了「对拍」的,都是开发期用 scikit-learn 1.9.1 的 f_classif / SelectKBest
逐值比对后写死的(见 README「与官方实现的对拍」)。
"""

import math

from stability_core import (
    Rng, subsample_indices, bootstrap_indices, make_dataset,
    f_oneway, f_classif, select_kbest, selection_sets,
    jaccard, dice, kuncheva, mean_pairwise, phi_stability, random_sets,
    selection_probabilities, stable_features, pfer_bound,
)
from main import (
    experiment_k_sensitivity, experiment_theorem5,
    experiment_error_control, experiment_stable_but_useless,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, label
    PASS += 1


def close(a, b, tol, label):
    if math.isinf(b) or math.isinf(a):
        ok(a == b, "%s: 期望 %r,实得 %r" % (label, b, a))
        return
    ok(abs(a - b) <= tol, "%s: 期望 %.12g ± %.3g,实得 %.12g" % (label, b, tol, a))


# ---------------------------------------------------------------- 1. 随机源
a, b = Rng(7), Rng(7)
ok([a.u32() for _ in range(5)] == [b.u32() for _ in range(5)], "同种子同序列")
ok(Rng(1).u32() != Rng(2).u32(), "不同种子不同起点")

pool = subsample_indices(50, 20, Rng(3))
ok(len(pool) == 20 and len(set(pool)) == 20, "无放回抽样:20 个互不相同的下标")
ok(all(0 <= i < 50 for i in pool), "下标都在范围内")
boot = bootstrap_indices(20, Rng(3))
ok(len(boot) == 20, "有放回抽样长度不变")
nrng = Rng(9)
draws = [nrng.normal() for _ in range(200)]
ok(len(set(draws)) == 200, "正态 200 次抽样互不相同")
ok(abs(sum(draws) / 200) < 0.25, "正态样本均值接近 0(实得 %+.3f)" % (sum(draws) / 200))

# ---------------------------------------------------------------- 2. f_oneway
close(f_oneway([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]), 13.5, 1e-12,
      "手算例:两组各 3 点,F = (13.5/1)/(4/4)")
close(f_oneway([[1.0, 1.0], [2.0, 2.0]]), math.inf, 0.0,
      "组内方差为零而组间为正 → msb/0 = inf")
ok(math.isnan(f_oneway([[3.0, 3.0], [3.0, 3.0]])), "常量列是 0/0 = nan(官方口径)")
ok(f_oneway([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
   == f_oneway([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]), "纯函数:同输入同输出")

y60 = [i % 2 for i in range(60)]
rng = Rng(77)
sig = [rng.normal() + (1.5 if y60[i] == 1 else 0.0) for i in range(60)]
noi = [rng.normal() for _ in range(60)]
X5 = [[sig[i]] * 4 + [noi[i]] for i in range(60)]
scores5 = f_classif(X5, y60)
close(scores5[0], 31.028359257590, 1e-9, "对拍 sklearn:f_oneway(强信号列)")
close(scores5[4], 0.201038785096, 1e-9, "对拍 sklearn:f_oneway(噪声列)")
ok(scores5[:4] == [scores5[0]] * 4, "互为副本的 4 列 F 值完全相同")

# ------------------------------------------------- 3. SelectKBest 的两条硬规则
ok(sorted(select_kbest(scores5, 1)) == [3], "并列时取稳定排序尾部 → 下标大的胜出")
ok(sorted(select_kbest(scores5, 2)) == [2, 3], "k=2 取并列组里下标最大的两个")
ok(sorted(select_kbest(scores5, 3)) == [1, 2, 3], "k=3 同理")
ok(sorted(select_kbest(scores5, 4)) == [0, 1, 2, 3], "k=4 才把 4 个副本全取上")
Xc = [row + [1.0] for row in X5]
scores_c = f_classif(Xc, y60)
ok(math.isnan(scores_c[-1]), "常量列分数是 nan")
ok(4 not in select_kbest(scores_c, 4), "nan 被换成 float 最小有限值 → 常量列永不入选")

# ---------------------------------------------------------------- 4. 度量本体
ok(jaccard({0, 1}, {0, 1}) == 1.0 and jaccard({0}, {1}) == 0.0, "Jaccard 端点")
ok(dice({0, 1}, {0, 1}) == 1.0 and dice({0}, {1}) == 0.0, "Dice 端点")
close(kuncheva({0, 1}, {1, 2}, 6), 0.25, 1e-12,
      "手算:r=1,d=6,k=2 → (6−4)/(2·4) = 0.25")
close(kuncheva({0, 1}, {2, 3}, 6), -0.5, 1e-12, "r=0 → −k/(d−k) = −0.5")
ok(kuncheva({0, 1, 2}, {0, 1, 2}, 6) == 1.0, "完全相同 → 1")
try:
    mean_pairwise([{0}], jaccard)
except ValueError as exc:
    ok("至少" in str(exc), "单集合无法算逐对平均")
else:
    raise AssertionError("应当抛 ValueError")

s1, s2 = {0, 1, 2}, {3, 4, 5}
close(phi_stability([s1, s1], 6), 1.0, 1e-12, "两集合全同 → Φ̂ = 1")
close(phi_stability([s1, s2], 6), -1.0, 1e-12, "M=2 且完全不相交 → 下界 −1/(M−1)")
close(phi_stability([s1, s2, s1, s2], 6), -1.0 / 3, 1e-12,
      "M=4 时同一配置取到 −1/(M−1) = −1/3,与 M=2 的 −1 是同一个公式")

# ------------------------------------------------- 5. 零模型:机会校正
d, k, m = 60, 5, 60
null_sets = random_sets(m, k, d, Rng(2026))
phi = phi_stability(null_sets, d)
ok(abs(phi) < 0.03, "零模型下 Φ̂ ≈ 0,实得 %+.4f" % phi)
ic = mean_pairwise(null_sets, lambda x, y_: kuncheva(x, y_, d))
close(ic, phi, 1e-12, "常量 k 时逐对 Kuncheva 平均 == Φ̂(Theorem 5)")
j0 = mean_pairwise(null_sets, jaccard)
d0 = mean_pairwise(null_sets, dice)
ok(abs(d0 - k / d) < 0.02, "零模型下 Dice 期望 ≈ k/d = %.4f,实得 %.4f" % (k / d, d0))
ok(abs(j0 - k / d) > 0.02, "零模型下 Jaccard 期望 ≠ k/d(实得 %.4f vs %.4f)" % (j0, k / d))

# 真实数据:Φ̂ 明显为正,且随 k 变化仍可解释
Xr, yr = make_dataset(100, 60, 4, 0.9, seed=7)
real = selection_sets(Xr, yr, 5, 60, Rng(11))
ok(phi_stability(real, 60) > 0.3, "有信号时 Φ̂ 明显为正")
ok(len(stable_features(selection_probabilities(real, 60), 0.6)) >= 1, "至少筛出一个稳定特征")
rows = experiment_k_sensitivity()
# 同一数据上 k 从 3 到 30,Φ̂ 单调下降;Jaccard 不是单调的,故不能跨 k 比较
ok(rows[0][2] > rows[1][2] > rows[2][2], "Φ̂ 随 k 单调下降:%s" % [round(r[2], 4) for r in rows])
jac = [round(r[1], 4) for r in rows]
ok(not (jac[0] > jac[1] > jac[2]), "Jaccard 不随 k 单调:%s" % jac)

# ------------------------------------------------- 6. 稳定性选择与误差控制
ic5, phi5 = experiment_theorem5()
close(ic5, phi5, 1e-12, "Theorem 5 再验一次(随机 Z 集)")
rows4 = experiment_error_control(trials=12, m=80)
for pi_thr, v, bound in rows4:
    ok(v <= bound, "π_thr=%.2f:实测 V=%.2f ≤ 上界 %.2f" % (pi_thr, v, bound))
ok(rows4[0][1] > rows4[1][1] > rows4[2][1], "π_thr 越大误选越少")
close(pfer_bound(math.sqrt(0.8 * 60), 0.9, 60), 1.0, 1e-12,
      "原文配方:π_thr=0.9、q=√(0.8p) → 上界正好 1")
ok(pfer_bound(8, 0.9, 60) < pfer_bound(8, 0.6, 60), "同一 q 下 π_thr 越大上界越小")
ok(len(stable_features([0.1, 0.7, 0.7, 0.0], 0.7)) == 2, "阈值含等号:≥ π_thr")

phi5v, probs5 = experiment_stable_but_useless()
close(phi5v, 1.0, 1e-12, "副本列:稳定性可以满分")
ok(probs5[2] == 1.0 and probs5[3] == 1.0, "并列组里下标最大的两个每次必入选")
ok(probs5[0] == 0.0 and probs5[1] == 0.0, "同组里下标小的两个从来没入选过(稳定排序取尾部)")
ok(max(probs5[4:]) == 0.0, "其余列一次都没被选中")
ok(sum(1 for p in probs5 if p > 0) == 2, "整轮只有 2 列入选,且这 2 列取值完全相同")

# ---------------------------------------------------------------- 7. 抽样封装
sets_b = selection_sets(Xr[:40], yr[:40], 3, 5, Rng(2), bootstrap=True)
ok(len(sets_b) == 5 and all(len(s) == 3 for s in sets_b), "bootstrap 分支:每次仍取 3 个")
ok(len(sets_b) <= 40, "特征集合是下标集合,不是样本数")

print("PASS %d" % PASS)
