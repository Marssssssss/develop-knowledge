"""学习率调度与梯度裁剪:三条调度曲线的闭式、AdamW 的解耦衰减、裁剪的方向保持。

只做四件可量化的事:
  1. Transformer 的 inverse-sqrt warmup 与 SGDR 余弦退火的闭式性质(交点、端点、周期倍增);
  2. Adam+L2 与 AdamW:权重衰减在不同参数上是否**等强度**;
  3. 梯度裁剪:按范数 vs 按元素,对**方向**的影响;
  4. warmup 的必要性:初始梯度量级极不平衡时,恒定大 lr 发散、warmup 收敛。

权威依据:
  - Vaswani et al. 2017 (arXiv:1706.03762) §5.3 学习率公式 + warmup_steps=4000
  - Loshchilov & Hutter 2016 (arXiv:1608.03983) SGDR 余弦退火 + warm restarts
  - Loshchilov & Hutter 2017 (arXiv:1711.05101) Decoupled Weight Decay(AdamW)
  - Pascanu et al. 2012 (arXiv:1211.5063) 梯度范数裁剪
  - PyTorch: clip_grad_norm_ 默认 max_norm=1.0;AdamW 默认 weight_decay=0.01
"""

import math

import numpy as np


# --------------------------------------------------------------------------
# 1. 调度曲线
# --------------------------------------------------------------------------
def transformer_lr(step, d_model=512, warmup=4000, factor=1.0):
    """lrate = d^-0.5 · min(step^-0.5, step·warmup^-1.5)  —— 原文公式 (3)。

    step<=0 时按 step=1 处理(原始实现里的保护)。
    """
    s = max(1, step)
    return factor * d_model ** -0.5 * min(s ** -0.5, s * warmup ** -1.5)


def cosine_restart_lr(step, cycle_len, eta_max, eta_min=0.0):
    """SGDR 单个周期内的余弦退火:eta_t = eta_min + 0.5(eta_max−eta_min)(1 + cos(pi·T_cur/T_i))。"""
    t_cur = step % cycle_len
    return eta_min + 0.5 * (eta_max - eta_min) * (1 + math.cos(math.pi * t_cur / cycle_len))


def sgdr_lr(step, t0=10, mult=2, eta_max=0.1, eta_min=0.0):
    """带 warm restart 的完整曲线:第 i 个周期长度 T_i = t0·mult^i。

    注意每个周期的**起点**都会把 lr 拉回 eta_max —— 这就是 "warm restart" 的字面含义,
    因此不能用 `step % cycle_len` 直接算(周期长度本身在变)。
    """
    start, T = 0, t0
    while step >= start + T:
        start += T
        T *= mult
    t_cur = step - start
    return eta_min + 0.5 * (eta_max - eta_min) * (1 + math.cos(math.pi * t_cur / T))


def warmup_cosine_lr(step, peak, warmup, total, min_ratio=0.1):
    """LLM 常用配方:线性 warmup 到 peak,再余弦降到 peak·min_ratio。"""
    if step < warmup:
        return peak * (step + 1) / warmup
    prog = min(1.0, (step - warmup) / max(1, total - warmup))
    return peak * (min_ratio + (1 - min_ratio) * 0.5 * (1 + math.cos(math.pi * prog)))


# --------------------------------------------------------------------------
# 2. 优化器:Adam+L2(耦合) vs AdamW(解耦)
# --------------------------------------------------------------------------
def run_optimizer(theta0, grads, lr, wd, kind, steps=500, betas=(0.9, 0.999), eps=1e-8):
    """theta0/grads 是 dict:{名字: 标量}。返回每个参数的衰减倍数 theta/theta0。"""
    names = list(theta0)
    m = {k: 0.0 for k in names}
    v = {k: 0.0 for k in names}
    th = dict(theta0)
    for t in range(1, steps + 1):
        for k in names:
            g = grads[k]
            if kind == "adam_l2":
                g = g + wd * th[k]  # 耦合:衰减项混进梯度,后面会被 1/sqrt(v) 缩放
            m[k] = betas[0] * m[k] + (1 - betas[0]) * g
            v[k] = betas[1] * v[k] + (1 - betas[1]) * g * g
            mh = m[k] / (1 - betas[0] ** t)
            vh = v[k] / (1 - betas[1] ** t)
            th[k] = th[k] - lr * mh / (math.sqrt(vh) + eps)
            if kind == "adamw":
                th[k] = th[k] - lr * wd * th[k]  # 解耦:直接按比例缩小
    return {k: th[k] / theta0[k] for k in names}


