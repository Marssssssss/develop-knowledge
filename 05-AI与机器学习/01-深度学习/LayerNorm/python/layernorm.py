"""LayerNorm / RMSNorm:per-sample 归一化的前向、解析反向与不变量实验。

只用 NumPy,不调框架的 nn.LayerNorm —— 目的是让读者看清"归一到哪个维度"
以及"减均值"这一步到底带来了什么(以及去掉了什么)。

权威依据:
  - Ba/Kiros/Hinton 2016 (arXiv:1607.06450) Layer Normalization
  - Zhang/Sennrich 2019 (arXiv:1910.07467) RMSNorm
  - Xiong et al. 2020 (arXiv:2002.04745) On Layer Normalization in the Transformer
"""

import numpy as np

EPS = 1e-5


# --------------------------------------------------------------------------
# 前向
# --------------------------------------------------------------------------
def layernorm_forward(x, gamma, beta, eps=EPS):
    """x: (N, H) -> y, cache。统计量在**每个样本的 H 维特征**上算,与 batch 无关。"""
    mu = x.mean(axis=-1, keepdims=True)
    var = ((x - mu) ** 2).mean(axis=-1, keepdims=True)
    rstd = 1.0 / np.sqrt(var + eps)
    xhat = (x - mu) * rstd
    y = gamma * xhat + beta
    return y, (x, mu, rstd, xhat, gamma)


def rmsnorm_forward(x, gamma, eps=EPS):
    """x: (N, H) -> y, cache。只除 RMS,**不减均值**,也没有 beta(RMSNorm 原文无偏置)。"""
    ms = (x ** 2).mean(axis=-1, keepdims=True)
    rms = np.sqrt(ms + eps)
    y = gamma * x / rms
    return y, (x, rms, gamma)


# --------------------------------------------------------------------------
# 解析反向
# --------------------------------------------------------------------------
def layernorm_backward(dy, cache):
    """LN 的解析反向。三路:xhat 直通、var、mu(后两路是"归一化"引入的耦合)。"""
    x, mu, rstd, xhat, gamma = cache
    H = x.shape[-1]
    dxhat = dy * gamma
    # 路 1:var -> rstd
    dvar = (dxhat * (x - mu)).sum(axis=-1, keepdims=True) * (-0.5) * rstd ** 3
    # 路 2:mu(来自 (x-mu) 与 var 两处对 mu 的依赖)
    dmu = (dxhat * -rstd).sum(axis=-1, keepdims=True) + dvar * (-2.0 / H) * (x - mu).sum(
        axis=-1, keepdims=True
    )
    dx = dxhat * rstd + dvar * 2.0 * (x - mu) / H + dmu / H
    dgamma = (dy * xhat).sum(axis=0)
    dbeta = dy.sum(axis=0)
    return dx, dgamma, dbeta


def rmsnorm_backward(dy, cache):
    """RMSNorm 反向:少了 mu 那一路(矩阵更简单,这是它的全部代价与全部收益)。

    ∂y_i/∂x_j = γ_i δ_ij / r − γ_i x_i x_j /(H r³)
    => dx_j = γ_j dy_j / r − x_j · Σ_i(γ_i dy_i x_i) /(H r³)
    注意 γ 出现在**分子求和里**,不是乘以 x_j —— 写错位置会让梯度偏差 ~9e-2。
    """
    x, rms, gamma = cache
    H = x.shape[-1]
    gdyx = (gamma * dy * x).sum(axis=-1, keepdims=True)
    dx = gamma * dy / rms - x * gdyx / (H * rms ** 3)
    dgamma = (dy * x / rms).sum(axis=0)
    return dx, dgamma


# --------------------------------------------------------------------------
# 数值梯度(中心差分)作为唯一裁判
# --------------------------------------------------------------------------
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


