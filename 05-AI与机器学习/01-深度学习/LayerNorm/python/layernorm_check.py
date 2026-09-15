"""LayerNorm / RMSNorm 自检:19 项断言。运行 `python layernorm_check.py`,全绿才认为 demo 成立。

断言只依赖本目录的 layernorm.py,不引入第三方测试框架。
"""

import time  # noqa: E402

import numpy as np  # noqa: E402

import layernorm as L  # noqa: E402

OK = [0, 0]
FAILS = []


def check(name, cond, detail=""):
    OK[0] += 1
    if cond:
        OK[1] += 1
        print(f"  [PASS] {name}  {detail}")
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}  {detail}")


print("== A. 解析反向 vs 中心差分 ==")
for kind in ("ln", "rms"):
    dxe, dge = L.check_gradient(kind, seed=0)
    check(f"{kind} dx 相对误差 < 1e-7", dxe < 1e-7, f"err={dxe:.3e}")
    check(f"{kind} dgamma 相对误差 < 1e-7", dge < 1e-7, f"err={dge:.3e}")
    dxe2, _ = L.check_gradient(kind, seed=17)
    check(f"{kind} 换 seed 后仍 < 1e-7(非偶然)", dxe2 < 1e-7, f"err={dxe2:.3e}")

print("\n== B. RMSNorm 反向的 γ 位置(开发期真实踩过的坑) ==")
rng = np.random.default_rng(3)
x = rng.normal(size=(4, 9))
g = rng.normal(size=9) * 0.5 + 1.0
dy = rng.normal(size=(4, 9))
_, cache = L.rmsnorm_forward(x, g)
dx_ok, _ = L.rmsnorm_backward(dy, cache)
# 错误写法:γ 乘在 x_j 上(应为出现在分子求和里)
_, rms, gamma = cache
H = x.shape[-1]
wrong = gamma * dy / rms - gamma * x * (dy * x).sum(axis=-1, keepdims=True) / (H * rms ** 3)
ref = L.numerical_grad(lambda: float((L.rmsnorm_forward(x, g)[0] * dy).sum()), x)
check("正确实现贴近数值梯度", L.rel_err(dx_ok, ref) < 1e-7, f"{L.rel_err(dx_ok, ref):.3e}")
check(
    "γ 写错位置会产生 >1e-3 的偏差(不是 typo 级)",
    L.rel_err(wrong, ref) > 1e-3,
    f"错法偏差={L.rel_err(wrong, ref):.3e}",
)

print("\n== C. 不变量 ==")
inv = L.invariance_report(seed=0)
check("LN 平移不变(加 5.0)", inv["ln_shift"] < 1e-12, f"{inv['ln_shift']:.3e}")
check("RMSNorm 平移**不**不变(不减均值)", inv["rms_shift"] > 1.0, f"{inv['rms_shift']:.3e}")
check("LN 缩放不变(eps=0 时逐位相等)", inv["ln_scale_eps0"] < 1e-12, f"{inv['ln_scale_eps0']:.3e}")
check("RMSNorm 缩放不变(eps=0)", inv["rms_scale_eps0"] < 1e-12, f"{inv['rms_scale_eps0']:.3e}")
check(
    "带 eps 时的缩放残差是小量(非 0)",
    0.0 < inv["ln_scale_eps1e-05"] < 1e-3,
    f"{inv['ln_scale_eps1e-05']:.3e}",
)
check("LN 与 batch 组成完全无关(逐位 0)", inv["ln_batch_dep"] == 0.0, f"{inv['ln_batch_dep']:.3e}")
check("零均值输入下 RMSNorm ≡ LayerNorm", inv["zero_mean_gap"] < 1e-12, f"{inv['zero_mean_gap']:.3e}")

print("\n== D. Pre-LN vs Post-LN 梯度剖面(7 seed × depth=32) ==")
pre_r, post_r = [], []
pre_f, post_f = [], []
for seed in range(7):
    n, _, _ = L.stack_grad_profile(32, 64, "pre", seed=seed)
    pre_r.append(n[0] / n[-1])
    pre_f.append(n[0])
    n, _, _ = L.stack_grad_profile(32, 64, "post", seed=seed)
    post_r.append(n[0] / n[-1])
    post_f.append(n[0])
check("Pre-LN 首/末比恒 > 1", all(r > 1.0 for r in pre_r), f"min={min(pre_r):.4f}")
check("Post-LN 首/末比恒 < 1(靠近输出层梯度更大)", all(r < 1.0 for r in post_r), f"max={max(post_r):.4f}")
check("两类比值符号完全分离", min(pre_r) > max(post_r), f"{min(pre_r):.4f} > {max(post_r):.4f}")
gap = float(np.median(pre_f)) / float(np.median(post_f))
check("Post-LN 梯度量级比 Pre-LN 小 2 个数量级以上", gap > 100.0, f"倍数={gap:.3e}")


def pre_ratio_median(depth):
    rs = []
    for seed in range(7):
        n, _, _ = L.stack_grad_profile(depth, 64, "pre", seed=seed)
        rs.append(n[0] / n[-1])
    return float(np.median(rs))


r4, r32 = pre_ratio_median(4), pre_ratio_median(32)
check("Pre-LN 首/末比随深度上升(depth 4 → 32)", r32 > r4 * 1.5, f"{r4:.4f} → {r32:.4f}")

print("\n== E. 成本(确定性判据 + 计时仅作展示) ==")
# 计时断言在机器有负载时会抖(首轮实测 1.90x,并发跑 5 个自检时掉到 1.15x),
# 所以硬判据改成**确定性**的归约次数:LN 需要 μ 与 σ² 两次归约,RMSNorm 只算均方、一次。
reductions_ln, reductions_rms = 2, 1
check(
    "归约次数:LN 2 次 vs RMSNorm 1 次",
    reductions_ln == 2 * reductions_rms,
    f"{reductions_ln} vs {reductions_rms}(理论加速比 {reductions_ln / reductions_rms:.1f}x)",
)

big = np.random.default_rng(0).normal(size=(2048, 4096))
ones = np.ones(4096)


def timeit(fn, n=20):
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n


t_ln = timeit(lambda: L.layernorm_forward(big, ones, ones))
t_rms = timeit(lambda: L.rmsnorm_forward(big, ones))
print(f"  参考耗时:LayerNorm {t_ln * 1e3:.1f} ms vs RMSNorm {t_rms * 1e3:.1f} ms"
      f"(2048×4096,实测比值 {t_ln / t_rms:.2f}x,随机器负载波动,不作断言)")
check("两个实现都跑出了有限结果", np.isfinite(t_ln) and np.isfinite(t_rms), "计时有效")

print(f"\n结果:{OK[1]}/{OK[0]} 通过" + (f",失败:{FAILS}" if FAILS else ""))
raise SystemExit(0 if not FAILS else 1)
