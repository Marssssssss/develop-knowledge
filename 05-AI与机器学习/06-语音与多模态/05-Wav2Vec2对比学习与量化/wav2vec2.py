"""wav2vec 2.0 的掩码、对比损失与乘积量化（模型层）

两条权威来源：

A. Baevski et al.《wav2vec 2.0: A Framework for Self-Supervised Learning of
   Speech Representations》(arXiv 2006.11477) 原文公式与超参：
   - §3.1 掩码：不放回地采样 p 比例的时间步作为起点，从每个起点起掩码后续 M 步，
     跨度可重叠；p=0.065、M=10 → 约 49% 的时间步被掩码，平均跨度 14.7（299 ms）
   - §3.2 式(3) Lm = −log exp(sim(ct,qt)/κ) / Σ_{q̃∈Qt} exp(sim(ct,q̃)/κ)，
     sim 是余弦相似度，κ=0.1，K=100 个干扰项从同一条语音的其他掩码步均匀采样
   - §3.2 式(4) Ld = (1/GV) Σ_g Σ_v p̄_gv log p̄_gv
   - §3.2 脚注 2：实现改成最大化 perplexity (GV − Σ_g exp(−Σ_v p log p))/GV，等价
   - §2 乘积量化：G 个码本各 V 个条目，各选一个拼接后线性变换得 q
   - §4.2 G=2、V=320 → 理论上界 102.4k 个码字；条目维度 d/G = 128(BASE)/384(LARGE)；
     Gumbel 温度 τ 从 2 按 0.999995 衰减到最小值 0.5(BASE)/0.1(LARGE)；
     κ=0.1、K=100、α=0.1；特征编码器 7 块 512 通道，strides (5,2,2,2,2,2,2)、
     kernels (10,3,3,3,3,2,2) → 49 Hz、约 20 ms 步长、感受野 400 样本(25 ms)

B. Facebook fairseq 官方实现（jsDelivr 取 main 分支原文）：
   - `fairseq/models/wav2vec/wav2vec2.py`：
     logit_temp=0.1、latent_vars=320、latent_groups=2、mask_length=10、
     mask_prob=0.65、num_negatives=100、latent_temp=(2, 0.5, 0.999995)
     compute_preds: cosine_similarity(x, targets)/logit_temp；neg_is_pos 置 −inf
   - `fairseq/modules/gumbel_vector_quantizer.py`：
     code_perplexity 用 hard one-hot 的平均分布、prob_perplexity 用 softmax 平均分布；
     set_num_updates: curr_temp = max(max_temp * decay**n, min_temp)；
     to_codebook_index: res += indices[..., i] * num_vars**(groups−i−1)
"""

import math
import random


# ---- 掩码（论文 §3.1） ----

def sample_mask(T, p=0.065, M=10, rng=None):
    """不放回采样 p·T 个起点，每个起点掩码后续 M 步，跨度可重叠。"""
    rng = rng or random.Random(0)
    n_start = max(1, int(round(p * T)))
    starts = rng.sample(range(T), n_start)
    mask = [False] * T
    for s in starts:
        for k in range(M):
            if s + k < T:
                mask[s + k] = True
    return mask, sorted(starts)


def mask_stats(mask):
    """返回 (被掩码比例, 平均跨度长度, 跨度个数)。"""
    T = len(mask)
    frac = sum(mask) / T
    runs, cur = [], 0
    for m in mask:
        if m:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return frac, (sum(runs) / len(runs) if runs else 0.0), len(runs)


# ---- 对比损失（论文 §3.2 式 3） ----

def cosine(a, b):
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def contrastive_loss(ct, qt, negatives, kappa=0.1):
    """Lm = −log exp(sim(ct,qt)/κ) / Σ_{q̃∈Qt} exp(sim(ct,q̃)/κ)。"""
    sims = [cosine(ct, q) / kappa for q in [qt] + negatives]
    m = max(sims)
    z = sum(math.exp(s - m) for s in sims)
    return -(sims[0] - m - math.log(z))


def sample_negatives(targets, num, n_negatives=100, rng=None, padding_count=0):
    """fairseq: 从同一条语音的其他时间步均匀采样；用 +1 技巧避开自身位置。"""
    rng = rng or random.Random(1)
    tsz = len(targets)
    high = tsz - padding_count
    out = []
    for t in range(num):
        idxs = []
        for _ in range(n_negatives):
            j = rng.randrange(0, high - 1)
            if j >= t:
                j += 1
            idxs.append(j)
        out.append(idxs)
    return out


