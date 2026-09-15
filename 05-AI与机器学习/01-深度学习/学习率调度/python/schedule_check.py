"""学习率调度与梯度裁剪自检:22 项断言。全绿才认为 demo 成立。

断言分三层:① 调度曲线的闭式性质(交点、端点、单调性、周期倍增);
② 优化器语义(解耦衰减是否等强度、裁剪是否保方向);③ 行为结论(warmup 是否救得回来)。
"""

import math

import numpy as np

import schedule as S

TOTAL = [0, 0]
FAILS = []


def check(name, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        TOTAL[1] += 1
        print(f"  [PASS] {name}  {detail}")
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}  {detail}")


print("== A. Transformer inverse-sqrt warmup(原文公式 3) ==")
peak = 512 ** -0.5 * 4000 ** -0.5
check(
    "峰值 = d^-0.5·warmup^-0.5",
    abs(S.transformer_lr(4000, 512, 4000) - peak) / peak < 1e-12,
    f"{S.transformer_lr(4000, 512, 4000):.10e}",
)
lhs = 4000 ** -0.5
rhs = 4000 * 4000 ** -1.5
check("两分支在 step=warmup 处严格相等", abs(lhs - rhs) < 1e-15, f"{lhs:.10f} vs {rhs:.10f}")
ups = [S.transformer_lr(s, 512, 4000) for s in range(1, 4001, 97)]
check("warmup 段严格单调上升", all(ups[i + 1] > ups[i] for i in range(len(ups) - 1)), f"{len(ups)} 个采样点")
downs = [S.transformer_lr(s, 512, 4000) for s in range(4000, 200001, 4000)]
check("衰减段严格单调下降", all(downs[i + 1] < downs[i] for i in range(len(downs) - 1)), f"{len(downs)} 个采样点")
check(
    "衰减段是 step^-0.5(比率 = 0.2)",
    abs(S.transformer_lr(100000, 512, 4000) / S.transformer_lr(4000, 512, 4000) - 0.2) < 1e-12,
    "0.200000",
)
check("step=0 被保护为 step=1", S.transformer_lr(0) == S.transformer_lr(1), f"{S.transformer_lr(1):.3e}")

print("\n== B. SGDR 余弦退火 + warm restart ==")
check("周期起点回到 η_max", all(abs(S.sgdr_lr(s, 10, 2, 0.1) - 0.1) < 1e-15 for s in (0, 10, 30, 70)), "0/10/30/70")
check("周期中点 = (η_max+η_min)/2", abs(S.sgdr_lr(5, 10, 2, 0.1) - 0.05) < 1e-15, "0.050000")
one = [S.sgdr_lr(s, 10, 2, 0.1) for s in range(0, 10)]
check("单个周期内单调下降", all(one[i + 1] < one[i] for i in range(9)), "10 个点")
restarts = [s for s in range(0, 150) if abs(S.sgdr_lr(s, 10, 2, 0.1) - 0.1) < 1e-12]
check("重启点恰为 0/10/30/70(周期 10/20/40/80)", restarts == [0, 10, 30, 70], str(restarts))
check("cosine_restart_lr 与 sgdr_lr 在首周期一致", abs(S.cosine_restart_lr(3, 10, 0.1) - S.sgdr_lr(3, 10, 2, 0.1)) < 1e-15, "相等")

print("\n== C. 线性 warmup + 余弦衰减(LLM 配方) ==")
check("warmup 峰值 = peak", abs(S.warmup_cosine_lr(1999, 3e-4, 2000, 10000) - 3e-4) < 1e-12, "3.000000e-04")
check("warmup 段单调上升", all(
    S.warmup_cosine_lr(s, 3e-4, 2000, 10000) < S.warmup_cosine_lr(s + 1, 3e-4, 2000, 10000)
    for s in range(0, 1999, 101)
), "采样检查")
check("衰减段单调下降", all(
    S.warmup_cosine_lr(s, 3e-4, 2000, 10000) > S.warmup_cosine_lr(s + 1, 3e-4, 2000, 10000)
    for s in range(2000, 9999, 101)
), "采样检查")
check(
    "终点 = peak·min_ratio",
    abs(S.warmup_cosine_lr(10000, 3e-4, 2000, 10000) - 3e-4 * 0.1) < 1e-18,
    "3.000000e-05",
)

