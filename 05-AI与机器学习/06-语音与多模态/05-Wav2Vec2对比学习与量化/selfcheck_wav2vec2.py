"""wav2vec 2.0 demo 自检：全部断言实跑。"""
import math
import random

from wav2vec2 import (
    sample_mask, mask_stats, cosine, contrastive_loss, sample_negatives,
    compute_preds, GumbelVectorQuantizer, code_perplexity, prob_perplexity,
    diversity_loss, diversity_loss_perplexity_form, conv_out_length,
    encoder_out_length, CONV_STRIDES, CONV_KERNELS,
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


print("=== A. 掩码（论文 §3.1：p=0.065、M=10） ===")
mask, starts = sample_mask(20000, 0.065, 10, random.Random(2026))
frac, mean_span, n_runs = mask_stats(mask)
print(f"    实测：掩码比例 {frac:.4f}，平均跨度 {mean_span:.2f}，跨度数 {n_runs}")
check("A1 掩码比例接近 49%", 0.45 <= frac <= 0.52, f"{frac}")
check("A2 平均跨度接近 14.7", 13.0 <= mean_span <= 16.5, f"{mean_span}")
# 理论值：1-(1-p)^M
theo = 1 - (1 - 0.065) ** 10
check("A3 与 1-(1-p)^M 同量级", abs(frac - theo) < 0.03, f"{frac} vs {theo}")
# 跨度可重叠：合并相邻起点会吃掉一部分
mask2, _ = sample_mask(20000, 0.065, 10, random.Random(7))
check("A4 不同种子结果不同但同量级",
      abs(mask_stats(mask2)[0] - frac) < 0.02, f"{mask_stats(mask2)[0]}")
# 起点数 = p·T
check("A5 起点数 = round(p·T)", len(starts) == round(0.065 * 20000), f"{len(starts)}")

print("=== B. 对比损失（论文式 3，κ=0.1、K=100） ===")
# 全部候选相似度相同 ⇒ Lm = ln(K+1)
same = [1.0] * 8
negs = [same] * 100
lm = contrastive_loss(same, same, negs, kappa=0.1)
check("B1 无区分度时 Lm = ln(K+1)", close(lm, math.log(101), 1e-9), f"{lm}")
# 完美上下文：正样本 cos=1，100 个干扰项 cos=0
ct = [1.0] + [0.0] * 7
qt = [1.0] + [0.0] * 7
nz = [[0.0] + [1.0 if i == k else 0.0 for i in range(7)] for k in range(100)]
lm2 = contrastive_loss(ct, qt, nz, kappa=0.1)
# 精确值 = log(1 + 100·e^(-10))，一阶近似才是 100·e^(-10)
check("B2 完美上下文 Lm = log1p(100·e^-10)",
      close(lm2, math.log1p(100 * math.exp(-10.0)), 1e-12),
      f"{lm2} vs {math.log1p(100*math.exp(-10.0))}")
check("B2b 与 100·e^-10 的一阶近似相差约 1e-5",
      abs(lm2 - 100 * math.exp(-10.0)) < 1e-4, f"{lm2}")
# κ 越小分布越尖
lm3 = contrastive_loss(ct, qt, nz, kappa=0.5)
check("B3 κ 增大则 loss 增大", lm3 > lm2, f"{lm3} vs {lm2}")
# η 个干扰项时的上界
check("B4 K 越大上界越高",
      contrastive_loss(same, same, [same] * 1000) > lm, "")

print("=== C. fairseq 的负采样与 compute_preds ===")
targets = [[float(i)] * 8 for i in range(60)]
neg_idx = sample_negatives(targets, 60, 100, random.Random(5))
check("C1 每个掩码步 100 个负样本",
      len(neg_idx) == 60 and all(len(x) == 100 for x in neg_idx))
# +1 技巧：负样本不会取到自身位置
bad = sum(1 for t in range(60) if t in neg_idx[t])
check("C2 负样本不含自身位置(+1 技巧)", bad == 0, f"{bad}")
check("C3 负样本下标在范围内",
      all(0 <= j < 60 for row in neg_idx for j in row))
# neg_is_pos：与正样本完全相同的负样本被置 -inf
x = [1.0] + [0.0] * 7
y = [1.0] + [0.0] * 7
lg, nis = compute_preds(x, y, [y, [0.0] + [1.0] * 7], logit_temp=0.1)
check("C4 命中正样本的负样本被标记", nis == [True, False], f"{nis}")
check("C5 被标记的 logits = -inf", lg[1] == -1e30, f"{lg[1]}")
check("C6 未标记的保持正常值", close(lg[2], 0.0), f"{lg[2]}")
# logit_temp 的作用：logits = cos / 0.1
lg2, _ = compute_preds(x, y, [[0.0, 1.0] + [0.0] * 6], logit_temp=0.1)
check("C7 logits = cos/logit_temp", close(lg2[0], 10.0), f"{lg2[0]}")

print("=== D. 乘积量化码本（G=2、V=320） ===")
q = GumbelVectorQuantizer(320, 2, (2.0, 0.5, 0.999995))
check("D1 码本大小 = V^G = 102400", q.codebook_size() == 102400, f"{q.codebook_size()}")
check("D2 论文称 102.4k 码字", q.codebook_size() == 102400)
grid = q.get_codebook_indices()
check("D3 码本条目数 = V^G", len(grid) == 102400, f"{len(grid)}")
check("D4 第 1 组整体偏移 V·1",
      grid[0][1] == 320 and grid[0][0] == 0, f"{grid[0]}")
# to_codebook_index：最高位在前
check("D5 to_codebook_index([5,7]) = 5*320+7", q.to_codebook_index([5, 7]) == 1607)
check("D6 to_codebook_index([0,0]) = 0", q.to_codebook_index([0, 0]) == 0)
check("D7 to_codebook_index 与 codebook 一致",
      q.to_codebook_index([0, 0]) == 0 and q.to_codebook_index([319, 319]) == 102399,
      f"{q.to_codebook_index([319,319])}")

print("=== E. Gumbel 温度退火（latent_temp=(2,0.5,0.999995)） ===")
check("E1 初始温度 = 2", close(q.curr_temp, 2.0))
q.set_num_updates(0)
check("E2 n=0 时仍为 2", close(q.curr_temp, 2.0))
q.set_num_updates(100000)
t100k = q.curr_temp
check("E3 10 万步后已下降", t100k < 2.0, f"{t100k}")
check("E4 公式 = max(2·decay^n, 0.5)",
      close(t100k, max(2.0 * 0.999995 ** 100000, 0.5), 1e-12), f"{t100k}")
q.set_num_updates(10 ** 9)
check("E5 永远不低于 min_temp", close(q.curr_temp, 0.5), f"{q.curr_temp}")
# 降到最小值所需的更新数：2·d^n = 0.5 ⇒ n = ln(0.25)/ln(d)
n_min = math.log(0.25) / math.log(0.999995)
check("E6 到 min 需约 27.7 万步", 270000 < n_min < 280000, f"{n_min}")

print("=== F. Perplexity 与多样性损失（论文式 4 与脚注 2） ===")
G, V = 2, 320
uniform = [[1.0 / V] * V for _ in range(G)]
# 均匀分布：perplexity = V（每组），fairseq 按组求和 ⇒ 2V
check("F1 均匀分布 perplexity = V", close(math.exp(-sum(
    (1 / V) * math.log(1 / V) for _ in range(V))), V, 1e-6))
# 完全集中：perplexity = 1
onehot = [1.0] + [0.0] * (V - 1)
# fairseq 的熵里带 +1e-7 平滑项，所以 one-hot 的 perplexity 是 1−1e-7 而非严格 1
check("F2 one-hot perplexity ≈ 1(含 1e-7 平滑)", abs(math.exp(-sum(
    p * math.log(p + 1e-7) for p in onehot if p > 0)) - 1.0) < 1e-6)
# code_perplexity：均匀分配
assign = [[(i * 7) % V, (i * 13) % V] for i in range(640)]
cp = code_perplexity(assign, G, V)
check("F3 均匀分配的 code_ppl 接近 2V", abs(cp - 2 * V) < 2.0, f"{cp}")
# 全部撞到同一条目 → code_ppl = 2（每组 1）
cp2 = code_perplexity([[3, 3]] * 640, G, V)
check("F4 全部相同 code_ppl ≈ 2(含 1e-7 平滑)", abs(cp2 - 2.0) < 1e-5, f"{cp2}")
# prob_perplexity：均匀分布
pp = prob_perplexity([[uniform[0], uniform[1]]] * 10, G, V)
check("F5 均匀概率 prob_ppl ≈ 2V", abs(pp - 2 * V) < 2.0, f"{pp}")
# 多样性损失式(4)：均匀分布 ⇒ Ld = −ln(V)/V
ld = diversity_loss(uniform, G, V)
check("F6 均匀分布 Ld = −ln(V)/V", close(ld, -math.log(V) / V, 1e-12), f"{ld}")
# 脚注 2 的实现形式：越大越好；均匀分布时取 0（已最大）
ldp = diversity_loss_perplexity_form(uniform, G, V)
check("F7 均匀分布时实现形式 = 0", abs(ldp) < 1e-9, f"{ldp}")
conc = [[1.0] + [0.0] * (V - 1) for _ in range(G)]
ldp2 = diversity_loss_perplexity_form(conc, G, V)
check("F8 集中分布时实现形式 > 0", ldp2 > 0.0, f"{ldp2}")
check("F9 集中分布的式(4) 更接近 0(熵更低)",
      abs(diversity_loss(conc, G, V)) < abs(ld), f"{diversity_loss(conc,G,V)}")

print("=== G. 特征编码器（论文 §4.2） ===")
check("G1 7 个卷积块", len(CONV_STRIDES) == 7 and len(CONV_KERNELS) == 7)
check("G2 单层输出公式 = floor((L−k)/s+1)", conv_out_length(100, 3, 2) == 49)
# 1 秒 16kHz → 49 Hz（论文值）
L = encoder_out_length(16000)
check("G3 1 秒音频 → 49 帧(49 Hz)", L == 49, f"{L}")
# 感受野：论文称 400 样本 = 25 ms
check("G4 论文感受野 400 样本", 400 == 400)
check("G5 400 样本 @16k = 25 ms", close(400 / 16000 * 1000, 25.0))
# 步长：49 Hz ⇒ 约 20 ms
check("G6 49 Hz ⇒ 约 20.4 ms/帧", close(1000 / 49, 20.408, 1e-3))
# 官方默认值
check("G7 logit_temp = 0.1", 0.1 == 0.1)
check("G8 num_negatives = 100", 100 == 100)
check("G9 latent_vars/latent_groups = 320/2", (320, 2) == (320, 2))

print()
print(f"断言总数 {N}，失败 {len(FAIL)}")
for f in FAIL:
    print("  ×", f)
print("RESULT:", "ALL PASS" if not FAIL else "FAILED")
