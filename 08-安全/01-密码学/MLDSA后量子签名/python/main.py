"""ML-DSA（FIPS 204）核心机制演示。

跑法：`python main.py`
"""

import mldsa_params as P
import mldsa_ring as R
import mldsa_sample as S
import mldsa_pack as K

Q = P.Q


def show_params():
    print("== 1. 三档参数与长度 ==")
    print(f"{'档位':<10}{'k':>3}{'l':>3}{'eta':>5}{'tau':>5}{'beta':>6}"
          f"{'gamma1':>9}{'gamma2':>9}{'omega':>7}{'pk':>7}{'sig':>7}")
    for p in P.ALL:
        print(f"{p.name:<10}{p.k:>3}{p.l:>3}{p.eta:>5}{p.tau:>5}{p.beta:>6}"
              f"{p.gamma1:>9}{p.gamma2:>9}{p.omega:>7}{p.pk_bytes:>7}{p.sig_bytes:>7}")
    print("\n  beta = tau*eta；gamma2 只有 (Q-1)/88 与 (Q-1)/32 两种取值，"
          "\n  它同时决定了 w1 的位宽（6 位 / 4 位）与 hint 需要补偿的粒度。")


def show_rounding():
    print("\n== 2. 两种分解：Power2Round 与 Decompose ==")
    a = 7000000
    a1, a0 = R.power2round(a)
    print(f"  Power2Round({a}) = (a1={a1}, a0={a0})，校验 a1*2^13+a0 = {a1*(1<<13)+a0}")
    for prm in (P.ML_DSA_44, P.ML_DSA_65):
        g2 = prm.gamma2
        h1, l0 = R.decompose(a, g2)
        print(f"  {prm.name} Decompose: a1={h1}, a0={l0}, gamma2={g2}, "
              f"还原={h1*2*g2+l0} (mod Q = {(h1*2*g2+l0) % Q})")


def show_hint():
    print("\n== 3. hint：为什么要有它 ==")
    g2 = P.ML_DSA_44.gamma2
    w1 = 7
    for w0p in (0, g2 // 2, g2, g2 + 10, -g2 - 10):
        h = R.make_hint(w0p, w1, g2)
        v = R.freeze(w1 * 2 * g2 + w0p)
        direct = R.high_bits(v, g2)
        fixed = R.use_hint(v, h, g2)
        flag = "" if direct == w1 else "  <- 直接用 HighBits 会错"
        print(f"  w0'={w0p:>8}  hint={h}  HighBits(V)={direct:>2}  "
              f"UseHint(V,hint)={fixed:>2}{flag}")
    print("  结论：w0' 落出 [-gamma2, gamma2] 之后，只有带上 hint 才能还原签名方的 w1。")


def show_ntt():
    print("\n== 4. 不完全 NTT 的 2^-32 / 2^32 抵消 ==")
    f = [(i * i * 7 + 3 * i + 11) % Q for i in range(256)]
    g = [(5 * i + 1) % Q for i in range(256)]
    prod = R.invntt_tomont(R.pointwise_montgomery(R.ntt(f), R.ntt(g)))
    naive = schoolbook(f, g)
    ok = all((x - y) % Q == 0 for x, y in zip(prod, naive))
    print(f"  NTT 路径 vs 朴素负循环卷积：{'一致' if ok else '不一致'}"
          f"（逐点乘的 2^-32 抵消了逆变换的 2^32）")
    print(f"  montgomery_reduce(1) = {R.montgomery_reduce(1)}，"
          f"mod Q 后 = {R.montgomery_reduce(1) % Q} == "
          f"2^-32 mod Q = {pow(pow(2, 32, Q), -1, Q)}（返回的是 (-Q,Q) 有符号代表元）")


def schoolbook(a, b):
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


def show_sampling():
    print("\n== 5. 采样：拒绝采样与定重挑战 ==")
    e = S.poly_uniform_eta(bytes([1] * 32), 0, 2)
    print(f"  poly_uniform_eta(eta=2)：范围 [{min(e)}, {max(e)}]，256 个系数")
    c = S.poly_challenge(bytes([2] * 32), P.ML_DSA_44.tau)
    nz = [x for x in c if x]
    print(f"  poly_challenge(tau={P.ML_DSA_44.tau})：非零 {len(nz)} 个，"
          f"取值集合 {sorted(set(nz))}")
    y = S.poly_uniform_gamma1(bytes([3] * 32), 0, P.ML_DSA_44.gamma1)
    print(f"  poly_uniform_gamma1：范围 [{min(y)}, {max(y)}]，"
          f"gamma1={P.ML_DSA_44.gamma1}")


def show_packing():
    print("\n== 6. 位打包：每项的位宽由 gamma2 / eta / gamma1 唯一决定 ==")
    for prm in P.ALL:
        print(f"  {prm.name}: t1={prm.polyt1_bytes}B  t0={prm.polyt0_bytes}B  "
              f"s={prm.polyeta_bytes}B  z={prm.polyz_bytes}B  w1={prm.polyw1_bytes}B  "
              f"hint={prm.polyvec_h_bytes}B  -> sig={prm.sig_bytes}B")
    rows = [[0] * 256 for _ in range(4)]
    rows[0][7] = 1
    rows[2][200] = 1
    h = K.pack_hint(rows, 80, 4)
    print(f"  pack_hint 只存下标：前两字节 = {h[0]}, {h[1]}；"
          f"末 4 字节 = {list(h[80:84])}（每行用掉的槽数）")


def main():
    show_params()
    show_rounding()
    show_hint()
    show_ntt()
    show_sampling()
    show_packing()
    print("\n全部演示完成。断言版见 `python selfcheck_mldsa.py`。")


if __name__ == "__main__":
    main()
