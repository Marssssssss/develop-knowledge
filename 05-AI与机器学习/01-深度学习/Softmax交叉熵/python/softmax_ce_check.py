"""Softmax + Cross-Entropy 自检:23 项断言。全绿才认为 demo 成立。

断言的是「语义」而不是「某次运行的数值」:除数到底是 Σw 还是 N、
平滑后的梯度是否仍然零和、log_softmax 在极端 logit 下是否还活着。
"""

import numpy as np

import softmax_ce as S

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


rng = np.random.default_rng(7)

print("== A. 数值稳定 ==")
naive_bad, stable_bad = 0, 0
for _ in range(200):
    x = rng.normal(size=(3, 5)) * 10 ** rng.uniform(1, 4)
    t = rng.integers(0, 5, size=3)
    if not np.isfinite(S.cross_entropy(x, t, stable=False)):
        naive_bad += 1
    if not np.isfinite(S.cross_entropy(x, t, stable=True)):
        stable_bad += 1
check("朴素 Σexp 确实会溢出", naive_bad > 0, f"失效 {naive_bad}/200")
check("max-shift 版本 200/200 有限", stable_bad == 0, f"失效 {stable_bad}/200")

x = rng.normal(size=(6, 9)) * 300.0
t = rng.integers(0, 9, size=6)
got = S.cross_entropy(x, t)
# 用 longdouble(80-bit)独立算一遍作为参考实现;注意不能走 S.cross_entropy,
# 它内部会把输入 cast 回 float64,参考值就白算了。
xl = x.astype(np.longdouble)
m = xl.max(axis=-1, keepdims=True)
lse_l = m + np.log(np.exp(xl - m).sum(axis=-1, keepdims=True))
ref = float((-(xl - lse_l))[np.arange(6), t].mean())
check("与 longdouble 参考一致", abs(got - ref) / abs(ref) < 1e-12, f"rel={abs(got - ref) / abs(ref):.3e}")

print("\n== B. 闭式梯度 ∂ℓ/∂x = p − y ==")
x = rng.normal(size=(5, 7))
t = rng.integers(0, 7, size=5)
w = rng.uniform(0.2, 3.0, size=7)
g, p, logp = S.ce_grad_logits(x, t)
gn = S.numerical_grad(lambda: float(S.cross_entropy(x, t, reduction="sum")), x)
check("无 weight:梯度与差分一致", S.rel_err(g, gn) < 1e-8, f"{S.rel_err(g, gn):.3e}")
gw, _, _ = S.ce_grad_logits(x, t, weight=w)
gwn = S.numerical_grad(lambda: float(S.cross_entropy(x, t, weight=w, reduction="sum")), x)
check("带 weight:梯度与差分一致", S.rel_err(gw, gwn) < 1e-8, f"{S.rel_err(gw, gwn):.3e}")
check("梯度有界 |p−y| ≤ 1", float(np.abs(g).max()) <= 1.0 + 1e-12, f"max={np.abs(g).max():.6f}")
check("p 是合法概率分布", float(np.abs(p.sum(axis=-1) - 1).max()) < 1e-12, f"{np.abs(p.sum(axis=-1) - 1).max():.2e}")
ymat = np.zeros((5, 7))
ymat[np.arange(5), t] = 1.0
check("带 weight 时梯度 = w_y·(p−y)", S.rel_err(gw, w[t][:, None] * (p - ymat)) < 1e-12, "逐元素相等")

print("\n== C. reduction='mean' 带 weight 的除数 ==")
x2 = np.array([[2.0, 0.0], [0.0, 2.0]])
t2 = np.array([0, 1])
w2 = np.array([1.0, 3.0])
per = S.cross_entropy(x2, t2, weight=w2, reduction="none")
mean_v = S.cross_entropy(x2, t2, weight=w2, reduction="mean")
check("除数 = Σ w_{y_n}(不是 N)", abs(mean_v - per.sum() / w2[t2].sum()) < 1e-12, f"{mean_v:.6f}")
check("用 N 做除数会差一倍", abs(mean_v - per.sum() / len(t2)) > 0.1, f"差 {abs(mean_v - per.sum() / len(t2)):.6f}")
check("reduction='sum' = 逐样本和", abs(S.cross_entropy(x2, t2, weight=w2, reduction="sum") - per.sum()) < 1e-12, "相等")
check("reduction='none' 返回形状 (N,)", per.shape == (2,), str(per.shape))

