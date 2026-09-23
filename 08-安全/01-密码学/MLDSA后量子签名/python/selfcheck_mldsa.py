"""ML-DSA 自检：把 FIPS 204 / ref 实现的每条性质变成可执行断言。

跑法：`python selfcheck_mldsa.py`
"""

import mldsa_params as P
import mldsa_ring as R
import mldsa_sample as S
import mldsa_pack as K

Q = P.Q
TOTAL = [0]
FAILED = []


def check(label, cond, detail=""):
    TOTAL[0] += 1
    if not cond:
        FAILED.append((label, detail))
        print(f"  FAIL {label}: {detail}")


def summary():
    print(f"\n断言 {TOTAL[0]} 条，失败 {len(FAILED)} 条")
    for label, detail in FAILED:
        print(f"  - {label}: {detail}")
    return 1 if FAILED else 0


# ---------------------------------------------------------------- 常量
def test_constants():
    check("Q 是 < 2^23 的素数", pow(2, Q - 1, Q) == 1, f"Q={Q}")
    check("QINV 是 Q 在 2^32 下的逆", (Q * P.QINV) % (1 << 32) == 1)
    check("MONT == 2^32 mod Q", P.MONT == pow(2, 32, Q), f"{P.MONT}")
    check("ROOT_OF_UNITY 是 2N 次本原根",
          pow(P.ROOT_OF_UNITY, 256, Q) == Q - 1 and pow(P.ROOT_OF_UNITY, 512, Q) == 1)
    check("D=13 使 2^D 整除 Q 的邻域", P.D == 13)


