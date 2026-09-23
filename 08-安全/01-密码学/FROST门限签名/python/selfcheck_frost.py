"""FROST 自检：把 RFC 9591 的每条性质变成可执行断言。

跑法：`python selfcheck_frost.py`
所有随机源都用固定字节钉死（见 `FIXED_*`）——随机行为不确定时「通过」只是运气。
"""

import frost_group as grp
import frost as F

Q = grp.Q
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


def is_prime(n, rounds=20):
    if n < 2:
        return False
    small = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37]
    for p in small:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for a in small:
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def test_group():
    check("q 是素数", is_prime(grp.Q))
    check("p 是素数", is_prime(grp.P))
    check("p == 2q+1（安全素数）", grp.P == 2 * grp.Q + 1)
    check("g 的阶是 q", pow(grp.G, grp.Q, grp.P) == 1)
    check("g 不是单位元", grp.G != 1)
    check("序列化元素是 33 字节", len(grp.serialize_element(grp.G)) == 33)
    check("序列化标量是 32 字节小端", len(grp.serialize_scalar(1)) == 32
          and grp.serialize_scalar(1)[0] == 1)
    check("反序列化元素会拒绝不在 q 阶子群里的值",
          _raises(lambda: grp.deserialize_element((grp.P - 1).to_bytes(33, "big"))))
    check("反序列化元素会拒绝单位元",
          _raises(lambda: grp.deserialize_element((1).to_bytes(33, "big"))))
    check("标量除法是真除法", grp.scalar_mul(grp.scalar_div(7, 3), 3) == 7)


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


def test_shamir():
    s = 0x1234567890ABCDEF
    coeffs = [0x1111, 0x2222]
    shares, full = F.secret_share_shard(s, coeffs, 5)
    check("分成 5 份", len(shares) == 5)
    check("每份的 x 是 1..5", [i for i, _ in shares] == [1, 2, 3, 4, 5])
    check("门限 3：任取 3 份都能还原",
          all(F.secret_share_combine([shares[i] for i in sub], 3) == s
              for sub in [(0, 1, 2), (1, 3, 4), (0, 2, 4), (2, 3, 4)]))
    check("门限 2：只给 2 份还原出的不是 s（信息不足）",
          F.secret_share_combine(shares[:2], 2) != s)
    check("拉格朗日插值与多项式求值一致",
          F.polynomial_evaluate(0, full) == s)
    # derive_interpolating_value 的错误分支
    check("x_i 不在 L 中时报错",
          _raises(lambda: F.derive_interpolating_value([1, 2, 3], 4)))
    check("L 有重复时报错",
          _raises(lambda: F.derive_interpolating_value([1, 2, 2], 1)))
    lam_sum = sum(F.derive_interpolating_value([1, 2, 3], i) for i in (1, 2, 3)) % Q
    check("三个拉格朗日系数之和为 1（常数项还原的充要条件）", lam_sum == 1)


def test_vss():
    s = 0xF00D
    coeffs = [0xABC, 0xDEF]
    shares, full = F.secret_share_shard(s, coeffs, 5)
    com = F.vss_commit(full)
    check("承诺个数 == 系数个数", len(com) == len(full))
    check("承诺的第 0 项是 g^s", com[0] == grp.scalar_base_mult(s))
    check("每份合法份额都能通过 VSS 校验",
          all(F.vss_verify(sh, com) for sh in shares))
    bad = (shares[0][0], (shares[0][1] + 1) % Q)
    check("被篡改的份额通不过 VSS 校验", not F.vss_verify(bad, com))


FIXED_H = [bytes([0xA0 + i] * 32) for i in range(8)]
FIXED_B = [bytes([0xB0 + i] * 32) for i in range(8)]


def setup(n=5, t=3, seed=0x5EED):
    """确定性搭一套 n 方、门限 t 的参数。"""
    s = (seed * 0x9E3779B97F4A7C15) % Q
    coeffs = [(seed + j) % Q for j in range(t - 1)]
    shares, full = F.secret_share_shard(s, coeffs, n)
    pk = grp.scalar_base_mult(s)
    pk_i = {i: grp.scalar_base_mult(y) for i, y in shares}
    return s, shares, pk, pk_i