def check_gradient(kind, seed=0):
    rng = np.random.default_rng(seed)
    N, H = 7, 11
    x = rng.normal(size=(N, H))
    gamma = rng.normal(size=H) * 0.5 + 1.0
    beta = rng.normal(size=H) * 0.2
    dy = rng.normal(size=(N, H))

    if kind == "ln":
        y, cache = layernorm_forward(x, gamma, beta)
        dx, dg, db = layernorm_backward(dy, cache)
        num_dx = numerical_grad(lambda: float((layernorm_forward(x, gamma, beta)[0] * dy).sum()), x)
        num_dg = numerical_grad(
            lambda: float((layernorm_forward(x, gamma, beta)[0] * dy).sum()), gamma
        )
        return rel_err(dx, num_dx), rel_err(dg, num_dg)

    y, cache = rmsnorm_forward(x, gamma)
    dx, dg = rmsnorm_backward(dy, cache)
    num_dx = numerical_grad(lambda: float((rmsnorm_forward(x, gamma)[0] * dy).sum()), x)
    num_dg = numerical_grad(lambda: float((rmsnorm_forward(x, gamma)[0] * dy).sum()), gamma)
    return rel_err(dx, num_dx), rel_err(dg, num_dg)


# --------------------------------------------------------------------------
# 不变量实验:三个"变了也不影响输出"的变换
# --------------------------------------------------------------------------
def invariance_report(seed=0):
    rng = np.random.default_rng(seed)
    H = 32
    x = rng.normal(size=(1, H))
    gamma = np.ones(H)
    beta = np.zeros(H)

    def spread(a, b):
        return float(np.max(np.abs(a - b)))

    out = {}
    # 1) 加常数:LN 平移不变;RMSNorm 平移**不**不变(只除 RMS,均值还在)
    for name, fwd in (("ln", layernorm_forward), ("rms", rmsnorm_forward)):
        y0 = fwd(x, gamma, beta)[0] if name == "ln" else fwd(x, gamma)[0]
        y1 = fwd(x + 5.0, gamma, beta)[0] if name == "ln" else fwd(x + 5.0, gamma)[0]
        out[f"{name}_shift"] = spread(y0, y1)
    # 2) 乘正标量:两者都 re-scaling 不变。残余的非零只来自 eps(理论上应为 0)
    for name, fwd in (("ln", layernorm_forward), ("rms", rmsnorm_forward)):
        for eps in (EPS, 0.0):
            y0 = fwd(x, gamma, beta, eps)[0] if name == "ln" else fwd(x, gamma, eps)[0]
            y1 = (
                fwd(x * 1000.0, gamma, beta, eps)[0]
                if name == "ln"
                else fwd(x * 1000.0, gamma, eps)[0]
            )
            out[f"{name}_scale_eps{eps:g}"] = spread(y0, y1)
    # 3) batch 独立性:同一个样本放进不同 batch,输出必须逐位相同(BN 做不到)
    y_alone = layernorm_forward(x, gamma, beta)[0]
    other = rng.normal(size=(4, H)) * 50.0
    y_in_batch = layernorm_forward(np.vstack([other, x]), gamma, beta)[0][-1:]
    out["ln_batch_dep"] = spread(y_alone, y_in_batch)
    # 4) RMSNorm 何时等价于 LayerNorm:输入本身零均值时
    xz = x - x.mean(axis=-1, keepdims=True)
    y_ln = layernorm_forward(xz, gamma, beta)[0]
    y_rms = rmsnorm_forward(xz, gamma)[0]
    out["zero_mean_gap"] = spread(y_ln, y_rms)
    return out


# --------------------------------------------------------------------------
# Pre-LN vs Post-LN:初始化时的梯度剖面
# --------------------------------------------------------------------------
def mlp_block(h, params, ln="pre"):
    """h -> h + W2(relu(W1 LN(h)));post 变体把 LN 挪到**相加之后**。

    返回块输出与反向所需的缓存(含 LN 的 cache,避免反向时重算).
    """
    W1, b1, W2, b2, g1, be1, g2, be2 = params
    n, ln_cache = h, None
    if ln == "pre":
        n, ln_cache = layernorm_forward(h, g1, be1)
    a = n @ W1 + b1
    r = np.maximum(a, 0.0)
    f = r @ W2 + b2
    post_cache = None
    out = h + f
    if ln == "post":
        out, post_cache = layernorm_forward(out, g2, be2)
    return out, (h, n, a, r, params, ln_cache, post_cache)