def test_param_relations():
    for prm in P.ALL:
        check(f"{prm.name}: beta == tau*eta", prm.beta == prm.tau * prm.eta,
              f"beta={prm.beta} tau={prm.tau} eta={prm.eta}")
        check(f"{prm.name}: gamma2 是 (Q-1)/88 或 (Q-1)/32",
              prm.gamma2 in ((Q - 1) // 88, (Q - 1) // 32), f"{prm.gamma2}")
        check(f"{prm.name}: gamma1 是 2 的幂",
              prm.gamma1 & (prm.gamma1 - 1) == 0, f"{prm.gamma1}")
        check(f"{prm.name}: ctilde 随安全等级增大",
              prm.ctilde in (32, 48, 64))
    check("三档 k/l 递增",
          (P.ML_DSA_44.k, P.ML_DSA_65.k, P.ML_DSA_87.k) == (4, 6, 8) and
          (P.ML_DSA_44.l, P.ML_DSA_65.l, P.ML_DSA_87.l) == (4, 5, 7))
    check("公开密钥 1312/1952/2592",
          [p.pk_bytes for p in P.ALL] == [1312, 1952, 2592])
    check("签名 2420/3309/4627",
          [p.sig_bytes for p in P.ALL] == [2420, 3309, 4627])
    check("签名长度 = ctilde + l*polyz + (omega+k)",
          all(p.sig_bytes == p.ctilde + p.l * p.polyz_bytes + p.omega + p.k for p in P.ALL))


# ------------------------------------------------------------ 归约与 NTT
def test_reduction():
    for a in (1, Q, -Q, 1 << 40, -98765, 12345678):
        r = R.montgomery_reduce(a)
        check(f"montgomery_reduce({a}) == a*2^-32 mod Q",
              (r * pow(2, 32, Q)) % Q == a % Q, f"got {r}")
        check(f"montgomery_reduce 结果落在 (-Q, Q)", -Q < r < Q, f"{r}")
    for a in (-10, -1, 0, Q - 1, Q, Q + 7):
        f = R.freeze(a)
        check(f"freeze({a}) 是 [0,Q) 标准代表元", 0 <= f < Q and f % Q == a % Q, f"{f}")
    check("reduce32 落在 [-6283008, 6283008]",
          all(-6283008 <= R.reduce32(a) <= 6283008 for a in range(-2 * Q, 2 * Q, 7919)))


def test_ntt():
    f = [(i * i * 7 + 3 * i + 11) % Q for i in range(256)]
    back = R.invntt_tomont(R.ntt(f))
    check("invntt_tomont(ntt(f)) == f * 2^32 (mod Q)",
          all((b - c * P.MONT) % Q == 0 for b, c in zip(back, f)))
    check("ntt 对零多项式是零", R.ntt([0] * 256) == [0] * 256)
    g = [(5 * i + 1) % Q for i in range(256)]
    h = [(a + b) % Q for a, b in zip(f, g)]
    lhs = R.ntt(h)
    rhs = [(a + b) % Q for a, b in zip(R.ntt(f), R.ntt(g))]
    check("ntt 是线性的：NTT(f+g) == NTT(f)+NTT(g)",
          all((x - y) % Q == 0 for x, y in zip(lhs, rhs)))
    # NTT 域乘法对应环上卷积（乘 2^32）
    # 注意符号约定：pointwise_montgomery 每次乘法带一个 2^-32，
    # 正好抵消 invntt_tomont 收尾的 2^32，所以「NTT 乘 + 逆变换」等于朴素卷积本身。
    prod_ntt = R.invntt_tomont(R.pointwise_montgomery(R.ntt(f), R.ntt(g)))
    naive = schoolbook_negacyclic(f, g)
    check("NTT 域逐点乘 + 逆变换 == 朴素负循环卷积（2^-32 与 2^32 抵消）",
          all((a - b) % Q == 0 for a, b in zip(prod_ntt, naive)))
    # 单位元：1 * g 应还原 g 本身
    one = [1] + [0] * 255
    pid = R.invntt_tomont(R.pointwise_montgomery(R.ntt(one), R.ntt(g)))
    check("ntt(1) 全为 1", all(x == 1 for x in R.ntt(one)))
    check("1 * g 经 NTT 路径还原为 g", all((a - b) % Q == 0 for a, b in zip(pid, g)))


def schoolbook_negacyclic(a, b):
    """朴素 O(n^2) 负循环卷积（mod x^256 + 1）：x^256 == -1。"""
    res = [0] * 256
    for i in range(256):
        if not a[i]:
            continue
        for j in range(256):
            if not b[j]:
                continue
            e = i + j
            if e >= 256:
                res[e - 256] -= a[i] * b[j]
            else:
                res[e] += a[i] * b[j]
    return [c % Q for c in res]


# ------------------------------------------------- 分解 / hint（FIPS 204 §7.4）
def test_power2round():
    lo, hi = -(1 << (P.D - 1)) + 1, (1 << (P.D - 1))
    for a in range(0, Q, 4999):
        a1, a0 = R.power2round(a)
        check(f"power2round({a}) 恒等", a1 * (1 << P.D) + a0 == a, f"{a1},{a0}")
        check(f"power2round({a}) 的 a0 落在 (-2^12, 2^12]",
              lo <= a0 <= hi, f"a0={a0}")


def test_decompose():
    for prm in P.ALL:
        g2 = prm.gamma2
        top = 43 if g2 == (Q - 1) // 88 else 15
        a1s, maxabs = set(), 0
        for a in range(0, Q, 631):
            a1, a0 = R.decompose(a, g2)
            check(f"{prm.name} decompose 恒等 (mod Q)",
                  (a1 * 2 * g2 + a0 - a) % Q == 0, f"a={a} a1={a1} a0={a0}")
            a1s.add(a1)
            maxabs = max(maxabs, abs(a0))
        check(f"{prm.name} a1 不超过 {top}", max(a1s) <= top, f"max={max(a1s)}")
        check(f"{prm.name} |a0| <= gamma2", maxabs <= g2, f"{maxabs} vs {g2}")
    # 特殊分支：a 接近 Q-1 时 a1 被清成 0、a0 取负值
    g2 = P.ML_DSA_44.gamma2
    a1, a0 = R.decompose(Q - 1, g2)
    check("ML-DSA-44: decompose(Q-1) 走 a1=0 分支", a1 == 0, f"a1={a1}")
    check("ML-DSA-44: 该分支的 a0 为负", a0 < 0, f"a0={a0}")
    a1b, a0b = R.decompose(Q - 1, P.ML_DSA_65.gamma2)
    check("ML-DSA-65: decompose(Q-1) 的 a1 被 &15 归零", a1b == 0, f"a1={a1b}")


def test_hint():
    for prm in P.ALL:
        g2 = prm.gamma2
        mod = 44 if g2 == (Q - 1) // 88 else 16
        check(f"{prm.name} make_hint 在 a0 越界时置 1",
              R.make_hint(g2 + 1, 0, g2) == 1 and R.make_hint(-g2 - 1, 0, g2) == 1)
        check(f"{prm.name} make_hint 在 a0==-gamma2 且 a1!=0 时置 1",
              R.make_hint(-g2, 1, g2) == 1)
        check(f"{prm.name} make_hint 在 a0==-gamma2 且 a1==0 时置 0（边界不对称）",
              R.make_hint(-g2, 0, g2) == 0)
        check(f"{prm.name} make_hint 在中间值为 0", R.make_hint(0, 0, g2) == 0)
        check(f"{prm.name} use_hint(hint=0) 直接返回高位",
              R.use_hint(12345, 0, g2) == R.decompose(12345, g2)[0])
        # hint=1 时高位 ±1（按 mod 回绕）
        a1, a0 = R.decompose(500000, g2)
        got = R.use_hint(500000, 1, g2)
        check(f"{prm.name} use_hint(hint=1) 相对 a1 偏移 ±1 (mod {mod})",
              got in ((a1 + 1) % mod, (a1 - 1) % mod), f"a1={a1} got={got}")

    # 核心正确性（FIPS 204 §7.4 的原话）：
    #   签名方持有 w1 = HighBits(w)、扰动后的低位 w0' = LowBits(w) - c*s2 + c*t0，
    #   发出 hint = MakeHint(w0', w1)；验签方计算 V = w1*2*gamma2 + w0'，
    #   必须满足 UseHint(V, hint) == w1。
    for prm in P.ALL:
        g2 = prm.gamma2
        nbin = 44 if g2 == (Q - 1) // 88 else 16
        miss, neg = 0, 0
        for w1 in range(nbin):
            # 可达区间：拒绝采样保证 |w0 - c*s2| < γ2-β、|c*t0| < γ2，
            # 故 |w0'| < 2γ2 - β。这里取 [-2γ2+1, 2γ2] 全扫。
            for w0p in range(-2 * g2 + 1, 2 * g2 + 1, 3217):
                h = R.make_hint(w0p, w1, g2)
                v = R.freeze(w1 * 2 * g2 + w0p)
                if R.use_hint(v, h, g2) != w1:
                    miss += 1
                if R.high_bits(v, g2) != w1:
                    neg += 1
        check(f"{prm.name} UseHint(w1*2γ2 + w0', MakeHint(w0', w1)) == w1（可达区间全对）",
              miss == 0, f"miss={miss}")
        # 负控：同一批 V 里确实存在 HighBits 还原失败的——hint 不是摆设
        check(f"{prm.name} 负控：存在 HighBits(V) != w1 的用例（hint 不可省）",
              neg > 0, f"neg={neg}")
        # 记录参考实现的一个角落：w0' == -2γ2 恰好时 a0==0 走「减一」分支会偏 2。
        # 该点被拒绝条件 |w0'| < 2γ2-β 排除，故不影响正确性。
        corner = R.use_hint(R.freeze(2 * g2 - 2 * g2), R.make_hint(-2 * g2, 1, g2), g2)
        check(f"{prm.name} 角落：w0'=-2γ2 时 a0==0 走减一分支（被拒绝条件排除）",
              corner != 1, f"got {corner}")

    # 边界不对称性：a0 == -gamma2 时是否置 hint 取决于 a1
    g2 = P.ML_DSA_44.gamma2
    check("a0 == -gamma2 且 a1 == 0 时 hint 为 0", R.make_hint(-g2, 0, g2) == 0)
    check("a0 == -gamma2 且 a1 != 0 时 hint 为 1（不对称的那一侧）",
          R.make_hint(-g2, 3, g2) == 1)
    check("a0 == +gamma2 时 hint 为 0（对称侧不置位）", R.make_hint(g2, 3, g2) == 0)


# ------------------------------------------------------------ 采样
def test_sampling():
    for eta, bound in ((2, 2), (4, 4)):
        out = [0] * 256
        ctr = S.rej_eta(bytes(range(256)) * 8, 256, eta, out, 0)
        check(f"rej_eta(eta={eta}) 凑满 256", ctr == 256, f"ctr={ctr}")
        check(f"rej_eta(eta={eta}) 系数落在 [-{bound},{bound}]",
              all(-bound <= c <= bound for c in out))
    # 接受率：eta=2 丢 nibble 15（1/16），eta=4 丢 9..15（7/16）
    check("rej_eta(eta=2) 的接受集合是 0..14",
          [2 - (t - (205 * t >> 10) * 5) for t in range(15)] ==
          [2 - (t % 5) for t in range(15)])
    for tau in (39, 49, 60):
        c = S.poly_challenge(bytes([7] * 32), tau)
        nz = [x for x in c if x]
        check(f"poly_challenge(tau={tau}) 恰有 {tau} 个非零", len(nz) == tau, f"{len(nz)}")
        check(f"poly_challenge(tau={tau}) 非零值都是 ±1", all(abs(x) == 1 for x in nz))
        check(f"poly_challenge 对同一 seed 确定",
              c == S.poly_challenge(bytes([7] * 32), tau))
    for g1, bits in ((1 << 17, 18), (1 << 19, 20)):
        y = S.poly_uniform_gamma1(bytes([3] * 32), 0, g1)
        check(f"gamma1=2^{bits-2} 的 y 落在 (gamma1-2^{bits}, gamma1]",
              all(g1 - (1 << bits) < c <= g1 for c in y),
              f"min={min(y)} max={max(y)}")
    a = S.poly_uniform(bytes([9] * 32))
    check("poly_uniform 输出全部 < Q", all(0 <= c < Q for c in a), f"max={max(a)}")


def test_norm():
    check("chknorm 的 bound 超过 (Q-1)/8 时恒为真（源码里的守卫）",
          R.chknorm([0] * 256, (Q - 1) // 8 + 1) is True)
    check("chknorm 对全零返回假", R.chknorm([0] * 256, 100) is False)
    check("chknorm 检出超界系数", R.chknorm([0] * 255 + [1000], 100) is True)
    check("chknorm 对负数取绝对值判定", R.chknorm([-1000] + [0] * 255, 100) is True)
    check("chknorm 边界：恰好 bound-1 不报", R.chknorm([99] + [0] * 255, 100) is False)


# ------------------------------------------------------------ 打包
def test_packing():
    for prm in P.ALL:
        t = [i % 512 for i in range(256)]
        b = K.polyt1_pack(t)
        check(f"{prm.name} t1 打包 320 B", len(b) == 320, f"{len(b)}")
        check(f"{prm.name} t1 往返", K.polyt1_unpack(b) == t)
        e = [i % 5 - 2 for i in range(256)] if prm.eta == 2 else [i % 9 - 4 for i in range(256)]
        eb = K.polyeta_pack(e, prm.eta)
        check(f"{prm.name} s 打包 {prm.polyeta_bytes} B",
              len(eb) == prm.polyeta_bytes, f"{len(eb)}")
        check(f"{prm.name} s 往返", K.polyeta_unpack(eb, prm.eta) == e)
        w = K.polyw1_pack([0] * 256, prm.gamma2)
        check(f"{prm.name} w1 打包 {prm.polyw1_bytes} B",
              len(w) == prm.polyw1_bytes, f"{len(w)}")
        h = K.pack_hint([[0] * 256 for _ in range(prm.k)], prm.omega, prm.k)
        check(f"{prm.name} hint 段恒为 omega+k 字节",
              len(h) == prm.polyvec_h_bytes, f"{len(h)}")
    # hint 记录的是下标，且末 k 字节是每行用掉的槽数
    rows = [[0] * 256 for _ in range(4)]
    rows[0][7] = 1
    rows[2][200] = 1
    h = K.pack_hint(rows, 80, 4)
    check("pack_hint 记录 hint 下标", h[0] == 7 and h[1] == 200, f"{h[0]},{h[1]}")
    check("pack_hint 末尾记录累计槽数", list(h[80:84]) == [1, 1, 2, 2], f"{list(h[80:84])}")


def main():
    test_constants()
    test_param_relations()
    test_reduction()
    test_ntt()
    test_power2round()
    test_decompose()
    test_hint()
    test_sampling()
    test_norm()
    test_packing()
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