def run_round(signers, sk, msg, fixed=True):
    """让 signers 这些参与者跑完两轮，返回 (commitment_list, shares)。"""
    nonces, comms = {}, {}
    for idx, i in enumerate(signers):
        rh = FIXED_H[idx] if fixed else None
        rb = FIXED_B[idx] if fixed else None
        nonces[i], comms[i] = F.commit(sk[i], rh, rb)
    clist = sorted((i, comms[i][0], comms[i][1]) for i in signers)
    zs = [F.sign(i, sk[i], PK, nonces[i], msg, clist) for i in signers]
    return clist, zs, comms


PK = None


def test_frost_e2e():
    global PK
    s, shares, PK, pk_i = setup()
    sk = dict(shares)
    signers = [1, 2, 3]
    msg = b"hello frost"
    clist, zs, comms = run_round(signers, sk, msg)
    check("三个参与者各出一个份额", len(zs) == 3)
    r, z = F.aggregate(clist, msg, PK, zs)
    check("聚合后的签名通过 Schnorr 验签", F.schnorr_verify(r, z, PK, msg))
    check("换消息则验签失败", not F.schnorr_verify(r, z, PK, b"other message"))
    # 份额可单独校验
    check("每个份额都能通过 verify_signature_share",
          all(F.verify_signature_share(i, pk_i[i], comms[i], zs[k],
                                       clist, PK, msg)
              for k, i in enumerate(signers)))
    # 少于门限：聚合不出有效签名
    clist2, zs2, _ = run_round([1, 2], sk, msg)
    r2, z2 = F.aggregate(clist2, msg, PK, zs2)
    check("只凑 2 份（< 门限 3）时聚合签名无效", not F.schnorr_verify(r2, z2, PK, msg))
    # 另一组参与者也该验过（门限内任意组合）
    clist3, zs3, _ = run_round([3, 4, 5], sk, msg)
    r3, z3 = F.aggregate(clist3, msg, PK, zs3)
    check("换成 {3,4,5} 组合同样有效", F.schnorr_verify(r3, z3, PK, msg))
    # 确定性：同样的输入得到同样的签名
    clist4, zs4, _ = run_round(signers, sk, msg)
    r4, z4 = F.aggregate(clist4, msg, PK, zs4)
    check("相同输入产生相同签名（nonce 由固定随机源钉死）", (r, z) == (r4, z4))


def test_identifiable_abort():
    s, shares, pk, pk_i = setup()
    sk = dict(shares)
    signers = [1, 2, 3]
    msg = b"abort demo"
    clist, zs, comms = run_round(signers, sk, msg)
    bad_z = list(zs)
    bad_z[1] = (bad_z[1] + 1) % Q  # 参与者 2 使坏
    r, z = F.aggregate(clist, msg, pk, bad_z)
    check("有坏份额时聚合签名验签失败", not F.schnorr_verify(r, z, pk, msg))
    verdicts = [F.verify_signature_share(i, pk_i[i], comms[i], bad_z[k], clist, pk, msg)
                for k, i in enumerate(signers)]
    check("可识别中止：只有参与者 2 的份额校验失败",
          verdicts == [True, False, True], f"{verdicts}")


