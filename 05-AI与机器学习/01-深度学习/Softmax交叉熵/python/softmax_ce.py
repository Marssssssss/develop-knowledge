"""Softmax + Cross-Entropy:数值稳定实现、梯度化简、weight/reduction/label_smoothing 语义。

不调框架的 loss 函数 —— 目的是把「logits -> loss -> 梯度」这条链上的每一处近似
都暴露出来:哪里会溢出、哪里会下溢、p−y 这个梯度是怎么化简出来的、
class weight 的除数到底是 Σw 还是 N。

权威依据:
  - PyTorch CrossEntropyLoss 文档(含 weight/reduction/label_smoothing 的精确定义)
  - Szegedy et al. 2016 (arXiv:1512.00567) label smoothing 的原始动机
"""

import numpy as np

NEG_INF = -np.inf


# --------------------------------------------------------------------------
# 前向:两条路线,一条会炸,一条不会
# --------------------------------------------------------------------------
def logsumexp_naive(x):
    """教科书公式:直接 Σexp。只要最大 logit 超过 ~709(float64)/~88(float32) 就溢出。"""
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        return np.log(np.exp(x).sum(axis=-1, keepdims=True))


def logsumexp_stable(x):
    """工程公式:先减去每行的 max 再 exp —— 指数最多是 exp(0)=1,永不溢出。"""
    m = x.max(axis=-1, keepdims=True)
    return m + np.log(np.exp(x - m).sum(axis=-1, keepdims=True))


def cross_entropy(logits, targets, weight=None, reduction="mean", label_smoothing=0.0,
                  stable=True, return_probs=False):
    """targets: (N,) 类别索引 或 (N, C) 概率分布。语义对齐 PyTorch CrossEntropyLoss。"""
    x = np.asarray(logits, dtype=np.float64)
    N, C = x.shape
    lse = (logsumexp_stable if stable else logsumexp_naive)(x)
    logp = x - lse

    if targets.ndim == 1:
        y = np.zeros((N, C))
        y[np.arange(N), targets] = 1.0
    else:
        y = np.asarray(targets, dtype=np.float64)
    if label_smoothing > 0.0:
        y = (1.0 - label_smoothing) * y + label_smoothing / C

    w = np.ones(C) if weight is None else np.asarray(weight, dtype=np.float64)
    # 逐样本损失(未 reduction);概率 target 走 Σ_c w_c logp_c y_c 的通用式
    if targets.ndim == 1 and label_smoothing == 0.0:
        per = -w[targets] * logp[np.arange(N), targets]
        denom = w[targets].sum()
    else:
        per = -(logp * (w * y)).sum(axis=-1)
        denom = float(N)

    if reduction == "none":
        out = per
    elif reduction == "sum":
        out = per.sum()
    else:
        out = per.sum() / denom if targets.ndim == 1 and label_smoothing == 0.0 else per.mean()
    return (out, logp) if return_probs else out


def ce_grad_logits(logits, targets, weight=None, label_smoothing=0.0):
    """闭式梯度:∂ℓ/∂x = p − y(带 weight 时是 w_y·(p − y))。

    这个捷径来自 log-softmax 的 Jacobian 与 CE 的导数相消;它同时说明
    梯度**有界**(每个分量落在 [−1,1] 内),所以 CE 本身不会梯度爆炸。
    """
    x = np.asarray(logits, dtype=np.float64)
    N, C = x.shape
    logp = x - logsumexp_stable(x)
    p = np.exp(logp)
    if targets.ndim == 1:
        y = np.zeros((N, C))
        y[np.arange(N), targets] = 1.0
    else:
        y = np.asarray(targets, dtype=np.float64)
    if label_smoothing > 0.0:
        y = (1.0 - label_smoothing) * y + label_smoothing / C
    w = np.ones(C) if weight is None else np.asarray(weight, dtype=np.float64)
    if targets.ndim == 1 and label_smoothing == 0.0:
        g = w[targets][:, None] * (p - y)
    else:
        g = w[None, :] * (p - y)
    return g, p, logp


def numerical_grad(f, arr, h=1e-6):
    g = np.zeros_like(arr, dtype=np.float64)
    it = np.nditer(arr, flags=["multi_index"])
    while not it.finished:
        idx = it.multi_index
        old = arr[idx]
        arr[idx] = old + h
        fp = f()
        arr[idx] = old - h
        fm = f()
        arr[idx] = old
        g[idx] = (fp - fm) / (2 * h)
        it.iternext()
    return g


def rel_err(a, b):
    return float(np.max(np.abs(a - b)) / max(1e-12, np.max(np.abs(a) + np.abs(b))))