print("\n== D. Adam+L2 vs AdamW ==")
t0 = {"small_grad": 1.0, "large_grad": 1.0}
gr = {"small_grad": 0.01, "large_grad": 1.0}
r_w = S.run_optimizer(t0, gr, lr=1e-3, wd=0.1, kind="adamw")
r_l2 = S.run_optimizer(t0, gr, lr=1e-3, wd=0.1, kind="adam_l2")
ratio_w = r_w["small_grad"] / r_w["large_grad"]
ratio_l2 = r_l2["small_grad"] / r_l2["large_grad"]
check("AdamW:两参数的衰减强度相同(比值 = 1)", abs(ratio_w - 1.0) < 1e-4, f"{ratio_w:.6f}")
check("Adam+L2:衰减强度被 1/sqrt(v) 差异化", abs(ratio_l2 - 1.0) > 0.05, f"{ratio_l2:.6f}")
check("Adam+L2 的差异远大于 AdamW", abs(ratio_l2 - 1.0) > 50 * abs(ratio_w - 1.0), f"{abs(ratio_l2 - 1.0):.4f} vs {abs(ratio_w - 1.0):.6f}")
check("AdamW 下两参数终值几乎逐位相同", abs(r_w["small_grad"] - r_w["large_grad"]) < 1e-5, f"{abs(r_w['small_grad'] - r_w['large_grad']):.2e}")
check("耦合版两参数终值差异显著", abs(r_l2["small_grad"] - r_l2["large_grad"]) > 0.01, f"{abs(r_l2['small_grad'] - r_l2['large_grad']):.4f}")

print("\n== E. 梯度裁剪 ==")
g = np.array([10.0, 0.1, -0.2, 0.05])
g_n, pre = S.clip_by_norm(g, 1.0)
g_v, _ = S.clip_by_value(g, 1.0)
check("按范数裁剪:方向严格不变", abs(S.cosine(g, g_n) - 1.0) < 1e-12, f"cos={S.cosine(g, g_n):.12f}")
check("按范数裁剪:长度恰好等于阈值", abs(float(np.linalg.norm(g_n)) - 1.0) < 1e-12, f"{np.linalg.norm(g_n):.12f}")
check("按元素裁剪:方向被改变", S.cosine(g, g_v) < 0.999, f"cos={S.cosine(g, g_v):.6f}")
ang = math.degrees(math.acos(min(1.0, S.cosine(g, g_v))))
check("按元素裁剪:夹角 > 5°", ang > 5.0, f"{ang:.2f}°")
g_small = np.array([0.3, 0.1])
g_keep, pre_keep = S.clip_by_norm(g_small, 1.0)
check("范数未超阈值时不改动梯度", np.allclose(g_keep, g_small) and abs(pre_keep - float(np.linalg.norm(g_small))) < 1e-15, "恒等")
check("返回的是裁剪前的范数(可用来打日志)", abs(pre - float(np.linalg.norm(g))) < 1e-12, f"{pre:.6f}")

print("\n== F. warmup 的必要性 ==")
_, lf_bad, g0 = S.tiny_net_train(peak_lr=2e-3, warmup_steps=0, steps=300, out_scale=30.0)
_, lf_good, _ = S.tiny_net_train(peak_lr=2e-3, warmup_steps=150, steps=300, out_scale=30.0)
check("初始梯度量级失衡(‖g_0‖ > 1e3)", g0 > 1e3, f"{g0:.3e}")
check("无 warmup + 失衡初始化 → 发散", not np.isfinite(lf_bad), f"终值 {lf_bad}")
check("加 150 步 warmup → 收敛", np.isfinite(lf_good) and lf_good < 5.0, f"终值 {lf_good:.4f}")
_, lf_a, _ = S.tiny_net_train(peak_lr=1e-3, warmup_steps=0, steps=300, out_scale=10.0)
_, lf_b, _ = S.tiny_net_train(peak_lr=1e-3, warmup_steps=150, steps=300, out_scale=10.0)
check("即使都不发散,warmup 也更低", lf_b < lf_a, f"{lf_a:.4f} → {lf_b:.4f}")

print(f"\n结果:{TOTAL[1]}/{TOTAL[0]} 通过" + (f",失败:{FAILS}" if FAILS else ""))
raise SystemExit(0 if not FAILS else 1)