def compute_preds(x, y, negatives, logit_temp=0.1, neg_inf=-1e30):
    """fairseq compute_preds：余弦相似度 / logit_temp；neg_is_pos 置 −inf。"""
    neg_is_pos = [all(a == b for a, b in zip(y, n)) for n in negatives]
    targets = [y] + negatives
    logits = [cosine(x, t) / logit_temp for t in targets]
    for i in range(len(negatives)):
        if neg_is_pos[i]:
            logits[i + 1] = neg_inf
    return logits, neg_is_pos


# ---- Gumbel 乘积量化 ----

class GumbelVectorQuantizer:
    def __init__(self, num_vars=320, groups=2, temp=(2.0, 0.5, 0.999995)):
        self.num_vars = num_vars
        self.groups = groups
        self.max_temp, self.min_temp, self.temp_decay = temp
        self.curr_temp = self.max_temp
        self.codebook_indices = None

    def set_num_updates(self, n):
        self.curr_temp = max(self.max_temp * self.temp_decay ** n, self.min_temp)

    def get_codebook_indices(self):
        """fairseq: product(range(V), G)，非 combine_groups 时第 b 组整体偏移 V*b。"""
        if self.codebook_indices is None:
            G, V = self.groups, self.num_vars
            inds = []
            for _ in range(G):
                inds = ([(i,) for i in range(V)] if not inds
                        else [a + (i,) for a in inds for i in range(V)])
            grid = [list(t) for t in inds]
            for b in range(1, G):
                for row in grid:
                    row[b] += V * b
            self.codebook_indices = grid
        return self.codebook_indices

    def to_codebook_index(self, indices):
        res = 0
        for i in range(self.groups):
            exponent = self.groups - i - 1
            res += indices[i] * (self.num_vars ** exponent)
        return res

    def codebook_size(self):
        return self.num_vars ** self.groups


def entropy_of(probs):
    return -sum(p * math.log(p + 1e-7) for p in probs if p > 0)


def perplexity_of(probs):
    return math.exp(entropy_of(probs))


def code_perplexity(hard_assignments, groups, V):
    """fairseq：对 hard one-hot 的批次平均分布取 exp(熵)，再按组求和。"""
    total = 0.0
    for g in range(groups):
        counts = [0.0] * V
        for a in hard_assignments:
            counts[a[g]] += 1.0
        n = len(hard_assignments)
        avg = [c / n for c in counts]
        total += perplexity_of(avg)
    return total


def prob_perplexity(prob_maps, groups, V):
    """fairseq：对 softmax 概率（无 Gumbel 噪声、无温度）的批次平均取 exp(熵)。"""
    total = 0.0
    for g in range(groups):
        avg = [0.0] * V
        for p in prob_maps:
            for v in range(V):
                avg[v] += p[g][v]
        n = len(prob_maps)
        avg = [x / n for x in avg]
        total += perplexity_of(avg)
    return total


def diversity_loss(avg_probs, G, V):
    """论文式(4)：Ld = (1/GV) Σ_g Σ_v p̄_gv log p̄_gv。"""
    s = 0.0
    for g in range(G):
        for v in range(V):
            p = avg_probs[g][v]
            if p > 0:
                s += p * math.log(p)
    return s / (G * V)


def diversity_loss_perplexity_form(avg_probs, G, V):
    """论文脚注 2 的实现形式：(GV − Σ_g exp(−Σ_v p log p)) / GV（越大越好）。"""
    s = 0.0
    for g in range(G):
        h = -sum(p * math.log(p) for p in avg_probs[g] if p > 0)
        s += math.exp(h)
    return (G * V - s) / (G * V)


# ---- 特征编码器输出长度（论文 §4.2） ----

CONV_STRIDES = (5, 2, 2, 2, 2, 2, 2)
CONV_KERNELS = (10, 3, 3, 3, 3, 2, 2)


def conv_out_length(L, k, s):
    return int(math.floor((L - k) / s + 1))


def encoder_out_length(L):
    for k, s in zip(CONV_KERNELS, CONV_STRIDES):
        L = conv_out_length(L, k, s)
    return L