# --------------------------------------------------------------------------
# 3. 梯度裁剪
# --------------------------------------------------------------------------
def clip_by_norm(g, max_norm):
    n = float(np.linalg.norm(g))
    pre = n
    if n > max_norm:
        g = g * (max_norm / n)
    return g, pre


def clip_by_value(g, v):
    return np.clip(g, -v, v), float(np.linalg.norm(g))


def cosine(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-30))


# --------------------------------------------------------------------------
# 4. warmup 必要性
# --------------------------------------------------------------------------
def tiny_net_train(peak_lr, warmup_steps, steps=300, seed=0, H=16, n=32, momentum=0.9,
                   out_scale=100.0):
    """两层网络,最后一层权重放大 out_scale 倍 —— 复现「靠近输出的层梯度极大」的初始化。

    返回 (loss_0, loss_final, max_grad_norm_0)。
    """
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, H))
    Y = np.tanh(X @ (rng.normal(size=(H, H)) / np.sqrt(H)))
    W1 = rng.normal(size=(H, H)) / np.sqrt(H)
    W2 = rng.normal(size=(H, H)) / np.sqrt(H) * out_scale  # 放大 -> 初始梯度失衡
    vel1 = np.zeros_like(W1)
    vel2 = np.zeros_like(W2)
    loss0 = None
    for step in range(steps):
        A = X @ W1
        R = np.maximum(A, 0.0)
        out = R @ W2
        diff = out - Y
        loss = 0.5 * float((diff ** 2).sum() / n)
        if step == 0:
            loss0 = loss
        dW2 = R.T @ diff / n
        dA = (diff @ W2.T) / n
        dW1 = X.T @ (dA * (A > 0))
        gnorm = math.sqrt(float((dW1 ** 2).sum() + (dW2 ** 2).sum()))
        if step == 0:
            g0 = gnorm
        # lr 调度:线性 warmup(未启用时 warmup_steps=0,直接用 peak)
        lr = peak_lr if warmup_steps == 0 else peak_lr * (step + 1) / warmup_steps
        lr = min(lr, peak_lr)
        vel1 = momentum * vel1 - lr * dW1
        vel2 = momentum * vel2 - lr * dW2
        W1 = W1 + vel1
        W2 = W2 + vel2
        if not np.isfinite(loss):
            return loss0, float("nan"), g0
    A = X @ W1
    out = np.maximum(A, 0.0) @ W2
    return loss0, 0.5 * float(((out - Y) ** 2).sum() / n), g0