def test_binding_factors():
    s, shares, pk, pk_i = setup()
    sk = dict(shares)
    msg1, msg2 = b"msg one", b"msg two"
    cl, _, _ = run_round([1, 2, 3], sk, msg1)
    bf1 = dict(F.compute_binding_factors(pk, cl, msg1))
    bf2 = dict(F.compute_binding_factors(pk, cl, msg2))
    check("绑定因子随消息改变（这正是抗 nonce 复用的关键）",
          bf1[1] != bf2[1])
    # 承诺列表顺序变了，哈希输入就变了，rho 随之改变
    rev = list(reversed(cl))
    bf_rev = dict(F.compute_binding_factors(pk, rev, msg1))
    check("承诺列表顺序改变会改变绑定因子",
          bf_rev[1] != bf1[1], f"{bf_rev[1]} vs {bf1[1]}")
    check("每个参与者有自己的绑定因子",
          len(set(bf1.values())) == 3)
    # 群承诺公式
    bfl = F.compute_binding_factors(pk, cl, msg1)
    r = F.compute_group_commitment(cl, bfl)
    manual = 1
    for ident, hiding, binding in cl:
        rho = F.binding_factor_for_participant(bfl, ident)
        manual = grp.element_add(manual, grp.scalar_mult(binding, rho))
        manual = grp.element_add(manual, hiding)
    check("群承诺满足 R = prod(D_i * E_i^rho_i)", r == manual)
    # nonce 生成带长期私钥
    n1 = F.nonce_generate(sk[1], FIXED_H[0])
    n2 = F.nonce_generate(sk[2], FIXED_H[0])
    check("nonce_generate 对同一随机源、不同私钥给出不同结果", n1 != n2)
    check("nonce_generate 对同一输入确定", n1 == F.nonce_generate(sk[1], FIXED_H[0]))


def test_nonce_reuse():
    """核心对照：朴素门限 Schnorr 在 nonce 复用下直接泄密，FROST 不会。"""
    s, shares, pk, pk_i = setup()
    sk = dict(shares)
    i = 1
    # --- 朴素方案：R 与消息无关，复用 nonce 即可解出私钥
    nonce = F.nonce_generate(sk[i], FIXED_H[0])
    d = grp.scalar_base_mult(nonce)
    r_naive = F.naive_group_commitment([d])
    m1, m2 = b"first", b"second"
    z1 = F.naive_sign_share(i, sk[i], nonce, pk, m1, r_naive)
    z2 = F.naive_sign_share(i, sk[i], nonce, pk, m2, r_naive)
    c1 = F.compute_challenge(r_naive, pk, m1)
    c2 = F.compute_challenge(r_naive, pk, m2)
    rec = F.recover_sk_from_reused_nonce(z1, z2, c1, c2)
    check("朴素方案：nonce 复用后能直接解出私钥份额", rec == sk[i],
          f"recovered={rec} sk={sk[i]}")

    # --- FROST：同一个 nonce 对复用在两条消息上，上面的式子不再成立
    cl, _, _ = run_round([1, 2, 3], sk, m1)
    bfl1 = F.compute_binding_factors(pk, cl, m1)
    bfl2 = F.compute_binding_factors(pk, cl, m2)
    hiding, binding = F.commit(sk[i], FIXED_H[0], FIXED_B[0])[0]
    rho1 = F.binding_factor_for_participant(bfl1, i)
    rho2 = F.binding_factor_for_participant(bfl2, i)
    r1 = F.compute_group_commitment(cl, bfl1)
    r2 = F.compute_group_commitment(cl, bfl2)
    cc1 = F.compute_challenge(r1, pk, m1)
    cc2 = F.compute_challenge(r2, pk, m2)
    xs = F.participants_from_commitment_list(cl)
    lam = F.derive_interpolating_value(xs, i)
    fz1 = (hiding + binding * rho1 + lam * sk[i] * cc1) % Q
    fz2 = (hiding + binding * rho2 + lam * sk[i] * cc2) % Q
    naive_rec = F.recover_sk_from_reused_nonce(fz1, fz2, cc1, cc2)
    check("FROST：同样的恢复式子解不出私钥（绑定因子破坏了可解性）",
          naive_rec != sk[i], f"got {naive_rec}")
    check("FROST：两条消息的群承诺也不同（rho 随消息变）", r1 != r2)


def main():
    test_group()
    test_shamir()
    test_vss()
    test_frost_e2e()
    test_identifiable_abort()
    test_binding_factors()
    test_nonce_reuse()
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
