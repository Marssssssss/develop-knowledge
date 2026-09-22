"""变分推断 / 平均场 —— 自检（全部实跑，黄金值均为解析式或枚举真值）。

两条独立参照：
  * 离散模型（n ≤ 4）：真后验由 2^n 穷举得到，logZ 由 logsumexp 得到
  * 二元高斯：真后验就是 N(μ, Λ^{-1})，且 KL(q*‖p) 有闭式 −½ ln(1 − ρ²)
凡「平均场会低估方差 / 会抹掉相关性」这类定性说法，本文件一律换成可测的不等式。
"""

import math
import os
import random
import sys
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    Ising,
    cavi_gauss,
    cavi_ising,
    det,
    elbo_ising,
    gauss_elbo,
    gauss_kl,
    gauss_log_z,
    inv,
    kl_ising,
    logit,
    q_prob,
    sigmoid,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1


# ======================================================= 1. 耦合模型（n=3）
mod = Ising([0.1, -0.2, 0.3],
            [[0.0, 1.2, -0.4], [1.2, 0.0, 0.8], [-0.4, 0.8, 0.0]])
lz = mod.log_z()

rng = random.Random(2026)
for _ in range(8):
    m = [rng.uniform(0.05, 0.95) for _ in range(mod.n)]
    e = elbo_ising(mod, m)
    k = kl_ising(mod, m)
    ok(abs(e + k - lz) < 1e-12,
       "Eq (14) 分解成立：ELBO + KL = log Z（%.10f + %.10f = %.10f）" % (e, k, lz))
    ok(e <= lz + 1e-12, "ELBO 是 log Z 的下界（%.10f ≤ %.10f）" % (e, lz))
    ok(k >= -1e-12, "KL(q‖p) 非负（%.10f）" % k)

m_star, hist = cavi_ising(mod, iters=60)
ok(all(hist[i] >= hist[i - 1] - 1e-12 for i in range(1, len(hist))),
   "CAVI 的 ELBO 单调不降（%d 轮）" % len(hist))
ok(hist[-1] <= lz + 1e-12, "收敛后的 ELBO 仍不越过 log Z")
ok(abs(hist[-1] + kl_ising(mod, m_star) - lz) < 1e-10, "收敛点处 Eq (14) 仍成立")

# 不动点自洽 + Eq (40)：ν_j = E_{−j}[η_j]
for j in range(mod.n):
    nu_self = mod.theta[j] + sum(mod.w[j][k] * m_star[k]
                                 for k in range(mod.n) if k != j)
    ok(abs(logit(m_star[j]) - nu_self) < 1e-9,
       "不动点自洽：logit(m_%d) = θ_%d + Σ_k w_%d,k m_k" % (j, j, j))
    # 按定义枚举 z_{−j} 求 E_{−j}[η_j]，验证「先取期望再代入」确实等于 ν_j
    others = [k for k in range(mod.n) if k != j]
    exp_eta = 0.0
    for bits in product((0, 1), repeat=len(others)):
        pw = 1.0
        z = [0] * mod.n
        for k, b in zip(others, bits):
            z[k] = b
            pw *= m_star[k] if b == 1 else (1.0 - m_star[k])
        exp_eta += pw * mod.eta(j, z)
    ok(abs(exp_eta - nu_self) < 1e-12,
       "Eq (40)：ν_%d = E_{−%d}[η_%d]，枚举 2^{n−1} 项验证" % (j, j, j))
    ok(abs(m_star[j] - sigmoid(nu_self)) < 1e-12,
       "ν_%d 经 sigmoid 回到均值 m_%d（指数族同族）" % (j, j))

# 平均场强加独立 ⇒ 协方差恒为 0，而真后验不是
ok(abs(m_star[0] * m_star[1] - sum(q_prob(m_star, z) * z[0] * z[1]
                                   for z in product((0, 1), repeat=mod.n))) < 1e-15,
   "q 下 E[z0 z1] 恒等于 m0·m1（平均场把相关性抹成 0）")
ok(abs(mod.pair(0, 1) - mod.marginal(0) * mod.marginal(1)) > 1e-3,
   "真后验不独立：P(z0=1,z1=1)=%.6f ≠ %.6f"
   % (mod.pair(0, 1), mod.marginal(0) * mod.marginal(1)))
ok(kl_ising(mod, m_star) > 1e-4,
   "耦合模型上平均场的 KL = %.6f > 0（近似有代价）" % kl_ising(mod, m_star))
for i in range(mod.n):
    ok(abs(m_star[i] - mod.marginal(i)) > 1e-4,
       "第 %d 个边缘：平均场 %.6f ≠ 真值 %.6f" % (i, m_star[i], mod.marginal(i)))

# 单次坐标更新必然不降 ELBO（Algorithm 1 的坐标上升性质）
m_rand = [rng.uniform(0.1, 0.9) for _ in range(mod.n)]
before = elbo_ising(mod, m_rand)
one, _ = cavi_ising(mod, iters=1, m0=m_rand)
ok(elbo_ising(mod, one) >= before - 1e-12,
   "单次坐标更新后 ELBO 不降（%.8f → %.8f）" % (before, elbo_ising(mod, one)))

# 在这个温和模型上两个方向的 KL 数值恰好很接近（见下方打印），
# 故「方向有讲究」改在强耦合模型上断言 —— 别拿巧合当证据。


def rev_kl(model, m):
    """反向 KL(p‖q)，按定义枚举。q 在某状态给 0 而 p 给正质量时为 +inf（zero-forcing）。"""
    tot = 0.0
    for z, p in model.posterior().items():
        qq = q_prob(m, z)
        if p > 0.0:
            tot += p * math.log(p / qq) if qq > 0.0 else float("inf")
    return tot


