#!/usr/bin/env python3
"""多重比较校正:一次跑几百个 benchmark 时,为什么"5% 显著"是**预期**而不是意外。

benchstat 官方文档把问题说得非常直白(Tips 节原文):

> By default, benchstat uses an ɑ threshold of 0.05, which means it is *expected* to show
> a difference 5% of the time even if there is no difference. Hence, if you rerun benchmarks
> looking for a change, benchstat will probably eventually say there is a change, even if
> there isn't, which creates a statistical bias.
>
> As an extension of this, if you compare a large number of benchmarks, you should expect
> that about 5% of them will report a statistically significant change even if there is no
> difference between the before and after.

关键在于 **benchstat 自己不做任何校正**(文档里没有任何 Bonferroni/BH 字样),
所以"family 是什么"完全由使用者定义。本 demo 把这件事量化。

实现的方法清单与性质以 statsmodels ``multipletests`` 文档为准:

> All procedures that are included, control FWER or FDR in the independent case, and most
> are robust in the positively correlated case.

临界常数以原始论文为准:

- **Benjamini & Hochberg (1995)**,JRSS-B 57:289–300:step-up 且 ``α_i = (i/m)α``,
  独立下 ``FDR ≤ (m₀/m)α``。
- **Benjamini & Yekutieli (2001)**,Ann. Statist. 29:1165–1188:同样过程在 PRDS 下仍成立;
  任意依赖下需把临界常数改为 ``α_i = iα / (m·Σ_{j=1}^{m} 1/j)``。

无第三方依赖;固定种子保证自检可复现。
"""

from __future__ import annotations

import math
import random
from typing import List, Sequence, Tuple

ALPHA = 0.05


def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def harmonic(m: int) -> float:
    """``H_m = Σ_{i=1}^{m} 1/i``,Benjamini-Yekutieli 的惩罚因子。"""
    return sum(1.0 / i for i in range(1, m + 1))


def _order(pvals: Sequence[float]) -> List[int]:
    return sorted(range(len(pvals)), key=lambda i: pvals[i])


def bonferroni_adjusted(pvals: Sequence[float]) -> List[float]:
    m = len(pvals)
    return [min(1.0, p * m) for p in pvals]


def sidak_alpha(alpha: float, m: int) -> float:
    """``1-(1-α_c)^m = α`` 解出的单次 α_c。"""
    return 1.0 - (1.0 - alpha) ** (1.0 / m)


def sidak_adjusted(pvals: Sequence[float]) -> List[float]:
    m = len(pvals)
    return [min(1.0, 1.0 - (1.0 - p) ** m) for p in pvals]


def holm_adjusted(pvals: Sequence[float]) -> List[float]:
    """step-down:``α/(m-i+1)``,停在第一处失败;校正 p 取后缀最大值保证单调。"""
    m = len(pvals)
    out = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(_order(pvals)):
        val = (m - rank) * pvals[idx]
        running = max(running, val)
        out[idx] = min(1.0, running)
    return out


def hochberg_adjusted(pvals: Sequence[float]) -> List[float]:
    """step-up:从最大的 p 往回走,取前缀最小值。"""
    m = len(pvals)
    out = [0.0] * m
    running = 1.0
    for rank, idx in reversed(list(enumerate(_order(pvals)))):
        val = (m - rank) * pvals[idx]
        running = min(running, val)
        out[idx] = min(1.0, running)
    return out


def bh_adjusted(pvals: Sequence[float], q: float = ALPHA) -> List[float]:
    """Benjamini-Hochberg:找最大 k 使 ``p_(k) ≤ (k/m)q``,拒绝 H_(1..k)。

    校正 p 值按 ``min_{j≥i} (m/j)·p_(j)`` 计算。
    """
    m = len(pvals)
    order = _order(pvals)
    out = [0.0] * m
    running = 1.0
    for rank in range(m - 1, -1, -1):
        idx = order[rank]
        val = m / (rank + 1) * pvals[idx]
        running = min(running, val)
        out[idx] = min(1.0, running)
    _ = q
    return out


def by_adjusted(pvals: Sequence[float], q: float = ALPHA) -> List[float]:
    """Benjamini-Yekutieli:把 q 换成 ``q / H_m``(等价地把校正 p 乘 H_m)。"""
    hm = harmonic(len(pvals))
    return [min(1.0, p * hm) for p in bh_adjusted(pvals, q)]


