"""FlashAttention 自检：与朴素注意力逐位等价 + 因果掩码支持集 + 数值稳定性。"""

import math
import random

from flash import (
    NEG_INF,
    flash_attention,
    naive_attention,
    naive_lse,
    seqlen_q_rounded,
    softmax_scale_for,
    validate,
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


def close(name, a, b, tol=1e-9):
    ok("%s (%r ≈ %r)" % (name, a, b), abs(a - b) <= tol * max(1.0, abs(a), abs(b)))


def allclose(name, xs, ys, tol=1e-9):
    ok("%s (逐项 %d 个)" % (name, len(xs)),
       len(xs) == len(ys) and all(abs(a - b) <= tol * max(1.0, abs(a), abs(b)) for a, b in zip(xs, ys)))


def flat(xs):
    out = []
    for e in xs:
        if isinstance(e, list):
            out.extend(flat(e))
        else:
            out.append(e)
    return out


def rand_mat(rows, cols, rnd, scale=1.0):
    return [[rnd.gauss(0, 1) * scale for _ in range(cols)] for _ in range(rows)]


def main():
    rnd = random.Random(20260924)

    # ---- 1. 默认 softmax_scale 与对齐 ----
    close("softmax_scale 缺省 = 1/sqrt(d)", softmax_scale_for(64), 1.0 / math.sqrt(64))
    close("显式传入时以传入值为准", softmax_scale_for(64, 0.25), 0.25)
    ok("seqlen_q_rounded = ceil(S/128)·128：S=1 → 128", seqlen_q_rounded(1) == 128)
    ok("seqlen_q_rounded：S=128 → 128（不进位）", seqlen_q_rounded(128) == 128)
    ok("seqlen_q_rounded：S=129 → 256", seqlen_q_rounded(129) == 256)
    try:
        validate([[0.0] * 129], [[0.0] * 129], [[0.0] * 129], 129)
        ok("d > 128 应当报错", False)
    except AssertionError as e:
        ok("d > 128 抛 AssertionError：%s" % e, "128" in str(e))

    # ---- 2. 与朴素注意力等价（非因果）----
    for sq, sk, d in ((4, 4, 8), (7, 5, 16), (13, 9, 4)):
        q = rand_mat(sq, d, rnd)
        k = rand_mat(sk, d, rnd)
        v = rand_mat(sk, d, rnd)
        o, lse, sc = flash_attention(q, k, v)
        ref = naive_attention(q, k, v)
        allclose("非因果 sq=%d sk=%d d=%d 与朴素实现一致" % (sq, sk, d), flat(o), flat(ref))
        allclose("返回的 lse 与朴素 log-sum-exp 一致", lse, naive_lse(q, k))
        close("返回的 scale = 1/sqrt(d)", sc, 1.0 / math.sqrt(d))

    # ---- 3. 因果 ----
    q = rand_mat(10, 8, rnd)
    k = rand_mat(10, 8, rnd)
    v = rand_mat(10, 8, rnd)
    oc, lsec, _ = flash_attention(q, k, v, causal=True)
    allclose("因果与朴素因果一致", flat(oc), flat(naive_attention(q, k, v, causal=True)))
    allclose("因果 lse 一致", lsec, naive_lse(q, k, causal=True))
    ok("因果与全注意力结果不同（负控）", flat(oc) != flat(naive_attention(q, k, v)))
    # 支持集探针：改 k[j>i] 不影响第 i 行
    k2 = [list(row) for row in k]
    for j in range(6, 10):
        for t in range(8):
            k2[j][t] += 5.0
    oc2, _, _ = flash_attention(q, k2, v, causal=True)
    allclose("因果：改 k[6..9] 不影响第 0~5 行", flat(oc2[:6]), flat(oc[:6]))
    ok("因果：改 k[6..9] 会改变第 6~9 行（负控）", flat(oc2[6:]) != flat(oc[6:]))
    on2 = naive_attention(q, k2, v)
    ok("非因果下同一个改动会影响所有行", flat(on2[:6]) != flat(naive_attention(q, k, v)[:6]))

    # ---- 4. 分块大小不变性 ----
    q = rand_mat(24, 8, rnd)
    k = rand_mat(20, 8, rnd)
    v = rand_mat(20, 8, rnd)
    ref = naive_attention(q, k, v)
    for bm, bn in ((128, 128), (8, 8), (3, 7), (1, 20), (24, 1)):
        o, _, _ = flash_attention(q, k, v, block_m=bm, block_n=bn)
        allclose("分块 %dx%d 与非分块等价" % (bm, bn), flat(o), flat(ref))
    refc = naive_attention(q, k, v, causal=True)
    for bm, bn in ((128, 128), (8, 8), (5, 5)):
        o, _, _ = flash_attention(q, k, v, causal=True, block_m=bm, block_n=bn)
        allclose("因果分块 %dx%d 等价" % (bm, bn), flat(o), flat(refc))

    # ---- 5. lse 的语义 ----
    q = rand_mat(6, 8, rnd)
    k = rand_mat(6, 8, rnd)
    v = rand_mat(6, 8, rnd)
    _o, lse, sc = flash_attention(q, k, v)
    for i in range(6):
        scores = [sum(a * b for a, b in zip(q[i], k[j])) * sc for j in range(6)]
        mx = max(scores)
        tot = 0.0
        for s in scores:
            tot += math.exp(s - mx)
        close("第 %d 行 exp(lse) = Σ exp(score)" % i, math.exp(lse[i]), math.exp(mx) * tot)
    ok("lse ≥ 该行最大 score（log-sum-exp 的下界性质）",
       all(lse[i] >= max(sum(a * b for a, b in zip(q[i], k[j])) * sc for j in range(6)) - 1e-12
           for i in range(6)))

    # ---- 6. m_ij 的基准：lse_i vs m_i ----
    o_a, _, _ = flash_attention(q, k, v, use_lse_for_max=True)
    o_b, _, _ = flash_attention(q, k, v, use_lse_for_max=False)
    allclose("用 lse_i 当基准与用 m_i 当基准结果相同", flat(o_a), flat(o_b))
    o_c, _, _ = flash_attention(q, k, v, causal=True, use_lse_for_max=True)
    o_d, _, _ = flash_attention(q, k, v, causal=True, use_lse_for_max=False)
    allclose("因果下两者也相同", flat(o_c), flat(o_d))

    # ---- 7. 数值稳定性：大 logits ----
    qb = [[50.0, 50.0, 50.0, 50.0], [50.0, 50.0, 50.0, 50.0]]
    kb = [[50.0, 50.0, 50.0, 50.0], [0.0, 0.0, 0.0, 0.0]]
    vb = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    o_s, lse_s, sc = flash_attention(qb, kb, vb)
    # q0·k0 = 4·50·50 = 10000，×scale(=1/2) = 5000；q0·k1 = 0
    big = 10000.0 * sc
    close("大 logits 下 lse[0] = log(e^{10000·s} + 1)", lse_s[0],
          big + math.log(1.0 + math.exp(-big)))
    ok("大 logits 下结果有限", all(math.isfinite(x) for x in flat(o_s)))
    # 朴素「先 exp 再除」会溢出
    try:
        raw = math.exp(big)
        overflow = not math.isfinite(raw)
    except OverflowError:
        overflow = True
    ok("对照：直接 exp(10000·scale) 会溢出（%s）→ 必须先减 max" % overflow, overflow)
    ok("大 logits 下注意力几乎全给第一个 key（v[0]）", abs(o_s[0][0] - 1.0) < 1e-6)

    # ---- 8. bias 路径 ----
    q = rand_mat(5, 8, rnd)
    k = rand_mat(7, 8, rnd)
    v = rand_mat(7, 8, rnd)
    bias = [rnd.gauss(0, 1) for _ in range(7)]
    o_bias, _, _ = flash_attention(q, k, v, bias=bias)
    allclose("带 bias 时与朴素实现一致", flat(o_bias), flat(naive_attention(q, k, v, bias=bias)))
    o_nb, _, _ = flash_attention(q, k, v)
    ok("bias 确实改变了结果（负控）", flat(o_bias) != flat(o_nb))
    # 常数 bias 不改变 softmax 结果（softmax 平移不变）
    c = [2.5] * 7
    o_cb, _, _ = flash_attention(q, k, v, bias=c)
    allclose("常数 bias 不改变输出（softmax 平移不变）", flat(o_cb), flat(o_nb))

    # ---- 9. 退化形状 ----
    q1 = rand_mat(1, 4, rnd)
    k1 = rand_mat(1, 4, rnd)
    v1 = rand_mat(1, 4, rnd)
    o1, lse1, _ = flash_attention(q1, k1, v1)
    allclose("S=1：输出 = v[0]（唯一一个 key）", flat(o1), flat(v1))
    close("S=1：lse = q·k·scale", lse1[0], sum(a * b for a, b in zip(q1[0], k1[0])) * softmax_scale_for(4))
    q2 = rand_mat(3, 4, rnd)
    k2 = rand_mat(1, 4, rnd)
    v2 = rand_mat(1, 4, rnd)
    o2, _, _ = flash_attention(q2, k2, v2, causal=True)
    allclose("S_k=1 且因果：每行都只能看 j=0（非首行被全掩码）", flat(o2), flat(naive_attention(q2, k2, v2, causal=True)))

    print()
    print("断言总数 %d，失败 %d" % (PASSED + len(FAILED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  失败：%s" % f)
        raise SystemExit(1)
    print("FlashAttention 自检全部通过")


if __name__ == "__main__":
    main()
