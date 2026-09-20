"""CTC 前向-后向与解码（模型层）

依据 A. Graves et al.《Connectionist Temporal Classification: Labelling
Unsegmented Sequence Data with Recurrent Neural Networks》(ICML 2006) 原文公式实现：

- §3.1 输出层单元数 = |L| + 1，多出的一个即 blank；路径 π ∈ L'^T，多对一映射 B
  “先合并相邻重复、再去掉 blank”（原文: "removing all blanks and repeated labels"，
  例 B(a-ab-) = B(-aa--abb) = aab）
- §4.1 扩展序列 l' = 在 l 的首尾与每两个标签之间插 blank，长度 2|l|+1
- §4.1 式(5)(6)(7) 前向变量 α 与初始化；式(8) p(l|x) = α_T(|l'|) + α_T(|l'|-1)
- §4.1 式(9)(10)(11) 后向变量 β；式(14) p(l|x) = Σ_s α_t(s)β_t(s) / y^t_{l'_s}
- §4.1 缩放：C_t = Σ_s α_t(s)，ln p(l|x) = Σ_t ln C_t
- §4.1 式(15) ∂p/∂y^t_k = (1/(y^t_k)^2) Σ_{s∈lab(l,k)} α_t(s)β_t(s)
- §4.1 式(16) 对未归一化输出 u 的误差信号
- §3.2 best path decoding：π* = 每帧取最大激活，h(x) ≈ B(π*)

全部下标在代码内按 0-based；论文是 1-based。
"""

import math
import itertools

BLANK = -1  # 论文里的 'blank'/'b'


# ---------- B 映射与扩展序列 ----------

def collapse(path):
    """B: 先合并相邻重复，再去掉 blank（论文 §3.1）。"""
    merged = []
    for c in path:
        if not merged or merged[-1] != c:
            merged.append(c)
    return tuple(c for c in merged if c != BLANK)


def extend_target(labels):
    """l' : [b, l1, b, l2, ..., lU, b]，长度 2|l|+1（论文 §4.1）。"""
    ext = [BLANK]
    for c in labels:
        ext.append(c)
        ext.append(BLANK)
    return ext


def lab_positions(ext, k):
    """lab(l, k) = {s : l'_s = k}，可能为空（论文 §4.1 定义）。"""
    return [s for s, c in enumerate(ext) if c == k]


# ---------- 数值工具 ----------

def logsumexp(vals):
    m = max(vals)
    if m == -math.inf:
        return -math.inf
    return m + math.log(sum(math.exp(v - m) for v in vals))


def log_softmax(u):
    return [ui - logsumexp(u) for ui in u]


# ---------- 前向（log 空间，数值稳定） ----------

def alpha_zero_bound(ext, T, t1):
    """论文 §4.1: α_t(s)=0 ∀s < |l'|−2(T−t)−1（1-based t、s）。返回 1-based 的下界。"""
    return len(ext) - 2 * (T - t1) - 1


def beta_zero_bound(T, t1):
    """论文 §4.1: β_t(s)=0 ∀s > 2t（1-based）。返回 1-based 的上界。"""
    return 2 * t1


def forward_log(log_y, ext, prune=False):
    """返回 log_alpha[t][s]，t 为 0-based 帧号。y 已是每个符号的对数概率。

    prune=True 时把论文 §4.1 的零区（s < |l'|−2(T−t)−1）显式置零。
    """
    T = len(log_y)
    S = len(ext)
    NEG = -math.inf
    la = [[NEG] * S for _ in range(T)]

    # 初始化：α_1(1)=y^1_b, α_1(2)=y^1_{l1}, α_1(s)=0 ∀s>2
    la[0][0] = log_y[0][BLANK]
    if S >= 2:
        la[0][1] = log_y[0][ext[1]]
    if prune:
        b = alpha_zero_bound(ext, T, 1)
        for s in range(S):
            if s + 1 < b:
                la[0][s] = NEG

    for t in range(1, T):
        prev = la[t - 1]
        cur = la[t]
        b = alpha_zero_bound(ext, T, t + 1)
        for s in range(S):
            terms = [prev[s]]
            if s - 1 >= 0:
                terms.append(prev[s - 1])
            # l'_s = b 或 l'_{s-2} = l'_s 时不引入 s-2 项
            if not (ext[s] == BLANK or (s >= 2 and ext[s - 2] == ext[s])):
                if s - 2 >= 0:
                    terms.append(prev[s - 2])
            if all(v == NEG for v in terms):
                cur[s] = NEG
            else:
                cur[s] = logsumexp(terms) + log_y[t][ext[s]]
        if prune:
            for s in range(S):
                if s + 1 < b:
                    cur[s] = NEG
    return la


def backward_log(log_y, ext, prune=False):
    """论文 §4.1 式(10)(11) 的 β，log 空间。prune=True 时置零 s > 2t。"""
    T = len(log_y)
    S = len(ext)
    NEG = -math.inf
    lb = [[NEG] * S for _ in range(T)]
    lb[-1][S - 1] = log_y[-1][BLANK]
    if S >= 2:
        lb[-1][S - 2] = log_y[-1][ext[S - 2]]
    if prune:
        b = beta_zero_bound(T, T)
        for s in range(S):
            if s + 1 > b:
                lb[-1][s] = NEG
    for t in range(T - 2, -1, -1):
        nxt = lb[t + 1]
        b = beta_zero_bound(T, t + 1)
        for s in range(S):
            terms = [nxt[s]]
            if s + 1 < S:
                terms.append(nxt[s + 1])
            if not (ext[s] == BLANK or (s + 2 < S and ext[s + 2] == ext[s])):
                if s + 2 < S:
                    terms.append(nxt[s + 2])
            if all(v == NEG for v in terms):
                lb[t][s] = NEG
            else:
                lb[t][s] = logsumexp(terms) + log_y[t][ext[s]]
        if prune:
            for s in range(S):
                if s + 1 > b:
                    lb[t][s] = NEG
    return lb


