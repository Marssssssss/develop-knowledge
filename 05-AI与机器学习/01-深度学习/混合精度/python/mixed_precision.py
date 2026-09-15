"""混合精度训练:FP16 的可表示范围、loss scaling、master weights、动态缩放与 FP32 累加。

用 NumPy 的 float16 做真实舍入(不是模拟),所有结论都能在本机复现。

权威依据:
  - Micikevicius et al. 2017 (arXiv:1710.03740) Mixed Precision Training
  - IEEE-754 二进制浮点格式(fp16: 1/5/10;bf16: 1/8/7)
"""

import numpy as np

# --------------------------------------------------------------------------
# 格式参数(IEEE-754 二进制 16 与二进制 32)
# --------------------------------------------------------------------------
F16 = np.finfo(np.float16)
F32 = np.finfo(np.float32)


def bf16_round(x):
    """把 fp32 截断成 bf16(保留高 16 位):1 符号 + 8 指数 + 7 尾数。

    只做截断、不做 round-to-nearest-even —— 这正是它在"范围优先"取舍下的
    典型精度损失来源,误差量级仍是 2^-8。
    """
    b = np.float32(x).view(np.uint32)
    return (b & np.uint32(0xFFFF0000)).view(np.float32)


def fp16(x):
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        return np.float16(x)


# --------------------------------------------------------------------------
# 1. 可表示范围
# --------------------------------------------------------------------------
def range_table():
    return {
        "fp16 最小正规数": float(F16.tiny),                       # 2^-14
        "fp16 最小非正规数": float(F16.smallest_subnormal),       # 2^-24
        "fp16 最大值": float(F16.max),                            # 65504
        "fp16 eps(相对间距)": float(F16.eps),                     # 2^-10
        "fp32 最小正规数": float(F32.tiny),
        "fp32 最大值": float(F32.max),
    }


def underflow_sweep(lo=-30, hi=-15):
    return {k: float(fp16(2.0 ** k)) for k in range(lo, hi)}


# --------------------------------------------------------------------------
# 2. 梯度下溢与 loss scaling
# --------------------------------------------------------------------------
def scaling_table(scales=(1.0, 2.0 ** 10, 2.0 ** 18, 2.0 ** 20), n=20000, seed=0):
    """梯度量级横跨 1e-9 ~ 1e-2(深度学习里常见的实际分布)。

    返回每个 S 下的 (被冲成 0 的比例, 还原后的最大相对误差, 是否发生溢出)。
    """
    rng = np.random.default_rng(seed)
    g = 10.0 ** rng.uniform(-9, -2, size=n)
    out = []
    for S in scales:
        scaled = fp16(g * S)                     # 反向传播里流转的是 fp16 梯度
        over = bool(np.any(~np.isfinite(scaled)))
        flushed = float(np.mean(scaled == 0))
        recovered = np.float32(scaled) / np.float32(S)   # 反缩放**必须在 fp32 里做**
        finite = np.isfinite(recovered) & (g > 0)
        rel = float(np.max(np.abs(recovered[finite] - g[finite]) / g[finite])) if finite.any() else float("nan")
        out.append((S, flushed, rel, over))
    return out


# --------------------------------------------------------------------------
# 3. master weights 是否必要
# --------------------------------------------------------------------------
def master_weight_demo(w0=1.0, lr=1e-7, steps=1000):
    """返回 (纯 fp16 权重, fp32 master 权重, master 权重舍入回 fp16 的值)。"""
    w_h = fp16(w0)
    for _ in range(steps):
        w_h = fp16(np.float32(w_h) - np.float32(lr))   # 更新也走 fp16
    w_f = np.float32(w0)
    for _ in range(steps):
        w_f = np.float32(w_f) - np.float32(lr)          # 更新在 fp32 里累加
    return float(w_h), float(w_f), float(fp16(w_f))# --------------------------------------------------------------------------
# 4. 动态缩放(GradScaler 的语义)
# --------------------------------------------------------------------------
def dynamic_scaler(steps=200, init_scale=2.0 ** 16, growth=2.0, backoff=0.5, interval=10,
                   overflow_at=(50, 120), seed=0):
    """每 interval 个成功步把 scale 翻倍;一旦出现 inf/nan 就把 scale 减半并**跳过该步**。"""
    rng = np.random.default_rng(seed)
    scale = float(init_scale)
    log = []
    skipped = 0
    ok_streak = 0
    for step in range(steps):
        g = 10.0 ** rng.uniform(-7, -3)
        overflow = step in overflow_at or (g * scale > float(F16.max))
        if overflow:
            scale *= backoff
            skipped += 1
            ok_streak = 0
        else:
            ok_streak += 1
            if ok_streak >= interval:
                scale *= growth
                ok_streak = 0
        log.append((step, scale, overflow))
    return log, skipped


