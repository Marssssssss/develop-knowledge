"""HMM 三个算法 —— 自检（全部实跑）。

黄金值：
  SLP3 Eq A.7：P(3 1 3 | hot hot cold) = P(3|hot)·P(1|hot)·P(3|cold) = .4 × .2 × .1 = 0.008
  其余定量断言一律由「枚举全部 N^T 条路径」独立验证，不依赖教材插图里的 A 读数
  hmmlearn 官方规则：长度为 1 的样本不更新转移矩阵；map 解码 = 逐点后验最大
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    HMM,
    backward,
    backward_scaled,
    baum_welch,
    brute_force,
    forward,
    forward_scaled,
    gamma_scaled,
    gamma_unscaled,
    ice_cream,
    posterior_decode,
    viterbi,
    xi,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1


def path_prob(hmm, obs, path):
    p = hmm.pi[path[0]] * hmm.b[path[0]][obs[0]]
    for t in range(1, len(obs)):
        p *= hmm.a[path[t - 1]][path[t]] * hmm.b[path[t]][obs[t]]
    return p


# ------------------------------------------------- 1. 教材 Eq A.7 的指定路径
hmm = ice_cream()
ok(abs(hmm.b[0][2] * hmm.b[0][0] * hmm.b[1][2] - 0.008) < 1e-12,
   "Eq A.6/A.7：P(3 1 3 | hot hot cold) = P(3|hot)P(1|hot)P(3|cold) = .4×.2×.1 = 0.008")
ok(path_prob(hmm, [2, 0, 2], [0, 0, 1]) < 0.008,
   "Eq A.8 的联合还要再乘转移概率，故必小于式 A.7 的输出似然")
ok(abs(hmm.b[0][0] - 0.2) < 1e-12 and abs(hmm.b[0][2] - 0.4) < 1e-12,
   "Fig A.2 的 B 行（HOT）：P(1|HOT)=.2, P(3|HOT)=.4")
ok(abs(hmm.b[1][0] - 0.5) < 1e-12 and abs(hmm.b[1][2] - 0.1) < 1e-12,
   "Fig A.2 的 B 行（COLD）：P(1|COLD)=.5, P(3|COLD)=.1")
ok(hmm.pi == [0.2, 0.8], "Fig A.2 的 π = [.2, .8]（HOT, COLD）")

# --------------------------------------- 2. 前向概率 == 暴力枚举（独立参照）
for obs in ([2, 0, 2], [0, 0, 1], [1, 2, 0, 2], [2, 2, 2, 0]):
    alpha, prob = forward(hmm, obs)
    total, _, _ = brute_force(hmm, obs)
    ok(abs(prob - total) < 1e-12, "前向 P(O) 与枚举一致：obs=%s → %.8f" % (obs, prob))

# ------------------------------------------------------------ 3. 缩放前向
obs = [2, 0, 2]
alpha, prob = forward(hmm, obs)
ahat, cs, logp = forward_scaled(hmm, obs)
ok(abs(logp - math.log(prob)) < 1e-12, "缩放前向：log P = −Σ log c_t")
for t in range(len(obs)):
    ok(abs(sum(ahat[t]) - 1.0) < 1e-12, "缩放后 α̂_%d 逐时刻归一化（和为 1）" % t)
ok(all(c > 0 for c in cs), "缩放因子 c_t 全为正")

# ----------------------------------------------- 4. 后向与前向后向一致性
beta = backward(hmm, obs)
ok(all(abs(v - 1.0) < 1e-12 for v in beta[-1]), "β_{T−1}(i) = 1")
for t in range(len(obs)):
    s = sum(alpha[t][i] * beta[t][i] for i in range(hmm.n))
    ok(abs(s - prob) < 1e-12, "不变量：Σ_i α_t(i)β_t(i) = P(O)（t=%d）" % t)

# --------------------------------------------------------- 5. γ 与 ξ 的性质
g, prob2 = gamma_unscaled(hmm, obs)
for t in range(len(obs)):
    ok(abs(sum(g[t]) - 1.0) < 1e-12, "γ_%d 归一化（和为 1）" % t)
x = xi(hmm, obs, alpha, beta, prob)
for t in range(len(obs) - 1):
    tot = sum(x[t][i][j] for i in range(hmm.n) for j in range(hmm.n))
    ok(abs(tot - 1.0) < 1e-12, "Σ_ij ξ_%d = 1" % t)
    rowsum = [sum(x[t][i][j] for j in range(hmm.n)) for i in range(hmm.n)]
    ok(all(abs(rowsum[i] - g[t][i]) < 1e-12 for i in range(hmm.n)),
       "γ_%d(i) = Σ_j ξ_%d(i,j)" % (t, t))
    colsum = [sum(x[t][i][j] for i in range(hmm.n)) for j in range(hmm.n)]
    ok(all(abs(colsum[j] - g[t + 1][j]) < 1e-12 for j in range(hmm.n)),
       "γ_%d(j) = Σ_i ξ_%d(i,j)" % (t + 1, t))

# 缩放口径给出的 γ 与未缩放口径完全相同（hmmlearn：fwd*bwd 后逐行归一化）
bhat = backward_scaled(hmm, obs, cs)
gs = gamma_scaled(ahat, bhat)
for t in range(len(obs)):
    ok(all(abs(gs[t][i] - g[t][i]) < 1e-12 for i in range(hmm.n)),
       "缩放与未缩放给出同一个 γ_%d" % t)

# ------------------------------------------------------------ 6. Viterbi
for obs2 in ([2, 0, 2], [0, 0, 1], [1, 2, 0, 2]):
    score, path = viterbi(hmm, obs2)
    _, bf_path, bf_best = brute_force(hmm, obs2)
    ok(path == bf_path, "Viterbi 路径与枚举最优一致：obs=%s → %s" % (obs2, path))
    ok(abs(math.exp(score) - bf_best) < 1e-12, "Viterbi 得分 = 最优路径概率")

# -------------------------------- 7. 后验解码（map）与 Viterbi 是两件事
m2 = HMM([0.5, 0.5], [[0.9, 0.1], [0.1, 0.9]],
         [[0.7, 0.2, 0.1], [0.1, 0.2, 0.7]])
obs3 = [0, 0, 2]
g3, _ = gamma_unscaled(m2, obs3)
pd = posterior_decode(g3)
sc3, vp = viterbi(m2, obs3)
ok(pd != vp, "后验解码 %s 与 Viterbi %s 不同 —— 逐点最大 ≠ 序列最大" % (pd, vp))
ok(path_prob(m2, obs3, vp) > path_prob(m2, obs3, pd),
   "Viterbi 路径的联合概率 %.6f 高于后验解码路径 %.6f"
   % (path_prob(m2, obs3, vp), path_prob(m2, obs3, pd)))
ok(abs(math.exp(sc3) - path_prob(m2, obs3, vp)) < 1e-12, "Viterbi 得分就是该路径概率")

# ---------------------------------------------------------- 8. Baum-Welch
logs, final = baum_welch(hmm, obs, iterations=15)
ok(len(logs) == 16, "返回 15 次重估 + 末次似然，共 16 个 log 值")
for i in range(1, len(logs)):
    ok(logs[i] >= logs[i - 1] - 1e-12,
       "EM 第 %d 步对数似然不下降（%.6f → %.6f）" % (i, logs[i - 1], logs[i]))
ok(logs[-1] > logs[0], "EM 最终似然 %.6f 高于初值 %.6f" % (logs[-1], logs[0]))

# 长度为 1 的序列：hmmlearn 明确规定没有转移 ⇒ A 不动
one = HMM([0.4, 0.6], [[0.3, 0.7], [0.8, 0.2]], [[0.5, 0.5], [0.5, 0.5]])
_, after1 = baum_welch(one, [0], iterations=3)
ok(after1.a == one.a, "长度为 1 的样本不更新转移矩阵（hmmlearn 官方规则）")
ok(abs(after1.b[0][0] + after1.b[0][1] - 1.0) < 1e-12, "长度为 1 时 B 仍被更新且归一化")
ok(abs(after1.pi[0] + after1.pi[1] - 1.0) < 1e-12, "π 保持归一化")

# ---------------------------------------- 9. 在合成数据上真的能学到参数
rng = random.Random(2026)
true_hmm = HMM([0.6, 0.4], [[0.8, 0.2], [0.3, 0.7]],
               [[0.7, 0.2, 0.1], [0.15, 0.35, 0.5]])
seq, state = [], 0
for _ in range(60):
    if not seq:
        state = 0 if rng.random() < true_hmm.pi[0] else 1
    else:
        state = 0 if rng.random() < true_hmm.a[state][0] else 1
    r, acc = rng.random(), 0.0
    for v in range(3):
        acc += true_hmm.b[state][v]
        if r <= acc:
            seq.append(v)
            break
    else:
        seq.append(2)
init = HMM([0.5, 0.5], [[0.5, 0.5], [0.5, 0.5]], [[0.34, 0.33, 0.33], [0.33, 0.33, 0.34]])
logs2, learned = baum_welch(init, seq, iterations=25)
ok(logs2[-1] > logs2[0], "合成数据上 EM 提升对数似然 %.4f → %.4f" % (logs2[0], logs2[-1]))
ok(all(abs(sum(row) - 1.0) < 1e-9 for row in learned.a), "学到的 A 每行和为 1")
ok(all(abs(sum(row) - 1.0) < 1e-9 for row in learned.b), "学到的 B 每行和为 1")
ok(abs(sum(learned.pi) - 1.0) < 1e-9, "学到的 π 和为 1")

print("PASS =", PASS)