def main():
    rng = np.random.default_rng(0)

    print("== 1. 数值稳定:朴素 Σexp vs max-shift ==")
    broken = 0
    worst = None
    for k in range(200):
        scale = 10 ** rng.uniform(1, 4)
        x = rng.normal(size=(3, 5)) * scale
        naive = cross_entropy(x, np.array([0, 1, 2]), stable=False)
        stable = cross_entropy(x, np.array([0, 1, 2]), stable=True)
        if not np.isfinite(naive):
            broken += 1
            if worst is None:
                worst = (scale, x.max(), naive, stable)
    print(f"  200 组随机 logits(scale 10^1~10^4):朴素路线失效 {broken} 组")
    if worst:
        print(f"  首个失效样例:scale={worst[0]:.1f} max_logit={worst[1]:.1f} "
              f"朴素={worst[2]} 稳定={worst[3]:.6f}")

    print("\n== 2. 闭式梯度 p−y vs 中心差分 ==")
    x = rng.normal(size=(4, 6))
    t = rng.integers(0, 6, size=4)
    g, p, _ = ce_grad_logits(x, t)
    gn = numerical_grad(lambda: float(cross_entropy(x, t, reduction="sum")), x)
    print(f"  无 weight: 相对误差 {rel_err(g, gn):.3e}   max|p−y| = {np.abs(g).max():.6f}")
    check_bound = float(np.abs(g).max()) <= 1.0 + 1e-12
    print(f"  |p−y| ≤ 1 恒成立:{check_bound}")

    w = np.array([1.0, 3.0, 0.5, 2.0, 1.0, 1.0])
    gw, _, _ = ce_grad_logits(x, t, weight=w)
    gwn = numerical_grad(lambda: float(cross_entropy(x, t, weight=w, reduction="sum")), x)
    print(f"  带 weight: 相对误差 {rel_err(gw, gwn):.3e}")

    print("\n== 3. reduction='mean' 带 weight 时的除数 ==")
    x2 = np.array([[2.0, 0.0], [0.0, 2.0]])
    t2 = np.array([0, 1])
    w2 = np.array([1.0, 3.0])
    per = cross_entropy(x2, t2, weight=w2, reduction="none")
    mean_pytorch_style = cross_entropy(x2, t2, weight=w2, reduction="mean")
    print(f"  逐样本 l_n(已乘 w) = {per}")
    print(f"  Σw_{{y_n}} = {w2[t2].sum():.0f}(不是 N={len(t2)})")
    print(f"  实测 mean = {mean_pytorch_style:.6f};按 Σw 除 = {per.sum() / w2[t2].sum():.6f};"
          f"按 N 除 = {per.sum() / len(t2):.6f}")
    print(f"  两者相差 {abs(per.sum() / w2[t2].sum() - per.sum() / len(t2)):.6f}"
          f"(= 1/Σw−1/N 的放大效应)")

    print("\n== 4. log_softmax 与 log(softmax) 的下溢差别 ==")
    for gap in (100.0, 800.0):
        x3 = np.array([[0.0, -gap]])
        _, logp = cross_entropy(x3, np.array([1]), return_probs=True)
        with np.errstate(divide="ignore"):
            sep = np.log(np.exp(x3 - logsumexp_stable(x3)))[0, 1]  # 先 softmax 再 log
        print(f"  logit 差 {gap:5.0f}: log_softmax = {logp[0, 1]:.6f}   log(softmax) = {sep}")

    print("\n== 5. label_smoothing ==")
    x4 = np.array([[2.0, 1.0, 0.0]])
    t4 = np.array([0])
    for eps in (0.0, 0.1, 0.5):
        v = cross_entropy(x4, t4, label_smoothing=eps)
        g4, p4, _ = ce_grad_logits(x4, t4, label_smoothing=eps)
        print(f"  eps={eps:<4} loss={v:.6f}  梯度={np.round(g4, 6)}  Σ梯度={g4.sum():.3e}")
    # 平滑项指向均匀分布,而「对任意分布 p、与均匀分布 y=1/C 的交叉熵」最小值是 log C
    C = x4.shape[1]
    print(f"  eps=0.5 时平滑分量有不可约下界 ε·log C = 0.5×{np.log(C):.4f} = {0.5 * np.log(C):.4f}"
          f" —— 所以平滑后 loss 不可能趋近 0")

    print("\n== 6. logits 平移/缩放不变性 ==")
    x5 = rng.normal(size=(2, 4))
    base = cross_entropy(x5, np.array([1, 2]))
    shifted = cross_entropy(x5 + 1000.0, np.array([1, 2]))
    print(f"  全体 +1000: {base:.9f} → {shifted:.9f}  Δ={abs(base - shifted):.3e}(softmax 平移不变)")
    # 缩放**不是**不变性:两种情形分别饱和到 0 与 √(2N)
    t_ok = x5.argmax(axis=-1)                        # 预测正确
    t_wrong = (x5.argmax(axis=-1) + 1) % x5.shape[1]  # 预测错误
    for tag, t in (("预测正确(与 argmax 一致)", t_ok), ("预测错误(argmax 是别的类)", t_wrong)):
        cells = []
        for s in (1.0, 10.0, 100.0):
            gs, _, _ = ce_grad_logits(x5 * s, t)
            cells.append(f"×{s:<4.0f} {np.linalg.norm(gs):.6f}")
        print(f"  {tag}: " + "   ".join(cells))
    print(f"  饱和上界参考:预测错误时 √(2N) = {np.sqrt(2 * x5.shape[0]):.6f};"
          f"预测正确时 → 0(梯度消失)")

    print("\n== 7. 概率型 target 不做校验的后果 ==")
    x6 = np.array([[1.0, 0.5, -1.0]])
    t_good = np.array([[0.6, 0.3, 0.1]])
    t_bad = t_good * 10.0            # 既不归一化、也超出 [0,1]
    l_good = cross_entropy(x6, t_good)
    l_bad = cross_entropy(x6, t_bad)
    print(f"  合法概率 target: loss = {l_good:.6f}")
    print(f"  ×10 的非法 target: loss = {l_bad:.6f}(= {l_bad / l_good:.1f}× 合法值,无任何报错)")
    p6 = np.exp(x6 - logsumexp_stable(x6))
    print(f"  合法 target 下 Σ(p−y) = {float((p6 - t_good).sum()):.3e}(应为 0)")
    print(f"  非法 target 下 Σ(p−y) = {float((p6 - t_bad).sum()):.3e}(不再为 0 → 梯度方向失义)")


if __name__ == "__main__":
    main()