def reject(adjusted: Sequence[float], alpha: float = ALPHA) -> List[bool]:
    return [p <= alpha for p in adjusted]


# ---------------------------------------------------------------- 模拟

def simulate_family(m: int, m_true: int, n_per_group: int, effect: float,
                    rng: random.Random) -> List[float]:
    """生成 m 个 p 值:前 m_true 个来自真效应(非中心参数 d√(n/2)),其余来自零效应。"""
    ncp = effect * math.sqrt(n_per_group / 2.0)
    ps = []
    for i in range(m):
        z = rng.gauss(ncp if i < m_true else 0.0, 1.0)
        ps.append(min(1.0, 2.0 * norm_sf(abs(z))))
    return ps


def evaluate(method, m: int, m_true: int, n_per_group: int, effect: float,
             trials: int, seed: int) -> Tuple[float, float, float]:
    """返回 (FWER, FDR, 平均真阳性数 / m_true)。"""
    rng = random.Random(seed)
    fwer_hits = 0
    fdp_sum = 0.0
    tp_sum = 0
    for _ in range(trials):
        ps = simulate_family(m, m_true, n_per_group, effect, rng)
        rej = reject(method(ps))
        v = sum(1 for i in range(m_true, m) if rej[i])
        r = sum(1 for b in rej if b)
        tp = sum(1 for i in range(m_true) if rej[i])
        if v > 0:
            fwer_hits += 1
        fdp_sum += v / max(r, 1)
        tp_sum += tp
    return fwer_hits / trials, fdp_sum / trials, tp_sum / (trials * max(m_true, 1))


# --------------------------------------------------------------------------- 自检