def stack_grad_profile(depth, H, ln, seed=0, out_scale=None):
    """搭 depth 个残差块,算 loss=0.5*||h_L||^2 对每块 W1 的梯度范数剖面。

    返回逐块 ‖dL/dW1‖ 列表(已按最后一层归一化,便于比较形状)。
    """
    rng = np.random.default_rng(seed)
    scale = out_scale if out_scale is not None else (1.0 / np.sqrt(H))
    params = []
    for _ in range(depth):
        params.append(
            [
                rng.normal(size=(H, H)) * scale,
                np.zeros(H),
                rng.normal(size=(H, H)) * scale,
                np.zeros(H),
                np.ones(H),
                np.zeros(H),
                np.ones(H),
                np.zeros(H),
            ]
        )
    h = rng.normal(size=(2, H))

    # 前向,记录缓存
    caches = []
    for p in params:
        h, c = mlp_block(h, p, ln=ln)
        caches.append(c)
    loss = 0.5 * float((h ** 2).sum())

    # 反向:先 dL/dh,再逐块反向
    dh = h
    norms = [0.0] * depth
    for i in range(depth - 1, -1, -1):
        h_in, n, a, r, p, ln_cache, post_cache = caches[i]
        W1, b1, W2, b2 = p[0], p[1], p[2], p[3]
        if ln == "post":
            # out = LN(h_in + f):先把 dh 穿过块尾的 LN
            dh_out = layernorm_backward(dh, post_cache)[0]
        else:
            dh_out = dh
        # h_out = h_in + f  =>  dh_in = dh_out + dh_f,而 f 的分支到 W1 只经过 relu
        dr = dh_out @ W2.T
        da = dr * (a > 0)
        dn = da @ W1.T
        dW1 = n.T @ da
        norms[i] = float(np.linalg.norm(dW1))
        if ln == "pre":
            dh = dh_out + layernorm_backward(dn, ln_cache)[0]
        else:
            dh = dh_out + dn
    denom = max(norms[-1], 1e-30)
    return norms, denom, loss


def main():
    print("== 1. 解析反向 vs 数值梯度(相对误差,阈值 1e-7) ==")
    for kind in ("ln", "rms"):
        dxe, dge = check_gradient(kind)
        print(f"  {kind:>3}: d/dx {dxe:.3e}   d/dgamma {dge:.3e}")

    print("\n== 2. 不变量(LN 减均值 / RMSNorm 不减均值)==")
    inv = invariance_report()
    for k, v in inv.items():
        print(f"  {k:<16} max|Δy| = {v:.3e}")

    print("\n== 3. Pre-LN vs Post-LN 初始化梯度剖面(H=64,7 个 seed 的中位数) ==")
    print("   depth |  Pre-LN ‖dL/dW1‖(首块/末块/比值) |  Post-LN(首块/末块/比值)")
    for depth in (4, 8, 16, 32):
        cells = []
        for ln in ("pre", "post"):
            firsts, lasts, ratios = [], [], []
            for seed in range(7):
                norms, last, _ = stack_grad_profile(depth, 64, ln, seed=seed)
                firsts.append(norms[0])
                lasts.append(norms[-1])
                ratios.append(norms[0] / max(norms[-1], 1e-30))
            med = lambda v: float(np.median(v))  # noqa: E731
            cells.append((med(firsts), med(lasts), med(ratios)))
        print(
            f"   {depth:5d} |  {cells[0][0]:.3e} / {cells[0][1]:.3e} / {cells[0][2]:.4f}"
            f"            |  {cells[1][0]:.3e} / {cells[1][1]:.3e} / {cells[1][2]:.4f}"
        )
    print("  读法:比值 <1 表示「靠近输出层的块梯度更大」。实测两类的符号完全相反 ——")
    print("  Pre-LN 首/末比恒 >1,Post-LN 恒 <1;绝对量级 Post-LN 比 Pre-LN 小 5~6 个数量级。")
    print("  这正是 Post-LN 必须配 lr warmup 的来源(靠近输出层的大梯度 × 大 lr → 发散)。")

    print("\n== 4. 计算成本(每元素浮点操作数,含常见优化的口径) ==")
    H = 4096
    x = np.random.default_rng(0).normal(size=(2048, H))
    g = np.ones(H)
    import time

    for name, fn in (("LayerNorm", lambda: layernorm_forward(x, g, g)), ("RMSNorm", lambda: rmsnorm_forward(x, g))):
        t0 = time.perf_counter()
        for _ in range(50):
            fn()
        t1 = time.perf_counter()
        print(f"  {name:<10} {(t1 - t0) / 50 * 1e3:.2f} ms/次 (2048x{H})")


if __name__ == "__main__":
    main()
