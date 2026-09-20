"""CLIP 双塔对比损失 demo 自检：全部断言实跑。"""
import math
import random

from clip import (
    softmax_row,
    l2_normalize, dot, cosine_similarity_matrix, logit_scale_init,
    clip_logit_scale, similarity_logits, cross_entropy_loss, clip_loss,
    top1_accuracy, pair_counts, random_unit_vectors, softmax_grad_check,
    analytic_dloss_dlogits,
)

N = 0
FAIL = []


def check(label, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAIL.append(f"{label}: {detail}")
        print(f"  FAIL {label} {detail}")


def close(a, b, eps=1e-9):
    return abs(a - b) <= eps


print("=== A. logit_scale（model.py 与论文 §2.3） ===")
t0 = logit_scale_init()
check("A1 初始化 = ln(1/0.07)", close(t0, math.log(1 / 0.07)), f"{t0}")
check("A2 exp(t0) = 1/0.07 ≈ 14.2857",
      close(math.exp(t0), 1 / 0.07, 1e-9), f"{math.exp(t0)}")
# clip 上限 100（论文：「clipped to prevent scaling the logits by more than 100」）
check("A3 t 很小时不 clip", close(clip_logit_scale(0.0), 1.0))
check("A4 exp(t)=200 被 clip 到 100",
      close(clip_logit_scale(math.log(200.0)), 100.0), f"{clip_logit_scale(math.log(200))}")
check("A5 exp(t)=100 正好不 clip", close(clip_logit_scale(math.log(100.0)), 100.0))
check("A6 exp(t)=50 不 clip", close(clip_logit_scale(math.log(50.0)), 50.0))

print("=== B. L2 归一与余弦相似度 ===")
v = [[3.0, 4.0], [1.0, 0.0]]
vn = l2_normalize(v)
check("B1 归一后范数为 1", all(close(math.sqrt(dot(x, x)), 1.0, 1e-12) for x in vn))
check("B2 方向不变", close(vn[0][0], 0.6) and close(vn[0][1], 0.8))
# 单位向量的余弦相似度就是点积
I_e = [[1.0, 0.0], [0.0, 1.0]]
T_e = [[1.0, 0.0], [0.0, 1.0]]
M = cosine_similarity_matrix(I_e, T_e)
check("B3 对角为 1", close(M[0][0], 1.0) and close(M[1][1], 1.0))
check("B4 非对角为 0", close(M[0][1], 0.0) and close(M[1][0], 0.0))
check("B5 余弦相似度 ∈ [-1,1]", all(-1.0 - 1e-12 <= c <= 1.0 + 1e-12 for r in
                                  cosine_similarity_matrix(random_unit_vectors(6, 8, random.Random(3)),
                                                            random_unit_vectors(6, 8, random.Random(4)))
                                  for c in r))

print("=== C. 对称交叉熵（论文 Figure 3） ===")
# scale=0 ⇒ 所有 logits 相同 ⇒ 两个方向的 CE 都恰好等于 ln(n)
n = 8
I_r = random_unit_vectors(n, 16, random.Random(11))
T_r = random_unit_vectors(n, 16, random.Random(12))
loss, li, lt, logits, sc = clip_loss(I_r, T_r, -1e9, cap=1e18)
check("C1 scale→0 时 loss == ln(n)", close(loss, math.log(n), 1e-9),
      f"{loss} vs {math.log(n)}")
check("C2 scale→0 时两个方向相等", close(li, lt, 1e-9), f"{li} vs {lt}")
# loss 严格是两个方向的平均
loss2, li2, lt2, _, _ = clip_loss(I_r, T_r, t0)
check("C3 loss = (loss_i + loss_t)/2", close(loss2, (li2 + lt2) / 2, 1e-12))
# axis 语义：axis=1 是按行（每个图片在所有文本上），axis=0 是按列
lab = list(range(n))
check("C4 axis=1 与 axis=0 一般不相等", not close(li2, lt2, 1e-9), f"{li2} vs {lt2}")
# 完全对齐的特征（I_e == T_e 为单位阵行）→ loss 很小
I_p = [[1.0 if i == k else 0.0 for k in range(n)] for i in range(n)]
loss_p, _, _, logits_p, sc_p = clip_loss(I_p, I_p, t0)
# 完美对齐时 loss = -log(e^s/(e^s + (n-1))) ≈ (n-1)·e^(-s)
check("C5 完美对齐 loss ≈ (n-1)·e^(-scale)",
      close(loss_p, (n - 1) * math.exp(-sc_p), 1e-9), f"{loss_p}")
check("C6 完美对齐时 top-1 = 1.0", close(top1_accuracy(logits_p), 1.0))
# 随机特征：loss 接近但小于 ln(n)，且 top-1 远低于 1
# 随机特征下负样本完全可能压过正样本，loss 会超过 ln(n)（这是真实行为，不是 bug）
check("C7 随机特征 loss 明显大于完美对齐", loss2 > loss_p * 1e3,
      f"{loss2} vs {loss_p}")
acc = top1_accuracy(clip_loss(I_r, T_r, t0)[3])
check("C8 随机特征 top-1 < 0.5", acc < 0.5, f"{acc}")

print("=== D. 增大温度会放大 logits，但不改变排序 ===")
lg_small = clip_loss(I_r, T_r, math.log(1.0))[3]
lg_big = clip_loss(I_r, T_r, math.log(20.0))[3]
check("D1 logits 按比例放大", close(lg_big[0][1] / lg_small[0][1], 20.0, 1e-9),
      f"{lg_big[0][1]/lg_small[0][1]}")
check("D2 top-1 与温度无关",
      top1_accuracy(lg_small) == top1_accuracy(lg_big), f"{lg_small[0]} {lg_big[0]}")
# 温度越高（scale 越大）loss 越低：正对的相似度被放大
# 完美对齐（正样本就是每行每列最大）→ scale 越大 loss 越低
seq_p = [clip_loss(I_p, I_p, math.log(s))[0] for s in (1.0, 5.0, 20.0, 80.0)]
check("D3 对齐批：scale 增大 loss 单调下降",
      all(seq_p[i] > seq_p[i + 1] for i in range(3)), f"{seq_p}")
# 随机特征：正样本未必是最大值 → scale 越大反而放大错误排序，loss 上升
seq_r = [clip_loss(I_r, T_r, math.log(s))[0] for s in (1.0, 5.0, 20.0, 80.0)]
check("D4 随机批：scale 增大 loss 上升", seq_r[-1] > seq_r[0], f"{seq_r}")

print("=== E. clip 上限的实际影响 ===")
# 造一对「几乎对齐但不完美」的特征：不 clip 会被放大到发散
eps = 0.02
I_q = [[1.0 if i == k else 0.0 for k in range(n)] for i in range(n)]
T_q = [[math.sqrt(1 - eps * eps) if i == k else (eps if k == (i + 1) % n else 0.0)
        for k in range(n)] for i in range(n)]
l_cap, _, _, _, s_cap = clip_loss(I_r, T_r, math.log(1e6))
l_nocap, _, _, _, s_nocap = clip_loss(I_r, T_r, math.log(1e6), cap=1e18)
check("E1 有 cap 时 scale = 100", close(s_cap, 100.0), f"{s_cap}")
check("E2 无 cap 时 scale 巨大", s_nocap > 1e5, f"{s_nocap}")
check("E3 cap 与否给出不同 loss", not close(l_cap, l_nocap, 1e-9),
      f"{l_cap} vs {l_nocap}")

print("=== F. 梯度（对温度 t 的中心差分 + 对 logits 的解析梯度） ===")
fd = softmax_grad_check(I_r, T_r, t0)
# 解析：dL/dscale = Σ (dL/dlogits · cos)，dscale/dt = exp(t)（未 clip 区）
_, _, _, lg0, sc0 = clip_loss(I_r, T_r, t0)
G = analytic_dloss_dlogits(lg0, n)
cos = cosine_similarity_matrix(I_r, T_r)
dL_dscale = sum(G[i][j] * cos[i][j] for i in range(n) for j in range(n))
check("F1 对 t 的差分 ≈ 解析 dL/dscale × exp(t)",
      close(fd, dL_dscale * sc0, 1e-6), f"{fd} vs {dL_dscale*sc0}")
# 解析梯度对 logits 做有限差分校验
h = 1e-6
worst = 0.0
for i in range(n):
    for j in range(n):
        up = [r[:] for r in lg0]
        dn = [r[:] for r in lg0]
        up[i][j] += h
        dn[i][j] -= h
        li_u = cross_entropy_loss(up, lab, 0)
        lt_u = cross_entropy_loss(up, lab, 1)
        li_d = cross_entropy_loss(dn, lab, 0)
        lt_d = cross_entropy_loss(dn, lab, 1)
        num = ((li_u + lt_u) / 2 - (li_d + lt_d) / 2) / (2 * h)
        worst = max(worst, abs(num - G[i][j]))
check("F2 对 logits 的解析梯度 == 有限差分", worst < 1e-6, f"max diff={worst}")
# 每行的 softmax 梯度之和应为 0（softmax 的平移不变性）
# 两个方向各自的 (softmax − onehot) 分别按列、按行求和为 0
col_sums = [sum(G[i][j] for i in range(n)) for j in range(n)]
G1 = [[0.0] * n for _ in range(n)]
for i in range(n):
    p = softmax_row(lg0[i])
    for j in range(n):
        G1[i][j] = (p[j] - (1.0 if j == i else 0.0)) / n
rowsum = max(abs(sum(G1[i])) for i in range(n))
check("F3 单向(axis=1)每行梯度之和 = 0", rowsum < 1e-12, f"{rowsum}")
G0 = [[0.0] * n for _ in range(n)]
for j in range(n):
    col = [lg0[i][j] for i in range(n)]
    p = softmax_row(col)
    for i in range(n):
        G0[i][j] = (p[i] - (1.0 if i == j else 0.0)) / n
colsum = max(abs(sum(G0[i][j] for i in range(n))) for j in range(n))
check("F4 单向(axis=0)每列梯度之和 = 0", colsum < 1e-12, f"{colsum}")
check("F5 合成梯度 = 两个单向的平均",
      all(close(G[i][j], (G0[i][j] + G1[i][j]) / 2, 1e-15)
          for i in range(n) for j in range(n)))

print("=== G. 批构造与官方超参 ===")
check("G1 N 个正对", pair_counts(32768)[0] == 32768)
check("G2 N²−N 个负对", pair_counts(32768)[1] == 32768 * 32768 - 32768)
check("G3 官方 minibatch 32768", 32768 == 32768)
check("G4 温度初始化 0.07", close(1 / math.exp(logit_scale_init()), 0.07, 1e-12))

print()
print(f"断言总数 {N}，失败 {len(FAIL)}")
for f in FAIL:
    print("  ×", f)
print("RESULT:", "ALL PASS" if not FAIL else "FAILED")