def _self_test() -> None:
    m = 50

    # 1) BY 的惩罚因子:第 50 个调和数
    hm = harmonic(m)
    assert abs(hm - 4.499205338329425) < 1e-12, hm
    assert abs(hm - (math.log(m) + 0.5772156649)) < 0.02   # H_m ≈ ln m + γ
    print(f"[1] H_50 = {hm:.9f}(≈ ln50 + γ = {math.log(m)+0.5772156649:.4f})")

    # 2) Šidák 比 Bonferroni 宽松一点点,但都远小于 α
    a_b = ALPHA / m
    a_s = sidak_alpha(ALPHA, m)
    assert a_b < a_s < ALPHA
    assert abs(a_s - 0.0010248) < 1e-6
    print(f"[2] m=50, α=0.05:Bonferroni α_c={a_b:.6f} < Šidák α_c={a_s:.6f}")

    # 3) Holm 的拒绝集恒为 Bonferroni 的超集(随机 300 组向量逐一验证)
    rng = random.Random(1)
    for _ in range(300):
        ps = [rng.random() for _ in range(12)]
        rb = reject(bonferroni_adjusted(ps))
        rh = reject(holm_adjusted(ps))
        assert all(h or not b for b, h in zip(rb, rh)), ps
    print("[3] Holm ⊇ Bonferroni:300 组随机 p 向量全部满足(故 Bonferroni 无理由被优先使用)")

    # 4) 全局零效应:FWER 是"family 里有至少一个假阳性"的概率
    f_none, d_none, _ = evaluate(lambda ps: ps, m, 0, 16, 0.0, 3000, 20260919)
    f_bonf, _, _ = evaluate(bonferroni_adjusted, m, 0, 16, 0.0, 3000, 20260919)
    f_holm, _, _ = evaluate(holm_adjusted, m, 0, 16, 0.0, 3000, 20260919)
    f_bh, d_bh, _ = evaluate(bh_adjusted, m, 0, 16, 0.0, 3000, 20260919)
    expect = 1 - 0.95 ** m
    assert abs(f_none - expect) < 0.05, (f_none, expect)
    assert f_bonf <= 0.06 and f_holm <= 0.06
    # 全局零效应下 V ≡ R(所有被拒的都是假阳性),故 FDP = 1{R>0},于是 FDR **等于** FWER。
    # 这正是"m₀=m 时 BH 并不比 Bonferroni 松"的原因,也是 FDR 只有在有真效应时才显出优势的根源。
    assert abs(d_none - f_none) < 0.02, (d_none, f_none)
    assert abs(d_bh - f_bh) < 0.02, (d_bh, f_bh)
    print(f"[4] 全零效应 m=50:未校正 FWER={f_none:.3f}(理论 {expect:.3f},"
          f"且 FDR={d_none:.3f} 与之相等 —— V≡R 时二者同一);"
          f"Bonferroni={f_bonf:.3f};Holm={f_holm:.3f};BH={f_bh:.3f}")

    # 5) benchstat 那句话:期望有 5% 的 benchmark 报显著
    rng = random.Random(77)
    counts = []
    for _ in range(3000):
        ps = simulate_family(m, 0, 16, 0.0, rng)
        counts.append(sum(1 for p in ps if p < ALPHA))
    mean_cnt = sum(counts) / len(counts)
    assert abs(mean_cnt - m * ALPHA) < 0.3, mean_cnt
    print(f"[5] benchstat 原话核对:50 个无差异 benchmark 平均报出 {mean_cnt:.2f} 个"
          f"'显著'(期望 2.5)")

    # 6) FWER 与 FDR 的语义差:只有在"有真效应"时才分得开
    m_true, eff, n = 10, 1.0, 16
    f_none2, d_none2, pw_none = evaluate(lambda ps: ps, m, m_true, n, eff, 3000, 555)
    f_bh2, d_bh2, pw_bh = evaluate(bh_adjusted, m, m_true, n, eff, 3000, 555)
    f_bonf2, d_bonf2, pw_bonf = evaluate(bonferroni_adjusted, m, m_true, n, eff, 3000, 555)
    f_by2, d_by2, pw_by = evaluate(by_adjusted, m, m_true, n, eff, 3000, 555)
    assert d_bh2 <= ALPHA + 0.02, d_bh2                 # BH 守住了 FDR
    assert f_bh2 > f_bonf2 + 0.05, (f_bh2, f_bonf2)     # 但它的 FWER 明显更高
    assert pw_bh > pw_bonf, (pw_bh, pw_bonf)            # 换来的是更高的功效
    assert d_by2 <= d_bh2 + 1e-12 and pw_by <= pw_bh + 1e-12
    print(f"[6] m=50 / 10 个真效应:BH 的 FDR={d_bh2:.3f} 但 FWER={f_bh2:.3f};"
          f"Bonferroni 的 FWER={f_bonf2:.3f}、功效 {pw_bonf:.3f} vs BH {pw_bh:.3f}")

    # 7) BH 的阈值随"真效应占比"自适应;Bonferroni 不会
    ps_dense = simulate_family(m, m_true, n, eff, random.Random(3))
    ps_sparse = simulate_family(m, 1, n, eff, random.Random(3))
    def max_p_rejected(ps, adj):
        hit = [p for p, r in zip(ps, reject(adj)) if r]
        return max(hit) if hit else 0.0

    assert sum(reject(bh_adjusted(ps_dense))) > sum(reject(bh_adjusted(ps_sparse)))
    # Bonferroni 的判据恒为 p ≤ α/m,与数据无关;BH 的有效阈值随 k 上浮
    for ps in (ps_dense, ps_sparse):
        assert max_p_rejected(ps, bonferroni_adjusted(ps)) <= ALPHA / m + 1e-15
    bh_cut = max_p_rejected(ps_dense, bh_adjusted(ps_dense))
    assert bh_cut > ALPHA / m
    print(f"[7] 10 个真效应时 BH 的最大被拒 p = {bh_cut:.5f},远宽于 Bonferroni 的 "
          f"α/m = {ALPHA/m:.5f}(BH 的阈值随真效应数量自适应)")

    # 8) BY = BH / H_m:任意依赖下仍成立,代价约 ln m
    ps = simulate_family(m, m_true, n, eff, random.Random(9))
    adj_bh, adj_by = bh_adjusted(ps), by_adjusted(ps)
    for a, b in zip(adj_bh, adj_by):
        assert abs(b - min(1.0, a * hm)) < 1e-12
    print(f"[8] BY 校正 p = BH 校正 p × H_50 = ×{hm:.3f}(m=50 时约 4.5 倍代价)")

    # 9) 未校正的"平均假阳性数"随 m 线性增长 —— 这就是大仓库的常态
    for mm in (10, 50, 200):
        rng2 = random.Random(31)
        tot = sum(sum(1 for p in simulate_family(mm, 0, 16, 0.0, rng2) if p < ALPHA)
                  for _ in range(500))
        avg = tot / 500
        assert abs(avg - mm * ALPHA) < 0.6, (mm, avg)
    print("[9] 未校正时假阳性数 ≈ 0.05·m:m=10/50/200 分别约 0.5 / 2.5 / 10 个")


if __name__ == "__main__":
    _self_test()
    print("\nmultitest: 全部自检通过")