def final_log_prob(la, ext):
    """式(8): p(l|x) = α_T(|l'|) + α_T(|l'|-1)。|l'|=1（空标签）时只有 α_T(|l'|)。"""
    S = len(ext)
    if S == 1:
        return la[-1][0]
    return logsumexp([la[-1][S - 1], la[-1][S - 2]])


def ctc_log_prob(log_y, labels):
    """返回 log p(l|x)。T < |l| 时无可行路径，返回 -inf。"""
    ext = extend_target(labels)
    if len(log_y) < len(labels):
        return -math.inf
    return final_log_prob(forward_log(log_y, ext), ext)


# ---------- 前向/后向（论文 §4.1 的 C_t / D_t 缩放版） ----------

def forward_scaled(y, ext, prune=True):
    """返回 (alphas, Cs)。alpha[t] 是归一化后的 α̂_t，C_t = Σ_s α_t(s)。

    论文 §4.1: 用 C_t 缩放后 ln p(l|x) = Σ_t ln C_t。该恒等式成立的前提是按
    论文把「剩余时间不够走完」的状态置零（prune=True）；不置零时 Σ_s α̂_T(s)=1
    会分摊到到不了终点的状态上，Π C_t 就不再等于 p(l)。
    """
    T = len(y)
    S = len(ext)

    def mask_row(row, t1):
        if not prune:
            return row
        b = alpha_zero_bound(ext, T, t1)
        return [0.0 if (s + 1) < b else row[s] for s in range(S)]

    a = [0.0] * S
    a[0] = y[0][BLANK]
    if S >= 2:
        a[1] = y[0][ext[1]]
    a = mask_row(a, 1)
    Ct = sum(a)
    a = [v / Ct for v in a]
    alphas = [a]
    Cs = [Ct]
    for t in range(1, T):
        prev = alphas[-1]
        cur = [0.0] * S
        for s in range(S):
            bar = prev[s] + (prev[s - 1] if s - 1 >= 0 else 0.0)
            if ext[s] == BLANK or (s >= 2 and ext[s - 2] == ext[s]):
                cur[s] = bar * y[t][ext[s]]
            else:
                cur[s] = (bar + (prev[s - 2] if s - 2 >= 0 else 0.0)) * y[t][ext[s]]
        cur = mask_row(cur, t + 1)
        c = sum(cur)
        Cs.append(c)
        alphas.append([v / c for v in cur] if c > 0 else cur)
    return alphas, Cs


def backward_scaled(y, ext, Cs):
    """返回 betas（按 D_t 归一化后的 β̂_t）。论文 §4.1 对称地用 D_t = Σ_s β_t(s)。"""
    T = len(y)
    S = len(ext)
    b = [0.0] * S
    b[S - 1] = y[-1][BLANK]
    if S >= 2:
        b[S - 2] = y[-1][ext[S - 2]]
    d = sum(b)
    b = [v / d for v in b]
    betas = [b]
    for t in range(T - 2, -1, -1):
        nxt = betas[0]
        cur = [0.0] * S
        for s in range(S):
            bar = nxt[s] + (nxt[s + 1] if s + 1 < S else 0.0)
            if ext[s] == BLANK or (s + 2 < S and ext[s + 2] == ext[s]):
                cur[s] = bar * y[t][ext[s]]
            else:
                cur[s] = (bar + (nxt[s + 2] if s + 2 < S else 0.0)) * y[t][ext[s]]
        d = sum(cur)
        # 论文对 β 用 D_t 缩放；这里再除以 C_t 使 α̂β̂ 与式(16) 的 Z_t 一致
        betas.insert(0, [v / d / Cs[t] if Cs[t] > 0 else v for v in cur])
    return betas


def gradient_wrt_u(y, ext, alphas, betas):
    """式(16): ∂O/∂u^t_k = y^t_k - (1/(y^t_k Z_t)) Σ_{s∈lab(l,k)} α̂_t(s)β̂_t(s)。"""
    T = len(y)
    grad = []
    for t in range(T):
        Zt = 0.0
        for s in range(len(ext)):
            denom = y[t][ext[s]]
            if denom > 0:
                Zt += alphas[t][s] * betas[t][s] / denom
        row = {}
        for k in y[t]:
            acc = 0.0
            for s in lab_positions(ext, k):
                acc += alphas[t][s] * betas[t][s]
            row[k] = y[t][k] - (acc / (y[t][k] * Zt) if (y[t][k] > 0 and Zt > 0) else 0.0)
        grad.append(row)
    return grad


# ---------- 解码 ----------

def best_path_decode(y, alphabet):
    """§3.2 best path decoding：π* 为每帧最大激活的拼接，再过 B。"""
    pi = []
    for row in y:
        pi.append(max(row, key=lambda k: row[k]))
    return collapse(pi)


def all_labelling_probs(y, alphabet, T):
    """穷举 L'^T 的全部路径，返回 {labelling: p}（仅用于小规模对照）。"""
    symbols = list(alphabet) + [BLANK]
    acc = {}
    for pi in itertools.product(symbols, repeat=T):
        p = 1.0
        for t, c in enumerate(pi):
            p *= y[t][c]
        if p == 0.0:
            continue
        lab = collapse(pi)
        acc[lab] = acc.get(lab, 0.0) + p
    return acc