def main():
    print("== 1. Transformer inverse-sqrt warmup(d_model=512, warmup=4000) ==")
    print("     step        lr          note")
    for s in (1, 100, 1000, 4000, 4001, 10000, 100000):
        note = "← 两分支交点(峰值)" if s == 4000 else ""
        print(f"  {s:7d}   {transformer_lr(s):.6e}   {note}")
    peak = 512 ** -0.5 * 4000 ** -0.5
    print(f"  解析峰值 d^-0.5·warmup^-0.5 = {peak:.6e};实测 lr(4000) = {transformer_lr(4000):.6e}")
    print(f"  两分支在 step=warmup 处相等:"
          f" step^-0.5 = {4000 ** -0.5:.6e}, step·warmup^-1.5 = {4000 * 4000 ** -1.5:.6e}")
    print(f"  之后按 step^-0.5 衰减:lr(100000)/lr(4000) = "
          f"{transformer_lr(100000) / transformer_lr(4000):.4f}(理论 {(100000 / 4000) ** -0.5:.4f})")

    print("\n== 2. SGDR 余弦退火 + warm restarts ==")
    print("   eta_max=0.1, eta_min=0, T_0=10, T_mult=2")
    step = 0
    for i in range(4):
        T_i = 10 * 2 ** i
        vals = [sgdr_lr(step + k, 10, 2, 0.1) for k in (0, T_i // 2, T_i - 1)]
        print(f"   cycle {i}: 周期 {T_i:3d} 步  η(重启点)={vals[0]:.6f}  η(中点)={vals[1]:.6f}"
              f"  η(末步)={vals[2]:.6f}")
        step += T_i
    print(f"  每个周期起点都回到 η_max=0.1(warm restart);4 个周期共 {step} 步(10+20+40+80)")

    print("\n== 3. LLM 配方:线性 warmup + 余弦衰减 ==")
    for s in (0, 1000, 1999, 2000, 5000, 10000):
        print(f"  step {s:6d}: lr = {warmup_cosine_lr(s, 3e-4, 2000, 10000):.6e}")

    print("\n== 4. Adam+L2 vs AdamW:权重衰减是否等强度 ==")
    # 两个参数初值相同,但梯度量级差 100 倍
    theta0 = {"small_grad": 1.0, "large_grad": 1.0}
    grads = {"small_grad": 0.01, "large_grad": 1.0}
    for kind in ("adam_l2", "adamw"):
        ratio = run_optimizer(theta0, grads, lr=1e-3, wd=0.1, kind=kind)
        a, b = ratio["small_grad"], ratio["large_grad"]
        print(f"  {kind:>9}: θ/θ0 = {a:.6f} / {b:.6f}   两者之比 = {a / b:.6f}")
    print("  解耦版本两者严格相等(比值 1.000000);耦合版本被 1/sqrt(v) 按参数差异化缩放。")

    print("\n== 5. 梯度裁剪:按范数 vs 按元素 ==")
    g = np.array([10.0, 0.1, -0.2, 0.05])
    g_n, pre = clip_by_norm(g, 1.0)
    g_v, _ = clip_by_value(g, 1.0)
    print(f"  原梯度 {g},‖g‖ = {pre:.6f}")
    print(f"  按范数裁剪后 {np.round(g_n, 6)}  cos(原, 裁) = {cosine(g, g_n):.10f}")
    print(f"  按元素裁剪后 {np.round(g_v, 6)}  cos(原, 裁) = {cosine(g, g_v):.10f}")
    print(f"  按范数:方向严格不变(cos=1),长度被压到 {np.linalg.norm(g_n):.6f}")
    print(f"  按元素:长度 {np.linalg.norm(g_v):.6f},但方向被改了 "
          f"{math.degrees(math.acos(min(1.0, cosine(g, g_v)))):.2f}°")

    print("\n== 6. warmup 的必要性(初始梯度量级失衡,‖g_0‖~1e3) ==")
    print("   配置(输出层放大倍数 / 峰值 lr) |  无 warmup  |  warmup 150 步")
    for out_scale, peak in ((10.0, 1e-3), (30.0, 2e-3)):
        cells = []
        for warm in (0, 150):
            _, lf, g0 = tiny_net_train(peak_lr=peak, warmup_steps=warm, steps=300, out_scale=out_scale)
            cells.append("发散(nan)" if lf != lf else f"{lf:.4f}")
        print(f"   scale={out_scale:<4.0f} peak={peak:<7.0e}(‖g_0‖={g0:.2e}) | {cells[0]:>10}  | {cells[1]:>10}")
    print("  同样的峰值学习率:失衡越严重,不用 warmup 越会直接发散;加了 warmup 就收敛。")


if __name__ == "__main__":
    main()
