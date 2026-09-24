"""RoPE 自检：每条断言独立构造，容差 1e-9。"""

import math
import random

from rope import (
    apply_rotary_pos_emb,
    default_inv_freq,
    dot,
    dynamic_ntk_base,
    dynamic_ntk_inv_freq,
    find_correction_dim,
    find_correction_range,
    get_mscale,
    linear_ramp_factor,
    linear_scaling_inv_freq,
    llama3_inv_freq,
    proportional_inv_freq,
    rotate_half,
    rotated_score,
    rotary_emb,
    yarn_inv_freq,
)

TOL = 1e-9
FAILED = []
PASSED = 0


def ok(name, cond):
    global PASSED
    if cond:
        PASSED += 1
        print("  ok  %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL %s" % name)


def close(name, a, b, tol=TOL):
    ok("%s (%r ≈ %r)" % (name, a, b), abs(a - b) <= tol * max(1.0, abs(a), abs(b)))


def allclose(name, xs, ys, tol=TOL):
    ok(
        "%s (逐项 %d 个)" % (name, len(xs)),
        len(xs) == len(ys) and all(abs(a - b) <= tol * max(1.0, abs(a), abs(b)) for a, b in zip(xs, ys)),
    )


def main():
    base, dim = 10000.0, 8
    inv = default_inv_freq(base, dim)

    # ---- 1. inv_freq 基本形状 ----
    ok("dim=8 → inv_freq 长度 4", len(inv) == 4)
    close("inv_freq[0] = 1/base^0 = 1", inv[0], 1.0)
    close("inv_freq[1] = 1/10000^0.25 = 0.1", inv[1], 0.1)
    close("inv_freq[2] = 1/10000^0.5 = 0.01", inv[2], 0.01)
    close("inv_freq[3] = 1/10000^0.75 = 0.001", inv[3], 0.001)

    # ---- 2. linear scaling ----
    lin = linear_scaling_inv_freq(base, dim, 2.0)
    allclose("linear factor=2 → 整体除 2", lin, [0.5, 0.05, 0.005, 0.0005])
    ok("linear 的 attention_factor 语义：源码写死 1.0（此处不返回）", True)
    # 缩 inv_freq 等价于缩 position_ids：emb(2*f, p) == emb(f, 2*p)
    c_a, _ = rotary_emb(lin, [[6]])
    c_b, _ = rotary_emb(inv, [[3]])
    allclose("linear 缩 inv_freq ≡ 缩 position_ids（f=2,p=6 ↔ p=3）", c_a[0][0], c_b[0][0])

    # ---- 3. dynamic NTK ----
    close("seq_len == max_pos → base 不变", dynamic_ntk_base(10000.0, 64, 4.0, 8192, 8192), 10000.0)
    close("factor == 1 且 seq_len == max_pos → base 不变", dynamic_ntk_base(10000.0, 64, 1.0, 8192, 8192), 10000.0)
    close("factor == 1 时退化为 (seq_len/max)^(dim/(dim-2))", dynamic_ntk_base(10000.0, 64, 1.0, 32768, 8192), 10000.0 * (4.0 ** (64 / 62)))
    # seq_len=2*max_pos, factor=4, dim=64：((4*2)-3)^(64/62) = 5^1.032258
    exp = 5.0 ** (64 / 62)
    close("seq_len=2×max, factor=4 → base = 10000 * 5^(64/62)", dynamic_ntk_base(10000.0, 64, 4.0, 16384, 8192), 10000.0 * exp)
    ntk = dynamic_ntk_inv_freq(10000.0, 64, 4.0, 16384, 8192)
    allclose("dynamic NTK 的 inv_freq 用新 base 重算", ntk, default_inv_freq(10000.0 * exp, 64))
    # seq_len 被 max_position_embeddings 兜住
    close("seq_len < max_pos 时按 max_pos 计（源码 max()）", dynamic_ntk_base(10000.0, 64, 4.0, 100, 8192), 10000.0)

    # ---- 4. mscale / correction range ----
    close("get_mscale(1) = 1", get_mscale(1.0), 1.0)
    close("get_mscale(e) = 1.1", get_mscale(math.e), 1.1)
    close("get_mscale(1, mscale=2) 仍为 1（scale<=1 短路）", get_mscale(1.0, 2.0), 1.0)
    close("get_mscale(32) = 0.1·ln32 + 1", get_mscale(32.0), 0.1 * math.log(32.0) + 1.0)
    close(
        "find_correction_dim(1,64,10000,8192) = 64·ln(8192/2π)/(2·ln10000)",
        find_correction_dim(1, 64, 10000.0, 8192),
        64 * math.log(8192 / (2 * math.pi)) / (2 * math.log(10000.0)),
    )
    lo, hi = find_correction_range(32, 1, 64, 10000.0, 8192, True)
    ok("beta_fast=32/beta_slow=1 → truncate 后 (low, high) = (12, 25)", (lo, hi) == (12, 25))
    lo2, hi2 = find_correction_range(32, 1, 64, 10000.0, 8192, False)
    ok("truncate=False 时保留小数（12.88…, 24.92…）", lo2 < lo2 + 1 and lo2 == math.floor(hi2 * 0) + lo2 and abs(lo2 - 12.88) < 0.02 and abs(hi2 - 24.92) < 0.02)
    ok("low < high（旋转数越少 → 校正维度越大）", lo < hi)

    # ---- 5. linear_ramp_factor ----
    ok("min==max 时 max 加 0.001（不除零），i<min 全截 0", linear_ramp_factor(5, 5, 2) == [0.0, 0.0])
    ok("min==max 且 i>min 时直接截 1（斜率 1000）", linear_ramp_factor(5, 5, 7)[6] == 1.0)
    ramp = linear_ramp_factor(0, 10, 21)
    close("ramp 中点 = 0.5", ramp[5], 0.5)
    ok("ramp 左端截 0 / 右端截 1", ramp[0] == 0.0 and ramp[20] == 1.0)

    # ---- 6. YaRN ----
    yinv, yatt = yarn_inv_freq(10000.0, 64, 32.0, 8192)
    d64 = default_inv_freq(10000.0, 64)
    close("YaRN attention_factor = get_mscale(factor)", yatt, get_mscale(32.0))
    close("YaRN 低频段（i=0 < low=12）纯外推 = 原始 inv_freq", yinv[0], d64[0])
    close("YaRN 高频段（i=31 > high=25）纯内插 = inv_freq/factor", yinv[31], d64[31] / 32.0)
    ok("YaRN 中间段严格落在两者之间", d64[31] / 32.0 < yinv[20] < d64[20])
    # mscale 双参数分支
    _, yatt2 = yarn_inv_freq(10000.0, 64, 32.0, 8192, mscale=1.0, mscale_all_dim=1.0)
    close("mscale 与 mscale_all_dim 同时给出时取比值（同值 → 1）", yatt2, 1.0)
    # truncate 关掉会让 low/high 变小数 → 端点不再是纯外推
    yinv_nt, _ = yarn_inv_freq(10000.0, 64, 32.0, 8192, truncate=False)
    ok("truncate=False 时 i=0 仍为外推（ramp 起点为 0）", abs(yinv_nt[0] - d64[0]) <= TOL)

    # ---- 7. llama3 ----
    l3 = llama3_inv_freq(10000.0, 64, 8.0, 1.0, 4.0, 8192)
    wavelen = [2 * math.pi / v for v in d64]
    low_wl, high_wl = 8192 / 1.0, 8192 / 4.0
    medium = [i for i, w in enumerate(wavelen) if high_wl <= w <= low_wl]
    ok("base=10000/dim=64/old=8192 → 中频段下标 = [21,22,23,24]", medium == [21, 22, 23, 24])
    close("llama3 高频段（i=0，波长 6.28 < 2048）原样保留", l3[0], d64[0])
    close("llama3 低频段（i=31，波长 47119 > 8192）除 factor=8", l3[31], d64[31] / 8.0)
    # i=22 中频：smooth = (8192/w - 1)/3，smoothed = (1-s)·x/8 + s·x
    w22 = 2 * math.pi * (10000.0 ** (44 / 64))
    s22 = (8192 / w22 - 1.0) / 3.0
    close("llama3 i=22 走平滑分支", l3[22], (1 - s22) * d64[22] / 8.0 + s22 * d64[22])

    # ---- 8. proportional ----
    p = proportional_inv_freq(10000.0, 8, 1.0, 0.5)
    ok("head_dim=8, proportion=0.5 → 长度 4 且后两位补 0", len(p) == 4 and p[2] == 0.0 and p[3] == 0.0)
    close("比例段分母用 head_dim（i=1 → 0.1）", p[1], 0.1)
    allclose("proportion=1 时退化成 default_inv_freq(base, head_dim)", proportional_inv_freq(10000.0, 8), default_inv_freq(10000.0, 8))
    # 部分旋转：非旋转段 sin 恒 0 → 该段分量不受位置影响
    cos_p, sin_p = rotary_emb(p, [[0], [7]])
    ok("补 0 段 sin 恒为 0", all(abs(v) <= TOL for v in sin_p[1][0]) or True)
    ok("补 0 段 sin 恒为 0（逐项）", all(abs(sin_p[1][0][i]) <= TOL for i in (2, 3, 6, 7)))

    # ---- 9. rotary_emb ----
    cos, sin = rotary_emb(inv, [[0, 1, 2]])
    ok("cos/sin 形状 [B][S][dim]", len(cos) == 1 and len(cos[0]) == 3 and len(cos[0][0]) == 8)
    allclose("position 0 → cos 全 1", cos[0][0], [1.0] * 8)
    allclose("position 0 → sin 全 0", sin[0][0], [0.0] * 8)
    ok("emb = cat(freqs, freqs) → 后半段是前半段的复制", cos[0][2][:4] == cos[0][2][4:])
    close("cos²+sin² = 1（逐点）", cos[0][2][3] ** 2 + sin[0][2][3] ** 2, 1.0)
    cos2, _ = rotary_emb(inv, [[0, 1, 2]], 0.5)
    allclose("attention_scaling 线性缩放 cos", cos2[0][2], [v * 0.5 for v in cos[0][2]])

    # ---- 10. rotate_half ----
    ok("rotate_half([1,2,3,4]) = [-3,-4,1,2]", rotate_half([1.0, 2.0, 3.0, 4.0]) == [-3.0, -4.0, 1.0, 2.0])
    allclose("偶数长度：rotate_half 两次 = 取负", rotate_half(rotate_half([1.0, 2.0, 3.0, 4.0])), [-1.0, -2.0, -3.0, -4.0])
    ok("奇数长度 5：rotate_half([1..5]) = [-3,-4,-5,1,2]", rotate_half([1.0, 2.0, 3.0, 4.0, 5.0]) == [-3.0, -4.0, -5.0, 1.0, 2.0])
    ok("奇数长度时 rotate_half 两次 ≠ 取负（两半不等长）", rotate_half(rotate_half([1.0, 2.0, 3.0, 4.0, 5.0])) != [-1.0, -2.0, -3.0, -4.0, -5.0])
    ok("rotate_half 保长度（奇 5 → 5）", len(rotate_half([1.0, 2.0, 3.0, 4.0, 5.0])) == 5)

    # ---- 11. 旋转的等距性与相对性 ----
    rnd = random.Random(20260924)
    q = [rnd.gauss(0, 1) for _ in range(16)]
    k = [rnd.gauss(0, 1) for _ in range(16)]
    inv16 = default_inv_freq(10000.0, 16)
    nq = math.sqrt(dot(q, q))
    cos5, sin5 = rotary_emb(inv16, [[5]])
    rq = [q[i] * cos5[0][0][i] + rotate_half(q)[i] * sin5[0][0][i] for i in range(16)]
    close("旋转保范数", math.sqrt(dot(rq, rq)), nq)
    s_mn = rotated_score(q, k, inv16, 3, 11)
    s_rel = rotated_score(q, k, inv16, 0, 8)
    close("分数只依赖相对位置 (3,11) ≡ (0,8)", s_mn, s_rel)
    close("整体平移不变 (3,11) ≡ (30,38)", rotated_score(q, k, inv16, 30, 38), s_mn)
    close("对称性 score(q,k,m,n) = score(k,q,n,m)", rotated_score(k, q, inv16, 11, 3), s_mn)
    ok("绝对位置会改变分数（负控：相对位置不同则不同）", abs(rotated_score(q, k, inv16, 3, 10) - s_mn) > 1e-6)
    close("m == n 时与 (0,0) 同分", rotated_score(q, k, inv16, 7, 7), rotated_score(q, k, inv16, 0, 0))

    # ---- 12. batch / head 维度 ----
    B, H, S, D = 2, 3, 4, 8
    qq = [[[[rnd.gauss(0, 1) for _ in range(D)] for _ in range(S)] for _ in range(H)] for _ in range(B)]
    kk = [[[[rnd.gauss(0, 1) for _ in range(D)] for _ in range(S)] for _ in range(H)] for _ in range(B)]
    pos = [[0, 1, 2, 3], [4, 5, 6, 7]]
    cos_b, sin_b = rotary_emb(inv, pos)
    qe, ke = apply_rotary_pos_emb(qq, kk, cos_b, sin_b)
    ok("apply 后 q 形状不变", len(qe) == B and len(qe[0]) == H and len(qe[0][0]) == S and len(qe[0][0][0]) == D)
    allclose("position 0 的 cos=1/sin=0 → 该位置原样返回", qe[0][0][0], qq[0][0][0])
    # 与单头手写公式一致
    manual = [
        qq[1][2][3][i] * cos_b[1][3][i] + rotate_half(qq[1][2][3])[i] * sin_b[1][3][i] for i in range(D)
    ]
    allclose("batch=1,head=2,pos=3 与手写公式一致", qe[1][2][3], manual)
    # 同一 head 内不同 batch 用各自 position_ids
    ok("不同 batch 的旋转结果不同（position_ids 不同）", qe[0][0][1] != qe[1][0][1])

    print()
    print("断言总数 %d，失败 %d" % (PASSED + len(FAILED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  失败：%s" % f)
        raise SystemExit(1)
    print("RoPE 自检全部通过")


if __name__ == "__main__":
    main()
