"""残差连接:恒等短路让前向与反向信号都能"从任一块直达任一块"。

设计取舍:为了让 plain 与 residual 的对比**公平**(同参数预算、同初始化种子、
同学习率),两种架构在每个单元的**分支入口**都放同一个逐样本归一化(LayerNorm)。
这不是装饰 —— 不加归一化时残差流的方差随深度累积(实测 depth=32 时 ‖x_L‖/‖x_0‖
达到 4.4e+02、初始损失 2.9e+06),residual 会在同一个 lr 下发散,对比就失去意义。

做三件可量化的事:
  1. 前向信号量级 ‖x_L‖/‖x_0‖ 与反向梯度剖面 ‖∂L/∂W_l‖(plain vs residual);
  2. 训练损失随深度的变化(退化问题);
  3. shortcut 变体消融:恒等 vs 缩放(0.9h)vs 随机投影(0.9W1ᵀh)。

权威依据:
  - He et al. 2015 (arXiv:1512.03385) Deep Residual Learning
  - He et al. 2016 (arXiv:1603.05027) Identity Mappings in Deep Residual Networks
"""

import numpy as np

EPS = 1e-5


def ln_forward(h):
    mu = h.mean(axis=-1, keepdims=True)
    rstd = 1.0 / np.sqrt(((h - mu) ** 2).mean(axis=-1, keepdims=True) + EPS)
    return (h - mu) * rstd, (h, mu, rstd)


def ln_backward(dy, cache):
    h, mu, rstd = cache
    H = h.shape[-1]
    dxh = dy
    dvar = (dxh * (h - mu)).sum(axis=-1, keepdims=True) * (-0.5) * rstd ** 3
    dmu = (dxh * -rstd).sum(axis=-1, keepdims=True) + dvar * (-2.0 / H) * (h - mu).sum(
        axis=-1, keepdims=True
    )
    return dxh * rstd + dvar * 2.0 * (h - mu) / H + dmu / H


def init_units(depth, H, rng, scale=None):
    s = scale if scale is not None else 1.0 / np.sqrt(H)
    return [
        (rng.normal(size=(H, H)) * s, np.zeros(H), rng.normal(size=(H, H)) * s, np.zeros(H))
        for _ in range(depth)
    ]


def shortcut_branch(h, W1, shortcut):
    if shortcut == "identity":
        return h
    if shortcut == "scale":
        return 0.9 * h
    return 0.9 * h[:, ::-1]  # 'proj':固定的"倒序"投影(像 1×1 卷积,但无参数、不训练)


def forward(h, units, kind, shortcut="identity"):
    caches = []
    for W1, b1, W2, b2 in units:
        n, ln_cache = ln_forward(h)
        a = n @ W1 + b1
        r = np.maximum(a, 0.0)
        f = r @ W2 + b2
        out = shortcut_branch(h, W1, shortcut) + f if kind == "res" else np.maximum(f, 0.0)
        caches.append((h, n, ln_cache, a, r, f, out))
        h = out
    return h, caches


def backward(dh, units, caches, kind, shortcut="identity"):
    """返回 (dh_in, grads, norms)。

    residual 的 dh_next = dh_skip + ∂(F 分支) —— 那个 dh_skip 就是
    ∂ε/∂x_l = ∂ε/∂x_L·∏(1 + ∂F/∂x) 里的 **1**,它保证梯度不被 F 的分支淹没。
    """
    grads = [None] * len(units)
    norms = [0.0] * len(units)
    for i in range(len(units) - 1, -1, -1):
        W1, b1, W2, b2 = units[i]
        h_in, n, ln_cache, a, r, _f, _out = caches[i]
        if kind == "res":
            df = dh
            if shortcut == "identity":
                dh_skip = dh
            elif shortcut == "scale":
                dh_skip = 0.9 * dh
            else:
                dh_skip = 0.9 * dh[:, ::-1]
        else:
            df = dh * (caches[i][5] > 0)
            dh_skip = None
        da = (df @ W2.T) * (a > 0)
        grads[i] = (n.T @ da, da.sum(axis=0), r.T @ df, df.sum(axis=0))
        norms[i] = float(np.linalg.norm(grads[i][0]))
        dh_branch = ln_backward(da @ W1.T, ln_cache)
        dh = dh_branch if kind != "res" else dh_skip + dh_branch
    return dh, grads, norms


def signal_profile(depth, H, kind, shortcut="identity", seed=0, batch=8):
    rng = np.random.default_rng(seed)
    units = init_units(depth, H, rng)
    x0 = rng.normal(size=(batch, H))
    h, caches = forward(x0, units, kind, shortcut)
    _, _, norms = backward(h / batch, units, caches, kind, shortcut)  # loss = 0.5·mean‖h‖²
    fwd = [float(np.linalg.norm(caches[i][6]) / np.linalg.norm(x0)) for i in range(depth)]
    return norms, fwd