# --------------------------------------------------------------------------
# 5. 累加精度:为什么 Tensor Core 要 FP32 累加
# --------------------------------------------------------------------------
def accumulation_demo(n=2000, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.uniform(0.5, 1.5, size=n).astype(np.float16)
    acc16 = np.float16(0.0)
    for v in x:                                   # 逐项在 fp16 里累加
        acc16 = np.float16(np.float32(acc16) + np.float32(v))
    acc32 = np.float32(0.0)
    for v in x:
        acc32 = np.float32(acc32) + np.float32(v)
    ref = float(np.sum(x.astype(np.float64)))
    return float(acc16), float(acc32), ref


# --------------------------------------------------------------------------
# 6. 前向舍入误差:fp16 vs bf16
# --------------------------------------------------------------------------
def rounding_error(n=20000, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.uniform(1.0, 2.0, size=n)
    e16 = np.abs(x - np.float32(fp16(x))) / x
    e_bf = np.abs(x - bf16_round(x)) / x
    return float(np.max(e16)), float(np.max(e_bf))


def main():
    print("== 1. 可表示范围 ==")
    for k, v in range_table().items():
        print(f"  {k:>22}: {v:.6g}")
    print(f"  fp16 最小正规数 = 2^-14 = {2.0 ** -14:.6e};最小非正规数 = 2^-24 = {2.0 ** -24:.6e}")
    print(f"  fp32 最小正规数 = 2^-126 = {2.0 ** -126:.6e} —— 比 fp16 多约 30 个数量级的下界")

    print("\n== 2. 下溢扫描:2^k 转成 fp16 之后是多少 ==")
    sw = underflow_sweep()
    for k, v in sw.items():
        tag = "  ← 已归零" if v == 0.0 else ""
        print(f"  2^{k:3d} = {2.0 ** k:.3e}  → fp16 {v:.3e}{tag}")
    print("  阈值:小于半个非正规步长 2^-25 ≈ 2.98e-08 的值会被舍入成 0")

    print("\n== 3. loss scaling:冲零比例 / 还原误差 / 溢出 ==")
    print("        S     冲零比例   还原后最大相对误差   溢出")
    for S, flushed, rel, over in scaling_table():
        print(f"  {S:9.0f}   {flushed * 100:7.3f}%   {rel:16.6e}   {over}")
    print("  S=1 时 20.75% 的梯度被冲成 0(相对误差 = 1,即信息全丢);")
    print("  S ≥ 2^18 后冲零比例归 0,相对误差稳定在 4.86e-04 —— 正好是 fp16 的 2^-11 精度,")
    print("  说明精度在「全部落进正规数区间」时饱和,再放大 S 只白吃溢出余量")

    print("\n== 4. master weights(fp32 主副本)是否必要 ==")
    for steps in (1000, 20000):
        wh, wf, wh_alias = master_weight_demo(steps=steps)
        print(f"  {steps:5d} 步 × 1e-7(fp32 里应累计 {steps * 1e-7:.1e} 的下降):")
        print(f"        纯 fp16 权重 = {wh!r}(完全没动);fp32 master = {wf:.9f};"
              f"master 舍入回 fp16 = {wh_alias!r}")
    print(f"  fp16 在 1.0 附近的间距(ulp)= 2^-10 = {2.0 ** -10:.6e} —— 单步更新 1e-7 小 4 个数量级,")
    print("  所以权重更新必须在 fp32 主副本上累加;fp16 副本只是前向传播的输入")

    print("\n== 5. 动态缩放:溢出 → 减半并跳步;连续成功 → 翻倍 ==")
    log, skipped = dynamic_scaler()
    for step, scale, over in log:
        if over or step in (0, 10, 49, 50, 51, 119, 120, 121, 199):
            print(f"  step {step:3d}: scale = {scale:9.1f}   {'溢出 → 减半 + 跳过该步' if over else ''}")
    print(f"  共 {len(log)} 步,跳过 {skipped} 步;scale 从 65536 起,溢出后回退")

    print("\n== 6. 累加精度:fp16 累加 vs fp32 累加 ==")
    a16, a32, ref = accumulation_demo()
    print(f"  2000 个 [0.5,1.5) 的数求和:参考(fp64) = {ref:.6f}")
    print(f"  fp16 逐项累加 = {a16:.6f}(相对误差 {abs(a16 - ref) / ref:.3e})")
    print(f"  fp32 逐项累加 = {a32:.6f}(相对误差 {abs(a32 - ref) / ref:.3e})")
    print("  这就是 Tensor Core「FP16 输入 + FP32 累加」的原因:乘得快,但和必须在高精度里攒")

    print("\n== 7. 前向舍入误差:fp16 vs bf16 ==")
    e16, ebf = rounding_error()
    print(f"  fp16 最大相对误差 = {e16:.6e}(round-to-nearest,理论 2^-11 = {2.0 ** -11:.6e})")
    print(f"  bf16 最大相对误差 = {ebf:.6e}(本实现是**截断**而非 round-to-nearest,")
    print(f"                              故上界是 2^-7 = {2.0 ** -7:.6e};若按最近舍入则 ≈ 2^-8)")


if __name__ == "__main__":
    main()