print("  [观察] 温和模型：KL(q‖p)=%.6f  KL(p‖q)=%.6f（数值接近，不足以区分方向）"
      % (kl_ising(mod, m_star), rev_kl(mod, m_star)))

# CAVI 确实在改善近似
ok(kl_ising(mod, m_star) < kl_ising(mod, [0.5] * mod.n),
   "收敛点的 KL 小于均匀初始化处的 KL")

# ============================================== 2. 负控：解耦时应完全精确
free = Ising([0.4, -0.7, 0.2], [[0.0] * 3 for _ in range(3)])
m_f, hist_f = cavi_ising(free, iters=3)
for i in range(free.n):
    ok(abs(m_f[i] - free.marginal(i)) < 1e-12,
       "w=0 时平均场边缘 = 真边缘（第 %d 个）" % i)
ok(abs(kl_ising(free, m_f)) < 1e-12, "w=0 时 KL(q*‖p) = 0 —— 负控必须过")
ok(abs(elbo_ising(free, m_f) - free.log_z()) < 1e-12, "w=0 时 ELBO 顶到 log Z")
ok(abs(hist_f[0] - hist_f[-1]) < 1e-12, "w=0 时一轮就收敛（没有耦合要传播）")

# ============ 3. 强排斥耦合：平均场被迫给出「比真值更极端」的联合
rep = Ising([0.0, 0.0], [[0.0, -5.0], [-5.0, 0.0]])
m_r, _ = cavi_ising(rep, iters=400)
ok(abs(m_r[0] - rep.marginal(0)) > 0.05,
   "强排斥下均值偏离真边缘：%.4f vs %.4f" % (m_r[0], rep.marginal(0)))
q11 = m_r[0] * m_r[1]
ok(q11 > 5.0 * rep.pair(0, 1),
   "q 被迫给 (1,1) 的概率 %.6f 是真值 %.6f 的 %.1f 倍"
   % (q11, rep.pair(0, 1), q11 / rep.pair(0, 1)))
ok(kl_ising(rep, m_r) > 0.05, "强耦合下近似代价显著：KL = %.6f" % kl_ising(rep, m_r))

# KL 的方向在这里差出一个量级：KL(q‖p) 惩罚「q 在 p 没有质量处放质量」
f_r, r_r = kl_ising(rep, m_r), rev_kl(rep, m_r)
ok(abs(f_r - r_r) > 0.02,
   "强耦合下方向差异显著：KL(q‖p)=%.6f ≠ KL(p‖q)=%.6f" % (f_r, r_r))

# ================================================== 4. 二元高斯：低估方差
mu = [0.5, -1.0]
lam = [[4.0, 1.5], [1.5, 2.0]]
sigma = inv(lam)
lz_g = gauss_log_z(lam)
m_g, var_g, hist_g = cavi_gauss(mu, lam, iters=80)

ok(all(abs(m_g[i] - mu[i]) < 1e-10 for i in range(2)), "高斯不动点的均值 = μ（无偏）")
for i in range(2):
    ok(abs(var_g[i] - 1.0 / lam[i][i]) < 1e-12,
       "q_%d 的方差 = 1/Λ_%d%d = %.8f" % (i, i, i, var_g[i]))
    ok(var_g[i] < sigma[i][i] - 1e-9,
       "低估方差：1/Λ_%d%d = %.6f < 真边缘方差 %.6f" % (i, i, var_g[i], sigma[i][i]))

ok(all(hist_g[i] >= hist_g[i - 1] - 1e-12 for i in range(1, len(hist_g))),
   "高斯 CAVI 的 ELBO 单调不降")
kl_g = gauss_kl(m_g, var_g, mu, lam)
ok(abs(gauss_elbo(m_g, var_g, mu, lam) + kl_g - lz_g) < 1e-10,
   "高斯侧 Eq (14) 同样成立：ELBO + KL = log Z")

# 闭式：KL(q*‖p) = −½ ln(1 − ρ²)，其中 ρ 为 p 的相关系数
rho = sigma[0][1] / math.sqrt(sigma[0][0] * sigma[1][1])
ok(abs(kl_g - (-0.5 * math.log(1.0 - rho * rho))) < 1e-10,
   "KL(q*‖p) = −½ ln(1 − ρ²)：%.10f vs %.10f（ρ = %.6f）"
   % (kl_g, -0.5 * math.log(1.0 - rho * rho), rho))

# 负控：对角 Λ（无耦合）时平均场精确
diag = [[4.0, 0.0], [0.0, 2.0]]
m_d, var_d, _ = cavi_gauss(mu, diag, iters=5)
ok(abs(gauss_kl(m_d, var_d, mu, diag)) < 1e-12, "Λ 对角时 KL = 0 —— 负控必须过")
sd = inv(diag)
ok(abs(var_d[0] - sd[0][0]) < 1e-12, "Λ 对角时变分方差 = 真边缘方差")

# 反向 KL 不同
quad = 0.0
for i in range(2):
    for j in range(2):
        quad += (1.0 / var_g[i] if i == j else 0.0) * sigma[i][j]
rev_g = 0.5 * (quad - 2 + math.log(det(lam)) + sum(math.log(v) for v in var_g))
ok(abs(rev_g - kl_g) > 1e-6, "高斯侧反向 KL(p‖q)=%.6f ≠ KL(q‖p)=%.6f" % (rev_g, kl_g))
ok(abs(sigma[0][1]) > 1e-9, "真后验相关系数非零，而 q 的协方差恒为 0")

print("PASS =", PASS)
