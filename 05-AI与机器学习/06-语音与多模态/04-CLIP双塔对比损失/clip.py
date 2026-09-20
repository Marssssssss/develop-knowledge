"""CLIP 双塔对比损失（模型层）

两处权威来源：

A. OpenAI CLIP 论文 *Learning Transferable Visual Models From Natural Language
   Supervision*（arXiv 2103.00020）—— Figure 3 的 numpy 伪代码与本 demo 一一对应：
     I_e = l2_normalize(I_f @ W_i, axis=1)
     T_e = l2_normalize(T_f @ W_t, axis=1)
     logits = (I_e @ T_e.T) * exp(t)
     labels = arange(n)
     loss_i = cross_entropy(logits, labels, axis=0)
     loss_t = cross_entropy(logits, labels, axis=1)
     loss   = (loss_i + loss_t) / 2
   §2.3：最大化 N 个真实对的余弦相似度、最小化 N²−N 个错误配对；
   τ 初始化为 0.07（源自 Wu et al. 2018），并 clip 到不超过 100 以防训练不稳；
   batch size 32,768；只有线性投影，没有非线性 projection head。

B. OpenAI CLIP 官方 `clip/model.py`（jsDelivr 取 main 分支原文）：
     self.logit_scale = nn.Parameter(torch.ones([]) * np.log(1 / 0.07))
     image_features = image_features / image_features.norm(dim=1, keepdim=True)
     text_features  = text_features  / text_features.norm(dim=1, keepdim=True)
     logit_scale = self.logit_scale.exp()
     logits_per_image = logit_scale * image_features @ text_features.t()
     logits_per_text  = logits_per_image.t()
"""

import math
import random


# ---- 特征归一化（论文伪代码与 model.py 都在 forward 里做 L2 归一） ----

def l2_normalize(vectors):
    out = []
    for v in vectors:
        n = math.sqrt(sum(x * x for x in v))
        out.append([x / n for x in v] if n > 0 else list(v))
    return out


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cosine_similarity_matrix(I_e, T_e):
    """I_e: (n, d) 已归一；T_e: (n, d) 已归一。返回 (n, n) 余弦相似度。"""
    return [[dot(i, t) for t in T_e] for i in I_e]


# ---- logit scale ----

def logit_scale_init():
    """model.py: nn.Parameter(torch.ones([]) * np.log(1 / 0.07))。"""
    return math.log(1.0 / 0.07)


def clip_logit_scale(t, cap=100.0):
    """论文 §2.3: clip 到「缩放不超过 100」，防止训练不稳。"""
    return min(math.exp(t), cap)


def similarity_logits(I_e, T_e, t, cap=100.0):
    """论文伪代码 logits = (I_e @ T_e.T) * exp(t)，带 cap。"""
    s = clip_logit_scale(t, cap)
    return [[c * s for c in row] for row in cosine_similarity_matrix(I_e, T_e)], s


# ---- 交叉熵（按论文 axis 语义） ----

def softmax_row(vals):
    m = max(vals)
    e = [math.exp(v - m) for v in vals]
    z = sum(e)
    return [v / z for v in e]


def cross_entropy_loss(logits, labels, axis):
    """axis=0 → 对每一列（每个文本）在所有图片上做 softmax；
    axis=1 → 对每一行（每个图片）在所有文本上做 softmax。"""
    n = len(logits)
    total = 0.0
    if axis == 0:
        for j in range(n):                      # 每个文本
            col = [logits[i][j] for i in range(n)]
            p = softmax_row(col)
            total += -math.log(max(p[labels[j]], 1e-300))
    else:
        for i in range(n):                      # 每个图片
            p = softmax_row(logits[i])
            total += -math.log(max(p[labels[i]], 1e-300))
    return total / n


def clip_loss(I_e, T_e, t, cap=100.0):
    """返回 (loss, loss_i, loss_t, logits, scale)。"""
    logits, scale = similarity_logits(I_e, T_e, t, cap)
    labels = list(range(len(logits)))
    li = cross_entropy_loss(logits, labels, axis=0)
    lt = cross_entropy_loss(logits, labels, axis=1)
    return (li + lt) / 2.0, li, lt, logits, scale


# ---- 检索指标 ----

def top1_accuracy(logits):
    """image→text 的 top-1：第 i 行最大值是否落在列 i。"""
    hit = 0
    for i, row in enumerate(logits):
        if max(range(len(row)), key=lambda j: row[j]) == i:
            hit += 1
    return hit / len(logits)


# ---- 批构造：N 个正对 + N²−N 个负对 ----

def pair_counts(n):
    return n, n * n - n


def random_unit_vectors(n, d, rng=None):
    rng = rng or random.Random(0)
    vecs = []
    for _ in range(n):
        v = [rng.gauss(0.0, 1.0) for _ in range(d)]
        norm = math.sqrt(sum(x * x for x in v))
        vecs.append([x / norm for x in v])
    return vecs


def softmax_grad_check(I_e, T_e, t, h=1e-6):
    """对标量温度 t 做中心差分，返回 (数值梯度, 解析梯度)。"""
    lp, _, _, _, _ = clip_loss(I_e, T_e, t + h)
    lm, _, _, _, _ = clip_loss(I_e, T_e, t - h)
    return (lp - lm) / (2 * h)


def analytic_dloss_dlogits(logits, n):
    """∂L/∂logits：两个方向的 (softmax − onehot) 各除以 n，再取对称平均。"""
    grad = [[0.0] * n for _ in range(n)]
    for j in range(n):                       # axis=0：列方向
        col = [logits[i][j] for i in range(n)]
        p = softmax_row(col)
        for i in range(n):
            grad[i][j] += (p[i] - (1.0 if i == j else 0.0)) / n
    for i in range(n):                       # axis=1：行方向
        p = softmax_row(logits[i])
        for j in range(n):
            grad[i][j] += (p[j] - (1.0 if j == i else 0.0)) / n
    for i in range(n):
        for j in range(n):
            grad[i][j] /= 2.0
    return grad