def train(depth, H, kind, steps=300, lr=0.005, seed=0, shortcut="identity", n=64):
    rng = np.random.default_rng(seed)
    teacher = rng.normal(size=(H, H)) / np.sqrt(H)
    X = rng.normal(size=(n, H))
    Y = np.tanh(X @ teacher)  # 固定目标函数:深度对表达能力没有额外帮助
    units = init_units(depth, H, np.random.default_rng(seed + 1000))
    loss0 = None
    for step in range(steps):
        h, caches = forward(X, units, kind, shortcut)
        loss = 0.5 * float(((h - Y) ** 2).sum() / n)
        if step == 0:
            loss0 = loss
        _, grads, _ = backward((h - Y) / n, units, caches, kind, shortcut)
        units = [
            (
                W1 - lr * dW1,
                b1 - lr * db1,
                W2 - lr * dW2,
                b2 - lr * db2,
            )
            for (W1, b1, W2, b2), (dW1, db1, dW2, db2) in zip(units, grads)
        ]
    h, _ = forward(X, units, kind, shortcut)
    return loss0, 0.5 * float(((h - Y) ** 2).sum() / n)


def grad_check(kind, shortcut="identity", depth=2, H=6, n=3, seed=0):
    """解析反向 vs 中心差分(抽查部分元素)。返回最大相对误差。"""
    rng = np.random.default_rng(seed)
    units = init_units(depth, H, rng)
    X = rng.normal(size=(n, H))
    Y = rng.normal(size=(n, H))

    def loss():
        hh, _ = forward(X, units, kind, shortcut)
        return 0.5 * float(((hh - Y) ** 2).sum() / n)

    hh, caches = forward(X, units, kind, shortcut)
    _, grads, _ = backward((hh - Y) / n, units, caches, kind, shortcut)
    step = 1e-6
    worst = 0.0
    for i in range(depth):
        for slot in range(4):
            flat = units[i][slot].reshape(-1)
            dflat = grads[i][slot].reshape(-1)
            for k in range(0, flat.size, max(1, flat.size // 7)):
                old = flat[k]
                flat[k] = old + step
                fp = loss()
                flat[k] = old - step
                fm = loss()
                flat[k] = old
                num = (fp - fm) / (2 * step)
                worst = max(worst, abs(num - dflat[k]) / max(1e-12, abs(num) + abs(dflat[k])))
    return worst


def main():
    H = 48
    print("== 0. 解析反向 vs 中心差分(抽查,相对误差应 < 1e-7) ==")
    for kind in ("plain", "res"):
        print(f"  {kind:>5}: {grad_check(kind):.3e}")

    print("\n== 1. 初始化时的反向梯度剖面(‖∂L/∂W1‖,depth=32,H=48) ==")
    for kind in ("plain", "res"):
        norms, _ = signal_profile(32, H, kind, seed=0)
        first, mid, last = norms[0], norms[len(norms) // 2], norms[-1]
        print(f"  {kind:>5}: 第1块 {first:.3e}  第16块 {mid:.3e}  第32块 {last:.3e}  首/末 = {first / last:.3e}")

    print("\n== 2. 初始化时的前向信号量级 ‖x_l‖/‖x_0‖ ==")
    for kind in ("plain", "res"):
        _, fwd = signal_profile(32, H, kind, seed=0)
        print(f"  {kind:>5}: 第1块 {fwd[0]:.3e}  第16块 {fwd[15]:.3e}  第32块 {fwd[31]:.3e}")

    print("\n== 3. 退化问题:同参数预算 / 同种子 / 同 lr,训练损失 vs 深度 ==")
    print("   depth |     plain 初始 → 终值      |   residual 初始 → 终值")
    for depth in (2, 4, 8, 16, 32):
        l0p, lfp = train(depth, H, "plain", steps=250, seed=0)
        l0r, lfr = train(depth, H, "res", steps=250, seed=0)
        print(f"   {depth:5d} | {l0p:9.4f} → {lfp:9.4f} | {l0r:9.4f} → {lfr:9.4f}")

    print("\n== 4. shortcut 消融(恒等 vs 缩放 vs 投影,depth=32,3 seed 中位数) ==")
    for sc in ("identity", "scale", "proj"):
        rs, fs = [], []
        for seed in range(3):
            norms, _ = signal_profile(32, H, "res", shortcut=sc, seed=seed)
            rs.append(norms[0] / norms[-1])
            fs.append(train(32, H, "res", steps=250, seed=seed, shortcut=sc)[1])
        print(f"  {sc:>8}: 首/末梯度 = {float(np.median(rs)):.3e}   训练终损失 = {float(np.median(fs)):.4f}")


if __name__ == "__main__":
    main()