print("\n== D. log_softmax vs log(softmax) ==")
x3 = np.array([[0.0, -800.0]])
_, lp = S.cross_entropy(x3, np.array([1]), return_probs=True)
with np.errstate(divide="ignore"):
    sep = float(np.log(np.exp(x3 - S.logsumexp_stable(x3)))[0, 1])
check("log_softmax 在 logit 差 800 时仍有限", np.isfinite(lp[0, 1]) and abs(lp[0, 1] + 800.0) < 1e-9, f"{lp[0, 1]:.6f}")
check("log(softmax) 同条件下下溢为 −inf", np.isneginf(sep), str(sep))
check("CE 因此也有限", np.isfinite(S.cross_entropy(x3, np.array([1]))), f"{S.cross_entropy(x3, np.array([1])):.3f}")

print("\n== E. label_smoothing ==")
x4 = np.array([[2.0, 1.0, 0.0]])
t4 = np.array([0])
base = float(S.cross_entropy(x4, t4))
_, lp4 = S.cross_entropy(x4, t4, return_probs=True)
eps = 0.1
sm = float(S.cross_entropy(x4, t4, label_smoothing=eps))
uni = float(-lp4.mean())
check("平滑 loss = (1−ε)·CE + ε·(均匀目标 CE)", abs(sm - ((1 - eps) * base + eps * uni)) < 1e-12, f"{sm:.6f}")
check("平滑使 loss 变大", sm > base, f"{base:.6f} → {sm:.6f}")
g0, _, _ = S.ce_grad_logits(x4, t4)
g1, _, _ = S.ce_grad_logits(x4, t4, label_smoothing=eps)
check("平滑后梯度仍零和(Σ_c(p−y)=0)", abs(float(g0.sum())) < 1e-12 and abs(float(g1.sum())) < 1e-12, f"{float(g0.sum()):.2e} / {float(g1.sum()):.2e}")
y_sm = (1 - eps) * np.array([[1.0, 0.0, 0.0]]) + eps / 3
p1 = np.exp(lp4)
check("平滑梯度 = p − y_smooth(显式构造)", S.rel_err(g1, p1 - y_sm) < 1e-12, "逐元素相等")
check("平滑后梯度不再指向 one-hot(非零下限)", float(np.abs(g1).min()) > 1e-3, f"min|g|={np.abs(g1).min():.3e}")
check("ε>0 时 loss 有下界 ε·log C", sm >= eps * np.log(3) - 1e-12, f"{sm:.6f} ≥ {eps * np.log(3):.6f}")

print("\n== F. 平移不变 / 缩放饱和 ==")
x5 = rng.normal(size=(2, 4))
check("全体平移 +1000 不改变 loss", abs(S.cross_entropy(x5, np.array([1, 2])) - S.cross_entropy(x5 + 1000.0, np.array([1, 2]))) < 1e-12, "Δ<1e-12")
t_ok = x5.argmax(axis=-1)
t_wrong = (x5.argmax(axis=-1) + 1) % 4
g_ok, _, _ = S.ce_grad_logits(x5 * 100.0, t_ok)
g_wr, _, _ = S.ce_grad_logits(x5 * 100.0, t_wrong)
check("预测正确 + 大 logits → 梯度消失", float(np.linalg.norm(g_ok)) < 1e-6, f"{np.linalg.norm(g_ok):.3e}")
check("预测错误 + 大 logits → 饱和到 √(2N)", abs(float(np.linalg.norm(g_wr)) - np.sqrt(2 * 2)) < 1e-6, f"{np.linalg.norm(g_wr):.6f}")

print("\n== G. 概率型 target 不做校验 ==")
x6 = np.array([[1.0, 0.5, -1.0]])
tg = np.array([[0.6, 0.3, 0.1]])
tb = tg * 10.0
check("target 放大 10 倍 → loss 恰好放大 10 倍(无校验)", abs(S.cross_entropy(x6, tb) / S.cross_entropy(x6, tg) - 10.0) < 1e-9, f"{S.cross_entropy(x6, tb) / S.cross_entropy(x6, tg):.9f}")
p6 = np.exp(x6 - S.logsumexp_stable(x6))
check("非法 target 使 Σ(p−y) ≠ 0", abs(float((p6 - tb).sum())) > 1.0, f"{float((p6 - tb).sum()):.3f}")
check("合法 target 下 Σ(p−y) = 0", abs(float((p6 - tg).sum())) < 1e-15, f"{float((p6 - tg).sum()):.2e}")

print(f"\n结果:{TOTAL[1]}/{TOTAL[0]} 通过" + (f",失败:{FAILS}" if FAILS else ""))
raise SystemExit(0 if not FAILS else 1)
